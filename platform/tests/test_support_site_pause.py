"""Admin-only pause/resume: provisioned sites only, recorded name only, data kept."""
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from support_admin_overview import overview
from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_management import install_management
from support_quota import SupportQuota
from support_rooms import SupportRooms
from support_site_jobs import SupportSiteJobs
from support_site_pause import SitePause, SitePauseBusy, paused_tenants
from support_chat_app import create_admin_chat_app


def provision(core, admin, tenant_id, name='acme', port=18042):
    worker = object()
    jobs = SupportSiteJobs(core, worker_capability=worker, recovery_capability=object())
    job = jobs.queue(admin, tenant_id, 'r-' + name)
    lease = jobs.claim(worker, job['job_id'])
    jobs.complete_provisioned(worker, lease, site_name=name, site_port=port, dns_record_id='d')


class SitePauseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'p.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'acme.example', platform_host='platform.example')
        self.calls = []
        self.service = SitePause(self.core, stopper=lambda n: self.calls.append(('stop', n)),
                                 starter=lambda n: self.calls.append(('start', n)), now=lambda: 1000.0)

    def paused(self):
        with self.core._connect() as conn:
            return paused_tenants(conn)

    def test_unprovisioned_site_is_refused(self):
        for change in (self.service.pause, self.service.resume):
            with self.subTest(change=change.__name__), self.assertRaises(Conflict):
                change(self.admin, self.tenant.id)
        self.assertEqual(self.calls, [])

    def test_pause_then_resume_uses_the_recorded_site_name(self):
        provision(self.core, self.admin, self.tenant.id, 'acme-recorded')
        self.assertEqual(self.service.pause(self.admin, self.tenant.id), {'paused': True})
        self.assertEqual(self.paused(), {self.tenant.id: 1000.0})
        self.assertEqual(self.service.resume(self.admin, self.tenant.id), {'paused': False})
        self.assertEqual(self.paused(), {})
        self.assertEqual(self.calls, [('stop', 'acme-recorded'), ('start', 'acme-recorded')])

    def test_failed_start_keeps_the_site_marked_paused(self):
        provision(self.core, self.admin, self.tenant.id)
        self.service.pause(self.admin, self.tenant.id)

        def broken(name):
            raise RuntimeError('docker: cannot start oc-acme')
        service = SitePause(self.core, stopper=lambda n: None, starter=broken)
        with self.assertRaises(RuntimeError):
            service.resume(self.admin, self.tenant.id)
        self.assertIn(self.tenant.id, self.paused())

    def test_failed_stop_still_records_the_pause(self):
        # No false 「沒有回應」 alert for a half-stopped site; resume stays offered.
        provision(self.core, self.admin, self.tenant.id)

        def broken(name):
            raise RuntimeError('docker: stop timed out')
        service = SitePause(self.core, stopper=broken, starter=lambda n: None)
        with self.assertRaises(RuntimeError):
            service.pause(self.admin, self.tenant.id)
        self.assertIn(self.tenant.id, self.paused())

    def test_only_an_administrator(self):
        provision(self.core, self.admin, self.tenant.id)
        token = self.core.issue_invite(self.admin, self.tenant.id)
        customer = self.core.redeem_invite(token, 'platform.example', 'alice', 'customer-password-1').actor
        for actor in (customer, object()):
            with self.subTest(actor=actor), self.assertRaises((Unauthorized, Exception)):
                self.service.pause(actor, self.tenant.id)
        self.assertEqual(self.calls, [])

    def test_host_must_match(self):
        provision(self.core, self.admin, self.tenant.id)
        self.service.check_host(self.admin, self.tenant.id, 'ACME.example')
        with self.assertRaises(Conflict):
            self.service.check_host(self.admin, self.tenant.id, 'other.example')
        with self.assertRaises(InvalidInput):
            self.service.check_host(self.admin, self.tenant.id, '')

    def test_one_change_at_a_time(self):
        provision(self.core, self.admin, self.tenant.id)
        gate, entered = threading.Event(), threading.Event()

        def slow(name):
            entered.set(); gate.wait(5)
        service = SitePause(self.core, stopper=slow, starter=lambda n: None)
        worker = threading.Thread(target=service.pause, args=(self.admin, self.tenant.id))
        worker.start(); entered.wait(5)
        try:
            with self.assertRaises(SitePauseBusy):
                service.resume(self.admin, self.tenant.id)
        finally:
            gate.set(); worker.join(5)

    def test_overview_shows_paused_without_probe_or_offline_alert(self):
        SupportQuota(self.core)  # the overview reads the quota tables
        SupportRooms(self.core)
        provision(self.core, self.admin, self.tenant.id)
        self.service.pause(self.admin, self.tenant.id)
        probed = []
        data = overview(self.core, self.admin, probe=lambda p: probed.append(p) or False,
                        board_installed=lambda *a: False)
        site = data['sites'][0]
        self.assertEqual((site['paused'], site['paused_at'], site['online']), (True, 1000.0, None))
        self.assertEqual(probed, [])
        self.assertNotIn('offline', [a['kind'] for a in data['attention']])
        self.assertEqual(data['totals']['paused'], 1)

    def test_scheduler_skips_a_paused_site(self):
        from support_site_scheduler import SiteScheduler
        provision(self.core, self.admin, self.tenant.id)
        scheduler = SiteScheduler.__new__(SiteScheduler)
        scheduler.core = self.core
        self.assertIn(self.tenant.id, scheduler.sites())
        self.service.pause(self.admin, self.tenant.id)
        self.assertNotIn(self.tenant.id, scheduler.sites())
        self.service.resume(self.admin, self.tenant.id)
        self.assertIn(self.tenant.id, scheduler.sites())


class SitePauseHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'h.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'acme.example')
        provision(self.core, self.admin, self.tenant.id)
        rooms = SupportRooms(self.core)
        self.fail = None

        def stopper(name):
            if self.fail:
                raise self.fail
        self.pause = SitePause(self.core, stopper=stopper, starter=lambda n: None)
        app = create_admin_chat_app(self.core, rooms, 'https://mother.example',
                                    request_assertion=lambda r: r.headers.get('x-test-admin'))
        install_management(app, self.core, rooms, SupportQuota(self.core), site_pause=self.pause)
        self.client = TestClient(app, base_url='https://mother.example')
        self.addCleanup(self.client.close)
        self.h = {'x-test-admin': 'test', 'origin': 'https://mother.example'}
        self.base = f'/admin/tenants/{self.tenant.id}'

    def test_routes(self):
        body = {'expected_host': 'acme.example'}
        self.assertEqual(self.client.post(self.base + '/pause', json=body,
                                          headers={'origin': 'https://mother.example'}).status_code, 401)
        ok = self.client.post(self.base + '/pause', json=body, headers=self.h)
        self.assertEqual((ok.status_code, ok.json()), (200, {'paused': True}))
        self.assertTrue(self.client.get('/admin/overview', headers=self.h).json()['sites'][0]['paused'])
        ok = self.client.post(self.base + '/resume', json=body, headers=self.h)
        self.assertEqual((ok.status_code, ok.json()), (200, {'paused': False}))
        self.assertTrue(self.client.get('/admin/overview', headers=self.h).json()['can_pause'])

    def test_wrong_host_or_shape_rejected(self):
        self.assertEqual(self.client.post(self.base + '/pause', json={'expected_host': 'other.example'},
                                          headers=self.h).status_code, 409)
        self.assertEqual(self.client.post(self.base + '/pause', json={}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/pause', json={'expected_host': 'acme.example', 'x': 1},
                                          headers=self.h).status_code, 400)

    def test_docker_failure_is_502_without_details(self):
        self.fail = RuntimeError('docker stop site-acme: /srv/sites/acme failed')
        r = self.client.post(self.base + '/pause', json={'expected_host': 'acme.example'}, headers=self.h)
        self.assertEqual((r.status_code, r.json()), (502, {'error': 'site_pause_failed'}))
        self.assertNotIn('srv', r.text)

    def test_not_installed_by_default(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = SupportCore(str(Path(tmp.name) / 'n.sqlite'), admin_verifier=lambda a: 'operator' if a == 'test' else None)
        rooms = SupportRooms(core)
        app = create_admin_chat_app(core, rooms, 'https://mother.example',
                                    request_assertion=lambda r: r.headers.get('x-test-admin'))
        install_management(app, core, rooms, SupportQuota(core))
        with TestClient(app, base_url='https://mother.example') as client:
            self.assertEqual(client.post('/admin/tenants/x/pause', json={'expected_host': 'a'},
                                         headers=self.h).status_code, 404)
            self.assertFalse(client.get('/admin/overview', headers=self.h).json()['can_pause'])


if __name__ == '__main__':
    unittest.main()
