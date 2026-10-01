"""Invitation -> persisted rules -> logout/login/restart; NOT a site E2E."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import SupportCore
from support_rooms import SupportRooms
from support_chat_app import create_customer_chat_app
from support_ui import install_customer_ui
from support_onboarding import RULES_VERSION


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'onboarding.sqlite')
        self.core = SupportCore(self.db, admin_verifier=lambda x: 'admin' if x == 'test' else None)
        admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(admin, 'one.example')
        self.invite = self.core.issue_invite(admin, self.tenant.id)
        self.client = self.client_for(self.core, 'one.example')
        self.origin = {'origin': 'https://one.example'}

    def client_for(self, core, host):
        app = create_customer_chat_app(core, SupportRooms(core), 'https://' + host)
        install_customer_ui(app)
        client = TestClient(app, base_url='https://' + host)
        self.addCleanup(client.close)
        return client

    def activate(self):
        response = self.client.post('/customer/activate', headers=self.origin, json={
            'invite': self.invite, 'username': 'alice', 'password': 'test-password-long'})
        self.assertEqual(response.status_code, 201)

    def test_invite_activate_ack_logout_login_restart_resume(self):
        landing = self.client.get('/?token=' + self.invite)
        self.assertEqual(landing.status_code, 200)
        self.assertNotIn(self.invite, landing.text)
        self.activate()
        status = self.client.get('/customer/onboarding/status').json()
        self.assertFalse(status['rules_acknowledged'])
        response = self.client.post('/customer/onboarding/acknowledge', headers=self.origin,
                                    json={'rules_version': RULES_VERSION})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['rules_acknowledged'])
        self.assertFalse(response.json()['ready_for_work'])
        # Steps reflect real state: nothing is provisioned yet, so the office
        # and the customer's own model cannot be read. Never hardcoded.
        self.assertEqual([s['status'] for s in response.json()['steps'][2:]],
                         ['pending', 'blocked', 'blocked'])
        self.assertIsNone(response.json()['office'])
        self.assertFalse(response.json()['ready_for_work'])
        replay = self.client.post('/customer/onboarding/acknowledge', headers=self.origin,
                                  json={'rules_version': RULES_VERSION})
        self.assertEqual(replay.json(), response.json())
        for path in ('/customer/onboarding', '/customer/ui/onboarding.js'):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.post('/customer/logout', headers=self.origin, json={}).status_code, 200)
        self.assertEqual(self.client.get('/customer/onboarding/status').status_code, 401)
        # Recreate all in-process state; resume from same SQLite with normal login.
        fresh = self.client_for(SupportCore(self.db), 'one.example')
        login = fresh.post('/customer/login', headers=self.origin,
                           json={'username': 'alice', 'password': 'test-password-long'})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(fresh.get('/customer/onboarding/status').json(), response.json())
        self.assertEqual(fresh.post('/customer/activate', headers=self.origin, json={
            'invite': self.invite, 'username': 'other', 'password': 'test-password-long'}).status_code, 401)

    def test_guards_no_customer_asserted_site_or_model_completion(self):
        self.assertEqual(self.client.get('/customer/onboarding', follow_redirects=False).headers['location'], '/customer/welcome')
        self.activate()
        path = '/customer/onboarding/acknowledge'
        for body in ({'rules_version': RULES_VERSION, 'site_ready': True},
                     {'rules_version': RULES_VERSION, 'tenant_id': self.tenant.id},
                     {'rules_version': RULES_VERSION, 'model_verified': True}):
            self.assertEqual(self.client.post(path, headers=self.origin, json=body).status_code, 400)
        self.assertEqual(self.client.post(path, headers=self.origin,
                                         json={'rules_version': 'old'}).status_code, 409)
        self.assertEqual(self.client.post(path, headers={'origin':'https://evil.example'},
                                         json={'rules_version':RULES_VERSION}).status_code, 403)
        self.assertEqual(self.client.get('/customer/onboarding/status?tenant_id=x').status_code, 400)
        self.assertEqual(self.client.get('/customer/onboarding/status', headers={'host':'two.example'}).status_code, 403)

    def test_progress_is_principal_bound_and_tenant_disable_revokes_access(self):
        self.activate()
        self.client.post('/customer/onboarding/acknowledge', headers=self.origin,
                         json={'rules_version':RULES_VERSION})
        admin = self.core.admin_actor('test')
        other_tenant = self.core.create_tenant(admin, 'two.example')
        other_invite = self.core.issue_invite(admin, other_tenant.id)
        other = self.client_for(self.core, 'two.example')
        self.assertEqual(other.post('/customer/activate', headers={'origin':'https://two.example'},
            json={'invite':other_invite, 'username':'alice', 'password':'test-password-long'}).status_code, 201)
        self.assertFalse(other.get('/customer/onboarding/status').json()['rules_acknowledged'])
        self.core.set_tenant_enabled(admin, self.tenant.id, False)
        self.assertEqual(self.client.get('/customer/onboarding/status').status_code, 401)
        self.assertEqual(self.client.get('/customer/onboarding', follow_redirects=False).headers['location'], '/customer/welcome')


class OfficeEditRouteTests(unittest.TestCase):
    """HTTP guards for schedule edits; the site is derived from the session."""

    def setUp(self):
        from support_site_office import SiteOffice
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.sites = root / 'sites'
        for name in ('ab', 'cd'):
            ws = self.sites / name / 'workspace'
            (ws / '.swarm').mkdir(parents=True)
            (ws / 'hives' / 'shop').mkdir(parents=True)
            (ws / '.swarm' / 'registry.yaml').write_text(
                'hives:\n  - id: shop\n    enabled: false\n    agents:\n      pm:\n        model: ""\n')
            (ws / 'hives' / 'shop' / 'project.yaml').write_text('project:\n  id: shop\nreports: {}\n')
        self.core = SupportCore(str(root / 'db.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        admin = self.core.admin_actor('test')
        tenant = self.core.create_tenant(admin, 'platform.example')
        invite = self.core.issue_invite(admin, tenant.id)
        self.host = {'site_host': 'ab.example.cc'}
        app = create_customer_chat_app(self.core, SupportRooms(self.core), 'https://platform.example',
                                       site_office=SiteOffice(self.sites, 'example.cc'))
        onboarding = next(r.endpoint for r in app.routes
                          if getattr(r, 'path', '') == '/customer/office/schedule')
        self.assertIsNotNone(onboarding)
        # Pretend provisioning finished for this tenant, pointing at ab.
        import support_onboarding
        original = support_onboarding.SupportSiteJobs.customer_status
        self.addCleanup(setattr, support_onboarding.SupportSiteJobs, 'customer_status', original)
        support_onboarding.SupportSiteJobs.customer_status = lambda _self, actor: {
            'status': 'succeeded_provisioned', 'simulated': False, 'provisioned': True, **self.host}
        self.client = TestClient(app, base_url='https://platform.example')
        self.addCleanup(self.client.close)
        self.origin = {'origin': 'https://platform.example'}
        self.assertEqual(self.client.post('/customer/activate', headers=self.origin, json={
            'invite': invite, 'username': 'alice', 'password': 'test-password-long'}).status_code, 201)

    def body(self, **kw):
        status = self.client.get('/customer/onboarding/status').json()
        body = {'revision': status['office']['revision'], 'hive': 'shop', 'original': '',
                'name': 'digest', 'role': 'pm', 'cron': '0 8 * * *', 'skill': '',
                'context': '整理待辦'}
        body.update(kw)
        return body

    def test_customer_edits_only_their_own_site(self):
        response = self.client.post('/customer/office/schedule', headers=self.origin, json=self.body())
        self.assertEqual(response.status_code, 200)
        [job] = response.json()['office']['hives'][0]['schedules']
        self.assertEqual((job['name'], job['cron']), ('digest', '0 8 * * *'))
        self.assertIn('digest', (self.sites / 'ab' / 'workspace' / '.swarm' / 'registry.yaml').read_text())
        self.assertNotIn('digest', (self.sites / 'cd' / 'workspace' / '.swarm' / 'registry.yaml').read_text())
        # Extra fields (e.g. a site name) are refused rather than honoured.
        extra = self.body(name='other')
        extra['site'] = 'cd'
        self.assertEqual(self.client.post('/customer/office/schedule', headers=self.origin,
                                          json=extra).status_code, 400)
        toggle = self.client.post('/customer/office/hive-enabled', headers=self.origin,
                                  json={'revision': response.json()['office']['revision'],
                                        'hive': 'shop', 'enabled': 'true'})
        self.assertEqual(toggle.json()['office']['hives'][0]['schedules'][0]['state'], 'ready')

    def test_guards(self):
        path = '/customer/office/schedule'
        self.assertEqual(self.client.post(path, headers={'origin': 'https://evil.example'},
                                          json=self.body()).status_code, 403)
        stale = self.body()
        stale['revision'] = 'old'
        conflict = self.client.post(path, headers=self.origin, json=stale)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn('重新讀取', conflict.json()['message'])
        refused = self.client.post(path, headers=self.origin, json=self.body(cron='0 9 * * 1'))
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(self.client.post(path + '?x=1', headers=self.origin,
                                          json=self.body()).status_code, 400)
        self.assertEqual(self.client.post(path, headers=self.origin,
                                          json={**self.body(), 'enabled': True}).status_code, 400)
        self.host = {'site_host': 'evil.other.cc'}
        self.assertEqual(self.client.post(path, headers=self.origin, json={
            'revision': 'x', 'hive': 'shop', 'original': '', 'name': 'a', 'role': 'pm',
            'cron': '0 8 * * *', 'skill': '', 'context': 'x'}).status_code, 422)
        self.client.post('/customer/logout', headers=self.origin, json={})
        self.assertEqual(self.client.post(path, headers=self.origin, json={
            'revision': 'x', 'hive': 'shop', 'original': '', 'name': 'a', 'role': 'pm',
            'cron': '0 8 * * *', 'skill': '', 'context': 'x'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
