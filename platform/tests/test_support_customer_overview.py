"""swarm#556: customer overview data and one-click board entry."""
import tempfile
import time
import unittest
from pathlib import Path

from support_core import SupportCore, Unauthorized
from support_customer_overview import customer_overview
from support_site_entry import SiteEntry
from support_site_jobs import SupportSiteJobs


class CustomerOverviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'db.sqlite'), admin_verifier=lambda x: 'a' if x == 't' else None)
        admin = self.core.admin_actor('t')
        self.mine = self.core.create_tenant(admin, 'me.example', platform_host='p.example')
        other = self.core.create_tenant(admin, 'other.example', platform_host='p.example')
        w = object(); jobs = SupportSiteJobs(self.core, worker_capability=w, recovery_capability=object())
        j = jobs.queue(admin, self.mine.id, 'r')
        jobs.complete_provisioned(w, jobs.claim(w, j['job_id']), site_name='me', site_port=1, dns_record_id='d')
        self.me = self.core.redeem_invite(self.core.issue_invite(admin, self.mine.id), 'p.example', 'me', 'test-password-long').actor
        now = int(time.time()); month = time.strftime('%Y-%m', time.gmtime(now))
        with self.core._connect() as c:
            c.execute('CREATE TABLE support_site_model_usage (tenant_id TEXT, request_id TEXT, month TEXT, model TEXT, '
                      'estimate INTEGER, state TEXT, actual INTEGER, created_at INTEGER)')
            c.execute('CREATE TABLE support_schedule_runs (id TEXT, tenant_id TEXT, hive TEXT, name TEXT, slot TEXT, '
                      'trigger TEXT, status TEXT, started_at INTEGER, finished_at INTEGER, output TEXT)')
            c.execute('INSERT INTO support_site_model_usage VALUES (?,?,?,?,?,?,?,?)', (self.mine.id, 'r1', month, 'm', 100, 'done', 120, now))
            c.execute('INSERT INTO support_site_model_usage VALUES (?,?,?,?,?,?,?,?)', (other.id, 'r2', month, 'm', 999, 'done', 999, now))
            c.execute("INSERT INTO support_schedule_runs VALUES ('a',?,'shop','daily','s','schedule','failed',?,?,'secret output')", (self.mine.id, now - 60, now))
            c.execute("INSERT INTO support_schedule_runs VALUES ('b',?,'x','theirs','s','schedule','succeeded',?,?,'')", (other.id, now - 30, now))

    def test_only_my_site_and_whitelisted_fields(self):
        d = customer_overview(self.core, self.me)
        self.assertEqual(d['site_host'], 'me.example')
        self.assertEqual(d['usage_month']['tokens'], 120)                 # actual over estimate; not the other tenant
        self.assertEqual([r['name'] for r in d['recent_runs']], ['daily'])
        self.assertNotIn('secret output', repr(d))
        self.assertEqual(d['attention'][0]['kind'], 'schedule_failed')

    def test_requires_a_customer_session(self):
        with self.assertRaises(Unauthorized):
            customer_overview(self.core, object())


    def test_onboarding_board_flag_follows_the_recorded_site(self):
        import os
        from unittest.mock import patch

        import common
        from support_onboarding import _board_installed
        status = {'provisioned': True, 'site_host': 'me.example'}
        with tempfile.TemporaryDirectory() as root, patch.object(common, 'SITES_ROOT', Path(root)):
            self.assertFalse(_board_installed(self.core, self.me, status))
            lib = Path(root, 'me', 'dsh-home', 'profiles', 'web', 'node_modules', 'dsh-swarm-board', 'lib')
            lib.mkdir(parents=True)
            (lib / 'index.js').write_text('')
            self.assertTrue(_board_installed(self.core, self.me, status))
            self.assertFalse(_board_installed(self.core, self.me, {'provisioned': False}))
            self.assertFalse(_board_installed(self.core, object(), status))  # no session, no flag


class BoardEntryTests(CustomerOverviewTests):
    """One-click 看板: the ticket URL may continue to /board/, and only to allow-listed paths."""

    def test_next_is_allow_listed(self):
        e = SiteEntry(self.core, entry_secret='s' * 40)
        home = e.entry_url(self.me)
        self.assertTrue(home.startswith('https://me.example/auth/enter?ticket='))
        self.assertNotIn('next=', home)
        board = e.entry_url(self.me, '/board/')
        self.assertTrue(board.endswith('&next=%2Fboard%2F'))
        for bad in ('/admin', 'https://evil.example/', '//evil.example', '/board/../x'):
            with self.assertRaises(ValueError):
                e.entry_url(self.me, bad)


if __name__ == '__main__':
    unittest.main()


class OverviewRouteTests(unittest.TestCase):
    def test_route_takes_the_request_not_a_query_field(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from support_customer_overview import install_customer_overview
        app = FastAPI()

        async def no_session(request):
            raise Unauthorized('no session')
        app.state.support_actor_for = no_session
        install_customer_overview(app, core=None)
        r = TestClient(app).get('/customer/overview')
        self.assertEqual(r.status_code, 401)        # not 422 "query.request missing"
