"""End-to-end ASGI composition tests for nginx-stripped /admin prefix."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import auth as swarm_auth
from support_core import InvalidInput, SupportCore, Unauthorized
from support_http_policy import EdgePolicy
from support_rooms import SupportRooms
from support_swarm_admin import admin_edge_policy, install_swarm_admin_support


class SwarmAdminAdapterTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('ADMIN_USERNAME', 'admin'),
                            ('SESSION_SECRET', 'disposable-adapter-test-signing-secret')]:
            patcher = patch.object(swarm_auth, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = SupportCore(Path(self.tmp.name) / 'adapter.sqlite',
                                admin_verifier=lambda value: value if value == 'admin' else None)
        self.actor = self.core.admin_actor('admin')
        self.tenant = self.core.create_tenant(self.actor, 'customer.example')
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.actor, self.tenant.id)['id']
        self.app = FastAPI()
        self.app.middleware('http')(swarm_auth.auth_middleware)
        self.app.add_api_route('/hub', lambda: PlainTextResponse('legacy hub'))
        self.app.add_api_route('/legacy-admin', lambda: PlainTextResponse('legacy admin'))
        install_swarm_admin_support(self.app, self.core, self.rooms,
                                    'https://admin.example.com')
        # Simulate requests nginx sends upstream after stripping external /admin/.
        self.client = TestClient(self.app, base_url='https://admin.example.com',
                                 follow_redirects=False)
        self.addCleanup(self.client.close)
        self.cookie = {swarm_auth.COOKIE_NAME: swarm_auth.issue_session()}
        self.origin = {'origin': 'https://admin.example.com'}

    def test_existing_signed_admin_auth_guards_support_routes(self):
        response = self.client.get('/support/rooms', headers=self.origin)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['location'], '/login')
        response = self.client.get('/support/rooms', cookies=self.cookie, headers=self.origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/support/rooms',
                         headers={**self.origin, 'x-test-admin':'admin'}).status_code, 302)

    def test_exact_origin_and_customer_cookie_are_not_admin_identity(self):
        response = self.client.get('/support/rooms', cookies=self.cookie,
                                   headers={'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        response = self.client.get('/support/rooms',
                                   headers={**self.origin, 'cookie':'__Host-customer_session='+'A'*43})
        self.assertEqual(response.status_code, 302)

    def test_html_assets_and_management_api_keep_public_support_prefix(self):
        page = self.client.get('/support/customers', cookies=self.cookie,
                               headers=self.origin)
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/admin/support/customers/assets/style.css"', page.text)
        self.assertIn('src="/admin/support/customers/assets/management.js"', page.text)
        script = self.client.get('/support/customers/assets/management.js',
                                 cookies=self.cookie, headers=self.origin)
        self.assertEqual(script.status_code, 200)
        self.assertIn("fetch('/admin/support/tenants' + path", script.text)
        tenants = self.client.get('/support/tenants', cookies=self.cookie,
                                  headers=self.origin)
        self.assertEqual(tenants.status_code, 200)
        self.assertEqual(tenants.json()[0]['id'], self.tenant.id)
        css = self.client.get('/support/customers/assets/style.css', cookies=self.cookie,
                              headers=self.origin)
        self.assertEqual(css.status_code, 200)

    def test_customer_conversations_live_in_customer_management(self):
        page = self.client.get('/support/customers', cookies=self.cookie, headers=self.origin)
        self.assertIn('src="/admin/support/customers/assets/chat.js"', page.text)
        self.assertIn('id="chat-section"', page.text)
        script = self.client.get('/support/customers/assets/chat.js', cookies=self.cookie,
                                 headers=self.origin)
        self.assertEqual(script.status_code, 200)
        self.assertIn("fetch('/admin/support/' + path", script.text)
        overview = self.client.get('/support/room-overview', cookies=self.cookie, headers=self.origin)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()[0]['site_host'], 'customer.example')
        self.assertEqual(self.client.get('/support/room-overview', headers=self.origin).status_code, 302)
        reply = self.client.post(f'/support/rooms/{self.room}/reply', cookies=self.cookie,
                                 headers=self.origin, json={'client_message_id': 'r1', 'body': '您好'})
        self.assertEqual(reply.status_code, 200)
        # No model composed: 指示 AI is unavailable, not silently faked.
        self.assertEqual(self.client.post(f'/support/rooms/{self.room}/assist', cookies=self.cookie,
                                          headers=self.origin, json={'instruction': '說明'}).status_code, 503)
        self.assertEqual(self.client.post(f'/support/rooms/{self.room}/assist',
                                          headers=self.origin, json={'instruction': '說明'}).status_code, 302)
        self.assertEqual(reply.json()['room']['mode'], 'ai')  # a reply never changes the mode
        self.assertEqual(self.client.post(f'/support/rooms/{self.room}/reply', cookies=self.cookie,
                                          headers=self.origin, json={'body': 'x'}).status_code, 400)
        self.assertEqual(self.client.post(f'/support/rooms/{self.room}/reply', cookies=self.cookie,
                                          headers={'origin': 'https://evil.example'},
                                          json={'client_message_id': 'r2', 'body': 'x'}).status_code, 403)
        # The retired standalone support console points at the new panel.
        old = self.client.get('/support/support', cookies=self.cookie, headers=self.origin)
        self.assertEqual(old.status_code, 303)
        self.assertEqual(old.headers['location'], 'customers#chat-section')
        self.assertEqual(old.headers['cache-control'], 'no-store')

    def test_loopback_public_paths_map_directly_without_nginx(self):
        app = FastAPI()
        core = self.core
        rooms = self.rooms
        install_swarm_admin_support(app, core, rooms, 'http://localhost:18203',
                                    allow_loopback_http_test_origin=True)
        client = TestClient(app, base_url='http://localhost:18203', follow_redirects=False)
        self.addCleanup(client.close)
        headers = {'host': 'localhost:18203'}
        for path, content_type in [
            ('/admin/support/customers', 'text/html'),
            ('/admin/support/customers/assets/management.js', 'text/javascript'),
            ('/admin/support/customers/assets/style.css', 'text/css'),
            ('/admin/support/tenants', 'application/json'),
        ]:
            response = client.get(path, cookies=self.cookie, headers=headers)
            self.assertEqual(response.status_code, 200, path)
            self.assertTrue(response.headers['content-type'].startswith(content_type), path)

    def test_strict_edge_headers_in_both_modes(self):
        for loopback, origin, path in [
            (False, 'https://admin.example.com', '/support/tenants'),
            (True, 'http://localhost:18203', '/admin/support/tenants'),
        ]:
            app = FastAPI()
            install_swarm_admin_support(app, self.core, self.rooms, origin,
                                        allow_loopback_http_test_origin=loopback)
            with TestClient(app, base_url=origin) as client:
                client.cookies.update(self.cookie)
                host = origin.split('://')[1]
                cases = [
                    [('host', host), ('host', host)],
                    [('origin', origin), ('origin', origin)],
                    [('host', 'evil.example')],
                    [('origin', 'https://evil.example')],
                    [('sec-fetch-site', 'cross-site')],
                    [('sec-fetch-site', 'same-origin'), ('sec-fetch-site', 'same-origin')],
                ]
                for headers in cases:
                    with self.subTest(loopback=loopback, headers=headers):
                        self.assertEqual(client.get(path, headers=headers).status_code, 403)
                # Missing POST Origin must not be manufactured by the adapter.
                self.assertEqual(client.post(path, json={}).status_code, 403)
                for headers in cases:
                    self.assertEqual(client.post(path, json={}, headers=headers).status_code, 403)

    def test_missing_host_and_duplicate_raw_headers_fail_closed(self):
        for loopback, origin in [(False, 'https://admin.example.com'),
                                 (True, 'http://localhost:18203')]:
            policy = admin_edge_policy(origin, allow_loopback_http_test_origin=loopback)
            for method in ('GET', 'POST'):
                with self.assertRaises(Unauthorized):
                    policy.validate(method, [(b'origin', origin.encode())])

    def test_https_post_preserves_exact_origin_headers_for_child(self):
        observations = []
        validate = EdgePolicy.validate

        def capture(policy, method, headers):
            observations.append(list(headers))
            return validate(policy, method, headers)

        headers = {'origin': 'https://admin.example.com', 'sec-fetch-site': 'same-origin'}
        with patch.object(EdgePolicy, 'validate', capture):
            # Valid authority/auth but invalid body: reaches child, no metadata write.
            self.assertEqual(self.client.post('/support/tenants', cookies=self.cookie,
                                              headers=headers, json={}).status_code, 400)
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0], observations[1])

    def test_loopback_exception_is_opt_in_and_fixed_authority(self):
        for origin, enabled in [('http://localhost:18203', False),
                                ('http://evil.example:18203', True),
                                ('http://localhost:9999', True)]:
            with self.assertRaises((ValueError, InvalidInput)):
                install_swarm_admin_support(FastAPI(), self.core, self.rooms, origin,
                                            allow_loopback_http_test_origin=enabled)
        # HTTPS adapter does not expose the direct tunnel alias.
        self.assertEqual(self.client.get('/admin/support/customers',
                                         cookies=self.cookie).status_code, 404)

    def test_chat_api_routes_and_legacy_routes_coexist(self):
        response = self.client.get('/support/rooms', cookies=self.cookie,
                                   headers=self.origin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/hub', cookies=self.cookie).text, 'legacy hub')
        self.assertEqual(self.client.get('/legacy-admin', cookies=self.cookie).text, 'legacy admin')
        # Prefix conflicts fail closed rather than overriding an old route.
        with self.assertRaises(ValueError):
            install_swarm_admin_support(self.app, self.core, self.rooms,
                                        'https://admin.example.com', prefix='/hub')

    def test_query_credentials_rejected_and_duplicate_install_fails(self):
        response = self.client.get('/support/customers?token=secret', cookies=self.cookie,
                                   headers=self.origin)
        self.assertEqual(response.status_code, 400)
        with self.assertRaises(ValueError):
            install_swarm_admin_support(self.app, self.core, self.rooms,
                                        'https://admin.example.com')


if __name__ == '__main__':
    unittest.main()
