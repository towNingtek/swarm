"""Task library (operator prompts handed to sites) and the site scheduler."""
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from support_chat_app import create_customer_chat_app
from support_core import SupportCore, Unauthorized
from support_rooms import SupportRooms
from support_site_office import OfficeError, SiteOffice, read_office
from support_site_scheduler import TZ, SiteScheduler, build_prompt, cron_matches
from support_task_library import TaskLibrary

TEMPLATE = {'name': 'daily_todo', 'title': '每日待辦', 'role': 'pm', 'cron': '0 9 * * *',
            'skill': '', 'context': '整理今天要做的三件事', 'is_default': True}


def make_site(sites, name, *, hive=True, enabled=True):
    ws = sites / name / 'workspace'
    (ws / '.swarm').mkdir(parents=True)
    if hive:
        (ws / 'hives' / 'shop').mkdir(parents=True)
        (ws / '.swarm' / 'registry.yaml').write_text(
            '# 保留\nhives:\n  - id: shop\n    enabled: %s\n    agents:\n      pm:\n        model: ""\n'
            '      marketing:\n        model: ""\n' % ('true' if enabled else 'false'))
        (ws / 'hives' / 'shop' / 'project.yaml').write_text('project:\n  name: 小店\nreports: {}\n')
    else:
        (ws / '.swarm' / 'registry.yaml').write_text('hives: []\n')
    return ws


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.sites = root / 'sites'
        self.core = SupportCore(str(root / 'db.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.office = SiteOffice(self.sites, 'example.cc')
        self.now = [datetime(2026, 9, 29, 8, 59, 30, tzinfo=TZ).timestamp()]
        self.lib = TaskLibrary(self.core, self.office, clock=lambda: self.now[0])
        self.tenant = self.core.create_tenant(self.admin, 'platform.example')

    def schedules(self, name='ab'):
        return read_office(self.sites / name)['hives'][0]['schedules']


class LibraryTests(Base):
    def test_admin_only_and_validation(self):
        with self.assertRaises(Unauthorized):
            self.lib.templates(object())
        for bad in ({'cron': '0 9 * * 1'}, {'name': 'Bad Name'}, {'context': ''},
                    {'context': 'key sk-abcdefghijklmnopqrstuv'}, {'is_default': 'yes'}):
            with self.assertRaises(OfficeError):
                self.lib.save_template(self.admin, {**TEMPLATE, **bad})
        self.lib.save_template(self.admin, TEMPLATE)
        with self.assertRaises(OfficeError):
            self.lib.save_template(self.admin, TEMPLATE)  # duplicate
        rows = self.lib.save_template(self.admin, {**TEMPLATE, 'name': 'todo2'}, original='daily_todo')
        self.assertEqual([r['name'] for r in rows], ['todo2'])

    def test_direct_waits_for_hive_then_applies_once(self):
        make_site(self.sites, 'ab', hive=False)
        self.lib.save_template(self.admin, TEMPLATE)
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'direct')
        hosts = {self.tenant.id: 'ab.example.cc'}
        self.assertEqual(self.lib.apply_pending(hosts), 0)  # no hive yet
        self.assertEqual(self.lib.deliveries(self.admin, self.tenant.id)[0]['status'], 'pending')
        # Customer creates a hive; the delivery lands in it.
        import shutil
        shutil.rmtree(self.sites / 'ab')
        make_site(self.sites, 'ab')
        self.assertEqual(self.lib.apply_pending(hosts), 1)
        [job] = self.schedules()
        self.assertEqual((job['name'], job['role'], job['cron'], job['state']),
                         ('daily_todo', 'pm', '0 9 * * *', 'ready'))
        self.assertEqual(self.lib.apply_pending(hosts), 0)  # applied at most once
        # Template edits never rewrite the site: it is the customer's now.
        self.lib.save_template(self.admin, {**TEMPLATE, 'context': '改了'}, original='daily_todo')
        self.assertEqual(self.schedules()[0]['context'], '整理今天要做的三件事')
        ws = self.sites / 'ab' / 'workspace'
        self.assertTrue((ws / '.swarm' / 'registry.yaml').read_text().startswith('# 保留'))
        self.assertTrue(list((ws / '.swarm' / '.backups').iterdir()))

    def test_name_clash_and_missing_role_fall_back(self):
        make_site(self.sites, 'ab')
        self.lib.save_template(self.admin, {**TEMPLATE, 'role': 'bd'})
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'direct')
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'direct')
        self.assertEqual(self.lib.apply_pending({self.tenant.id: 'ab.example.cc'}), 2)
        jobs = sorted((j['name'], j['role']) for j in self.schedules())
        self.assertEqual(jobs, [('daily_todo', 'pm'), ('daily_todo_2', 'pm')])

    def test_long_names_that_clash_get_a_suffix_and_terminate(self):
        make_site(self.sites, 'ab')
        for length in (61, 62, 63):
            long_name = 'x' * length
            self.lib.save_template(self.admin, {**TEMPLATE, 'name': long_name, 'is_default': False})
            for _ in range(3):
                self.lib.push(self.admin, self.tenant.id, [long_name], 'direct')
        # 9 deliveries but MAX_SCHEDULES caps the hive; it must finish, not hang.
        done = threading.Event()
        threading.Thread(target=lambda: (self.lib.apply_pending({self.tenant.id: 'ab.example.cc'}),
                                         done.set()), daemon=True).start()
        self.assertTrue(done.wait(10), 'apply_pending hung on long clashing names')
        names = [j['name'] for j in self.schedules()]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(len(n) <= 63 for n in names))
        self.assertIn('x' * 61 + '_2', names)

    def test_one_unreadable_site_never_blocks_another(self):
        make_site(self.sites, 'ab')
        bad = make_site(self.sites, 'cd')
        other = self.core.create_tenant(self.admin, 'platform2.example')
        self.lib.save_template(self.admin, TEMPLATE)
        self.lib.push(self.admin, other.id, ['daily_todo'], 'direct')
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'direct')
        hosts = {other.id: 'cd.example.cc', self.tenant.id: 'ab.example.cc'}
        for registry in ('hives:\n- id: shop\n  enabled: true\n  x: 2024-13-45\n',
                         'hives: ' + '[' * 5000 + ']' * 5000 + '\n'):
            with self.subTest(registry=registry[:30]):
                (bad / '.swarm' / 'registry.yaml').write_text(registry)
                self.assertEqual(self.office.for_host('cd.example.cc')['registry'], 'unreadable')
        self.assertEqual(self.lib.apply_pending(hosts), 1)
        self.assertEqual([j['name'] for j in self.schedules()], ['daily_todo'])

    def test_seed_defaults_only_default_templates(self):
        self.lib.save_template(self.admin, TEMPLATE)
        self.lib.save_template(self.admin, {**TEMPLATE, 'name': 'extra', 'is_default': False})
        self.assertEqual(self.lib.seed_defaults(self.tenant.id), 1)
        [d] = self.lib.deliveries(self.admin, self.tenant.id)
        self.assertEqual((d['name'], d['mode']), ('daily_todo', 'direct'))

    def test_push_limits(self):
        self.lib.save_template(self.admin, TEMPLATE)
        for names, mode in ((['daily_todo'], 'push'), ([], 'offer'), (['nope'], 'offer'),
                            (['daily_todo', 'daily_todo'], 'offer')):
            with self.assertRaises(OfficeError):
                self.lib.push(self.admin, self.tenant.id, names, mode)


