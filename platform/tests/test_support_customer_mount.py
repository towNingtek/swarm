"""Path split contract; no proxy or DSH process is started."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import SupportCore
from support_rooms import SupportRooms
from support_chat_app import create_customer_chat_app
from support_ui import install_customer_ui
from support_customer_mount import CustomerSupportPath


class CustomerPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = SupportCore(Path(self.tmp.name) / 'customer.sqlite')
        self.rooms = SupportRooms(self.core)
        self.app = create_customer_chat_app(self.core, self.rooms, 'https://customer.example')
        install_customer_ui(self.app)
        self.mount = CustomerSupportPath(self.app)
        self.client = TestClient(self.mount, base_url='https://customer.example', follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_no_token_root_is_explicit_dsh_fallthrough_not_platform_page(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.text, 'route to DSH')
        self.assertNotIn('token=', response.text)

    def test_invite_root_scrubs_token_and_returns_welcome_directly(self):
        token = 'A' * 43
        response = self.client.get('/?token=' + token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<form id="credentials"', response.text)
        self.assertNotIn(token, response.text)
        self.assertNotIn('data-invite="' + token, response.text)
        self.assertNotIn(token, response.headers.get('location', ''))
        self.assertNotIn('location:', {k.lower():v for k,v in response.headers.items()})
        self.assertEqual(response.headers['referrer-policy'], 'same-origin')

    def test_duplicate_or_extra_query_fields_rejected(self):
        for query in ('?token=' + 'A'*43 + '&token=' + 'B'*43,
                      '?token=' + 'A'*43 + '&next=/admin', '?bad=x'):
            self.assertEqual(self.client.get('/' + query).status_code, 400)

    def test_malformed_queries_are_redacted_400_not_parser_errors(self):
        for query in (b'token', b'token=', b'token=%', b'token=%GG',
                      b'token=' + b'A'*42, b'token=' + b'A'*44,
                      b'token=' + b'A'*43 + b'&', b'token=' + b'A'*43 + b'\xff',
                      b'token=' + b'A'*43 + b';other=x',
                      b'%74oken=' + b'A'*43, b'token=%41' + b'A'*42,
                      b'token=' + b'A'*43 + b'\x00', b'other=x'):
            with self.subTest(query=query):
                called, sent = [], []
                async def child(scope, receive, send):
                    called.append(scope)
                async def send(message):
                    sent.append(message)
                async def receive():
                    return {'type':'http.request', 'body':b'', 'more_body':False}
                asyncio.run(CustomerSupportPath(child)(
                    {'type':'http', 'method':'GET', 'path':'/', 'query_string':query}, receive, send))
                self.assertEqual(called, [])
                self.assertEqual(sent[0]['status'], 400)
                self.assertEqual(sent[-1]['body'], b'request rejected')
                self.assertIn((b'cache-control', b'no-store'), sent[0]['headers'])

    def test_invite_never_forwarded_in_child_query_or_response(self):
        seen, sent = [], []
        async def child(scope, receive, send):
            seen.append(scope)
            await send({'type':'http.response.start', 'status':200, 'headers':[]})
            await send({'type':'http.response.body', 'body':b'fixed welcome'})
        async def send(message):
            sent.append(message)
        async def receive():
            return {'type':'http.request', 'body':b'', 'more_body':False}
        scope = {'type':'http', 'method':'GET', 'path':'/', 'raw_path':b'/',
                 'query_string':b'token=' + b'A'*43,
                 'headers':[(b'cookie', b'dsh_web_auth=not-proof; native=not-proof')]}
        asyncio.run(CustomerSupportPath(child)(scope, receive, send))
        self.assertEqual(seen[0]['path'], '/customer/welcome')
        self.assertEqual(seen[0]['query_string'], b'')
        self.assertEqual(scope['query_string'], b'token=' + b'A'*43)
        self.assertNotIn('A'*43, repr(sent))

    def test_native_token_shape_never_selects_dsh_and_composition_fails_closed(self):
        # Even a native-looking 43-character value only receives static platform
        # invite UI; this does not declare it valid or mint either session.
        response = self.client.get('/?token=' + 'N'*43,
                                   headers={'cookie':'dsh_web_auth=arbitrary; native=arbitrary'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('set-cookie', response.headers)
        self.assertNotIn('location', response.headers)
        self.assertNotIn('N'*43, response.text)
        for mode in ('legacy-query', 'tokenless', False, object()):
            with self.assertRaises(ValueError):
                CustomerSupportPath(self.app, native_handoff=mode)

    def test_legitimate_invite_get_does_not_consume_then_activate_once(self):
        admin_core = SupportCore(self.core.db_path, admin_verifier=lambda x: 'admin' if x == 'test' else None)
        admin = admin_core.admin_actor('test')
        tenant = admin_core.create_tenant(admin, 'customer.example')
        invite = admin_core.issue_invite(admin, tenant.id)
        for _ in range(2):
            page = self.client.get('/?token=' + invite)
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(invite, page.text)
            self.assertNotIn('set-cookie', page.headers)
        body = {'invite':invite, 'username':'alice', 'password':'test-password-long'}
        headers = {'origin':'https://customer.example'}
        self.assertEqual(self.client.post('/customer/activate', headers=headers, json=body).status_code, 201)
        self.assertEqual(self.client.post('/customer/activate', headers=headers, json=body).status_code, 401)

    def test_root_invite_is_get_only_and_does_not_activate(self):
        for method in ('POST', 'PUT', 'DELETE', 'HEAD'):
            response = self.client.request(method, '/?token=' + 'A'*43)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.headers['allow'], 'GET')
            self.assertNotIn('set-cookie', response.headers)
        self.assertEqual(self.client.get('/customer/me').status_code, 401)


if __name__ == '__main__':
    unittest.main()
