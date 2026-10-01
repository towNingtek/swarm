"""Customer model selection: revision-bound, secret-free, simulated probe only.

Nothing here contacts a provider. simulated_pass is evidence that the SIMULATED
verifier ran, never that a customer model works; verified_for_work stays False.
"""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_rooms import SupportRooms
from support_chat_app import create_customer_chat_app
from support_model_settings import CustomerModelSettings, ProbeTicket

CATALOG = ('fake-model-a', 'fake-model-b')


class ModelSettingsCoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / 'model.sqlite')
        self.now = [1000]
        self.core = SupportCore(self.db, clock=lambda: self.now[0],
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'one.example')
        self.cap = object()
        self.settings = CustomerModelSettings(self.core, catalog=CATALOG, verifier_capability=self.cap)
        self.actor = self.login('alice')

    def login(self, username, tenant=None):
        tenant = tenant or self.tenant
        invite = self.core.issue_invite(self.admin, tenant.id)
        return self.core.redeem_invite(invite, tenant.site_host, username, 'test-password-long').actor

    def relogin(self, username='alice', host='one.example'):
        return self.core.login(host, username, 'test-password-long').actor

    def test_catalog_validation_and_capability_rejection(self):
        with self.assertRaises(InvalidInput):
            CustomerModelSettings(self.core, catalog=('a', 'a'))
        with self.assertRaises(InvalidInput):
            CustomerModelSettings(self.core, catalog=('bad model',))
        with self.assertRaises(InvalidInput):
            CustomerModelSettings(self.core, verifier_capability='not-opaque')
        readonly = CustomerModelSettings(self.core, catalog=CATALOG)
        self.settings.configure(self.actor, 'fake-model-a', 0)
        with self.assertRaises(Unauthorized):
            readonly.begin_simulated_probe(object(), self.actor, 1)
        with self.assertRaises(Unauthorized):
            self.settings.begin_simulated_probe(object(), self.actor, 1)
        # Empty catalog: nothing is selectable, status is honest.
        empty = CustomerModelSettings(self.core)
        self.assertEqual(empty.status(self.actor)['available_selections'], [])
        with self.assertRaises(InvalidInput):
            empty.configure(self.actor, 'fake-model-a', 1)

    def test_revision_conflicts_and_revoke(self):
        initial = self.settings.status(self.actor)
        self.assertEqual(initial, {'revision': 0, 'selection_id': None, 'state': 'unconfigured',
                                   'simulated': False, 'verified_for_work': False,
                                   'credentials_configured': False,
                                   'available_selections': list(CATALOG),
                                   'simulated_probe_available': True, 'adapter_connected': False})
        # Without an injected verifier the flag is false; it is never request-controlled.
        self.assertFalse(CustomerModelSettings(self.core, catalog=CATALOG)
                         .status(self.actor)['simulated_probe_available'])
        with self.assertRaises(InvalidInput):
            self.settings.configure(self.actor, 'not-in-catalog', 0)
        with self.assertRaises(InvalidInput):
            self.settings.configure(self.actor, 'fake-model-a', -1)
        view = self.settings.configure(self.actor, 'fake-model-a', 0)
        self.assertEqual((view['revision'], view['state']), (1, 'pending'))
        with self.assertRaises(Conflict):
            self.settings.configure(self.actor, 'fake-model-b', 0)  # stale revision
        with self.assertRaises(Conflict):
            self.settings.revoke(self.actor, 0)
        view = self.settings.revoke(self.actor, 1)
        self.assertEqual((view['revision'], view['state'], view['selection_id']), (2, 'unconfigured', None))

    def test_probe_is_invalidated_by_change_session_rotation_and_lifecycle(self):
        self.settings.configure(self.actor, 'fake-model-a', 0)
        ticket = self.settings.begin_simulated_probe(self.cap, self.actor, 1)
        self.assertIsInstance(ticket, ProbeTicket)
        self.assertNotIn('token', repr(ticket))
        self.assertEqual(self.settings.status(self.actor)['state'], 'probing')
        # A customer changing selection mid-probe discards the in-flight ticket.
        self.settings.configure(self.actor, 'fake-model-b', 1)
        with self.assertRaises(Conflict):
            self.settings.finish_simulated_probe(self.cap, ticket, passed=True)
        self.assertEqual(self.settings.status(self.actor)['state'], 'pending')
        # Fresh probe passes, but only as simulation.
        ticket = self.settings.begin_simulated_probe(self.cap, self.actor, 2)
        result = self.settings.finish_simulated_probe(self.cap, ticket, passed=True)
        self.assertEqual(result, {'state': 'simulated_pass', 'simulated': True, 'verified_for_work': False})
        view = self.settings.status(self.actor)
        self.assertEqual((view['state'], view['simulated'], view['verified_for_work']),
                         ('simulated_pass', True, False))
        with self.assertRaises(Conflict):  # ticket single-use
            self.settings.finish_simulated_probe(self.cap, ticket, passed=True)
        # Session rotation (re-login) makes the old evidence pending again.
        rotated = self.relogin()
        self.assertEqual(self.settings.status(rotated)['state'], 'pending')
        # Tenant disable/re-enable bumps lifecycle; evidence from before is void.
        ticket = self.settings.begin_simulated_probe(self.cap, rotated, 2)
        self.settings.finish_simulated_probe(self.cap, ticket, passed=True)
        self.assertEqual(self.settings.status(rotated)['state'], 'simulated_pass')
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        with self.assertRaises(Unauthorized):
            self.settings.status(rotated)
        self.core.set_tenant_enabled(self.admin, self.tenant.id, True)
        self.assertEqual(self.settings.status(rotated)['state'], 'pending')
        # In-flight ticket across a lifecycle bump is rejected at publish time.
        ticket = self.settings.begin_simulated_probe(self.cap, rotated, 2)
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        self.core.set_tenant_enabled(self.admin, self.tenant.id, True)
        with self.assertRaises(Conflict):
            self.settings.finish_simulated_probe(self.cap, ticket, passed=True)

    def test_catalog_withdrawal_and_cross_tenant_ticket(self):
        self.settings.configure(self.actor, 'fake-model-b', 0)
        ticket = self.settings.begin_simulated_probe(self.cap, self.actor, 1)
        self.settings.finish_simulated_probe(self.cap, ticket, passed=False)
        self.assertEqual(self.settings.status(self.actor)['state'], 'failed')
        # A restart with a narrower catalog reports the selection as unavailable.
        narrowed = CustomerModelSettings(SupportCore(self.db, clock=lambda: self.now[0]),
                                         catalog=('fake-model-a',), verifier_capability=self.cap)
        view = narrowed.status(narrowed.core.login('one.example', 'alice', 'test-password-long').actor)
        self.assertEqual((view['state'], view['selection_id']), ('unavailable', None))
        # A forged ticket naming another tenant's principal cannot publish.
        other = self.core.create_tenant(self.admin, 'two.example')
        bob = self.login('bob', other)
        self.settings.configure(bob, 'fake-model-a', 0)
        real = self.settings.begin_simulated_probe(self.cap, bob, 1)
        forged = ProbeTicket(real.principal_id, self.tenant.id, real.host, real.revision,
                             real.selection_id, real.lifecycle_version, real.token, real.session_digest)
        with self.assertRaises(Conflict):
            self.settings.finish_simulated_probe(self.cap, forged, passed=True)
        with self.assertRaises(InvalidInput):
            self.settings.finish_simulated_probe(self.cap, {'passed': True}, passed=True)
        with self.assertRaises(InvalidInput):
            self.settings.finish_simulated_probe(self.cap, real, passed='yes')
        self.assertEqual(self.settings.finish_simulated_probe(self.cap, real, passed=True)['state'],
                         'simulated_pass')


class ModelSettingsHttpTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / 'model-http.sqlite')
        self.core = SupportCore(self.db, admin_verifier=lambda x: 'admin' if x == 'test' else None)
        admin = self.core.admin_actor('test')
        tenant = self.core.create_tenant(admin, 'one.example')
        invite = self.core.issue_invite(admin, tenant.id)
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://one.example',
                                       model_catalog=CATALOG)
        self.client = TestClient(app, base_url='https://one.example')
        self.addCleanup(self.client.close)
        self.origin = {'origin': 'https://one.example'}
        self.assertEqual(self.client.post('/customer/activate', headers=self.origin, json={
            'invite': invite, 'username': 'alice', 'password': 'test-password-long'}).status_code, 201)

    def test_customer_can_select_but_never_assert_verification(self):
        self.assertEqual(self.client.get('/customer/model-settings?x=1').status_code, 400)
        status = self.client.get('/customer/model-settings')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['state'], 'unconfigured')
        self.assertEqual(status.headers['cache-control'], 'no-store')
        for body in ({'selection_id': 'fake-model-a'},
                     {'selection_id': 'fake-model-a', 'expected_revision': 0, 'state': 'simulated_pass'},
                     {'selection_id': 'fake-model-a', 'expected_revision': 0, 'api_key': 'sk-x'},
                     {'selection_id': 'nope', 'expected_revision': 0},
                     {'selection_id': 'fake-model-a', 'expected_revision': '0'}):
            self.assertEqual(self.client.post('/customer/model-settings', headers=self.origin,
                                              json=body).status_code, 400, body)
        ok = self.client.post('/customer/model-settings', headers=self.origin,
                              json={'selection_id': 'fake-model-a', 'expected_revision': 0})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual((ok.json()['revision'], ok.json()['state']), (1, 'pending'))
        self.assertFalse(ok.json()['verified_for_work'])
        stale = self.client.post('/customer/model-settings', headers=self.origin,
                                 json={'selection_id': 'fake-model-b', 'expected_revision': 0})
        self.assertEqual(stale.status_code, 409)
        # No HTTP route can start or finish a probe.
        for path in ('/customer/model-settings/probe', '/customer/model-settings/verify'):
            self.assertEqual(self.client.post(path, headers=self.origin, json={}).status_code, 404)
        onboarding = self.client.get('/customer/onboarding/status').json()
        self.assertEqual(onboarding['model_settings']['state'], 'pending')
        self.assertFalse(onboarding['ready_for_work'])
        # A platform-side selection never proves the site's own model: the
        # customer-model step follows the site's real default, not this record.
        own = {s['id']: s for s in onboarding['steps']}['own_model']
        self.assertEqual(own['status'], 'blocked')
        self.assertFalse(onboarding['model_settings']['verified_for_work'])
        revoked = self.client.post('/customer/model-settings/revoke', headers=self.origin,
                                   json={'expected_revision': 1})
        self.assertEqual((revoked.status_code, revoked.json()['state']), (200, 'unconfigured'))
        self.assertEqual(self.client.post('/customer/logout', headers=self.origin, json={}).status_code, 200)
        self.assertEqual(self.client.get('/customer/model-settings').status_code, 401)
        self.assertEqual(self.client.post('/customer/model-settings', headers=self.origin,
                                          json={'selection_id': 'fake-model-a', 'expected_revision': 2}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
