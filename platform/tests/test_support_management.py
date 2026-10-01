"""Offline ASGI tests only; fixture hosts never access DNS or Docker."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from support_app import MAX_BODY
from support_chat_app import create_admin_chat_app
from support_core import SupportCore, InvalidInvite
from support_management import install_management
from support_quota import SupportQuota, QuotaDenied
from support_rooms import SupportRooms


class ManagementTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'test.sqlite'),
                                admin_verifier=lambda a: 'operator' if a == 'test-only' else None)
        self.actor = self.core.admin_actor('test-only')
        self.rooms = SupportRooms(self.core)
        self.quota = SupportQuota(self.core)
        self.acme = self.core.create_tenant(self.actor, 'acme.example', quota_mode='unlimited')
        self.other = self.core.create_tenant(self.actor, 'other.example')
        self.rooms.create_room(self.actor, self.acme.id)
        self.rooms.create_room(self.actor, self.other.id)
        self.customer = self.core.redeem_invite(
            self.core.issue_invite(self.actor, self.acme.id), 'acme.example',
            'alice', 'test-password-long').actor
        self.app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example',
                                        request_assertion=lambda r: r.headers.get('x-test-admin'))
        install_management(self.app, self.core, self.rooms, self.quota)
        self.client = TestClient(self.app, base_url='https://mother.example')
        self.addCleanup(self.client.close)
        self.headers = {'origin': 'https://mother.example', 'x-test-admin': 'test-only'}
        self.path = '/admin/tenants/' + self.acme.id

    def post(self, suffix, body, path=None):
        return self.client.post((path or self.path) + suffix, json=body, headers=self.headers)

    def test_admin_required_for_every_request_and_customer_cannot_cross_tenants(self):
        for tenant in (self.acme, self.other):
            path = '/admin/tenants/' + tenant.id
            for suffix, body in (('/invites', {'ttl_seconds': 60}),
                                 ('/quota', {'policy': {'mode': 'unlimited'}})):
                self.assertEqual(self.client.post(path + suffix, json=body,
                    headers={'origin': 'https://mother.example'}).status_code, 401)
        self.assertEqual(self.client.get('/admin/tenants').status_code, 401)

        async def customer_adapter(request):
            return self.customer
        self.app.state.support_actor_for = customer_adapter
        for tenant in (self.acme, self.other):
            path = '/admin/tenants/' + tenant.id
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 401)
            self.assertEqual(self.post('/invites', {'ttl_seconds': 60}, path).status_code, 401)
            self.assertEqual(self.post('/quota', {'policy': {'mode': 'unlimited'}}, path).status_code, 401)
        self.assertEqual(self.client.get('/admin/tenants', headers=self.headers).status_code, 401)

    def test_summary_is_allowlisted_and_metadata_unlimited_does_not_grant_quota(self):
        response = self.client.get('/admin/tenants', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        rows = {r['id']: r for r in response.json()}
        self.assertEqual(set(rows), {self.acme.id, self.other.id})
        for row in rows.values():
            # platform_host is where customers activate; the admin UI needs it to
            # build an invite link that works before the site host exists.
            # site_job lets the operator see build progress; it is a status
            # string only, never ports, container ids or DNS evidence.
            self.assertEqual(set(row), {'id', 'host', 'platform_host', 'enabled', 'quota_mode',
                                        'policy', 'metadata_only', 'provisioning', 'site_job',
                                        'template'})
            self.assertEqual(row['template'], 'swarm')
            self.assertIsNone(row['site_job'])
            self.assertEqual(row['policy']['mode'], 'disabled')
            self.assertTrue(row['metadata_only'])
        self.assertEqual(rows[self.acme.id]['quota_mode'], 'unlimited')
        with self.assertRaises(QuotaDenied):
            self.quota.reserve(self.customer, 'default-disabled', 1)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('alice', response.text)
        self.assertNotIn('digest', response.text)

    def test_template_choice_is_admin_only_validated_and_per_tenant(self):
        listing = self.client.get('/admin/site-templates', headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        ids = [t['id'] for t in listing.json()['templates']]
        self.assertEqual(listing.json()['default'], 'swarm')
        self.assertIn('none', ids)
        self.assertIn('swarm', ids)
        self.assertEqual(self.client.get('/admin/site-templates').status_code, 401)
        self.assertEqual(self.client.post(self.path + '/template', json={'template': 'none'},
                         headers={'origin': 'https://mother.example'}).status_code, 401)
        for bad in ({'template': 'nope'}, {'template': '../x'}, {'template': 1}, {}):
            self.assertEqual(self.post('/template', bad).status_code, 400)
        self.assertEqual(self.post('/template', {'template': 'none'}).status_code, 200)
        self.assertEqual(self.client.get(self.path, headers=self.headers).json()['template'], 'none')
        other = self.client.get('/admin/tenants/' + self.other.id, headers=self.headers).json()
        self.assertEqual(other['template'], 'swarm')
        self.assertEqual(self.post('/template', {'template': 'none'},
                                   '/admin/tenants/unknown').status_code, 400)

    def test_explicit_acme_unlimited_and_other_capped_are_independent(self):
        policy = {'policy': {'mode': 'unlimited'}}
        for _ in range(2):
            self.assertEqual(self.post('/quota', policy).status_code, 200)
        self.quota.reserve(self.customer, 'allowed', 2)
        other_path = '/admin/tenants/' + self.other.id
        self.assertEqual(self.post('/quota', {'policy': {'mode': 'capped', 'monthly_limit': 5}},
                                   other_path).status_code, 200)
        other = self.client.get(other_path, headers=self.headers).json()
        self.assertEqual(other['policy'], {'mode': 'capped', 'monthly_limit': 5})
        self.assertEqual(other['quota_mode'], 'metered')
        self.assertEqual(self.client.get(self.path, headers=self.headers).json()['policy']['mode'], 'unlimited')
        self.assertEqual(self.post('/quota', {'policy': {'mode': 'disabled'}}).status_code, 200)
        with self.assertRaises(QuotaDenied):
            self.quota.reserve(self.customer, 'now-disabled', 1)

    def test_invite_is_one_time_no_store_and_retry_mints_distinct_capability(self):
        first = self.post('/invites', {'ttl_seconds': 60})
        second = self.post('/invites', {'ttl_seconds': 60})
        self.assertEqual(first.status_code, 201)
        token = first.json()['token']
        self.assertFalse(first.json()['retry_safe'])
        self.assertNotEqual(token, second.json()['token'])
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertEqual(first.headers['referrer-policy'], 'same-origin')
        with self.assertRaises(InvalidInvite):
            self.core.preview_invite(token, 'other.example')
        self.core.redeem_invite(token, 'acme.example', 'bob', 'test-password-long')
        with self.assertRaises(InvalidInvite):
            self.core.redeem_invite(token, 'acme.example', 'eve', 'test-password-long')
        self.assertNotIn(token, self.client.get(self.path, headers=self.headers).text)
        with self.core._connect() as conn:
            self.assertFalse(conn.execute('SELECT 1 FROM invites WHERE digest=?', (token,)).fetchone())

    def test_strict_invalid_inputs_no_mutation(self):
        for ttl in (0, -1, 59, 86401, True, 60.0, '60', None):
            self.assertEqual(self.post('/invites', {'ttl_seconds': ttl}).status_code, 400)
        for policy in ({}, {'mode': 'metered'}, {'mode': 'capped'},
                       {'mode': 'capped', 'monthly_limit': True},
                       {'mode': 'capped', 'monthly_limit': -1},
                       {'mode': 'capped', 'monthly_limit': self.quota.MAX_MONTHLY_LIMIT + 1},
                       {'mode': 'unlimited', 'monthly_limit': 1}, {'mode': ['disabled']}):
            self.assertEqual(self.post('/quota', {'policy': policy}).status_code, 400)
        for raw in ('{"ttl_seconds":60,"ttl_seconds":61}', '{"ttl_seconds":NaN}',
                    '{"ttl_seconds":60,"extra":1}'):
            self.assertEqual(self.client.post(self.path + '/invites', content=raw,
                headers={**self.headers, 'content-type': 'application/json'}).status_code, 400)
        self.assertEqual(self.client.post(self.path + '/invites', content='x' * (MAX_BODY + 1),
            headers={**self.headers, 'content-type': 'application/json'}).status_code, 413)
        self.assertEqual(self.client.get(self.path + '?token=secret', headers=self.headers).status_code, 400)
        self.assertEqual(self.post('/invites', {'ttl_seconds': 60}, '/admin/tenants/missing').status_code, 400)
        self.assertEqual(self.post('/quota', {'policy': {'mode': 'disabled'}}, '/admin/tenants/missing').status_code, 400)
        self.assertEqual(self.client.get('/admin/tenants/missing', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get(self.path, headers=self.headers).json()['policy']['mode'], 'disabled')

    def test_edge_redaction_disabled_tenant_and_reject_legacy_create_body(self):
        self.assertEqual(self.client.post('/admin/tenants', json={'host': 'new.example',
                         'quota_mode': 'unlimited'}, headers=self.headers).status_code, 400)
        response = self.client.post(self.path + '/invites', json={'ttl_seconds': 60},
                                   headers={**self.headers, 'origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        with patch.object(self.core, 'issue_invite', side_effect=RuntimeError('secret-internal')):
            response = self.post('/invites', {'ttl_seconds': 60})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret-internal', response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.core.set_tenant_enabled(self.actor, self.acme.id, False)
        self.assertEqual(self.post('/invites', {'ttl_seconds': 60}).status_code, 400)

    def test_atomic_bootstrap_http_replay_auth_and_strict_json(self):
        path = '/admin/tenants'
        body = {'client_request_id': 'http-1', 'host': 'new.example'}
        def create(value=body, headers=None):
            return self.client.post(path, json=value, headers=headers or self.headers)
        response = create()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), create().json())
        self.assertTrue(response.json()['metadata_only'])
        self.assertEqual(response.json()['provisioning'], 'not_provisioned')
        self.assertEqual(response.json()['policy']['mode'], 'disabled')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(create({**body, 'host': 'other-new.example'}).status_code, 409)
        self.assertEqual(create({**body, 'client_request_id': 'http-2'}).status_code, 409)
        self.assertEqual(create(headers={'origin': 'https://mother.example'}).status_code, 401)
        self.assertEqual(create(headers={**self.headers, 'origin': 'https://evil.example'}).status_code, 403)
        for raw in ('[]', '{', '{"client_request_id":"x","host":"a.example","host":"b.example"}',
                    '{"client_request_id":"x","host":"a.example","policy":{"mode":"disabled","mode":"unlimited"}}',
                    '{"client_request_id":"x","host":"a.example","policy":{"mode":"capped","monthly_limit":NaN}}'):
            self.assertEqual(self.client.post(path, content=raw, headers={**self.headers,
                             'content-type': 'application/json'}).status_code, 400)
        for change in ({'extra': True}, {'room_mode': None}, {'policy': {'mode': 'capped', 'monthly_limit': True}},
                       {'host': 'https://new.example'}, {'policy': {'mode': 'unlimited', 'monthly_limit': None}}):
            self.assertEqual(create({**body, **change}).status_code, 400)
        self.assertEqual(self.client.post(path, content='x' * (MAX_BODY + 1), headers={**self.headers,
                         'content-type': 'application/json'}).status_code, 413)
        self.assertEqual(self.client.post(path + '?x=1', json=body, headers=self.headers).status_code, 400)
        async def customer_adapter(request):
            return self.customer
        self.app.state.support_actor_for = customer_adapter
        self.assertEqual(create().status_code, 401)
        self.assertEqual(create({**body, 'host': 'third.example', 'client_request_id': 'third'}).status_code, 401)

    def test_bootstrap_http_failure_rolls_back_redacts_and_retries(self):
        from support_bootstrap import SupportBootstrap
        body = {'client_request_id': 'fault', 'host': 'fault.example',
                'room_mode': 'human', 'policy': {'mode': 'capped', 'monthly_limit': 5}}
        with patch.object(SupportBootstrap, '_checkpoint', side_effect=RuntimeError('secret-internal')):
            response = self.client.post('/admin/tenants', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret-internal', response.text)
        with self.core._connect() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM tenants WHERE site_host='fault.example'").fetchone())
        response = self.client.post('/admin/tenants', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['policy'], {'mode': 'capped', 'monthly_limit': 5})

    def test_install_requires_existing_adapter_edge_and_shared_dependencies(self):
        with self.assertRaises(ValueError):
            install_management(FastAPI(), self.core, self.rooms, self.quota)
        bare = FastAPI()
        bare.state.support_actor_for = self.app.state.support_actor_for
        with self.assertRaises(ValueError):
            install_management(bare, self.core, self.rooms, self.quota)
        with self.assertRaises(ValueError):
            install_management(self.app, self.core, self.rooms, self.quota)


if __name__ == '__main__':
    unittest.main()