class CronTests(unittest.TestCase):
    def test_apscheduler_semantics(self):
        tue = datetime(2026, 9, 29, 9, 0, tzinfo=TZ)  # a Tuesday
        self.assertTrue(cron_matches('0 9 * * *', tue))
        self.assertTrue(cron_matches('0 9 * * tue', tue))
        self.assertTrue(cron_matches('0 9 * * mon-fri', tue))
        self.assertFalse(cron_matches('0 9 * * mon,wed', tue))
        self.assertTrue(cron_matches('*/15 * * * *', tue))
        self.assertFalse(cron_matches('5 9 * * *', tue))
        self.assertTrue(cron_matches('0 9 29 9 *', tue))
        self.assertFalse(cron_matches('0 9 28 * tue', tue))  # fields are ANDed

    def test_prompt_is_scoped_to_the_hive(self):
        prompt = build_prompt('shop', '小店', 'pm', 'daily', 'calendar', '整理待辦')
        for part in ('hives/shop/HIVE.md', 'hives/shop/agents/pm/WORKING.md',
                     '.dsh/skills/calendar/SKILL.md', '整理待辦', '繁體中文', '.keys'):
            self.assertIn(part, prompt)


class SchedulerTests(Base):
    def setUp(self):
        super().setUp()
        self.calls, self.gate = [], threading.Event()

        def runner(site, prompt):
            self.calls.append((site, prompt))
            self.gate.wait(5)
            return True, '完成：三件事'
        self.sched = SiteScheduler(self.core, self.office, runner=runner,
                                   clock=lambda: self.now[0], library=self.lib)
        self.sched.sites = lambda: {self.tenant.id: 'ab.example.cc'}
        make_site(self.sites, 'ab')
        self.lib.save_template(self.admin, TEMPLATE)
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'direct')

    def wait_done(self):
        self.gate.set()
        for _ in range(100):
            runs = self.sched.runs_for(self.tenant.id)
            if runs and all(r['status'] != 'running' for r in runs):
                return runs
            threading.Event().wait(0.02)
        self.fail('run did not finish')

    def test_runs_due_schedule_once_and_logs(self):
        self.assertEqual(self.sched.tick(), [])  # 08:59: delivery applied, nothing due
        self.now[0] += 60                        # 09:00
        started = self.sched.tick()
        self.assertEqual([(s['name'], s['trigger']) for s in started], [('daily_todo', 'schedule')])
        self.assertEqual(self.sched.tick(), [])  # same minute: never twice
        [run] = self.wait_done()
        self.assertEqual((run['status'], run['output']), ('succeeded', '完成：三件事'))
        self.assertEqual(self.calls[0][0], 'ab')
        self.assertIn('整理今天要做的三件事', self.calls[0][1])

    def test_disabled_hive_does_not_run(self):
        import shutil
        shutil.rmtree(self.sites / 'ab')
        make_site(self.sites, 'ab', enabled=False)
        self.sched.tick()
        self.now[0] += 60
        self.assertEqual(self.sched.tick(), [])

    def test_manual_run_and_one_at_a_time(self):
        self.sched.tick()
        run = self.sched.run_now(self.tenant.id, 'ab.example.cc', 'shop', 'daily_todo')
        self.assertEqual(run['trigger'], 'manual')
        with self.assertRaises(OfficeError):
            self.sched.run_now(self.tenant.id, 'ab.example.cc', 'shop', 'daily_todo')
        with self.assertRaises(OfficeError):
            self.sched.run_now(self.tenant.id, 'ab.example.cc', 'shop', 'nope')
        self.wait_done()

    def test_a_broken_site_does_not_stop_other_sites(self):
        bad = make_site(self.sites, 'cd')
        (bad / '.swarm' / 'registry.yaml').write_text('hives:\n- id: shop\n  enabled: true\n  x: 2024-13-45\n')
        boom = self.office.for_host

        def for_host(host):  # even an unexpected crash in one site is contained
            if host.startswith('zz.'):
                raise RuntimeError('boom')
            return boom(host)
        self.office.for_host = for_host
        self.sched.sites = lambda: {'t-bad': 'cd.example.cc', 't-zz': 'zz.example.cc',
                                    self.tenant.id: 'ab.example.cc'}
        self.sched._site_name = lambda host: host.split('.')[0]
        self.sched.tick()
        self.now[0] += 60
        self.assertEqual([s['name'] for s in self.sched.tick()], ['daily_todo'])
        self.wait_done()

    def test_no_catch_up_after_long_pause(self):
        self.sched.tick()
        self.now[0] += 3 * 3600  # platform was down across 09:00
        self.assertEqual(self.sched.tick(), [])


