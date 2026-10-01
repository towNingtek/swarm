import os
import tempfile
import unittest
from pathlib import Path

from support_site_status import SiteStatusWriter, _write, render_status

OFFICE = {'default_model': {'provider': 'platform-starter', 'model': 'cloud-fast', 'starter': True},
          'hives': [{'id': 'shop', 'name': '小店', 'enabled': True, 'exists': True, 'schedules': [
              {'name': 'digest', 'role': 'pm', 'cron': '0 9 * * *', 'state': 'ready'},
              {'name': 'orphan', 'role': None, 'cron': None, 'state': 'missing_time'}]},
                    {'id': 'gone', 'name': 'x', 'enabled': True, 'exists': False, 'schedules': []}]}


class RenderTests(unittest.TestCase):
    def render(self, **kw):
        args = dict(site_host='ab.example', office=OFFICE,
                    starter={'mode': 'capped', 'used': 1200, 'limit': 5000},
                    runs=[], offers_waiting=0, now=1800000000)
        args.update(kw)
        return render_status(**args)

    def test_model_allowance_and_schedule_states_in_plain_words(self):
        text = self.render()
        self.assertIn('起步模型 `cloud-fast`', text)
        self.assertIn('已用 1,200 / 5,000 tokens（剩 3,800）', text)
        self.assertIn('`digest`　角色 pm　時間 `0 9 * * *`　→ 會執行', text)
        self.assertIn('registry 裡沒有這個排程的時間', text)
        self.assertNotIn('gone', text)  # registered but no directory: not listed
        own = self.render(office={**OFFICE, 'default_model':
                                  {'provider': 'openrouter', 'model': 'm', 'starter': False}},
                          starter={'mode': 'disabled', 'used': 0, 'limit': None})
        self.assertIn('使用者自己的 `openrouter/m`', own)
        self.assertIn('目前停用', own)

    def test_runs_are_short_and_secret_like_output_is_withheld(self):
        runs = [{'started_at': 1800000000, 'hive': 'shop', 'name': 'digest', 'trigger': 'schedule',
                 'status': 'succeeded', 'output': 'x' * 1000},
                {'started_at': 1800000000, 'hive': 'shop', 'name': 'digest', 'trigger': 'manual',
                 'status': 'failed', 'output': 'leaked sk-' + 'a' * 30}]
        text = self.render(runs=runs, offers_waiting=2)
        self.assertIn('（排程）：成功', text)
        self.assertIn('x' * 300 + '…', text)
        self.assertNotIn('x' * 301, text)
        self.assertNotIn('sk-aaaa', text)
        self.assertIn('未列出', text)
        self.assertIn('有 2 個任務等使用者', text)


class WriteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Path(tmp.name) / 'workspace'
        (self.ws / '.swarm').mkdir(parents=True)
        self.target = self.ws / '.swarm' / 'platform-status.md'

    def test_writes_only_on_real_change_and_never_through_symlinks(self):
        self.assertTrue(_write(self.ws, '# h\n時間 1\n\n## A\nx\n'))
        self.assertFalse(_write(self.ws, '# h\n時間 2\n\n## A\nx\n'))  # only the time moved
        self.assertTrue(_write(self.ws, '# h\n時間 3\n\n## A\ny\n'))
        self.assertIn('y', self.target.read_text())
        outside = self.ws.parent / 'secret.txt'
        outside.write_text('keep')
        self.target.unlink()
        self.target.symlink_to(outside)
        self.assertFalse(_write(self.ws, '# h\n\n## B\n'))
        self.assertEqual(outside.read_text(), 'keep')
        # A .swarm that is a symlink is refused too.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        ws2 = Path(other.name) / 'workspace'
        ws2.mkdir()
        (ws2 / '.swarm').symlink_to(self.ws.parent)
        self.assertFalse(_write(ws2, '# h\n\n## C\n'))
        self.assertFalse((self.ws.parent / 'platform-status.md').exists())

    def test_writer_refreshes_each_site_and_isolates_failures(self):
        class Office:
            sites_root = self.ws.parent.parent

            def for_host(self, host):
                if host == 'bad.example':
                    raise RuntimeError('broken')
                return OFFICE

        class Core:
            def _connect(self):
                import sqlite3
                conn = sqlite3.connect(':memory:')
                conn.row_factory = sqlite3.Row
                return conn

        name = self.ws.parent.name

        class Scheduler:
            office, core = Office(), Core()

            def sites(self):
                return {'t1': 'bad.example', 't2': name + '.example'}

            def _site_name(self, host):
                return host.split('.')[0]

            def runs_for(self, tenant_id, limit):
                return []

        clock = [1800000000.0]
        writer = SiteStatusWriter(Scheduler(), lambda conn, t: {'mode': 'unlimited', 'used': 5},
                                  clock=lambda: clock[0], interval=120)
        self.assertEqual(writer.refresh(), [name + '.example'])
        self.assertIn('不設上限', self.target.read_text())
        clock[0] += 10
        self.assertEqual(writer.refresh(), [])  # throttled
        clock[0] += 200
        self.assertEqual(writer.refresh(), [])  # nothing changed


if __name__ == '__main__':
    unittest.main()
