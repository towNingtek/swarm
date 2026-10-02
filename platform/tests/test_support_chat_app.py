"""Offline ASGI-only tests; no sockets, keys or production admin adapter."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from support_app import COOKIE, MAX_BODY
from support_chat_app import create_admin_chat_app, create_customer_chat_app
from support_core import SupportCore
from support_model import FakeModel
from support_rooms import SupportRooms


class ChatAppTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'chat.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test-assertion' else None)
        self.admin_actor = self.core.admin_actor('test-assertion')
        self.tenant = self.core.create_tenant(self.admin_actor, 'customer.example')
        self.other = self.core.create_tenant(self.admin_actor, 'other.example')
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.admin_actor, self.tenant.id)['id']
        self.other_room = self.rooms.create_room(self.admin_actor, self.other.id)['id']
        self.invite = self.core.issue_invite(self.admin_actor, self.tenant.id)
        self.customer = self.client(create_customer_chat_app(
            self.core, self.rooms, 'https://customer.example', model=FakeModel()), 'customer.example')
        # This header convention exists ONLY in tests. There is no production
        # credential adapter or ambient cookie reader in support_chat_app.
        self.admin = self.client(create_admin_chat_app(
            self.core, self.rooms, 'https://mother.example',
            request_assertion=lambda r: ('test-assertion' if
                r.headers.getlist('x-test-admin') == ['test-only'] else None)), 'mother.example')
        self.ch = {'origin': 'https://customer.example'}
        self.ah = {'origin': 'https://mother.example', 'x-test-admin': 'test-only'}
        self.cp = '/customer/rooms/' + self.room
        self.ap = '/admin/rooms/' + self.room

    def test_admin_assist_posts_an_ai_message_and_keeps_the_instruction_private(self):
        admin = self.client(create_admin_chat_app(
            self.core, self.rooms, 'https://mother.example', model=FakeModel(),
            request_assertion=lambda r: ('test-assertion' if
                r.headers.getlist('x-test-admin') == ['test-only'] else None)), 'mother.example')
        path = self.ap + '/assist'
        self.assertEqual(admin.post(path, headers={'origin': 'https://mother.example'},
                                    json={'instruction': '說明'}).status_code, 401)
        self.assertEqual(admin.post(path, headers=self.ah, json={'instruction': 1}).status_code, 400)
        self.assertEqual(admin.post(path, headers=self.ah, json={'instruction': '說明', 'x': 1}).status_code, 400)
        self.assertEqual(admin.post('/admin/rooms/nope/assist', headers=self.ah,
                                    json={'instruction': '說明'}).status_code, 401)
        ok = admin.post(path, headers=self.ah, json={'instruction': 'SECRET-INSTRUCTION'})
        self.assertEqual(ok.status_code, 200)
        self.safe(ok)
        self.assertEqual(ok.json()['message']['sender_kind'], 'ai')
        self.assertNotIn('SECRET-INSTRUCTION', ok.text)
        self.activate()
        public = self.customer.get(self.cp, headers=self.ch)
        self.assertEqual(public.status_code, 200)
        self.assertNotIn('SECRET-INSTRUCTION', public.text)
        # The customer app never mounts the operator route.
        self.assertEqual(self.customer.post(self.cp + '/assist', headers=self.ch,
                                            json={'instruction': 'x'}).status_code, 404)
        # The model allowlist stays exact-type.
        with self.assertRaises(ValueError):
            create_admin_chat_app(self.core, self.rooms, 'https://mother.example', model=object())

    def client(self, app, host):
        client = TestClient(app, base_url='https://' + host)
        self.addCleanup(client.close)
        return client

    def activate(self):
        response = self.customer.post('/customer/activate', headers=self.ch, json={
            'invite': self.invite, 'username': 'alice', 'password': 'test-password-long'})
        self.assertEqual(response.status_code, 201)
        return self.customer.cookies.get(COOKIE)

    def safe(self, response):
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'same-origin')
        self.assertNotIn(self.invite, response.text)
        self.assertNotIn('test-assertion', response.text)
        self.assertNotIn('test-password-long', response.text)
        token = self.customer.cookies.get(COOKIE)
        if token:
            self.assertNotIn(token, response.text)

    def test_bounded_replay_cursor(self):
        self.activate()
        for index in range(105):
            self.rooms.post_message(self.admin_actor, self.room, 'page-' + str(index), 'message')
        first = self.customer.get(self.cp).json()['messages']
        self.assertEqual(len(first), 100)
        second = self.customer.get(self.cp + '?after=100').json()['messages']
        self.assertEqual([m['seq'] for m in second], list(range(101, 106)))
        for query in ('after=-1', f'after={2**63}', 'after=1&after=2', 'token=secret'):
            self.assertEqual(self.customer.get(self.cp + '?' + query).status_code, 400)
        self.assertEqual(self.admin.get(self.ap + '?after=100', headers=self.ah).status_code, 200)

    def test_dual_client_lifecycle_takeover_and_epoch(self):
        self.activate()
        listed = self.customer.get('/customer/rooms')
        self.assertEqual([r['id'] for r in listed.json()], [self.room])
        response = self.customer.post(self.cp + '/messages', headers=self.ch,
                                      json={'client_message_id': 'c1', 'body': 'How to configure?'})
        self.assertEqual(response.json()['sender_kind'], 'customer')
        ai = self.customer.post(self.cp + '/respond', headers=self.ch, json={'requested': False})
        self.assertEqual(ai.status_code, 200)
        self.assertTrue(ai.json()['simulated'])
        self.assertEqual(ai.json()['message']['sender_kind'], 'ai')
        read = self.admin.get(self.ap, headers=self.ah)
        self.assertEqual(len(read.json()['messages']), 2)
        self.assertEqual(len(self.admin.get('/admin/rooms', headers=self.ah).json()), 2)
        human = self.admin.post(self.ap + '/messages', headers=self.ah,
                               json={'client_message_id': 'h1', 'body': 'I can help.'})
        self.assertEqual(human.json()['sender_kind'], 'human')
        control = self.admin.post(self.ap + '/control', headers=self.ah,
                                  json={'mode': 'human', 'expected_epoch': 0})
        self.assertEqual(control.json()['epoch'], 1)
        self.assertEqual(self.admin.post(self.ap + '/control', headers=self.ah,
                         json={'mode': 'ai', 'expected_epoch': 0}).status_code, 409)
        blocked = self.customer.post(self.cp + '/respond', headers=self.ch, json={'requested': True})
        self.assertEqual(blocked.status_code, 409)
        replay = self.customer.get(self.cp)
        self.assertEqual([m['sender_kind'] for m in replay.json()['messages']], ['customer', 'ai', 'human'])
        for response in (listed, ai, read, human, control, blocked, replay):
            self.safe(response)

    def test_cross_tenant_and_admin_cookie_rejected(self):
        self.activate()
        foreign = '/customer/rooms/' + self.other_room
        responses = [self.customer.get(foreign),
                     self.customer.post(foreign + '/messages', headers=self.ch,
                                        json={'client_message_id': 'x', 'body': 'forbidden'}),
                     self.customer.post(foreign + '/respond', headers=self.ch, json={'requested': True})]
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.safe(response)
        # A foreign admin-looking cookie (a sibling site can plant one on the
        # parent domain) grants nothing and is ignored rather than locking the
        # customer out; the customer still sees only their own room.
        planted = self.customer.get(self.cp, headers={'cookie':
            COOKIE + '=' + self.customer.cookies.get(COOKIE) + '; swarm_admin_session=test-only'})
        self.assertEqual(planted.status_code, 200)
        self.assertEqual(self.customer.get(foreign, headers={'cookie':
            COOKIE + '=' + self.customer.cookies.get(COOKIE) + '; swarm_admin_session=test-only'}).status_code, 401)
        self.assertEqual(self.customer.get('/admin/rooms', headers=self.ah).status_code, 403)
        self.assertEqual(self.customer.get('/admin/rooms').status_code, 404)
        self.assertEqual(self.customer.post(self.cp + '/control', headers=self.ch,
                         json={'mode': 'ai', 'expected_epoch': 0}).status_code, 404)

    def test_support_model_allowlist_is_exact(self):
        """Only the offline fixture and the approved adapter, by exact type."""
        from support_model_gateway import GatewayModel

        class LookalikeModel:
            def reply(self, turns, *, max_output_tokens):
                raise AssertionError('must never be called')

        for rogue in (LookalikeModel(), object(), 'cloud-fast', 42):
            with self.assertRaises(ValueError, msg=repr(rogue)):
                create_customer_chat_app(self.core, self.rooms,
                                         'https://customer.example', model=rogue)

        class Sneaky(GatewayModel):
            pass

        # A subclass is not the approved type: composition stays exact.
        with self.assertRaises(ValueError):
            create_customer_chat_app(self.core, self.rooms, 'https://customer.example',
                                     model=Sneaky('https://gw/v1', 'k', 'm'))
        # The approved adapter itself is accepted.
        create_customer_chat_app(self.core, self.rooms, 'https://customer.example',
                                 model=GatewayModel('https://gw/v1', 'k', 'm'))

    def test_admin_auth_origin_authority_and_default_deny(self):
        for method, path, body in [('GET', '/admin/rooms', None), ('GET', self.ap, None),
                                  ('POST', self.ap + '/messages', {'client_message_id': 'x', 'body': 'no'}),
                                  ('POST', self.ap + '/control', {'mode': 'human', 'expected_epoch': 0})]:
            response = self.admin.request(method, path, headers={'origin': 'https://mother.example'}, json=body)
            self.assertEqual(response.status_code, 401)
            self.safe(response)
        for headers in [dict(self.ah, origin='https://customer.example'),
                        dict(self.ah, host='customer.example'),
                        {'x-test-admin': 'test-only'},
                        dict(self.ah, **{'sec-fetch-site': 'cross-site'})]:
            response = self.admin.post(self.ap + '/control', headers=headers,
                                       json={'mode': 'human', 'expected_epoch': 0})
            self.assertEqual(response.status_code, 403)
            self.safe(response)
        default = self.client(create_admin_chat_app(self.core, self.rooms, 'https://mother.example'), 'mother.example')
        self.assertEqual(default.get('/admin/rooms', headers=self.ah).status_code, 401)
        bad_core = SupportCore(str(Path(self.core.db_path).with_name('deny.sqlite')))
        bad_rooms = SupportRooms(bad_core)
        denied = self.client(create_admin_chat_app(bad_core, bad_rooms, 'https://mother.example',
                            request_assertion=lambda r: 'test-assertion'), 'mother.example')
        self.assertEqual(denied.get('/admin/rooms', headers=self.ah).status_code, 401)

    def test_strict_bodies_queries_and_redacted_errors(self):
        self.activate()
        for content in ['{"mode":"human","expected_epoch":true}',
                        '{"mode":"human","expected_epoch":"0"}',
                        '{"mode":"human","expected_epoch":0,"secret":"hidden"}',
                        '{"mode":"ai","mode":"human","expected_epoch":0}',
                        '{"mode":"human","expected_epoch":NaN}', '[]']:
            response = self.admin.post(self.ap + '/control', headers=dict(self.ah, **{'content-type': 'application/json'}), content=content)
            self.assertEqual(response.status_code, 400)
            self.safe(response)
        response = self.customer.post(self.cp + '/messages', headers=dict(self.ch, **{'content-type': 'application/json'}), content='x' * (MAX_BODY + 1))
        self.assertEqual(response.status_code, 413)
        self.safe(response)
        for client, path, headers in [(self.customer, self.cp, self.ch), (self.admin, self.ap, self.ah)]:
            response = client.get(path + '?token=never-a-query-credential', headers=headers)
            self.assertEqual(response.status_code, 400)
            self.safe(response)
        self.assertEqual(self.customer.post(self.cp + '/respond', headers=self.ch,
                         json={'requested': 'true'}).status_code, 400)

    def test_disabled_model_does_not_create_lease_and_real_model_refused(self):
        token = self.activate()
        disabled = self.client(create_customer_chat_app(self.core, self.rooms, 'https://customer.example'), 'customer.example')
        response = disabled.post(self.cp + '/respond', headers=dict(self.ch, cookie=COOKIE + '=' + token), json={'requested': False})
        self.assertEqual(response.status_code, 503)
        self.safe(response)
        self.assertEqual(self.customer.post(self.cp + '/respond', headers=self.ch, json={'requested': False}).status_code, 200)
        with self.assertRaises(ValueError):
            create_customer_chat_app(self.core, self.rooms, 'https://customer.example', model=object())


if __name__ == '__main__':
    unittest.main()
