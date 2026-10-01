import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from support_chat_app import create_customer_chat_app
from support_core import SupportCore
from support_model import FakeModel, ModelUnavailable
from support_notify import DiscordNotifier
from support_rooms import SupportRooms


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class NotifierRulesTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.clock = [], Clock()
        self.n = DiscordNotifier('secret-bot-token', '123', 'https://p.example/admin/customers',
                                 clock=self.clock, send=self.sent.append)

    def ctx(self, mode='ai', count=2, room='r1'):
        return {'room_id': room, 'site_host': 'ab.example', 'mode': mode, 'customer_messages': count}

    def test_ai_mode_only_first_message_notifies(self):
        self.assertFalse(self.n.customer_message(self.ctx('ai', 2), 'hi'))
        self.assertTrue(self.n.customer_message(self.ctx('ai', 1, 'r2'), '第一句'))
        self.assertIn('新對話', self.sent[-1])
        self.assertIn('ab.example', self.sent[-1])
        self.assertIn('https://p.example/admin/customers', self.sent[-1])

    def test_taken_over_rooms_notify_and_are_throttled_per_room(self):
        self.assertTrue(self.n.customer_message(self.ctx('coassist'), '幫我問 openai key'))
        self.assertIn('需要你回覆', self.sent[-1])
        self.assertFalse(self.n.customer_message(self.ctx('human'), '還在嗎'))
        self.assertTrue(self.n.customer_message(self.ctx('human', room='r2'), '另一位'))
        self.clock.now += 121
        self.assertTrue(self.n.customer_message(self.ctx('human'), '還在嗎'))
        self.assertEqual(len(self.sent), 3)

    def test_preview_is_short_single_line_and_cannot_break_formatting(self):
        self.n.customer_message(self.ctx('human'), 'a`b\n' + 'x' * 300)
        quote = self.sent[-1].split('\n')[1]
        self.assertTrue(quote.startswith('> a\'b x'))
        self.assertLessEqual(len(quote), 2 + 100 + 1)
        self.assertTrue(quote.endswith('…'))

    def test_ai_failure_notifies(self):
        self.assertTrue(self.n.ai_failed(self.ctx()))
        self.assertIn('AI 助手沒能回覆', self.sent[-1])

    def test_token_never_shown_and_config_is_validated(self):
        self.assertNotIn('secret-bot-token', repr(self.n))
        for token, channel in (('', '1'), ('t', ''), ('t', 'abc')):
            with self.assertRaises(ValueError):
                DiscordNotifier(token, channel, 'u')

    def test_mentions_are_disabled_in_the_request(self):
        import json
        import urllib.request
        seen = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b''

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda req, timeout: seen.append(req) or Response()
        try:
            DiscordNotifier('tok', '42', 'u')._post('@everyone hi')
        finally:
            urllib.request.urlopen = original
        body = json.loads(seen[0].data)
        self.assertEqual(body['allowed_mentions'], {'parse': []})
        self.assertTrue(seen[0].full_url.endswith('/channels/42/messages'))


class NotifyWiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'n.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'customer.example')
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.admin, self.tenant.id)['id']
        self.sent = []
        self.notifier = DiscordNotifier('tok', '1', 'https://p/admin', clock=Clock(),
                                        send=self.sent.append)

    def client(self, model):
        app = create_customer_chat_app(self.core, self.rooms, 'https://customer.example',
                                       model=model, notifier=self.notifier)
        client = TestClient(app, base_url='https://customer.example')
        self.addCleanup(client.close)
        headers = {'origin': 'https://customer.example'}
        invite = self.core.issue_invite(self.admin, self.tenant.id)
        self.assertEqual(client.post('/customer/activate', headers=headers, json={
            'invite': invite, 'username': 'alice', 'password': 'test-password-long'}).status_code, 201)
        return client, headers

    def test_first_customer_message_and_failed_ai_notify(self):
        # The app accepts only exact model types: break an instance instead.
        broken_model = FakeModel()
        def down(turns, **kw):
            raise ModelUnavailable('down')
        broken_model.reply = down
        client, headers = self.client(FakeModel())
        path = f'/customer/rooms/{self.room}'
        self.assertEqual(client.post(path + '/messages', headers=headers,
                                     json={'client_message_id': 'c1', 'body': '你好'}).status_code, 200)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('你好', self.sent[0])
        # A second message in an AI room: no ping.
        client.post(path + '/messages', headers=headers, json={'client_message_id': 'c2', 'body': '再問'})
        self.assertEqual(len(self.sent), 1)
        # Model failure pings (new room avoids the throttle).
        self.notifier._last.clear()
        app = create_customer_chat_app(self.core, self.rooms, 'https://customer.example',
                                       model=broken_model, notifier=self.notifier)
        broken = TestClient(app, base_url='https://customer.example', cookies=client.cookies)
        self.addCleanup(broken.close)
        self.assertEqual(broken.post(path + '/respond', headers=headers,
                                     json={'requested': True}).status_code, 503)
        self.assertIn('AI 助手沒能回覆', self.sent[-1])

    def test_notifier_failure_never_breaks_the_customer_request(self):
        def explode(content):
            raise RuntimeError('discord down')
        self.notifier._send = explode
        client, headers = self.client(FakeModel())
        response = client.post(f'/customer/rooms/{self.room}/messages', headers=headers,
                               json={'client_message_id': 'c1', 'body': '你好'})
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
