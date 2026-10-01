"""Production platform entrypoint: strict config, no admin surface, opt-in provisioning."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import platform_service

ORIGIN = 'https://platform.example'


class PlatformServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'platform.sqlite')

    def env(self, **extra):
        return {'PLATFORM_ORIGIN': ORIGIN, 'PLATFORM_DB': self.db, **extra}

    def build(self, **extra):
        with patch.dict(os.environ, self.env(**extra), clear=True):
            return platform_service.build()

    def test_configuration_must_be_explicit_and_safe(self):
        for env in ({}, {'PLATFORM_ORIGIN': ORIGIN}, {'PLATFORM_DB': self.db}):
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(RuntimeError):
                    platform_service.build()
        for bad in ('http://platform.example', 'https://platform.example:8443',
                    'https://platform.example/path', 'platform.example', 'https://'):
            with patch.dict(os.environ, self.env(PLATFORM_ORIGIN=bad), clear=True):
                with self.assertRaises(RuntimeError):
                    platform_service.build()
        # Relative path, and a path inside the repository, are both refused.
        for bad in ('relative.sqlite', str(Path(__file__).with_name('inrepo.sqlite'))):
            with patch.dict(os.environ, self.env(PLATFORM_DB=bad), clear=True):
                with self.assertRaises(RuntimeError):
                    platform_service.build()

    def test_no_admin_surface_is_exposed(self):
        app, _ = self.build()
        client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        self.addCleanup(client.close)
        for path in ('/admin/tenants', '/admin/customers', '/admin/support/login',
                     '/admin/support/customers', '/admin/hub'):
            self.assertIn(client.get(path).status_code, (404, 403), path)
        # Customer entry points do exist.
        self.assertEqual(client.get('/').status_code, 200)

    def test_real_provisioning_is_off_by_default(self):
        app, provisioner = self.build()
        self.assertIsNone(provisioner)
        _, enabled = self.build(PLATFORM_PROVISION='1')
        from support_site_executor import SiteProvisioner
        self.assertIsInstance(enabled, SiteProvisioner)
        # Provisioning off must not disable the rest of the platform.
        client = TestClient(app, base_url=ORIGIN)
        self.addCleanup(client.close)
        self.assertEqual(client.get('/').status_code, 200)

    def test_copilot_is_disabled_unless_an_explicit_fake_is_requested(self):
        app, _ = self.build()
        # No model configured: the respond route must not invent one.
        self.assertIsNone(getattr(app.state, 'support_copilot_model', None))
        # A real provider cannot be enabled by a stray value.
        for value in ('openai', 'true', '1', 'real'):
            with self.assertRaises(RuntimeError, msg=value):
                self.build(PLATFORM_COPILOT=value)
        # The explicit offline fake is accepted.
        fake_app, _ = self.build(PLATFORM_COPILOT='fake')
        routes = {getattr(r, 'path', '') for r in fake_app.routes}
        self.assertIn('/customer/rooms/{room_id}/respond', routes)

    def test_model_selection_cannot_be_marked_verified_on_this_deployment(self):
        app, _ = self.build(PLATFORM_MODELS='model-a, model-b')
        core = app.state.platform_core
        admin_core = self.operator_core()
        admin = admin_core.admin_actor(self.SENTINEL)
        tenant = admin_core.create_tenant(admin, 'acme.example', platform_host='platform.example')
        token = admin_core.issue_invite(admin, tenant.id)
        client = TestClient(app, base_url=ORIGIN)
        self.addCleanup(client.close)
        origin = {'origin': ORIGIN}
        self.assertEqual(client.post('/customer/activate', headers=origin, json={
            'invite': token, 'username': 'alice', 'password': 'customer-password-1'}).status_code, 201)
        status = client.get('/customer/model-settings').json()
        self.assertEqual(status['available_selections'], ['model-a', 'model-b'])
        # No verifier is composed here, so nothing can claim a model works.
        self.assertFalse(status['simulated_probe_available'])
        self.assertFalse(status['verified_for_work'])
        self.assertEqual(client.post('/customer/model-settings/simulated-probe', headers=origin,
                                     json={'expected_revision': 0}).status_code, 404)
        self.assertFalse(client.get('/customer/onboarding/status').json()['ready_for_work'])
        self.assertIs(core, app.state.platform_core)

    SENTINEL = None

    def operator_core(self):
        import platform_admin_cli
        self.SENTINEL = platform_admin_cli._SENTINEL
        with patch.dict(os.environ, {'PLATFORM_DB': self.db,
                                     'PLATFORM_ORIGIN': ORIGIN}, clear=True):
            return platform_admin_cli._core()

    def test_operator_cli_issues_platform_scoped_invites(self):
        import platform_admin_cli
        env = {'PLATFORM_DB': self.db, 'PLATFORM_ORIGIN': ORIGIN}
        with patch.dict(os.environ, env, clear=True):
            platform_admin_cli.main(['add-tenant', '--site-host', 'acme.example'])
            core = platform_admin_cli._core()
            with core._connect() as conn:
                row = conn.execute('SELECT id, site_host, platform_host FROM tenants').fetchone()
            # The invite is bound to the platform, not the not-yet-existing site.
            self.assertEqual(row['platform_host'], 'platform.example')
            self.assertEqual(row['site_host'], 'acme.example')
            for bad in ('30', '999999'):
                with self.assertRaises(SystemExit):
                    platform_admin_cli.main(['invite', '--tenant', row['id'], '--ttl', bad])
            platform_admin_cli.main(['invite', '--tenant', row['id'], '--ttl', '3600'])
            platform_admin_cli.main(['list'])
        # A forged admin assertion cannot mint authority.
        from support_core import SupportCore, Unauthorized
        plain = SupportCore(Path(self.db))
        with self.assertRaises(Unauthorized):
            plain.admin_actor(object())


if __name__ == '__main__':
    unittest.main()
