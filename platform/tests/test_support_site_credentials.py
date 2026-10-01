"""One-time site password delivery: stored with success, readable once, then gone."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from support_chat_app import create_customer_chat_app
from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_rooms import SupportRooms
from support_site_credentials import SiteCredentials
from support_site_jobs import SupportSiteJobs

PLATFORM = 'platform.example'


class SiteCredentialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.now = [1000.0]
        self.core = SupportCore(str(Path(tmp.name) / 'cred.sqlite'), clock=lambda: self.now[0],
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'acme.example',
                                              platform_host=PLATFORM)
        self.writer = object()
        self.creds = SiteCredentials(self.core, writer_capability=self.writer)
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)
        self.actor = self.activate('alice')

    def activate(self, username, tenant=None):
        tenant = tenant or self.tenant
        token = self.core.issue_invite(self.admin, tenant.id)
        return self.core.redeem_invite(token, PLATFORM, username, 'customer-password-1').actor

    def provision(self, tenant=None, password='site-secret-1'):
        tenant = tenant or self.tenant
        job = self.jobs.queue(self.admin, tenant.id, 'r-' + tenant.id[:6])
        lease = self.jobs.claim(self.worker, job['job_id'])

        def writer(conn, tenant_id, site_host):
            self.creds.store(self.writer, conn, tenant_id, site_host, 'admin', password)

        return self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                              site_port=18042, dns_record_id=None,
                                              credential_writer=writer)

    def test_password_is_delivered_exactly_once(self):
        self.assertEqual(self.creds.peek(self.actor), {'available': False, 'reason': 'none'})
        self.provision()
        peek = self.creds.peek(self.actor)
        self.assertTrue(peek['available'])
        self.assertEqual(peek['site_host'], 'acme.example')
        self.assertNotIn('password', peek)  # peek must never leak the value
        revealed = self.creds.reveal(self.actor)
        self.assertEqual(revealed['password'], 'site-secret-1')
        self.assertEqual(revealed['username'], 'admin')
        # Reading deletes it: the platform stops holding the customer's credential.
        with self.assertRaises(Conflict):
            self.creds.reveal(self.actor)
        self.assertEqual(self.creds.peek(self.actor), {'available': False, 'reason': 'none'})

    def test_credential_and_success_commit_together(self):
        # A failing writer must abort the whole completion, never leave a
        # provisioned site whose password was lost.
        job = self.jobs.queue(self.admin, self.tenant.id, 'r1')
        lease = self.jobs.claim(self.worker, job['job_id'])

        def broken(conn, tenant_id, site_host):
            raise RuntimeError('storage failed')

        with self.assertRaises(RuntimeError):
            self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                           site_port=18042, dns_record_id=None,
                                           credential_writer=broken)
        self.assertEqual(self.jobs.worker_status(self.worker, job['job_id'])['status'], 'running')
        self.assertFalse(self.creds.peek(self.actor)['available'])

    def test_expired_secret_is_never_returned_and_is_discarded(self):
        self.provision()
        from support_site_credentials import DEFAULT_TTL
        self.now[0] += DEFAULT_TTL + 1
        # The session expires long before the credential does; log in again so
        # this test isolates credential expiry rather than session expiry.
        actor = self.core.login(PLATFORM, 'alice', 'customer-password-1').actor
        self.assertEqual(self.creds.peek(actor), {'available': False, 'reason': 'expired'})
        with self.assertRaises(Conflict):
            self.creds.reveal(actor)
        # Even a failed read removes it: no stale secret of unknown exposure.
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM support_site_credentials')
                             .fetchone()[0], 0)

    def test_other_tenants_cannot_read_and_writer_is_capability_gated(self):
        other = self.core.create_tenant(self.admin, 'other.example', platform_host=PLATFORM)
        bob = self.activate('bob', other)
        self.provision()
        self.assertFalse(self.creds.peek(bob)['available'])
        with self.assertRaises(Conflict):
            self.creds.reveal(bob)
        self.assertTrue(self.creds.peek(self.actor)['available'])  # unaffected
        with self.core._connect() as conn:
            for wrong in (None, object(), self.worker):
                with self.assertRaises(Unauthorized):
                    self.creds.store(wrong, conn, self.tenant.id, 'acme.example', 'admin', 'p' * 12)
        with self.core._connect() as conn:
            for bad in (('bad user', 'p' * 12), ('admin', 'short'), ('admin', 'x' * 300)):
                with self.assertRaises(InvalidInput):
                    self.creds.store(self.writer, conn, self.tenant.id, 'acme.example', *bad)

    def test_http_reveal_is_post_only_and_session_bound(self):
        app = create_customer_chat_app(self.core, SupportRooms(self.core),
                                       'https://' + PLATFORM, site_credentials=self.creds)
        client = TestClient(app, base_url='https://' + PLATFORM)
        self.addCleanup(client.close)
        origin = {'origin': 'https://' + PLATFORM}
        token = self.core.issue_invite(self.admin, self.tenant.id)
        # Use a second tenant so this client owns its own session.
        self.assertEqual(client.post('/customer/activate', headers=origin, json={
            'invite': token, 'username': 'carol', 'password': 'customer-password-1'}).status_code, 201)
        self.provision()
        peek = client.get('/customer/site-credential')
        self.assertEqual(peek.status_code, 200)
        self.assertTrue(peek.json()['available'])
        self.assertNotIn('site-secret-1', peek.text)
        # GET must not consume the secret.
        self.assertEqual(client.get('/customer/site-credential/reveal').status_code, 405)
        self.assertTrue(client.get('/customer/site-credential').json()['available'])
        revealed = client.post('/customer/site-credential/reveal', headers=origin, json={})
        self.assertEqual(revealed.status_code, 200)
        self.assertEqual(revealed.json()['password'], 'site-secret-1')
        self.assertEqual(revealed.headers['cache-control'], 'no-store')
        self.assertEqual(client.post('/customer/site-credential/reveal', headers=origin,
                                     json={}).status_code, 409)
        self.assertEqual(client.post('/customer/logout', headers=origin, json={}).status_code, 200)
        self.assertEqual(client.get('/customer/site-credential').status_code, 401)

    def test_route_absent_unless_composed(self):
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://' + PLATFORM)
        client = TestClient(app, base_url='https://' + PLATFORM)
        self.addCleanup(client.close)
        token = self.core.issue_invite(self.admin, self.tenant.id)
        origin = {'origin': 'https://' + PLATFORM}
        client.post('/customer/activate', headers=origin, json={
            'invite': token, 'username': 'dave', 'password': 'customer-password-1'})
        self.assertEqual(client.get('/customer/site-credential').status_code, 404)


if __name__ == '__main__':
    unittest.main()
