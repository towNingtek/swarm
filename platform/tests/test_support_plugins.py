"""PLATFORM_PLUGINS: loading, isolation of failures, service call sites, and the
SiteScheduler plugin hooks (site_office, site_runner, run_check, tick_hooks)."""
import io
import contextlib
import os
import tempfile
import textwrap
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import support_plugins
from support_core import SupportCore
from support_site_office import OfficeError, SiteOffice
from support_site_scheduler import TZ, SiteScheduler

ORIGIN = 'https://platform.example'


def write_plugin(root, name, body):
    d = Path(root) / name
    d.mkdir(parents=True)
    (d / 'plugin.py').write_text(textwrap.dedent(body))
    return str(d)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def run_install(self, value, **objects):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            done = support_plugins.install_plugins('platform', value, **objects)
        return done, out.getvalue()

    def test_dirs_are_split_and_deduplicated(self):
        self.assertEqual(support_plugins.plugin_dirs(' /a, /b ,,/a '), ['/a', '/b'])
        self.assertEqual(support_plugins.plugin_dirs(''), [])
        self.assertEqual(support_plugins.plugin_dirs(None), [])

    def test_install_receives_context_and_can_import_its_own_modules(self):
        seen = []
        d = write_plugin(self.root, 'good', '''
            import helper_mod_for_test
            def install(context):
                context.sink.append((context.service, context.plugin_dir, context.item,
                                     helper_mod_for_test.VALUE))
        ''')
        Path(d, 'helper_mod_for_test.py').write_text('VALUE = 7\n')
        done, log = self.run_install(d, sink=seen, item='x')
        self.assertEqual(done, [d])
        self.assertEqual(seen, [('platform', d, 'x', 7)])
        self.assertIn('installed (platform)', log)

    def test_broken_plugins_are_skipped_and_others_still_install(self):
        seen = []
        bad_import = write_plugin(self.root, 'badimport', 'raise RuntimeError("boom")\n')
        no_install = write_plugin(self.root, 'noinstall', 'X = 1\n')
        raises = write_plugin(self.root, 'raises', '''
            def install(context):
                raise ValueError('nope')
        ''')
        good = write_plugin(self.root, 'good2', '''
            def install(context):
                context.sink.append('good')
        ''')
        missing = str(Path(self.root) / 'missing')
        relative = 'relative/dir'
        value = ','.join([bad_import, no_install, raises, missing, relative, good])
        done, log = self.run_install(value, sink=seen)
        self.assertEqual(done, [good])
        self.assertEqual(seen, ['good'])
        self.assertEqual(log.count('skipped'), 5)

    def test_default_reads_the_environment(self):
        seen = []
        d = write_plugin(self.root, 'envplug', '''
            def install(context):
                context.sink.append(context.service)
        ''')
        with patch.dict(os.environ, {'PLATFORM_PLUGINS': d}):
            with contextlib.redirect_stdout(io.StringIO()):
                support_plugins.install_plugins('admin', sink=seen)
        self.assertEqual(seen, ['admin'])


