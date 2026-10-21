"""Optional LINE push / HackMD publish after scheduled runs (PLATFORM_SITE_LINE).

No container, network or token is touched: the in-site agent and the HackMD
script runner are fakes.
"""
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import yaml

import support_site_line as line_mod
from support_core import SupportCore
from support_notify import DiscordNotifier
from support_site_line import (LineDelivery, ScheduleDelivery, SiteAlerts, answer_text,
                               hackmd_config, line_config, relay_channel, report_config,
                               truncate_for_line)
from support_site_office import SiteOffice
from support_site_scheduler import TZ, SiteScheduler

TO_A = 'U' + '1' * 32
TO_B = 'U' + '2' * 32

PROJECT = """
project: {name: Acme}
line:
  push:
    - {name: bot-a, token_file: shared/line-bot-a.token, to: %s}
    - {name: bot-b, relay: {site: beta, channel: bot-b}}
notify: {channel_id: "12345678", token_file: shared/discord.token}
reports:
  daily_digest:
    line_push: true
    context: 整理今日重點
  weekly_review:
    line_push: true
    relay: false
    context: 每週回顧
  quiet:
    context: 不推播的任務
""" % TO_A

DELIVERABLE = '今日重點\n1. repo-a ⭐ 1200 — 一個很棒的工具\n2. repo-b ⭐ 900 — 另一個好用的工具'


def make_site(sites, name='acme', relay_ok=True, crons=None):
    ws = sites / name / 'workspace'
    (ws / '.swarm').mkdir(parents=True)
    (ws / 'hives' / 'shop').mkdir(parents=True)
    crons = crons or {'daily_digest': '0 9 * * *', 'weekly_review': '0 10 * * mon',
                      'quiet': '0 12 * * *'}
    agents = ''.join(f'        {k}: "{v}"\n' for k, v in crons.items())
    (ws / '.swarm' / 'registry.yaml').write_text(
        'hives:\n  - id: shop\n    enabled: true\n    agents:\n      pm:\n        model: ""\n' + agents)
    (ws / 'hives' / 'shop' / 'project.yaml').write_text(PROJECT)
    (sites / name / 'site.yaml').write_text(f'name: {name}\n')
    beta = sites / 'beta'
    (beta / 'workspace' / '.swarm').mkdir(parents=True, exist_ok=True)
    (beta / 'site.yaml').write_text('name: beta\n')
    if relay_ok:
        (beta / 'workspace' / '.swarm' / 'line-channels.yaml').write_text(
            'channels:\n  bot-b:\n    token_file: shared/line-bot-b.token\n    to: %s\n'
            '    accept_from:\n      - site: %s\n' % (TO_B, name))
    return ws


