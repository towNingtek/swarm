"""Operator console: separate authority, real controls, no provisioning of its own."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import admin_service
from support_core import SupportCore

# Same origin as the customer platform: the console is path-separated, so the
# tests must exercise that shared-origin arrangement, not an idealised one.
ADMIN = 'https://platform.example'
PLATFORM = 'https://platform.example'
PASSWORD = 'operator-password-1234'


class AdminServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'platform.sqlite')
        SupportCore(Path(self.db))  # the platform service owns creation
        self.app = self.build()
        self.client = TestClient(self.app, base_url=ADMIN, follow_redirects=False)
        self.addCleanup(self.client.close)
        self.headers = {'origin': ADMIN, 'sec-fetch-site': 'same-origin'}

    def env(self, **extra):
        return {'ADMIN_ORIGIN': ADMIN, 'PLATFORM_ORIGIN': PLATFORM, 'PLATFORM_DB': self.db,
                'ADMIN_PASSWORD': PASSWORD, 'SWARM_DOMAIN': 'example.com', **extra}

    def build(self, **extra):
        with patch.dict(os.environ, self.env(**extra), clear=True):
            return admin_service.build()

    def login(self):
        return self.client.post('/admin/login', data={'password': PASSWORD}, headers=self.headers)

    def test_login_attempts_are_rate_limited_per_visitor(self):
        # TestClient's peer is not loopback, so it is its own visitor.
        wrong = {'password': 'wrong-password-123456'}
        codes = [self.client.post('/admin/login', data=wrong, headers=self.headers).status_code
                 for _ in range(10)]
        self.assertEqual(codes, [401] * 10)
        self.assertEqual(self.client.post('/admin/login', data=wrong, headers=self.headers).status_code, 429)
        # Even the right password waits: the limit is counted before it is read.
        self.assertEqual(self.login().status_code, 429)
        # Another visitor behind the same nginx is not affected.
        from support_app import client_address
        scope = {'client': ('127.0.0.1', 1), 'headers': [(b'x-swarm-client', b'198.51.100.7')]}
        self.assertEqual(client_address(scope), '198.51.100.7')

    def test_configuration_is_strict(self):
        for env in ({'ADMIN_ORIGIN': ADMIN}, {'ADMIN_PASSWORD': PASSWORD}):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    admin_service.build()
        # Weak password, non-https origin, and a missing database are all refused.
        for extra in ({'ADMIN_PASSWORD': 'short'}, {'ADMIN_ORIGIN': 'http://dh.example'},
                      {'ADMIN_ORIGIN': 'https://dh.example:8443'},
                      {'PLATFORM_DB': str(Path(self.tmp.name) / 'absent.sqlite')}):
            with self.assertRaises(RuntimeError, msg=str(extra)):
                self.build(**extra)

    def test_console_requires_login_and_uses_its_own_authority(self):
        for path in ('/admin', '/admin/customers', '/admin/tenants'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertEqual(response.headers['location'], '/admin/login')
        # A foreign authority is refused outright.
        self.assertEqual(self.client.get('/admin/customers',
                                         headers={'host': 'evil.example'}).status_code, 403)
        self.assertEqual(self.login().status_code, 303)
        cookie = self.client.cookies.get(admin_service.OperatorSessions.COOKIE_NAME)
        self.assertTrue(cookie)
        # The admin cookie must be confined to /admin so it is never sent with
        # the customer requests that share this origin.
        raw = self.login().headers['set-cookie']
        self.assertIn('Path=/admin', raw)
        self.assertNotIn('__Host-', raw)
        self.assertIn('HttpOnly', raw)
        self.assertIn('Secure', raw)
        page = self.client.get('/admin/customers')
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-mode="live"', page.text)
        # Invite links must point at the customer platform, not this console.
        self.assertIn(f'data-invite-origin="{PLATFORM}"', page.text)
        self.assertNotIn('fixture', page.text.lower())

    def test_wrong_password_never_issues_a_session(self):
        bad = self.client.post('/admin/login', data={'password': 'wrong'}, headers=self.headers)
        self.assertEqual(bad.status_code, 401)
        self.assertNotIn('set-cookie', bad.headers)
        self.assertEqual(self.client.get('/admin/customers').status_code, 302)

    def test_operator_can_create_tenant_invite_and_queue_a_build(self):
        self.assertEqual(self.login().status_code, 303)
        created = self.client.post('/admin/tenants', headers=self.headers, json={
            'client_request_id': 'admin-1', 'host': 'acme.example',
            'room_mode': 'ai', 'policy': {'mode': 'disabled'}})
        self.assertEqual(created.status_code, 200, created.text)
        tenant = created.json()['tenant']
        self.assertEqual(created.json()['provisioning'], 'not_provisioned')
        # The tenant is bound to the platform host, so the invite works before
        # the customer's own site exists.
        listed = self.client.get('/admin/tenants').json()[0]
        self.assertEqual(listed['platform_host'], 'platform.example')
        self.assertIsNone(listed['site_job'])
        invite = self.client.post(f"/admin/tenants/{tenant['id']}/invites",
                                  headers=self.headers, json={'ttl_seconds': 3600})
        self.assertEqual(invite.status_code, 201)
        token = invite.json()['token']
        self.assertNotIn(token, self.client.get('/admin/tenants').text)
        queued = self.client.post(f"/admin/tenants/{tenant['id']}/site-jobs",
                                  headers=self.headers, json={'client_request_id': 'admin-job-1'})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()['job']['status'], 'queued')
        # This console records intent only; it never provisions.
        self.assertFalse(queued.json()['executor_connected'])
        self.assertEqual(self.client.get('/admin/tenants').json()[0]['site_job'], 'queued')
        # The invite is redeemable on the platform authority, proving the two
        # services share one database.
        core = SupportCore(Path(self.db))
        grant = core.redeem_invite(token, 'platform.example', 'alice', 'customer-password-1')
        self.assertEqual(grant.actor.tenant_id, tenant['id'])

    def test_admin_cookie_cannot_authenticate_a_customer(self):
        """Sharing an origin means both cookies coexist; names must not cross."""
        from support_app import COOKIE, _cookie
        self.assertEqual(self.login().status_code, 303)
        admin_cookie = self.client.cookies.get(admin_service.OperatorSessions.COOKIE_NAME)
        headers = [(b'cookie', f'{admin_service.OperatorSessions.COOKIE_NAME}={admin_cookie}'
                    .encode('latin1'))]
        # The customer app extracts no session from an admin cookie.
        self.assertIsNone(_cookie(headers))
        # And a customer cookie is not accepted as an operator session.
        auth = admin_service.OperatorSessions()
        self.assertFalse(auth.verify_session('x' * 43))
        self.assertFalse(auth.verify_session(admin_cookie))  # different secret
        self.assertNotEqual(admin_service.OperatorSessions.COOKIE_NAME, COOKIE)

    def test_delete_route_absent_unless_explicitly_enabled(self):
        self.assertEqual(self.login().status_code, 303)
        created = self.client.post('/admin/tenants', headers=self.headers, json={
            'client_request_id': 'admin-1', 'host': 'acme.example',
            'room_mode': 'ai', 'policy': {'mode': 'disabled'}})
        tenant = created.json()['tenant']
        # ADMIN_ALLOW_DELETE is unset in this fixture: the route must not exist.
        self.assertEqual(self.client.post(f"/admin/tenants/{tenant['id']}/delete",
                                          headers=self.headers,
                                          json={'expected_host': 'acme.example'}).status_code, 404)

    def test_delete_requires_the_expected_host(self):
        app = self.build(ADMIN_ALLOW_DELETE='1')
        client = TestClient(app, base_url=ADMIN, follow_redirects=False)
        self.addCleanup(client.close)
        client.post('/admin/login', data={'password': PASSWORD}, headers=self.headers)
        created = client.post('/admin/tenants', headers=self.headers, json={
            'client_request_id': 'admin-2', 'host': 'acme.example',
            'room_mode': 'ai', 'policy': {'mode': 'disabled'}})
        tenant = created.json()['tenant']
        # A mismatched host must not delete anything.
        wrong = client.post(f"/admin/tenants/{tenant['id']}/delete", headers=self.headers,
                            json={'expected_host': 'other.example'})
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(len(client.get('/admin/tenants').json()), 1)
        ok = client.post(f"/admin/tenants/{tenant['id']}/delete", headers=self.headers,
                         json={'expected_host': 'acme.example'})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIsNone(ok.json()['site_removed'])
        self.assertEqual(client.get('/admin/tenants').json(), [])
        # The creation receipt goes too: the same request id may create anew.
        again = client.post('/admin/tenants', headers=self.headers, json={
            'client_request_id': 'admin-2', 'host': 'acme.example',
            'room_mode': 'ai', 'policy': {'mode': 'disabled'}})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(len(client.get('/admin/tenants').json()), 1)

    def test_customer_endpoints_are_not_served_by_the_console(self):
        self.assertEqual(self.login().status_code, 303)
        # Assert the invariant directly: no customer route is registered here.
        # (A request merely redirects, because the /admin-scoped cookie is not
        # sent to /customer/*, so status alone would not prove absence.)
        paths = {getattr(route, 'path', '') for route in self.app.routes}
        self.assertFalse([p for p in paths if p.startswith('/customer')], paths)
        # Even authenticated, the console serves nothing under /customer.
        self.assertEqual(self.login().status_code, 303)
        for path in ('/customer/activate', '/customer/me', '/customer/onboarding'):
            body = self.client.get(path, headers={
                'cookie': f'{admin_service.OperatorSessions.COOKIE_NAME}='
                          f'{self.client.cookies.get(admin_service.OperatorSessions.COOKIE_NAME)}'})
            self.assertEqual(body.status_code, 404, path)


if __name__ == '__main__':
    unittest.main()