class ServiceCallSiteTests(unittest.TestCase):
    """Both services call plugins; a failing plugin never stops them."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / 'platform.sqlite')
        self.plugin = write_plugin(self.root, 'routes', '''
            from starlette.requests import Request
            from starlette.responses import JSONResponse
            def install(context):
                names = sorted(k for k, v in vars(context).items())
                # Console routes live under /admin inside the console app.
                target, path = ((context.app, '/plugin-probe') if context.service == 'platform'
                                else (context.admin_app, '/admin/plugin-probe'))
                async def probe(request: Request):
                    return JSONResponse({'service': context.service, 'fields': names,
                                         'scheduler': context.scheduler is not None})
                target.add_api_route(path, probe, methods=['GET'])
        ''')
        self.broken = write_plugin(self.root, 'broken', '''
            def install(context):
                raise RuntimeError('broken plugin')
        ''')

    def build_platform(self, **extra):
        import platform_service
        env = {'PLATFORM_ORIGIN': ORIGIN, 'PLATFORM_DB': self.db, 'SWARM_DOMAIN': 'example.com',
               **extra}
        with patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return platform_service.build()

    def test_platform_service_installs_plugins_and_survives_a_broken_one(self):
        app, _ = self.build_platform(PLATFORM_PLUGINS=f'{self.broken},{self.plugin}',
                                     PLATFORM_SITE_SCHEDULER='1')
        self.assertIsNotNone(app.state.site_scheduler)
        client = TestClient(app, base_url=ORIGIN)
        self.addCleanup(client.close)
        r = client.get('/plugin-probe', headers={'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {'service': 'platform', 'scheduler': True, 'fields': r.json()['fields']})
        route = [x for x in app.routes if getattr(x, 'path', None) == '/plugin-probe']
        self.assertEqual(len(route), 1)

    def test_platform_context_fields(self):
        d = write_plugin(self.root, 'fields', '''
            import json, os
            def install(context):
                with open(os.environ['PLUGIN_OUT'], 'w') as fh:
                    json.dump(sorted(vars(context)), fh)
        ''')
        out = self.root / 'fields.json'
        self.build_platform(PLATFORM_PLUGINS=d, PLUGIN_OUT=str(out))
        import json
        fields = json.loads(out.read_text())
        for name in ('service', 'plugin_dir', 'app', 'core', 'origin', 'db_path', 'sites_root',
                     'domain', 'office', 'task_library', 'scheduler', 'notifier',
                     'line_delivery', 'alerts', 'provisioner'):
            self.assertIn(name, fields)

    def test_platform_without_plugins_is_unchanged(self):
        app, _ = self.build_platform()
        self.assertFalse([x for x in app.routes if getattr(x, 'path', None) == '/plugin-probe'])

    def test_admin_service_installs_plugins_on_the_console_app(self):
        import admin_service
        self.build_platform()        # creates the database
        env = {'ADMIN_ORIGIN': ORIGIN, 'PLATFORM_ORIGIN': ORIGIN, 'PLATFORM_DB': self.db,
               'ADMIN_PASSWORD': 'operator-password-123', 'SWARM_DOMAIN': 'example.com',
               'PLATFORM_PLUGINS': f'{self.broken},{self.plugin}'}
        with patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            app = admin_service.build()
        inner = app.state.support_admin_app
        self.assertTrue([x for x in inner.routes if getattr(x, 'path', None) == '/admin/plugin-probe'])
        client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        self.addCleanup(client.close)
        # Not signed in: the console boundary answers before the plugin route.
        self.assertIn(client.get('/admin/plugin-probe').status_code, (302, 401, 403))
        r = client.post('/admin/login', data={'password': 'operator-password-123'},
                        headers={'Origin': ORIGIN, 'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(r.status_code, 303)
        r = client.get('/admin/plugin-probe', headers={'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['service'], 'admin')
        self.assertIn('admin_app', r.json()['fields'])


REGISTRY = ('hives:\n  - id: shop\n    enabled: true\n    agents:\n      pm:\n'
            '        daily: "0 9 * * *"\n')
PROJECT = 'project: {name: Shop}\nreports:\n  daily:\n    context: 整理今日重點\n'


class SchedulerHookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.sites = root / 'sites'
        ws = self.sites / 'acme' / 'workspace'
        (ws / '.swarm').mkdir(parents=True)
        (ws / 'hives' / 'shop').mkdir(parents=True)
        (ws / '.swarm' / 'registry.yaml').write_text(REGISTRY)
        (ws / 'hives' / 'shop' / 'project.yaml').write_text(PROJECT)
        (self.sites / 'acme' / 'site.yaml').write_text('name: acme\n')
        self.core = SupportCore(str(root / 'db.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.tenant = self.core.create_tenant(self.core.admin_actor('test'), 'platform.example')
        self.now = [datetime(2026, 10, 16, 8, 59, 30, tzinfo=TZ).timestamp()]
        self.default_runs = []
        self.sched = SiteScheduler(
            self.core, SiteOffice(self.sites, 'example.cc'), clock=lambda: self.now[0],
            runner=lambda site, prompt: (self.default_runs.append(site), (True, 'default'))[1])
        self.sched.sites = lambda: {self.tenant.id: 'acme.example.cc'}

    def wait(self):
        for _ in range(250):
            runs = self.sched.runs_for(self.tenant.id)
            if runs and all(r['status'] != 'running' for r in runs):
                return runs
            threading.Event().wait(0.02)
        self.fail('run did not finish')

    def due(self):
        self.sched.tick()
        self.now[0] += 60
        return self.sched.tick()

    def test_without_hooks_the_default_runner_is_used(self):
        self.assertEqual([s['name'] for s in self.due()], ['daily'])
        [run] = self.wait()
        self.assertEqual((run['status'], run['output']), ('succeeded', 'default'))
        self.assertEqual(self.default_runs, ['acme'])

    def test_site_runner_replaces_the_default_runner(self):
        seen = []

        def runner(prompt, meta):
            seen.append(meta)
            return True, 'from plugin'
        self.sched.site_runner = lambda site, settings: runner if site == 'acme' else None
        self.due()
        [run] = self.wait()
        self.assertEqual(run['output'], 'from plugin')
        self.assertEqual(self.default_runs, [])
        self.assertEqual({k: seen[0][k] for k in ('tenant_id', 'hive_id', 'role', 'report_name',
                                                  'deliver')},
                         {'tenant_id': self.tenant.id, 'hive_id': 'shop', 'role': 'pm',
                          'report_name': 'daily', 'deliver': True})
        self.assertEqual(seen[0]['run_id'], run['id'])

    def test_site_runner_returning_none_or_raising_falls_back(self):
        self.sched.site_runner = lambda site, settings: None
        self.due()
        self.wait()
        self.assertEqual(self.default_runs, ['acme'])

        def boom(site, settings):
            raise RuntimeError('x')
        self.sched.site_runner = boom
        with contextlib.redirect_stdout(io.StringIO()):
            self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily')
        self.wait()
        self.assertEqual(self.default_runs, ['acme', 'acme'])

    def test_site_runner_errors_fail_the_run_without_delivery(self):
        class Delivery:
            calls = []

            def after_run(self, **kw):
                self.calls.append(kw)
                return ''

            def purge(self):
                return 0

            def check_quotas(self, sites):
                return {}
        self.sched.delivery = Delivery()

        def runner(prompt, meta):
            raise RuntimeError('broken')
        self.sched.site_runner = lambda site, settings: runner
        with contextlib.redirect_stdout(io.StringIO()):
            self.due()
        [run] = self.wait()
        self.assertEqual(run['status'], 'failed')
        self.assertIn('RuntimeError', run['output'])
        self.assertEqual(Delivery.calls, [])

    def test_site_office_supplies_schedules_and_settings(self):
        office = {'hives': [{'id': 'other', 'name': 'other', 'enabled': True, 'exists': True,
                             'schedules': [{'role': 'pm', 'name': 'plug', 'cron': '0 9 * * *',
                                            'skill': None, 'context': '', 'state': 'ready'}]}]}
        got = []
        self.sched.site_office = lambda site, host: (office, {'mark': site})
        self.sched.site_runner = lambda site, settings: (got.append(settings),
                                                         lambda p, m: (True, 'ok'))[1]
        self.assertEqual([s['name'] for s in self.due()], ['plug'])
        self.wait()
        self.assertEqual(got, [{'mark': 'acme'}])

    def test_site_office_none_or_error_uses_the_default_office(self):
        self.sched.site_office = lambda site, host: None
        self.assertEqual([s['name'] for s in self.due()], ['daily'])
        self.wait()

        def boom(site, host):
            raise RuntimeError('x')
        self.sched.site_office = boom
        with contextlib.redirect_stdout(io.StringIO()):
            run = self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily')
        self.assertEqual(run['name'], 'daily')
        self.wait()

    def test_run_check_can_refuse_a_manual_run(self):
        seen = []

        def check(site, settings, job, *, deliver, operator):
            seen.append((site, job['name'], deliver, operator))
            if deliver:
                raise OfficeError('不行')
        self.sched.run_check = check
        with self.assertRaises(OfficeError):
            self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily')
        self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily', deliver=False,
                           operator=True)
        self.wait()
        self.assertEqual(seen, [('acme', 'daily', True, False), ('acme', 'daily', False, True)])

    def test_tick_hooks_run_every_tick_and_errors_are_contained(self):
        calls = []

        def boom(s):
            raise RuntimeError('x')
        self.sched.tick_hooks.extend([boom, lambda s: calls.append(s)])
        with contextlib.redirect_stdout(io.StringIO()):
            self.sched.tick()
            self.now[0] += 20
            self.sched.tick()
        self.assertEqual(calls, [self.sched, self.sched])
        self.assertEqual(self.sched.last_tick_at, self.now[0])


if __name__ == '__main__':
    unittest.main()
