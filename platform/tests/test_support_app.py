"""Offline tests; run with python -m unittest test_support_app -v."""
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from support_core import SupportCore
from support_app import COOKIE, MAX_BODY, create_app


class CustomerAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = SupportCore(str(Path(self.tmp.name) / 'test.sqlite'),
                                admin_verifier=lambda assertion: 'test-admin')
        admin = self.core.admin_actor('test')
        tenant = self.core.create_tenant(admin, 'customer.example')
        self.invite = self.core.issue_invite(admin, tenant.id)
        self.now = 100.0
        self.app = create_app(self.core, 'https://customer.example', clock=lambda: self.now)
        self.client = TestClient(self.app, base_url='https://customer.example')
        self.addCleanup(self.client.close)
        self.headers = {'origin': 'https://customer.example'}
        self.credentials = {'username': 'alice', 'password': 'a-long-test-password'}

    def post(self, path, body, **kw):
        return self.client.post('/customer/' + path, json=body, headers=self.headers, **kw)

    def activate(self):
        return self.post('activate', dict(self.credentials, invite=self.invite))

    def assert_safe(self, response):
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'same-origin')
        self.assertNotIn(self.invite, response.text)
        self.assertNotIn(self.credentials['password'], response.text)

    def test_lifecycle_and_login(self):
        response = self.activate()
        self.assertEqual(response.status_code, 201)
        self.assert_safe(response)
        cookie = response.headers['set-cookie']
        for item in ('Secure', 'HttpOnly', 'SameSite=strict', 'Path=/', COOKIE):
            self.assertIn(item, cookie)
        self.assertNotIn('Domain', cookie)
        token = self.client.cookies.get(COOKIE)
        me = self.client.get('/customer/me')
        self.assertEqual(me.json()['username'], 'alice')
        self.assertNotIn(token, me.text)
        self.assert_safe(me)
        self.assertEqual(self.post('logout', {}).status_code, 200)
        self.assertEqual(self.client.get('/customer/me').status_code, 401)
        replay = self.client.get('/customer/me', headers={'cookie': COOKIE + '=' + token})
        self.assertEqual(replay.status_code, 401)
        self.assertEqual(self.post('login', self.credentials).status_code, 200)
        self.assertEqual(self.client.get('/customer/me').status_code, 200)

    def test_reuse_and_wrong_password_are_generic(self):
        self.assertEqual(self.activate().status_code, 201)
        reuse = self.activate()
        wrong = self.post('login', dict(self.credentials, password='wrong-password-long'))
        self.assertEqual(reuse.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(reuse.json(), wrong.json())
        self.assert_safe(reuse)
        self.assert_safe(wrong)

    def test_raw_edge_and_admin_cookies(self):
        cases = [ [('host', 'evil.example')],
                  [('host', 'customer.example'), ('host', 'customer.example')],
                  [('origin', 'https://evil.example')],
                  [('origin', 'https://customer.example'), ('origin', 'https://customer.example')],
                  [('sec-fetch-site', 'cross-site')],
                  [('cookie', COOKIE + '=' + 'a' * 43 + '; ' + COOKIE + '=' + 'a' * 43)],
                  [('cookie', COOKIE + '=short')],
                  [('cookie', COOKIE + '=' + 'a' * 43), ('cookie', COOKIE + '=' + 'b' * 43)] ]
        for headers in cases:
            with self.subTest(headers=headers):
                response = self.client.get('/customer/me', headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assert_safe(response)
        response = self.client.post('/customer/login', json=self.credentials)
        self.assertEqual(response.status_code, 403)
        # Cookies a sibling site can plant on the parent domain (malformed,
        # or named like an admin cookie) never authenticate and never lock the
        # visitor out either.
        for planted in ('swarm_admin_session=admin-secret', 'malformed-cookie', 'x="a b',
                        'platform_admin_session=x'):
            with self.subTest(planted=planted):
                self.assertEqual(self.client.get('/customer/me', headers={'cookie': planted}).status_code, 401)
                self.assertEqual(self.client.post('/customer/login', json={}, headers=dict(
                    self.headers, cookie=planted)).status_code, 400)
        self.assertEqual(self.client.get('/admin').status_code, 404)
        self.assertEqual(self.client.get('/docs').status_code, 404)

    def test_dsh_cookies_coexist_but_never_authenticate(self):
        unrelated = 'dsh_session=dsh-secret; dsh_csrf=csrf-secret'
        response = self.client.get('/customer/me', headers={'cookie': unrelated})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.activate().status_code, 201)
        token = self.client.cookies.get(COOKIE)
        response = self.client.get('/customer/me', headers={
            'cookie': unrelated + '; ' + COOKIE + '=' + token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['username'], 'alice')
        self.assert_safe(response)
        response = self.client.get('/customer/me', headers={'cookie': unrelated})
        self.assertEqual(response.status_code, 401)
        response = self.client.get('/customer/me', headers={
            'cookie': 'swarm_admin_session=secret; junk="a b; ' + COOKIE + '=' + token})
        self.assertEqual(response.status_code, 200)

    def test_customer_session_cannot_cross_host(self):
        self.assertEqual(self.activate().status_code, 201)
        token = self.client.cookies.get(COOKIE)
        other_app = create_app(self.core, 'https://other.example')
        with TestClient(other_app, base_url='https://other.example') as other:
            response = other.get('/customer/me', headers={'cookie': COOKIE + '=' + token})
            self.assertEqual(response.status_code, 401)
            self.assert_safe(response)

    def test_internal_errors_are_redacted(self):
        with patch.object(self.core, 'login', side_effect=RuntimeError(self.credentials['password'])):
            response = self.post('login', self.credentials)
        self.assertEqual(response.status_code, 500)
        self.assert_safe(response)

    def test_strict_body(self):
        bodies = ['[]', 'null', '{"username":"a","username":"b","password":"long-password"}',
                  json.dumps(dict(self.credentials, admin=True)),
                  json.dumps(dict(self.credentials, password=123)),
                  json.dumps(dict(self.credentials, username='bad space'))]
        for body in bodies:
            response = self.client.post('/customer/login', content=body,
                                       headers=dict(self.headers, **{'content-type': 'application/json'}))
            self.assertEqual(response.status_code, 400)
            self.assert_safe(response)
        response = self.client.post('/customer/login', content='{}', headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_rate_limit_clock_forwarded_and_capacity(self):
        app = create_app(self.core, 'https://customer.example', clock=lambda: self.now,
                         rate_limit=2, rate_capacity=1)
        with TestClient(app, base_url='https://customer.example') as client:
            for i in range(2):
                response = client.post('/customer/login', json={}, headers=dict(
                    self.headers, **{'x-forwarded-for': str(i)}))
                self.assertEqual(response.status_code, 400)
            response = client.post('/customer/activate', json={}, headers=self.headers)
            self.assertEqual(response.status_code, 429)
            self.assert_safe(response)
            self.now += 61
            self.assertEqual(client.post('/customer/login', json={}, headers=self.headers).status_code, 400)
        from support_app import _Limiter, client_address
        # Behind nginx every peer is loopback; the nginx-set header separates
        # visitors. A non-loopback peer cannot choose its own bucket, and
        # X-Forwarded-For / X-Real-IP are never read.
        loop = ('127.0.0.1', 5)
        self.assertEqual(client_address({'client': loop, 'headers': [(b'x-swarm-client', b'203.0.113.9')]}),
                         '203.0.113.9')
        self.assertEqual(client_address({'client': ('::1', 5), 'headers': [(b'x-swarm-client', b'2001:db8::1')]}),
                         '2001:db8::1')
        self.assertEqual(client_address({'client': ('198.51.100.1', 5),
                                         'headers': [(b'x-swarm-client', b'203.0.113.9')]}), '198.51.100.1')
        for headers in ([(b'x-forwarded-for', b'203.0.113.9')], [(b'x-real-ip', b'203.0.113.9')],
                        [(b'x-swarm-client', b'not-an-ip')],
                        [(b'x-swarm-client', b'203.0.113.9'), (b'x-swarm-client', b'203.0.113.10')]):
            self.assertEqual(client_address({'client': loop, 'headers': headers}), '127.0.0.1', headers)
        limiter = _Limiter(lambda: self.now, 2, 60, 1)
        self.assertTrue(limiter.allow(('ip1', 'host')))
        self.assertFalse(limiter.allow(('ip2', 'host')))
        self.assertEqual(len(limiter.entries), 1)

    def test_stream_size_bounded_without_content_length(self):
        async def exercise():
            messages = iter([{'type': 'http.request', 'body': b'x' * MAX_BODY, 'more_body': True},
                             {'type': 'http.request', 'body': b'x', 'more_body': True}])
            received, output = [], []
            async def receive():
                value = next(messages)
                received.append(value)
                return value
            async def send(message):
                output.append(message)
            scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST',
                     'scheme': 'https', 'path': '/customer/login', 'raw_path': b'/customer/login',
                     'query_string': b'', 'root_path': '', 'server': ('customer.example', 443),
                     'client': ('127.0.0.1', 123), 'headers': [(b'host', b'customer.example'),
                     (b'origin', b'https://customer.example'), (b'content-type', b'application/json')]}
            await self.app(scope, receive, send)
            self.assertEqual(output[0]['status'], 413)
            self.assertEqual(len(received), 2)
        asyncio.run(exercise())

    def test_hashing_in_worker_thread(self):
        import support_core
        original = support_core._password_hash
        threads = []
        def checked(password):
            threads.append(threading.current_thread().name)
            return original(password)
        with patch('support_core._password_hash', checked):
            self.assertEqual(self.activate().status_code, 201)
        self.assertIn('worker', threads[0].lower())

    def test_taken_username_reports_conflict_not_bad_credentials(self):
        """Regression: a username already in use returned 401, so the customer
        was told their password was wrong and had no way to recover.

        Usernames are unique per platform host, so the clash can come from
        another tenant entirely; the message must not blame credentials.
        """
        self.assertEqual(self.activate().status_code, 201)
        admin = self.core.admin_actor('test')
        with self.core._connect() as conn:
            tenant_id = conn.execute('SELECT id FROM tenants').fetchone()['id']
        second = self.core.issue_invite(admin, tenant_id)
        response = self.post('activate', dict(self.credentials, invite=second))
        self.assertEqual(response.status_code, 409)
        self.assert_safe(response)
        # A free username still activates, and the original account still works.
        third = self.core.issue_invite(admin, tenant_id)
        self.assertEqual(self.post('activate', dict(self.credentials, invite=third,
                                                    username='bob')).status_code, 201)
        self.assertEqual(self.post('login', self.credentials).status_code, 200)

    def test_loopback_mode_is_fixed_authority_and_never_default(self):
        from support_app import LOOPBACK_COOKIE, LOOPBACK_ORIGIN
        # Production factory: __Host- Secure cookie regardless of any option omission.
        self.assertIn('Secure', self.activate().headers['set-cookie'])
        for bad in ('http://localhost:18203', 'http://127.0.0.1:18204', 'https://localhost:18204',
                    'http://localhost:18204/', 'http://evil.example'):
            with self.assertRaises(ValueError):
                create_app(self.core, bad, loopback_http_test=True)
        # Loopback tenant is bound to DNS host 'localhost'; the port only lives in Host.
        admin = self.core.admin_actor('test')
        invite = self.core.issue_invite(admin, self.core.create_tenant(admin, 'localhost').id)
        app = create_app(self.core, LOOPBACK_ORIGIN, loopback_http_test=True)
        client = TestClient(app, base_url=LOOPBACK_ORIGIN)
        self.addCleanup(client.close)
        headers = {'origin': LOOPBACK_ORIGIN}
        response = client.post('/customer/activate', headers=headers,
                               json=dict(self.credentials, invite=invite))
        self.assertEqual(response.status_code, 201)
        cookie = response.headers['set-cookie']
        self.assertIn(LOOPBACK_COOKIE, cookie)
        self.assertNotIn('__Host-', cookie)
        self.assertNotIn('Secure', cookie)
        self.assertEqual(client.get('/customer/me').json()['username'], 'alice')
        # Wrong Host/Origin still rejected in loopback mode; production cookie name ignored.
        self.assertEqual(client.get('/customer/me', headers={'host': 'localhost:18203'}).status_code, 403)
        self.assertEqual(client.post('/customer/logout', json={},
                                     headers={'origin': 'http://localhost:18203'}).status_code, 403)
        token = client.cookies.get(LOOPBACK_COOKIE)
        client.cookies.clear()
        self.assertEqual(client.get('/customer/me', headers={'cookie': COOKIE + '=' + token}).status_code, 401)
        # The loopback session cannot be replayed against the production app.
        self.assertEqual(self.client.get('/customer/me', headers={'cookie': COOKIE + '=' + token}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
