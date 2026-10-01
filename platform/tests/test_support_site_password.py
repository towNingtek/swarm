"""Customer-chosen site password: own site only, verified reuse, never stored or echoed."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from support_chat_app import create_customer_chat_app
from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_rooms import SupportRooms
from support_site_jobs import SupportSiteJobs
from support_site_password import SitePassword, SitePasswordBusy

PLATFORM = 'platform.example'
PLATFORM_PW = 'customer-password-1'


class SitePasswordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / 'pw.sqlite'
        self.core = SupportCore(str(self.db),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)
        self.calls = []
        self.fail = None
        self.clock = [0.0]

        def setter(name, password):
            self.calls.append((name, password))
            if self.fail:
                raise self.fail
        self.pw = SitePassword(self.core, setter=setter, clock=lambda: self.clock[0])
        self.tenant, self.actor = self.customer('acme', 'alice')

    def customer(self, label, username):
        tenant = self.core.create_tenant(self.admin, f'{label}.example', platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, tenant.id)
        return tenant, self.core.redeem_invite(token, PLATFORM, username, PLATFORM_PW).actor

    def provision(self, tenant, name):
        job = self.jobs.queue(self.admin, tenant.id, 'r-' + name)
        lease = self.jobs.claim(self.worker, job['job_id'])
        self.jobs.complete_provisioned(self.worker, lease, site_name=name,
                                       site_port=18042, dns_record_id=None)

    def test_only_after_real_provisioning(self):
        with self.assertRaises(Conflict):
            self.pw.set(self.actor, password='a-long-site-pass')
        self.jobs.queue(self.admin, self.tenant.id, 'r1')
        with self.assertRaises(Conflict):
            self.pw.set(self.actor, password='a-long-site-pass')
        self.assertEqual(self.calls, [])

    def test_sets_own_site_only(self):
        self.provision(self.tenant, 'acme')
        other, bob = self.customer('other', 'bob')
        self.provision(other, 'other')
        result = self.pw.set(self.actor, password='a-long-site-pass')
        self.assertEqual(result, {'site_host': 'acme.example', 'username': 'admin'})
        self.clock[0] += 100
        self.pw.set(bob, password='bobs-site-password')
        self.assertEqual([c[0] for c in self.calls], ['acme', 'other'])

    def test_validation_before_any_effect(self):
        self.provision(self.tenant, 'acme')
        for bad in ('short', 'x' * 11, 'x' * 1025, 'has\nnewline-in-it', 'tab\there-long-enough', 12345, None):
            with self.subTest(bad=bad), self.assertRaises(InvalidInput):
                self.pw.set(self.actor, password=bad)
        with self.assertRaises(InvalidInput):
            self.pw.set(self.actor)                       # neither
        with self.assertRaises(InvalidInput):
            self.pw.set(self.actor, password='a-long-site-pass', platform_password=PLATFORM_PW)
        self.assertEqual(self.calls, [])

    def test_reuse_requires_the_real_platform_password(self):
        self.provision(self.tenant, 'acme')
        with self.assertRaises(Unauthorized):
            self.pw.set(self.actor, platform_password='wrong-password-123')
        self.assertEqual(self.calls, [])
        self.pw.set(self.actor, platform_password=PLATFORM_PW)
        self.assertEqual(self.calls, [('acme', PLATFORM_PW)])

    def test_short_platform_password_cannot_be_reused(self):
        tenant, carol = self.core.create_tenant(self.admin, 'short.example', platform_host=PLATFORM), None
        token = self.core.issue_invite(self.admin, tenant.id)
        carol = self.core.redeem_invite(token, PLATFORM, 'carol', 'eight-ch').actor
        self.provision(tenant, 'short')
        with self.assertRaises(InvalidInput):
            self.pw.set(carol, platform_password='eight-ch')
        self.assertEqual(self.calls, [])

    def test_cooldown_and_no_storage(self):
        self.provision(self.tenant, 'acme')
        self.pw.set(self.actor, password='a-long-site-pass')
        with self.assertRaises(SitePasswordBusy):
            self.pw.set(self.actor, password='another-site-pass')
        self.clock[0] += 31
        self.pw.set(self.actor, password='another-site-pass')
        self.assertEqual(len(self.calls), 2)
        raw = self.db.read_bytes()
        for secret in (b'a-long-site-pass', b'another-site-pass'):
            self.assertNotIn(secret, raw)

    def test_disabled_tenant_or_logged_out_session_refused(self):
        self.provision(self.tenant, 'acme')
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        with self.assertRaises((Conflict, Unauthorized)):
            self.pw.set(self.actor, password='a-long-site-pass')
        self.assertEqual(self.calls, [])

    def test_setter_failure_propagates_and_cooldown_still_applies(self):
        self.provision(self.tenant, 'acme')
        self.fail = RuntimeError('/srv/sites/acme container oc-acme failed')
        with self.assertRaises(RuntimeError):
            self.pw.set(self.actor, password='a-long-site-pass')

    def test_http_route(self):
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://' + PLATFORM,
                                       site_password=self.pw)
        client = TestClient(app, base_url='https://' + PLATFORM)
        self.addCleanup(client.close)
        origin = {'origin': 'https://' + PLATFORM}
        tenant = self.core.create_tenant(self.admin, 'web.example', platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, tenant.id)
        self.assertEqual(client.post('/customer/activate', headers=origin, json={
            'invite': token, 'username': 'dave', 'password': PLATFORM_PW}).status_code, 201)
        url = '/customer/site-password'
        self.assertEqual(client.get(url).json(), {'available': True})
        self.assertEqual(client.post(url, headers=origin, json={'password': 'a-long-site-pass'}).status_code, 409)
        self.provision(tenant, 'web')
        for bad in ({}, {'password': 'a-long-site-pass', 'platform_password': PLATFORM_PW},
                    {'password': 123456789012}, {'other': 'a-long-site-pass'}, {'password': 'short'}):
            with self.subTest(bad=bad):
                self.assertEqual(client.post(url, headers=origin, json=bad).status_code, 400)
        self.assertEqual(client.post(url, headers=origin,
                                     json={'platform_password': 'wrong-password-123'}).status_code, 401)
        ok = client.post(url, headers=origin, json={'password': 'a-long-site-pass'})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {'site_host': 'web.example', 'username': 'admin'})
        self.assertNotIn('a-long-site-pass', ok.text)
        self.assertEqual(ok.headers['cache-control'], 'no-store')
        self.assertEqual(client.post(url, headers=origin, json={'password': 'a-long-site-pass'}).status_code, 429)
        self.clock[0] += 31
        self.fail = RuntimeError('/srv/sites/web oc-web secret-path')
        failed = client.post(url, headers=origin, json={'password': 'a-long-site-pass'})
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn('srv', failed.text)
        # Cross-origin is refused at the edge.
        self.assertEqual(client.post(url, headers={'origin': 'https://evil.example'},
                                     json={'password': 'a-long-site-pass'}).status_code, 403)
        self.assertEqual(client.post('/customer/logout', headers=origin, json={}).status_code, 200)
        self.assertEqual(client.post(url, headers=origin, json={'password': 'a-long-site-pass'}).status_code, 401)

    def test_route_absent_unless_composed(self):
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://' + PLATFORM)
        routes = {getattr(r, 'path', None) for r in app.routes}
        self.assertNotIn('/customer/site-password', routes)


if __name__ == '__main__':
    unittest.main()