class CustomerRouteTests(Base):
    def setUp(self):
        super().setUp()
        make_site(self.sites, 'ab')
        invite = self.core.issue_invite(self.admin, self.tenant.id)
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://platform.example',
                                       site_office=self.office, task_library=self.lib)
        import support_onboarding
        original = support_onboarding.SupportSiteJobs.customer_status
        self.addCleanup(setattr, support_onboarding.SupportSiteJobs, 'customer_status', original)
        support_onboarding.SupportSiteJobs.customer_status = lambda _self, actor: {
            'status': 'succeeded_provisioned', 'simulated': False, 'provisioned': True,
            'site_host': 'ab.example.cc'}
        self.client = TestClient(app, base_url='https://platform.example')
        self.addCleanup(self.client.close)
        self.origin = {'origin': 'https://platform.example'}
        self.assertEqual(self.client.post('/customer/activate', headers=self.origin, json={
            'invite': invite, 'username': 'alice', 'password': 'test-password-long'}).status_code, 201)
        self.lib.save_template(self.admin, TEMPLATE)
        self.lib.push(self.admin, self.tenant.id, ['daily_todo'], 'offer')

    def test_offer_accept_into_chosen_hive(self):
        status = self.client.get('/customer/onboarding/status').json()
        self.assertFalse(status['tasks']['executor'])
        [offer] = status['tasks']['offers']
        self.assertEqual(offer['title'], '每日待辦')
        r = self.client.post('/customer/office/task-accept', headers=self.origin,
                             json={'delivery': offer['id'], 'hive': 'shop'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['tasks']['offers'], [])
        self.assertEqual(self.schedules()[0]['name'], 'daily_todo')
        again = self.client.post('/customer/office/task-accept', headers=self.origin,
                                 json={'delivery': offer['id'], 'hive': 'shop'})
        self.assertEqual(again.status_code, 422)

    def test_dismiss_and_foreign_delivery(self):
        other = self.core.create_tenant(self.admin, 'other.example')
        self.lib.push(self.admin, other.id, ['daily_todo'], 'offer')
        [foreign] = self.lib.deliveries(self.admin, other.id)
        r = self.client.post('/customer/office/task-accept', headers=self.origin,
                             json={'delivery': foreign['id'], 'hive': 'shop'})
        self.assertEqual(r.status_code, 422)  # cannot touch another tenant's delivery
        [offer] = self.client.get('/customer/onboarding/status').json()['tasks']['offers']
        r = self.client.post('/customer/office/task-dismiss', headers=self.origin,
                             json={'delivery': offer['id']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.schedules(), [])
        self.assertEqual(self.lib.deliveries(self.admin, self.tenant.id)[0]['status'], 'dismissed')

    def test_run_now_refused_without_executor(self):
        r = self.client.post('/customer/office/schedule-run', headers=self.origin,
                             json={'hive': 'shop', 'name': 'x'})
        self.assertEqual(r.status_code, 422)


if __name__ == '__main__':
    unittest.main()