class FakeAgent:
    """In-site line_agent.js stand-in: records calls, answers per (site, op)."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, site, request):
        self.calls.append((site, dict(request)))
        ans = self.answers.get((site, request['op']))
        return ans(request) if callable(ans) else (ans or {'ok': True})

    def ops(self, op):
        return [(s, r) for s, r in self.calls if r['op'] == op]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def system_failure(self, text):
        self.sent.append(text)


class Env(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sites = self.root / 'sites'
        self.sites.mkdir()
        self.core = SupportCore(str(self.root / 'db.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.now = [datetime(2026, 10, 16, 8, 59, 30, tzinfo=TZ).timestamp()]
        self.clock = lambda: self.now[0]
        self.agent = FakeAgent()
        self.notifier = FakeNotifier()
        self.alerts = SiteAlerts(notifier=self.notifier, agent=self.agent, clock=self.clock)
        self.line = LineDelivery(self.core, self.sites, agent=self.agent, alerts=self.alerts,
                                 clock=self.clock)
        self.ws = make_site(self.sites)
        self.project = line_mod.load_project(self.ws, 'shop')

    def push(self, report='daily_digest', run_id='r1'):
        return self.line.push_run(tenant_id='t1', site='acme', project=self.project, run_id=run_id,
                                  report=report_config(self.project, report), deliverable=DELIVERABLE)


class ConfigTests(unittest.TestCase):
    def test_line_config_validates(self):
        cfg = line_config({'line': {'push': [
            {'name': 'ok', 'token_file': 'shared/a.token', 'to': TO_A},
            {'name': 'bad-to', 'token_file': 'shared/a.token', 'to': 'Uxyz'},
            {'name': 'escape', 'token_file': '../../etc/passwd', 'to': TO_A},
            {'name': 'abs', 'token_file': '/etc/x', 'to': TO_A},
            {'name': 'bot-b', 'relay': {'site': 'beta', 'channel': 'bot-b'}},
            {'name': 'bad-relay', 'relay': {'site': '../x', 'channel': 'c'}}]}})
        self.assertEqual([t['name'] for t in cfg['targets']], ['ok', 'bot-b'])
        self.assertEqual(line_config({'line': {'push': 'nope'}}), {'targets': []})
        self.assertEqual(line_config({}), {'targets': []})

    def test_report_config_is_opt_in(self):
        project = yaml.safe_load(PROJECT)
        self.assertEqual(report_config(project, 'daily_digest'),
                         {'line_push': True, 'relay': True, 'hackmd': None})
        self.assertFalse(report_config(project, 'quiet')['line_push'])
        self.assertFalse(report_config(project, 'weekly_review')['relay'])
        self.assertIsNone(report_config(project, 'nope'))
        # Only a real boolean true opts in.
        self.assertFalse(report_config({'reports': {'x': {'line_push': 'yes'}}}, 'x')['line_push'])

    def test_hackmd_config_validates(self):
        self.assertEqual(hackmd_config({'repo': 'bd-agent', 'token_file': 'shared/hackmd.token',
                                        'owner': 'Acme'}),
                         {'repo': 'bd-agent', 'token_file': 'shared/hackmd.token', 'owner': 'Acme'})
        for bad in ({'repo': '../etc', 'token_file': 'x', 'owner': 'y'},
                    {'repo': '/abs', 'token_file': 'x', 'owner': 'y'},
                    {'repo': 'r', 'token_file': 'a/../b', 'owner': 'y'},
                    {'repo': 'r', 'token_file': 'x', 'owner': ''},
                    {'repo': 'r', 'token_file': 'x', 'owner': 'y' * 41}, 'r'):
            self.assertIsNone(hackmd_config(bad), bad)

    def test_truncate_and_answer_text(self):
        long = truncate_for_line('字' * 2000)
        self.assertTrue(long.startswith('字' * 1600) and long.endswith('完整內容見 repo）'))
        self.assertEqual(len(long.split('\n')[0]), 1600)
        self.assertEqual(truncate_for_line('短'), '短')
        self.assertEqual(answer_text('[model=a/b] [tried=a/b]\n結果'), '結果')
        self.assertEqual(answer_text('沒有標頭'), '沒有標頭')


class DeliveryTests(Env):
    def test_push_and_relay_with_consent(self):
        out = self.push()
        self.assertEqual(set(out), {'bot-a', 'bot-b'})
        self.assertEqual([(s, r['to'], r['token_file'], r['keys_dir']) for s, r in self.agent.ops('push')], [
            ('acme', TO_A, 'shared/line-bot-a.token', '/home/dsh/workspace/.swarm/keys'),
            ('beta', TO_B, 'shared/line-bot-b.token', '/home/dsh/workspace/.swarm/keys')])
        with self.core._connect() as conn:
            rows = conn.execute('SELECT kind,target,status,content FROM support_line_deliveries '
                                'ORDER BY kind').fetchall()
        self.assertEqual([(r['kind'], r['target'], r['status']) for r in rows],
                         [('push', 'bot-a', 'sent'), ('relay', 'beta/bot-b', 'sent')])
        self.assertEqual(rows[0]['content'], DELIVERABLE)

    def test_no_relay_without_consent_or_with_relay_false(self):
        (self.sites / 'beta' / 'workspace' / '.swarm' / 'line-channels.yaml').unlink()
        out = self.push()
        self.assertEqual(out['bot-b']['error'], 'relay not accepted')
        self.assertEqual([s for s, _ in self.agent.ops('push')], ['acme'])
        with self.core._connect() as conn:
            row = conn.execute("SELECT status, reason FROM support_line_deliveries "
                               "WHERE kind='relay'").fetchone()
        self.assertEqual(row['status'], 'failed')
        self.agent.calls.clear()
        self.assertEqual(set(self.push('weekly_review', 'r2')), {'bot-a'})
        # accept_from must name the source site exactly.
        ws = self.sites / 'beta' / 'workspace'
        (ws / '.swarm' / 'line-channels.yaml').write_text(
            'channels:\n  bot-b:\n    token_file: shared/x\n    to: %s\n    accept_from: [{site: other}]\n'
            % TO_B)
        self.assertIsNone(relay_channel(ws, 'bot-b', 'acme'))
        self.assertIsNotNone(relay_channel(ws, 'bot-b', 'other'))

    def test_relay_to_missing_site_is_refused(self):
        import shutil
        shutil.rmtree(self.sites / 'beta')
        out = self.push()
        self.assertFalse(out['bot-b']['ok'])
        self.assertEqual([s for s, _ in self.agent.ops('push')], ['acme'])

    def test_quota_steps_and_exhaustion_skip(self):
        self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': 161}
        self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.assertEqual(len([t for t in self.notifier.sent if '80%' in t]), 1)
        self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': 191}
        self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.assertTrue(any('95%' in t for t in self.notifier.sent))
        # LINE refuses: month marked exhausted, later sends are skipped without a call.
        self.agent.answers[('beta', 'push')] = {'ok': False, 'error': 'monthly_limit'}
        for run in ('r1', 'r2'):
            self.push(run_id=run)
        self.assertEqual(len([1 for s, _ in self.agent.ops('push') if s == 'beta']), 1)
        with self.core._connect() as conn:
            statuses = [r[0] for r in conn.execute(
                "SELECT status FROM support_line_deliveries WHERE target='beta/bot-b'")]
        self.assertEqual(sorted(statuses), ['failed', 'skipped'])
        self.assertTrue(any('用完' in t for t in self.notifier.sent))

    def test_periodic_quota_check_covers_push_and_relay_without_sending(self):
        self.agent.answers[('acme', 'quota')] = {'ok': True, 'limit': 3000, 'used': 100}
        self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': 165}
        out = self.line.check_all_quotas([('acme', self.project), ('acme', self.project)])
        self.assertEqual(set(out), {('acme', 'bot-a'), ('beta', 'beta/bot-b')})
        self.assertEqual(len(self.agent.ops('quota')), 2)            # each bot once
        self.assertEqual(self.agent.ops('push'), [])                  # nothing sent
        self.line.check_all_quotas([('acme', self.project)])
        self.assertEqual(len([t for t in self.notifier.sent if '80%' in t]), 1)
        # A relay whose destination has not consented is not checked.
        (self.sites / 'beta' / 'workspace' / '.swarm' / 'line-channels.yaml').unlink()
        self.agent.calls.clear()
        self.line.check_all_quotas([('acme', self.project)])
        self.assertEqual([s for s, _ in self.agent.ops('quota')], ['acme'])

    def rules(self):
        f = self.root / 'line-quota.yaml'
        f.write_text('bots:\n  beta/bot-b: {steps: [50, 70, 80, 95], remaining_below: 30, projected_over: 90}\n')
        self.line.quota_file = f

    def test_defaults_without_rules_file(self):
        self.assertEqual(self.line.quota_rules('beta/bot-b'),
                         {'steps': [80, 95], 'remaining_below': None, 'projected_over': None})

    def test_custom_steps_once_each(self):
        self.rules()
        self.now[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ).timestamp()   # too early to project
        for used, expect in ((90, 0), (101, 1), (110, 0), (141, 1), (161, 1)):
            before = len(self.notifier.sent)
            self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': used}
            self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
            got = [t for t in self.notifier.sent[before:] if '額度 ' in t and '%' in t]
            self.assertEqual(len(got), expect, (used, got))
        # Another bot keeps the default 80/95: 60 % is silent.
        self.agent.answers[('acme', 'quota')] = {'ok': True, 'limit': 3000, 'used': 1800}
        before = len(self.notifier.sent)
        self.line.check_quota('acme', 'bot-a', 'shared/line-bot-a.token')
        self.assertEqual(self.notifier.sent[before:], [])

    def test_remaining_below_once(self):
        self.rules()
        self.now[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ).timestamp()
        for used in (171, 175):
            self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': used}
            self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        left = [t for t in self.notifier.sent if '只剩' in t]
        self.assertEqual(len(left), 1)
        self.assertIn('只剩 29 則', left[0])

    def test_projection_once_per_month(self):
        self.rules()
        self.now[0] = datetime(2026, 10, 15, 12, 0, tzinfo=TZ).timestamp()
        self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': 66}
        self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.assertFalse([t for t in self.notifier.sent if '預估' in t])
        for used in (85, 88):
            self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': used}
            self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.assertEqual(len([t for t in self.notifier.sent if '預估' in t]), 1)
        self.now[0] = datetime(2026, 11, 20, 12, 0, tzinfo=TZ).timestamp()
        self.agent.answers[('beta', 'quota')] = {'ok': True, 'limit': 200, 'used': 180}
        self.line.check_quota('beta', 'beta/bot-b', 'shared/line-bot-b.token')
        self.assertEqual(len([t for t in self.notifier.sent if '預估' in t]), 2)

    def test_burst_alerts_but_never_blocks(self):
        for i in range(33):
            self.push('weekly_review', f'r{i}')
        self.assertEqual(len(self.agent.ops('push')), 33)
        self.assertEqual(len([t for t in self.notifier.sent if '異常大量' in t]), 1)  # quiet 30 min

    def test_retention_30_days(self):
        self.line._log('t1', 'a', 'a', None, 'push', 'x', 'sent', None, 'old')
        self.now[0] += 31 * 86400
        self.line._log('t1', 'a', 'a', None, 'push', 'x', 'sent', None, 'new')
        self.assertEqual(self.line.purge(), 1)
        with self.core._connect() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT content FROM support_line_deliveries')],
                             ['new'])


class AlertTests(Env):
    def test_site_bot_first_platform_fallback_and_quiet(self):
        notify = {'channel_id': '12345678', 'token_file': 'shared/discord.token'}
        self.assertTrue(self.alerts.site('k', 'x', site='acme', notify=notify))
        self.assertFalse(self.alerts.site('k', 'x', site='acme', notify=notify))
        self.assertEqual(len(self.agent.ops('discord')), 1)
        self.assertEqual(self.notifier.sent, [])
        self.agent.answers[('acme', 'discord')] = {'ok': False, 'error': 'http_401'}
        self.alerts.site('k2', 'y', site='acme', notify=notify)
        self.assertIn('由平台代發', self.notifier.sent[-1])
        self.now[0] += 1801
        self.assertTrue(self.alerts.site('k', 'x', site='acme', notify=notify))

    def test_alerts_are_optional(self):
        quiet = SiteAlerts(notifier=None, agent=self.agent, clock=self.clock)
        self.assertFalse(quiet.platform('k', 'x'))
        self.assertFalse(quiet.site('k', 'x', site='acme', notify=None))

    def test_public_notifier_has_system_failure(self):
        sent = []
        n = DiscordNotifier('tok', '1', 'https://p.example/admin', send=sent.append)
        SiteAlerts(notifier=n, agent=self.agent, clock=self.clock).platform('k', '額度')
        self.assertEqual(len(sent), 1)
        self.assertIn('額度', sent[0])


class ScriptTests(unittest.TestCase):
    def test_request_goes_on_stdin_and_only_status_is_kept(self):
        import subprocess
        done = subprocess.CompletedProcess([], 0, stdout='noise\n{"ok": true, "secret": "x"}\n', stderr='')
        with mock.patch.object(line_mod.subprocess, 'run', return_value=done) as run:
            res = line_mod.docker_agent('acme', {'op': 'push', 'text': 'hi'})
        self.assertEqual(res, {'ok': True})
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ['docker', 'exec', '-i', line_mod.sitectl.container_name('acme')])
        self.assertNotIn('hi', ' '.join(argv))
        self.assertTrue(run.call_args.kwargs['input'].startswith('const __REQ = {"op": "push"'))

    def test_bad_site_never_reaches_docker(self):
        with mock.patch.object(line_mod.subprocess, 'run') as run:
            self.assertFalse(line_mod.docker_agent('../x', {'op': 'push'})['ok'])
        run.assert_not_called()

    def test_scripts_hold_no_deployment_values(self):
        for name in ('line_agent.js', 'bd_hackmd.js'):
            text = (line_mod.SCRIPTS / name).read_text()
            self.assertNotIn('/home/', text.replace('/home/dsh/', ''))
        agent = line_mod.LINE_SCRIPT.read_text()
        self.assertIn("'User-Agent': 'DiscordBot (https://github.com/towNingtek/swarm, 1)'", agent)


class HookTests(Env):
    """ScheduleDelivery.after_run on its own."""

    def setUp(self):
        super().setUp()
        self.scripts = []
        self.hook = ScheduleDelivery(self.sites, self.line, self.alerts, site_script=self.site_script)
        self.hackmd_answer = {'ok': True, 'url': 'https://hackmd.io/@acme/abc'}

    def site_script(self, site, script, request, timeout=60):
        self.scripts.append((site, script.name, request))
        return self.hackmd_answer

    def after(self, report='daily_digest', ok=True, output='[model=a/b] [tried=a/b]\n' + DELIVERABLE,
              deliver=True):
        return self.hook.after_run(tenant_id='t1', site='acme', hive_id='shop', report_name=report,
                                   run_id='r1', ok=ok, output=output, deliver=deliver)

    def set_hackmd(self, conf):
        pf = self.ws / 'hives' / 'shop' / 'project.yaml'
        d = yaml.safe_load(pf.read_text())
        d['reports']['daily_digest']['hackmd'] = conf
        pf.write_text(yaml.safe_dump(d, allow_unicode=True))

    def test_success_pushes_the_answer_without_the_model_line(self):
        note = self.after()
        self.assertEqual(note, '\n\n[LINE：bot-a=ok, bot-b=ok]')
        self.assertEqual([r['text'] for _, r in self.agent.ops('push')], [DELIVERABLE, DELIVERABLE])

    def test_short_answer_is_not_pushed_and_alerts(self):
        note = self.after(output='[model=a/b] [tried=a/b]\n好的，完成了')
        self.assertIn('[LINE 未推送：', note)
        self.assertEqual(self.agent.ops('push'), [])
        self.assertTrue(any('未推 LINE' in r['text'] for _, r in self.agent.ops('discord')))

    def test_failed_run_alerts_and_never_pushes(self):
        self.assertEqual(self.after(ok=False, output='全部模型都失敗'), '')
        self.assertEqual(self.agent.ops('push'), [])
        self.assertIn('排程失敗', self.agent.ops('discord')[0][1]['text'])

    def test_dry_run_and_not_opted_in_never_push(self):
        self.assertEqual(self.after(deliver=False), '\n\n[試跑：不推播]')
        self.assertEqual(self.after('quiet'), '')
        self.assertEqual(self.after('nope'), '')
        self.assertEqual(self.agent.calls, [])

    def test_push_failure_alerts(self):
        self.agent.answers[('acme', 'push')] = {'ok': False, 'error': 'http_500'}
        note = self.after()
        self.assertIn('bot-a=http_500', note)
        self.assertTrue(any('LINE 推播失敗' in r['text'] for _, r in self.agent.ops('discord')))

    def test_hook_never_raises(self):
        self.line.push_run = mock.Mock(side_effect=RuntimeError('boom'))
        self.assertIn('[LINE：錯誤 RuntimeError]', self.after())

    def test_hackmd_pushes_changes_and_url(self):
        self.set_hackmd({'repo': 'bd-agent', 'token_file': 'shared/hackmd.token', 'owner': 'Acme'})
        note = self.after()
        [(site, script, req)] = self.scripts
        self.assertEqual((site, script), ('acme', 'bd_hackmd.js'))
        self.assertEqual((req['workspace'], req['repo'], req['keys_dir'], req['token_file']),
                         ('/home/dsh/workspace', 'bd-agent', '/home/dsh/workspace/.swarm/keys',
                          'shared/hackmd.token'))
        self.assertEqual(req['changes'], DELIVERABLE)
        pushed = [r['text'] for _, r in self.agent.ops('push')]
        self.assertTrue(len(pushed) == 2 and all('https://hackmd.io/@acme/abc' in t for t in pushed))
        self.assertIn('[HackMD：https://hackmd.io/@acme/abc]', note)
        self.assertIn('[LINE：bot-a=ok', note)

    def test_hackmd_no_change_week_still_pushes_url(self):
        self.set_hackmd({'repo': 'bd-agent', 'token_file': 'shared/hackmd.token', 'owner': 'Acme'})
        self.after(output='[model=a/b] [tried=a/b]\n')
        self.assertIn('本週無更新', self.agent.ops('push')[0][1]['text'])

    def test_hackmd_failure_pushes_original(self):
        self.set_hackmd({'repo': 'bd-agent', 'token_file': 'shared/hackmd.token', 'owner': 'Acme'})
        self.hackmd_answer = {'ok': False, 'error': 'hackmd_update_500'}
        note = self.after()
        self.assertIn('改推原文', note)
        self.assertTrue(all('hackmd.io' not in r['text'] for _, r in self.agent.ops('push')))

    def test_hackmd_without_line_push_only_publishes(self):
        pf = self.ws / 'hives' / 'shop' / 'project.yaml'
        d = yaml.safe_load(pf.read_text())
        d['reports']['quiet']['hackmd'] = {'repo': 'bd-agent', 'token_file': 'shared/h.token', 'owner': 'A'}
        pf.write_text(yaml.safe_dump(d, allow_unicode=True))
        note = self.after('quiet')
        self.assertIn('[HackMD：', note)
        self.assertNotIn('[LINE', note)
        self.assertEqual(self.agent.ops('push'), [])
        self.assertEqual(len(self.scripts), 1)


class SchedulerHookTests(Env):
    """The hook wired into SiteScheduler: only after a finished run."""

    def setUp(self):
        super().setUp()
        self.tenant = self.core.create_tenant(self.core.admin_actor('test'), 'platform.example')
        self.result = (True, '[model=a/b] [tried=a/b]\n' + DELIVERABLE)
        self.office = SiteOffice(self.sites, 'example.cc')
        self.hook = ScheduleDelivery(self.sites, self.line, self.alerts,
                                     site_script=lambda *a, **k: {'ok': False})
        self.sched = SiteScheduler(self.core, self.office, clock=self.clock,
                                   runner=lambda site, prompt: self.result, delivery=self.hook)
        self.sched.sites = lambda: {self.tenant.id: 'acme.example.cc'}

    def wait(self):
        for _ in range(250):
            runs = self.sched.runs_for(self.tenant.id)
            if runs and all(r['status'] != 'running' for r in runs):
                return runs
            threading.Event().wait(0.02)
        self.fail('run did not finish')

    def scheduled_run(self):
        self.sched.tick()                      # 08:59
        self.now[0] += 60                      # 09:00: daily_digest is due
        started = self.sched.tick()
        self.assertEqual([(s['name'], s['trigger']) for s in started], [('daily_digest', 'schedule')])
        [run] = self.wait()
        return run

    def test_scheduled_success_pushes_and_logs(self):
        run = self.scheduled_run()
        self.assertEqual(run['status'], 'succeeded')
        self.assertTrue(run['output'].startswith('[model=a/b]'))
        self.assertIn('[LINE：bot-a=ok, bot-b=ok]', run['output'])
        self.assertEqual([s for s, _ in self.agent.ops('push')], ['acme', 'beta'])
        with self.core._connect() as conn:
            self.assertEqual({r[0] for r in conn.execute('SELECT run_id FROM support_line_deliveries')},
                             {run['id']})

    def test_scheduled_failure_does_not_push(self):
        self.result = (False, '全部模型都失敗（a/b）。\nx')
        run = self.scheduled_run()
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.agent.ops('push'), [])
        self.assertNotIn('[LINE', run['output'])

    def test_manual_dry_run_never_pushes(self):
        self.sched.tick()
        run = self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily_digest',
                                 deliver=False)
        self.assertEqual(run['trigger'], 'manual')
        [done] = self.wait()
        self.assertEqual(done['status'], 'succeeded')
        self.assertIn('[試跑：不推播]', done['output'])
        self.assertEqual(self.agent.ops('push'), [])

    def test_manual_run_delivers_by_default(self):
        self.sched.tick()
        self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'daily_digest')
        [done] = self.wait()
        self.assertIn('[LINE：bot-a=ok', done['output'])

    def test_report_without_line_push_does_not_push(self):
        self.sched.tick()
        self.sched.run_now(self.tenant.id, 'acme.example.cc', 'shop', 'quiet')
        [done] = self.wait()
        self.assertEqual(done['output'], self.result[1])
        self.assertEqual(self.agent.ops('push'), [])          # only the hourly quota reading ran

    def test_relay_consent_refused_pushes_only_own_bot(self):
        (self.sites / 'beta' / 'workspace' / '.swarm' / 'line-channels.yaml').unlink()
        run = self.scheduled_run()
        self.assertEqual(run['status'], 'succeeded')
        self.assertIn('bot-b=relay not accepted', run['output'])
        self.assertEqual([s for s, _ in self.agent.ops('push')], ['acme'])

    def test_without_delivery_nothing_changes(self):
        self.sched.delivery = None
        run = self.scheduled_run()
        self.assertEqual(run['output'], self.result[1])
        self.assertEqual(self.agent.calls, [])

    def test_quota_checked_hourly_and_log_purged_daily(self):
        calls, purges = [], []
        self.hook.check_quotas = lambda sites: calls.append(sites) or {}
        self.hook.purge = lambda: purges.append(1)
        self.sched.tick()
        self.assertEqual(calls, [[('acme', ['shop'])]])
        self.now[0] += 1800
        self.sched.tick()
        self.assertEqual(len(calls), 1)                         # not before an hour
        self.now[0] += 1801
        self.sched.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(purges), 1)                        # once per day
        self.now[0] += 86400
        self.sched.tick()
        self.assertEqual(len(purges), 2)

    def test_periodic_quota_check_reads_site_projects(self):
        self.agent.answers[('acme', 'quota')] = {'ok': True, 'limit': 100, 'used': 1}
        out = self.hook.check_quotas([('acme', ['shop'])])
        self.assertEqual(set(out), {('acme', 'bot-a'), ('beta', 'beta/bot-b')})


class ServiceWiringTests(unittest.TestCase):
    """PLATFORM_SITE_LINE=1 attaches delivery; without it the scheduler has none."""

    def build(self, extra):
        import os
        import platform_service
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {'PLATFORM_ORIGIN': 'https://platform.example.com',
               'PLATFORM_DB': str(Path(tmp.name) / 'db' / 'p.sqlite'),
               'PLATFORM_SITE_SCHEDULER': '1', **extra}
        keep = {k: v for k, v in os.environ.items()
                if k.startswith(('PLATFORM_', 'SWARM_ENTRY', 'SITE_MODEL', 'SCHEDULE_'))
                and k not in ('PLATFORM_ORIGIN',)}
        clean = {k: '' for k in keep}
        with mock.patch.dict(os.environ, {**clean, **env}):
            for k in clean:
                if k not in env:
                    os.environ.pop(k, None)
            app, _ = platform_service.build()
        return app.state.site_scheduler, Path(tmp.name)

    def test_off_by_default(self):
        sched, _ = self.build({})
        self.assertIsNone(sched.delivery)

    def test_on_with_flag_and_quota_file_next_to_db(self):
        sched, root = self.build({'PLATFORM_SITE_LINE': '1'})
        self.assertIsInstance(sched.delivery, ScheduleDelivery)
        self.assertEqual(Path(sched.delivery.line.quota_file), root / 'db' / 'line-quota.yaml')


if __name__ == '__main__':
    unittest.main()
