"""Pure local tests: fake upstream via httpx.MockTransport; no network, no real model."""
import json
import tempfile
import unittest
from datetime import datetime, timezone

import httpx
from starlette.testclient import TestClient

from site_model_relay import SseUsage, create_app, sanitize
from support_core import SupportCore
from support_quota import SupportQuota
from support_site_model import SiteModelAccess, SiteModelDenied, estimate_tokens

PLATFORM_KEY = 'platform-key-never-leaves'


class Upstream:
    def __init__(self):
        self.requests = []
        self.status = 200
        self.usage = {'prompt_tokens': 30, 'completion_tokens': 12, 'total_tokens': 42}
        self.break_stream = False

    def __call__(self, request):
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={'error': {'message': 'echo ' + PLATFORM_KEY}})
        body = json.loads(request.content)
        if not body.get('stream'):
            return httpx.Response(200, json={'choices': [{'message': {'content': '好'}}],
                                             'usage': self.usage})
        chunks = [b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n']
        if not self.break_stream:
            chunks.append(('data: ' + json.dumps({'choices': [], 'usage': self.usage}) + '\n\n').encode())
            chunks.append(b'data: [DONE]\n\n')
        return httpx.Response(200, content=b''.join(chunks),
                              headers={'content-type': 'text/event-stream'})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
        self.core = SupportCore(self.tmp.name + '/t.db', clock=lambda: self.now,
                                admin_verifier=lambda a: 'admin' if a == 'ok' else None)
        self.admin = self.core.admin_actor('ok')
        self.quota = SupportQuota(self.core)
        self.tenant = self.core.create_tenant(self.admin, 'ab.test')
        self.other = self.core.create_tenant(self.admin, 'cd.test')
        self.access = SiteModelAccess(self.core, max_outstanding=2)
        self.token = self.access.issue(self.tenant.id)

    def policy(self, mode, limit=None):
        self.quota.set_policy(self.admin, self.tenant.id, mode, limit)


class AccessTests(Base):
    def test_token_is_stored_as_digest_only(self):
        with self.core._connect() as conn:
            dump = '\n'.join(conn.iterdump())
        self.assertNotIn(self.token, dump)
        self.assertTrue(self.token.startswith('sms_'))

    def test_disabled_by_default_and_follows_admin_policy(self):
        with self.assertRaises(SiteModelDenied) as denied:
            self.access.admit(self.token, 'cloud-fast', 10)
        self.assertEqual(denied.exception.code, 'starter_model_disabled')
        self.policy('unlimited')
        tenant, _ = self.access.admit(self.token, 'cloud-fast', 10)
        self.assertEqual(tenant, self.tenant.id)

    def test_monthly_cap_counts_holds_and_settled_usage(self):
        self.policy('capped', 100)
        tenant, rid = self.access.admit(self.token, 'cloud-fast', 60)
        with self.assertRaises(SiteModelDenied) as denied:
            self.access.admit(self.token, 'cloud-fast', 60)  # 60 held + 60 > 100
        self.assertEqual(denied.exception.code, 'monthly_quota_exhausted')
        self.access.settle(tenant, rid, 20)
        self.access.admit(self.token, 'cloud-fast', 60)  # 20 + 60 <= 100
        self.assertEqual(self.access.month_usage(tenant), 80)

    def test_unknown_usage_bills_the_estimate_and_excess_is_billed(self):
        self.policy('unlimited')
        tenant, a = self.access.admit(self.token, 'cloud-fast', 50)
        self.access.settle(tenant, a, None)
        tenant, b = self.access.admit(self.token, 'cloud-fast', 10)
        self.access.settle(tenant, b, 25)
        self.assertEqual(self.access.month_usage(tenant), 75)
        self.access.settle(tenant, b, 0)  # already settled: no rewrite
        self.assertEqual(self.access.month_usage(tenant), 75)

    def test_shares_pool_with_copilot_ledger(self):
        self.policy('capped', 100)
        invite = self.core.issue_invite(self.admin, self.tenant.id)
        actor = self.core.redeem_invite(invite, 'ab.test', 'alice', 'test-password-long').actor
        reservation = self.quota.reserve(actor, 'copilot-1', 90)
        self.quota.settle(actor, reservation, 90)
        with self.assertRaises(SiteModelDenied):
            self.access.admit(self.token, 'cloud-fast', 20)

    def test_new_month_resets(self):
        self.policy('capped', 100)
        tenant, rid = self.access.admit(self.token, 'cloud-fast', 100)
        self.access.settle(tenant, rid, 100)
        self.now = datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp()
        self.access.admit(self.token, 'cloud-fast', 100)

    def test_outstanding_limit_and_stale_holds(self):
        self.policy('unlimited')
        self.access.admit(self.token, 'cloud-fast', 1)
        self.access.admit(self.token, 'cloud-fast', 1)
        with self.assertRaises(SiteModelDenied) as denied:
            self.access.admit(self.token, 'cloud-fast', 1)
        self.assertEqual(denied.exception.status, 429)
        self.assertEqual(self.access.release_stale_holds(), 2)
        self.access.admit(self.token, 'cloud-fast', 1)

    def test_rotation_revocation_disable_and_isolation(self):
        self.policy('unlimited')
        rotated = self.access.issue(self.tenant.id)
        for bad in (self.token, 'sms_' + 'x' * 43, 'nope', None):
            with self.assertRaises(SiteModelDenied) as denied:
                self.access.admit(bad, 'cloud-fast', 1)
            self.assertEqual(denied.exception.status, 401)
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        with self.assertRaises(SiteModelDenied) as denied:
            self.access.admit(rotated, 'cloud-fast', 1)
        self.assertEqual(denied.exception.status, 403)
        self.core.set_tenant_enabled(self.admin, self.tenant.id, True)
        other = self.access.issue(self.other.id)
        with self.assertRaises(SiteModelDenied):  # other tenant keeps its own (disabled) policy
            self.access.admit(other, 'cloud-fast', 1)
        self.access.revoke(self.tenant.id)
        with self.assertRaises(SiteModelDenied):
            self.access.admit(rotated, 'cloud-fast', 1)

    def test_estimate_covers_prompt_and_output(self):
        self.assertEqual(estimate_tokens(b'x' * 300, 1000), 1100)


class SanitizeTests(unittest.TestCase):
    def test_drops_routing_fields_and_forces_usage(self):
        clean, cap = sanitize({'model': 'cloud-fast', 'messages': [{'role': 'user', 'content': 'hi'}],
                               'stream': True, 'api_base': 'http://evil', 'api_key': 'x',
                               'metadata': {}, 'n': 5, 'max_tokens': 999999,
                               'stream_options': {'include_usage': False}},
                              ('cloud-fast',))
        self.assertNotIn('api_base', clean)
        self.assertNotIn('api_key', clean)
        self.assertNotIn('metadata', clean)
        self.assertEqual(clean['n'], 1)
        self.assertEqual(cap, 16384)
        self.assertEqual(clean['max_completion_tokens'], 16384)
        self.assertEqual(clean['stream_options'], {'include_usage': True})

    def test_rejects_other_models(self):
        with self.assertRaises(ValueError):
            sanitize({'model': 'cloud-smart', 'messages': [{}]}, ('cloud-fast',))

    def test_sse_usage_split_across_chunks(self):
        tracker = SseUsage()
        text = b'data: {"choices":[]}\n\ndata: {"usage":{"total_tokens":7}}\n\ndata: [DONE]\n\n'
        for i in range(0, len(text), 5):
            tracker.feed(text[i:i + 5])
        self.assertEqual(tracker.total, 7)


class RelayTests(Base):
    def setUp(self):
        super().setUp()
        self.upstream = Upstream()
        app = create_app(self.access, base_url='http://gateway.test/v1', api_key=PLATFORM_KEY,
                         models=['cloud-fast'], transport=httpx.MockTransport(self.upstream),
                         peer_check=lambda host: True)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def post(self, body, token=None):
        return self.client.post('/v1/chat/completions', json=body,
                                headers={'Authorization': f'Bearer {token or self.token}'})

    def body(self, **extra):
        return {'model': 'cloud-fast', 'messages': [{'role': 'user', 'content': '你好'}], **extra}

    def test_stream_passthrough_uses_platform_key_and_settles_real_usage(self):
        self.policy('capped', 100000)
        response = self.post(self.body(stream=True, api_base='http://evil'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('好', response.text)
        self.assertIn('[DONE]', response.text)
        sent = self.upstream.requests[0]
        self.assertEqual(str(sent.url), 'http://gateway.test/v1/chat/completions')
        self.assertEqual(sent.headers['authorization'], f'Bearer {PLATFORM_KEY}')
        self.assertNotIn(self.token, sent.headers['authorization'])
        self.assertNotIn('api_base', json.loads(sent.content))
        self.assertEqual(self.access.month_usage(self.tenant.id), 42)

    def test_broken_stream_bills_estimate(self):
        self.policy('unlimited')
        self.upstream.break_stream = True
        body = self.body(stream=True, max_completion_tokens=100)
        response = self.post(body)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(self.access.month_usage(self.tenant.id), 100)

    def test_non_stream_settles(self):
        self.policy('unlimited')
        response = self.post(self.body())
        self.assertEqual(response.json()['usage']['total_tokens'], 42)
        self.assertEqual(self.access.month_usage(self.tenant.id), 42)

    def test_denials_never_reach_upstream(self):
        self.assertEqual(self.post(self.body()).status_code, 403)  # policy disabled
        self.policy('unlimited')
        self.assertEqual(self.post(self.body(), token='sms_' + 'y' * 43).status_code, 401)
        self.assertEqual(self.post(self.body(model='cloud-smart')).status_code, 400)
        self.assertEqual(self.client.post('/key/generate', json={}).status_code, 404)
        self.assertEqual(self.upstream.requests, [])

    def test_upstream_error_body_is_not_relayed_and_not_billed(self):
        self.policy('unlimited')
        self.upstream.status = 500
        response = self.post(self.body())
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(PLATFORM_KEY, response.text)
        self.assertEqual(self.access.month_usage(self.tenant.id), 0)

    def test_models_requires_token(self):
        self.assertEqual(self.client.get('/v1/models').status_code, 401)
        listed = self.client.get('/v1/models', headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual([m['id'] for m in listed.json()['data']], ['cloud-fast'])

    def test_untrusted_peer_refused(self):
        app = create_app(self.access, base_url='http://gateway.test/v1', api_key=PLATFORM_KEY,
                         models=['cloud-fast'], transport=httpx.MockTransport(self.upstream))
        with TestClient(app, client=('203.0.113.9', 1234)) as client:
            self.assertEqual(client.get('/v1/models').status_code, 403)


if __name__ == '__main__':
    unittest.main()
