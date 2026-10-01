"""The platform's own model credential must stay in this process and bound."""
import io
import json
import unittest
import urllib.error

from support_model import ModelUnavailable, Turn, checked_reply
from support_model_gateway import MAX_RESPONSE_BYTES, GatewayModel

KEY = 'sk-fake-platform-secret-value'
TURNS = (Turn('system', 'you are support'), Turn('user', 'which model?'))


class FakeOpener:
    def __init__(self, status=200, payload=None, body=None, error=None):
        self.error = error
        self.requests = []
        if body is None:
            body = json.dumps(payload if payload is not None else {
                'choices': [{'message': {'content': 'use placeholder-a'}}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7},
            }).encode()
        self.body, self.status = body, status

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        stream = io.BytesIO(self.body)
        stream.__enter__ = lambda: stream
        stream.__exit__ = lambda *a: False
        return stream


class GatewayTests(unittest.TestCase):
    def model(self, **kwargs):
        opener = kwargs.pop('opener', None) or FakeOpener()
        return GatewayModel('https://gw.example/v1', KEY, 'cloud-fast',
                            opener=opener, **kwargs), opener

    def test_configuration_is_validated(self):
        for base, key, model in (
            ('ftp://gw', KEY, 'm'), ('', KEY, 'm'), ('https://gw', '', 'm'),
            ('https://gw', '   ', 'm'), ('https://gw', KEY, ''), (None, KEY, 'm'),
        ):
            with self.assertRaises(ValueError, msg=(base, model)):
                GatewayModel(base, key, model)
        for timeout in (0, 121, True, 'fast'):
            with self.assertRaises(ValueError, msg=timeout):
                GatewayModel('https://gw', KEY, 'm', timeout=timeout)

    def test_reply_carries_text_model_and_real_usage(self):
        model, _ = self.model()
        reply = checked_reply(model, TURNS)
        self.assertEqual(reply.text, 'use placeholder-a')
        self.assertEqual(reply.model, 'cloud-fast')
        self.assertEqual((reply.input_tokens, reply.output_tokens), (11, 7))
        # A real provider reply must never be labelled simulated.
        self.assertFalse(reply.simulated)

    def test_a_reply_cut_off_by_the_limit_says_so(self):
        payload = {'choices': [{'message': {'content': '第一步是選擇模'}, 'finish_reason': 'length'}]}
        model, _ = self.model(opener=FakeOpener(payload=payload))
        text = checked_reply(model, TURNS).text
        self.assertTrue(text.startswith('第一步是選擇模'))
        self.assertIn('截斷', text)
        # A normally finished reply is left exactly as sent.
        done = {'choices': [{'message': {'content': '完成。'}, 'finish_reason': 'stop'}]}
        model, _ = self.model(opener=FakeOpener(payload=done))
        self.assertEqual(checked_reply(model, TURNS).text, '完成。')

    def test_request_is_bounded_and_sends_the_key_only_as_a_header(self):
        model, opener = self.model()
        checked_reply(model, TURNS, max_output_tokens=256)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, 'https://gw.example/v1/chat/completions')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(timeout, 30.0)
        self.assertEqual(request.get_header('Authorization'), f'Bearer {KEY}')
        body = json.loads(request.data)
        self.assertEqual(body['max_tokens'], 256)
        self.assertFalse(body['stream'])
        # The credential must not appear in the body under any key.
        self.assertNotIn(KEY, request.data.decode())

    def test_unknown_usage_stays_unknown_never_zero(self):
        for usage in ({}, {'prompt_tokens': -1}, {'prompt_tokens': True},
                      {'prompt_tokens': '11'}, None):
            payload = {'choices': [{'message': {'content': 'ok'}}]}
            if usage is not None:
                payload['usage'] = usage
            model, _ = self.model(opener=FakeOpener(payload=payload))
            reply = checked_reply(model, TURNS)
            self.assertIsNone(reply.input_tokens, usage)
            self.assertIsNone(reply.output_tokens, usage)

    def test_upstream_failures_never_leak_the_body_or_the_key(self):
        cases = [
            FakeOpener(error=urllib.error.HTTPError(
                'u', 401, 'Unauthorized', {},
                io.BytesIO(f'invalid key {KEY}'.encode()))),
            FakeOpener(error=TimeoutError('timed out')),
            FakeOpener(error=OSError('connection refused')),
            FakeOpener(body=b'not json'),
            FakeOpener(payload={'choices': []}),
            FakeOpener(payload={'choices': [{'message': {'content': ''}}]}),
            FakeOpener(payload={'choices': [{'message': {'content': '   '}}]}),
            FakeOpener(body=b'x' * (MAX_RESPONSE_BYTES + 2)),
        ]
        for opener in cases:
            model, _ = self.model(opener=opener)
            with self.assertRaises(ModelUnavailable) as caught:
                checked_reply(model, TURNS)
            message = str(caught.exception)
            self.assertNotIn(KEY, message)
            self.assertNotIn('invalid key', message)

    def test_a_failure_is_not_retried(self):
        """A metered account must not be charged twice for one question."""
        opener = FakeOpener(error=TimeoutError('slow'))
        model, _ = self.model(opener=opener)
        with self.assertRaises(ModelUnavailable):
            checked_reply(model, TURNS)
        self.assertEqual(len(opener.requests), 1)

    def test_oversized_or_malformed_context_is_refused_before_any_call(self):
        model, opener = self.model()
        with self.assertRaises(ValueError):
            checked_reply(model, (Turn('user', 'x' * 32001),))
        with self.assertRaises(ValueError):
            checked_reply(model, (Turn('root', 'hi'),))
        self.assertEqual(opener.requests, [])


if __name__ == '__main__':
    unittest.main()
