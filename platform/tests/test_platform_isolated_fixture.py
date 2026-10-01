"""Socket-free public-browser journeys with isolated module/session/DB per test.

HTTPS transport models nginx's /admin prefix stripping only; it does not prove
actual TLS/nginx behavior. HTTP mode exercises the direct tunnel URL unchanged.
"""
import importlib.util
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from fastapi.testclient import TestClient


class FixtureJourney:
    loopback = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.password = 'disposable-test-password-123456'
        env = {'PLATFORM_FIXTURE_ADMIN_PASSWORD': self.password,
               'PLATFORM_LOOPBACK_HTTP_TEST': '1' if self.loopback else '0',
               'PLATFORM_FIXTURE_DB': self.tmp.name + '/fixture.sqlite'}
        spec = importlib.util.spec_from_file_location(
            'isolated_fixture_under_test', Path(__file__).resolve().parents[1] / 'platform_isolated_fixture.py')
        self.fixture = importlib.util.module_from_spec(spec)
        # Fresh module executes each time; never reads production auth env.
        with patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(self.fixture)
        self.origin = self.fixture.FIXTURE_ORIGIN
        self.host = urlsplit(self.origin).netloc
        fixture_app = self.fixture.app
        loopback = self.loopback

        async def transport(scope, receive, send):
            if scope['type'] == 'http' and not loopback and scope['path'].startswith('/admin/'):
                scope = dict(scope)
                scope['path'] = scope['path'][len('/admin'):]
                scope['raw_path'] = scope['path'].encode()
            await fixture_app(scope, receive, send)

        self.client = TestClient(transport, base_url=self.origin, follow_redirects=False)
        self.addCleanup(self.client.close)

    def login(self):
        return self.client.post('/admin/support/login', data={'password': self.password},
                                headers={'origin': self.origin, 'sec-fetch-site': 'same-origin'})

    def test_login_redirect_html_assets_api_and_hub(self):
        denied = self.client.get('/admin/support/customers')
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers['location'], '/admin/support/login')
        login_page = self.client.get(denied.headers['location'])
        self.assertEqual(login_page.status_code, 200)
        self.assertIn('action="/admin/support/login"', login_page.text)
        bad = self.client.post('/admin/support/login', data={'password': 'wrong'},
                               headers={'origin': self.origin})
        self.assertEqual(bad.status_code, 401)
        self.assertNotIn('set-cookie', bad.headers)
        good = self.login()
        self.assertEqual(good.status_code, 303)
        cookie = good.headers['set-cookie'].lower()
        self.assertEqual('secure' in cookie, not self.loopback)
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=lax', cookie)
        self.assertNotIn('domain=', cookie)
        page = self.client.get(good.headers['location'])  # real cookie jar, not forged auth
        self.assertEqual(page.status_code, 200)
        self.assertIn('客戶管理', page.text)
        assets = re.findall(r'(?:href|src)="([^"]+)"', page.text)
        self.assertEqual(len(assets), 4)  # style, management.js, tasks.js, chat.js
        script = ''
        for path in assets:
            self.assertTrue(path.startswith('/admin/support/customers/assets/'))
            asset = self.client.get(path)
            self.assertEqual(asset.status_code, 200, path)
            self.assertEqual(asset.headers['cache-control'], 'no-store')
            if path.endswith('management.js'):
                script = asset.text
        api_path = re.search(r"fetch\('([^']+)' \+ path", script).group(1)
        api = self.client.get(api_path)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json(), [])
        self.assertEqual(self.client.get('/admin/support/customers').status_code, 200)
        hub = self.client.get('/admin/hub')
        self.assertEqual(hub.status_code, 200)
        self.assertIn('legacy Hub route remains reachable', hub.text)

    def test_missing_and_tampered_session_cannot_read_hub_ui_assets_api(self):
        self.assertEqual(self.client.get('/__fixture/health').json()['ok'], True)
        for value in (None, 'invalid-session'):
            self.client.cookies.clear()
            if value:
                self.client.cookies.set(self.fixture.auth.COOKIE_NAME, value)
            for path in ('/admin/hub', '/admin/support/customers',
                         '/admin/support/customers/assets/management.js', '/admin/support/tenants'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302, path)
                self.assertEqual(response.headers['location'], '/admin/support/login')
                self.assertNotIn('legacy Hub route remains reachable', response.text)

    def test_login_rejects_bad_or_duplicate_edge_headers(self):
        cases = [
            [('host', self.host)],  # POST Origin missing
            [('host', self.host), ('origin', 'https://evil.example')],
            [('host', self.host), ('host', self.host), ('origin', self.origin)],
            [('host', 'evil.example'), ('origin', self.origin)],
            [('host', self.host), ('origin', self.origin), ('origin', self.origin)],
            [('host', self.host), ('origin', self.origin), ('sec-fetch-site', 'cross-site')],
            [('host', self.host), ('origin', self.origin),
             ('sec-fetch-site', 'same-origin'), ('sec-fetch-site', 'same-origin')],
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                response = self.client.post('/admin/support/login',
                                            data={'password': self.password}, headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('set-cookie', response.headers)

    def test_authenticated_edges_and_query_credentials_still_rejected(self):
        self.assertEqual(self.login().status_code, 303)
        for path in ('/admin/support/customers', '/admin/support/tenants', '/admin/hub'):
            for headers in ([('host', self.host), ('host', self.host)],
                            [('origin', self.origin), ('origin', self.origin)],
                            [('sec-fetch-site', 'cross-site')],
                            [('sec-fetch-site', 'same-origin'), ('sec-fetch-site', 'none')]):
                self.assertEqual(self.client.get(path, headers=headers).status_code, 403)
        for path in ('/admin/support/customers', '/admin/support/tenants',
                     '/admin/support/customers/assets/style.css', '/admin/support/login'):
            self.assertEqual(self.client.get(path + '?token=not-a-credential').status_code, 400)

    def test_no_duplicate_route_method_registrations(self):
        seen = set()
        for route in self.fixture.app.routes:
            for method in route.methods:
                key = route.path, method
                self.assertNotIn(key, seen)
                seen.add(key)


class HTTPSFixtureTests(FixtureJourney, unittest.TestCase):
    def test_no_customer_app_or_simulated_runners_outside_loopback(self):
        self.assertIsNone(self.fixture.customer_app)
        self.assertIsNone(self.fixture.site_executor)
        self.assertEqual(self.login().status_code, 303)
        response = self.client.post('/admin/support/tenants/x/site-jobs', json={'client_request_id': 'a'},
                                    headers={'origin': self.origin})
        self.assertEqual(response.status_code, 404)
        page = self.client.get('/admin/support/customers')
        self.assertNotIn('data-mode', page.text)


class LoopbackFixtureTests(FixtureJourney, unittest.TestCase):
    loopback = True

    def test_real_provisioning_is_off_unless_explicitly_enabled(self):
        from support_simulated_runners import SimulatedSiteExecutor
        # Default fixture composition must never hold a real provisioner.
        self.assertIsInstance(self.fixture.site_executor, SimulatedSiteExecutor)
        self.assertFalse(self.fixture.REAL_PROVISIONING)
        self.assertTrue(self.client.get('/__fixture/health').json()['ok'])

    def test_real_provisioning_requires_a_platform_host_and_loopback(self):
        import importlib.util
        from pathlib import Path as _Path

        def load(env):
            spec = importlib.util.spec_from_file_location(
                'fixture_real_check', _Path(__file__).resolve().parents[1] / 'platform_isolated_fixture.py')
            module = importlib.util.module_from_spec(spec)
            with patch.dict(os.environ, env, clear=True):
                spec.loader.exec_module(module)
            return module

        base = {'PLATFORM_FIXTURE_ADMIN_PASSWORD': self.password,
                'PLATFORM_FIXTURE_DB': self.tmp.name + '/real.sqlite'}
        # Enabled without a platform host, or outside loopback: refuse to start.
        with self.assertRaises(RuntimeError):
            load({**base, 'PLATFORM_LOOPBACK_HTTP_TEST': '1', 'PLATFORM_REAL_PROVISIONING': '1'})
        with self.assertRaises(RuntimeError):
            load({**base, 'PLATFORM_LOOPBACK_HTTP_TEST': '0', 'PLATFORM_REAL_PROVISIONING': '1',
                  'PLATFORM_FIXTURE_HOST': 'platform.example'})

    def customer_client(self):
        from support_app import LOOPBACK_ORIGIN
        client = TestClient(self.fixture.customer_app, base_url=LOOPBACK_ORIGIN, follow_redirects=False)
        self.addCleanup(client.close)
        return client, {'origin': LOOPBACK_ORIGIN}

    def test_full_simulated_customer_journey(self):
        origin = {'origin': self.origin}
        self.assertEqual(self.login().status_code, 303)
        page = self.client.get('/admin/support/customers')
        self.assertIn('data-mode="loopback"', page.text)
        # Admin: bootstrap a metadata-only tenant bound to DNS host localhost.
        created = self.client.post('/admin/support/tenants', headers=origin, json={
            'client_request_id': 'fixture-1', 'host': 'localhost', 'room_mode': 'ai',
            'policy': {'mode': 'disabled'}})
        self.assertEqual(created.status_code, 200, created.text)
        tenant = created.json()['tenant']
        self.assertEqual(created.json()['provisioning'], 'not_provisioned')
        invite = self.client.post('/admin/support/tenants/' + tenant['id'] + '/invites',
                                  headers=origin, json={'ttl_seconds': 600})
        self.assertEqual(invite.status_code, 201)
        token = invite.json()['token']
        self.assertNotIn(token, self.client.get('/admin/support/tenants').text)
        # Customer: the invite URL the admin page would render.
        customer, corigin = self.customer_client()
        self.assertEqual(customer.get('/__fixture/health').json()['role'], 'customer')
        welcome = customer.get('/?token=' + token)
        self.assertEqual(welcome.status_code, 200)
        self.assertEqual(customer.get('/customer/onboarding').status_code, 303)
        activated = customer.post('/customer/activate', headers=corigin, json={
            'invite': token, 'username': 'alice', 'password': 'alice-long-password-1'})
        self.assertEqual(activated.status_code, 201, activated.text)
        self.assertNotIn('Secure', activated.headers['set-cookie'])
        self.assertEqual(customer.get('/customer/onboarding').status_code, 200)
        status = customer.get('/customer/onboarding/status').json()
        self.assertEqual(status['site_job']['status'], 'not_requested')
        self.assertEqual(status['model_settings']['state'], 'unconfigured')
        self.assertTrue(status['model_settings']['simulated_probe_available'])
        self.assertFalse(status['ready_for_work'])
        # Customer: acknowledge, select a model, run the SIMULATED probe.
        ack = customer.post('/customer/onboarding/acknowledge', headers=corigin,
                            json={'rules_version': status['rules_version']})
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(customer.post('/customer/model-settings', headers=corigin, json={
            'selection_id': 'fake-model-b', 'expected_revision': 0}).json()['state'], 'pending')
        probe = customer.post('/customer/model-settings/simulated-probe', headers=corigin,
                              json={'expected_revision': 1})
        self.assertEqual((probe.status_code, probe.json()['state']), (200, 'failed'))
        self.assertEqual(customer.post('/customer/model-settings', headers=corigin, json={
            'selection_id': 'fake-model-a', 'expected_revision': 1}).json()['revision'], 2)
        for body in ({'expected_revision': 1}, {'expected_revision': 2, 'passed': True}):
            self.assertIn(customer.post('/customer/model-settings/simulated-probe', headers=corigin,
                                        json=body).status_code, (400, 409), body)
        probe = customer.post('/customer/model-settings/simulated-probe', headers=corigin,
                              json={'expected_revision': 2}).json()
        self.assertEqual((probe['state'], probe['simulated'], probe['verified_for_work']),
                         ('simulated_pass', True, False))
        # Admin queues the site job; the in-process simulated executor completes it.
        queued = self.client.post('/admin/support/tenants/' + tenant['id'] + '/site-jobs',
                                  headers=origin, json={'client_request_id': 'fixture-job-1'})
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertEqual(queued.json()['job']['status'], 'queued')
        self.assertFalse(queued.json()['executor_connected'])
        self.assertEqual(customer.get('/customer/onboarding/status').json()['site_job']['status'], 'queued')
        self.assertEqual(self.fixture.site_executor.run_once(), [queued.json()['job']['job_id']])
        self.assertEqual(self.fixture.site_executor.run_once(), [])
        status = customer.get('/customer/onboarding/status').json()
        self.assertEqual(status['site_job'], {'status': 'succeeded_simulated', 'simulated': True,
                                              'provisioned': False})
        self.assertEqual(status['model_settings']['state'], 'simulated_pass')
        self.assertFalse(status['ready_for_work'])
        # Simulated work must never read as a real site, and a simulated site
        # has no office to read.
        steps = {step['id']: step for step in status['steps']}
        self.assertEqual(steps['site_preparation']['reason'], 'simulated_only')
        self.assertEqual(steps['site_preparation']['status'], 'blocked')
        self.assertEqual(steps['office']['status'], 'blocked')
        self.assertIsNone(status['office'])
        # Resume after logout/login; the model probe is bound to the old session.
        self.assertEqual(customer.post('/customer/logout', headers=corigin, json={}).status_code, 200)
        self.assertEqual(customer.get('/customer/onboarding/status').status_code, 401)
        self.assertEqual(customer.post('/customer/login', headers=corigin, json={
            'username': 'alice', 'password': 'alice-long-password-1'}).status_code, 200)
        status = customer.get('/customer/onboarding/status').json()
        self.assertTrue(status['rules_acknowledged'])
        self.assertEqual(status['model_settings']['state'], 'pending')
        self.assertEqual(status['site_job']['status'], 'succeeded_simulated')
        # Customer chat with the explicit fake model only.
        rooms = customer.get('/customer/rooms').json()
        self.assertEqual(len(rooms), 1)
        # Admin session cookie never authenticates on the customer app and vice versa.
        admin_cookie = self.client.cookies.get(self.fixture.auth.COOKIE_NAME)
        customer.cookies.clear()
        self.assertEqual(customer.get('/customer/onboarding/status', headers={
            'cookie': self.fixture.auth.COOKIE_NAME + '=' + admin_cookie}).status_code, 401)
        self.assertEqual(customer.get('/customer/onboarding/status', headers={
            'host': 'localhost:18203'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
