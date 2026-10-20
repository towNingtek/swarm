"""Admin-only extra packages: provisioned sites only, recorded name only."""
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_management import install_management
from support_quota import SupportQuota
from support_rooms import SupportRooms
from support_site_jobs import SupportSiteJobs
from support_site_packages import SitePackages, SitePackagesBusy
from support_chat_app import create_admin_chat_app


class SitePackagesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'p.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'acme.example', platform_host='platform.example')
        self.calls = []
        self.state = {}

        def setter(name, packages):
            self.calls.append((name, packages))
            self.state[name] = sorted(set(packages))
            return {'packages': self.state[name]}
        self.service = SitePackages(self.core, setter=setter,
                                    reader=lambda name: self.state.get(name, []))

    def provision(self, name='acme'):
        worker = object()
        jobs = SupportSiteJobs(self.core, worker_capability=worker, recovery_capability=object())
        job = jobs.queue(self.admin, self.tenant.id, 'r-1')
        lease = jobs.claim(worker, job['job_id'])
        jobs.complete_provisioned(worker, lease, site_name=name, site_port=18042, dns_record_id='d')

    def test_unprovisioned_site_is_refused(self):
        with self.assertRaises(Conflict):
            self.service.set(self.admin, self.tenant.id, ['curl'])
        self.assertEqual(self.calls, [])

    def test_uses_the_recorded_site_name(self):
        self.provision('acme-recorded')
        self.assertEqual(self.service.set(self.admin, self.tenant.id, ['curl', 'jq'])['packages'],
                         ['curl', 'jq'])
        self.assertEqual(self.calls, [('acme-recorded', ['curl', 'jq'])])
        self.assertEqual(self.service.read(self.admin, self.tenant.id), {'packages': ['curl', 'jq']})

    def test_only_an_administrator(self):
        self.provision()
        token = self.core.issue_invite(self.admin, self.tenant.id)
        customer = self.core.redeem_invite(token, 'platform.example', 'alice', 'customer-password-1').actor
        for actor in (customer, object()):
            with self.subTest(actor=actor), self.assertRaises((Unauthorized, Exception)):
                self.service.set(actor, self.tenant.id, ['curl'])
        self.assertEqual(self.calls, [])

    def test_bad_shapes_rejected(self):
        self.provision()
        for bad in ('curl', [1], None, {'a': 1}):
            with self.subTest(bad=bad), self.assertRaises(InvalidInput):
                self.service.set(self.admin, self.tenant.id, bad)

    def test_one_change_at_a_time(self):
        self.provision()
        gate, entered = threading.Event(), threading.Event()

        def slow(name, packages):
            entered.set(); gate.wait(5)
            return {'packages': packages}
        service = SitePackages(self.core, setter=slow, reader=lambda n: [])
        worker = threading.Thread(target=service.set, args=(self.admin, self.tenant.id, ['curl']))
        worker.start(); entered.wait(5)
        try:
            with self.assertRaises(SitePackagesBusy):
                service.set(self.admin, self.tenant.id, ['jq'])
        finally:
            gate.set(); worker.join(5)


class SitePackagesHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'h.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'acme.example')
        rooms = SupportRooms(self.core)
        self.outcome = {'packages': ['curl']}

        def setter(name, packages):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome
        self.packages = SitePackages(self.core, setter=setter, reader=lambda n: ['curl'])
        self.packages._site = lambda actor, tid: (self.core._admin(actor), 'acme')[1]
        app = create_admin_chat_app(self.core, rooms, 'https://mother.example',
                                    request_assertion=lambda r: r.headers.get('x-test-admin'))
        install_management(app, self.core, rooms, SupportQuota(self.core), site_packages=self.packages)
        self.client = TestClient(app, base_url='https://mother.example')
        self.addCleanup(self.client.close)
        self.h = {'x-test-admin': 'test', 'origin': 'https://mother.example'}
        self.url = f'/admin/tenants/{self.tenant.id}/packages'

    def test_routes(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url, headers=self.h).json(), {'packages': ['curl']})
        ok = self.client.post(self.url, json={'packages': ['curl']}, headers=self.h)
        self.assertEqual((ok.status_code, ok.json()), (200, {'packages': ['curl']}))
        self.assertEqual(self.client.post(self.url, json={'packages': 'curl'}, headers=self.h).status_code, 400)
        self.assertEqual(self.client.post(self.url, json={'packages': [], 'x': 1}, headers=self.h).status_code, 400)
        self.outcome = RuntimeError('/var/lib/swarm/sites/acme sha256:abc build failed')
        failed = self.client.post(self.url, json={'packages': ['curl']}, headers=self.h)
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn('srv', failed.text)

    def test_not_installed_by_default(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        core = SupportCore(str(Path(tmp.name) / 'n.sqlite'), admin_verifier=lambda a: 'operator' if a == 'test' else None)
        rooms = SupportRooms(core)
        app = create_admin_chat_app(core, rooms, 'https://mother.example',
                                    request_assertion=lambda r: r.headers.get('x-test-admin'))
        install_management(app, core, rooms, SupportQuota(core))
        with TestClient(app, base_url='https://mother.example') as client:
            self.assertEqual(client.get('/admin/tenants/x/packages', headers=self.h).status_code, 404)


if __name__ == '__main__':
    unittest.main()
