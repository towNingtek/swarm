"""SCM token relay: pure local tests. Fake GitHub via httpx.MockTransport and a
fake signer; no network, no real key."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from scm_token_relay import AppSigner, GitHubAppMinter, check_private_key, load_apps, plan
from site_model_relay import create_app
from support_core import InvalidInput, SupportCore
from support_quota import SupportQuota
from support_site_model import SiteModelAccess
from support_site_scm import READ_ONLY, SiteScmAccess, SiteScmDenied

PM = {'contents': 'read', 'issues': 'write', 'pull_requests': 'read'}


class FakeGitHub:
    def __init__(self, clock):
        self.clock = clock
        self.requests = []
        self.installations = {'acme': 108813060}
        self.mint_status = 201
        self.serial = 0

    def __call__(self, request):
        self.requests.append(request)
        assert request.headers['authorization'].startswith('Bearer jwt-')
        path = request.url.path
        for kind in ('orgs', 'users'):
            prefix = f'/{kind}/'
            if path.startswith(prefix) and path.endswith('/installation'):
                owner = path[len(prefix):-len('/installation')]
                found = self.installations.get(owner.lower()) if kind == 'orgs' else None
                return httpx.Response(200, json={'id': found}) if found else httpx.Response(404, json={})
        if path.endswith('/access_tokens'):
            if self.mint_status != 201:
                return httpx.Response(self.mint_status, json={'message': 'nope'})
            self.serial += 1
            body = json.loads(request.content)
            expires = self.clock() + 3600
            from datetime import datetime, timezone
            return httpx.Response(201, json={
                'token': f'ghs_fake{self.serial:04d}',
                'expires_at': datetime.fromtimestamp(expires, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'permissions': body.get('permissions')})
        return httpx.Response(404, json={})

    def mints(self):
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith('/access_tokens')]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1_800_000_000.0
        clock = lambda: self.now
        self.core = SupportCore(self.tmp.name + '/t.db', clock=clock,
                                admin_verifier=lambda a: 'admin' if a == 'ok' else None)
        admin = self.core.admin_actor('ok')
        self.tenant = self.core.create_tenant(admin, 'acme.test')
        self.other = self.core.create_tenant(admin, 'other.test')
        self.scm = SiteScmAccess(self.core)
        self.token = self.scm.issue(self.tenant.id)
        self.github = FakeGitHub(clock)
        self.signs = 0

        def sign(message):
            self.signs += 1
            return b'sig'

        signer = AppSigner('Iv1exampleclientid0', '/nonexistent.pem', clock=clock, sign=sign)
        signer.jwt = (lambda inner: (lambda: 'jwt-' + inner().split('.')[1][:8]))(signer.jwt)
        self.minter = GitHubAppMinter(signer, transport=httpx.MockTransport(self.github), clock=clock)
        SupportQuota(self.core)
        model_access = SiteModelAccess(self.core)
        self.app = create_app(model_access, base_url='http://upstream.invalid/v1', api_key='k',
                              models=['m'], peer_check=lambda host: True,
                              scm_access=self.scm, scm_minters={'acme': self.minter})
        self.client = TestClient(self.app)

    def scope(self, tenant, repos, perms=None, app='acme'):
        return self.scm.set_repos(tenant, repos, perms, app=app)

    def ask(self, token=None, body=None):
        headers = {'Authorization': f'Bearer {token or self.token}'}
        return self.client.post('/scm/v1/github/token', headers=headers,
                                content=json.dumps(body) if body is not None else b'')


class AccessTests(Base):
    def test_relay_key_is_stored_as_digest_only(self):
        with self.core._connect() as conn:
            dump = '\n'.join(str(tuple(r)) for r in conn.execute('SELECT * FROM support_site_scm_tokens'))
        self.assertNotIn(self.token, dump)
        self.assertTrue(self.token.startswith('scs_'))

    def test_default_permissions_are_read_only(self):
        _, perms = self.scm.set_repos(self.tenant.id, ['acme/widgets'])
        self.assertEqual(perms, READ_ONLY)

    def test_permission_outside_allowlist_is_refused(self):
        for bad in ({'administration': 'write'}, {'workflows': 'write'}, {'contents': 'admin'}):
            with self.assertRaises(InvalidInput):
                self.scm.set_repos(self.tenant.id, ['a/b'], bad)

    def test_organization_projects_is_opt_in(self):
        self.assertNotIn('organization_projects', READ_ONLY)
        _, perms = self.scm.set_repos(self.tenant.id, ['acme/*'], {'issues': 'write', 'organization_projects': 'write'})
        self.assertEqual(perms['organization_projects'], 'write')
        with self.assertRaises(InvalidInput):
            self.scm.set_repos(self.tenant.id, ['a/b'], {'organization_projects': 'admin'})

    def test_metadata_is_forced_to_read(self):
        _, perms = self.scm.set_repos(self.tenant.id, ['a/b'], {'issues': 'write', 'metadata': 'write'})
        self.assertEqual(perms['metadata'], 'read')

    def test_corrupt_stored_permissions_fall_back_to_read_only(self):
        self.scm.set_repos(self.tenant.id, ['a/b'], PM)
        with self.core._connect() as conn:
            conn.execute("UPDATE support_site_scm_scopes SET permissions='{\"administration\":\"write\"}'")
        self.assertEqual(self.scm.scope_for(self.tenant.id)[1], READ_ONLY)

    def test_invalid_repo_names_are_refused(self):
        for bad in (['nope'], ['a/b/c'], ['../x'], ['*/*'], [], 'a/b'):
            with self.assertRaises(InvalidInput):
                self.scm.set_repos(self.tenant.id, bad)

    def test_site_can_narrow_but_not_widen(self):
        self.scope(self.tenant.id, ['acme/widgets'], PM)
        self.assertEqual(self.scm.authorize(self.token, 'ACME/WIDGETS')[2],
                         ['acme/widgets'])
        with self.assertRaises(SiteScmDenied) as caught:
            self.scm.authorize(self.token, 'acme/swarm')
        self.assertEqual(caught.exception.code, 'repo_not_allowed')

    def test_org_wildcard_covers_that_owner_only(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        self.assertEqual(self.scm.authorize(self.token, 'acme/swarm')[2],
                         ['acme/swarm'])
        with self.assertRaises(SiteScmDenied):
            self.scm.authorize(self.token, 'towNingtek/swarm')
        with self.assertRaises(SiteScmDenied):
            self.scm.authorize(self.token, 'acme-beta/x')

    def test_empty_scope_is_not_a_wildcard(self):
        with self.assertRaises(SiteScmDenied) as caught:
            self.scm.authorize(self.token)
        self.assertEqual(caught.exception.code, 'scm_not_configured')

    def test_disabled_tenant_is_refused(self):
        self.scope(self.tenant.id, ['a/b'])
        with self.core._connect() as conn:
            conn.execute('UPDATE tenants SET enabled=0 WHERE id=?', (self.tenant.id,))
        with self.assertRaises(SiteScmDenied) as caught:
            self.scm.authorize(self.token)
        self.assertEqual(caught.exception.status, 403)

    def test_rotation_and_revocation(self):
        old = self.token
        new = self.scm.issue(self.tenant.id)
        with self.assertRaises(SiteScmDenied):
            self.scm.authenticate(old)
        self.assertEqual(self.scm.authenticate(new), self.tenant.id)
        self.scm.revoke(self.tenant.id)
        with self.assertRaises(SiteScmDenied):
            self.scm.authenticate(new)

    def test_other_tenants_key_does_not_carry_scope(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        other = self.scm.issue(self.other.id)
        with self.assertRaises(SiteScmDenied) as caught:
            self.scm.authorize(other)
        self.assertEqual(caught.exception.code, 'scm_not_configured')


class PlanTests(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(plan(['o/*']), ('o', None))
        self.assertEqual(plan(['o/a', 'O/b']), ('o', ['a', 'b']))
        with self.assertRaises(ValueError):
            plan(['o/a', 'p/b'])


class RelayTests(Base):
    def test_mint_is_narrowed_to_tenant_permissions(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['token'].startswith('ghs_'))
        self.assertEqual(data['repositories'], 'all')
        sent = self.github.mints()[0]
        self.assertEqual(sent['permissions'], {**PM, 'metadata': 'read'})
        self.assertNotIn('repositories', sent)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_named_repo_is_sent_as_repositories(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        response = self.ask(body={'repo': 'acme/widgets'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.github.mints()[0]['repositories'], ['widgets'])
        self.assertEqual(response.json()['repositories'], ['acme/widgets'])

    def test_explicit_list_is_sent_as_repositories(self):
        self.scope(self.tenant.id, ['acme/widgets', 'acme/meetings'])
        self.assertEqual(self.ask().status_code, 200)
        sent = self.github.mints()[0]
        self.assertEqual(sorted(sent['repositories']), ['meetings', 'widgets'])
        self.assertEqual(sent['permissions'], READ_ONLY)

    def test_cached_until_five_minutes_before_expiry(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        first = self.ask().json()['token']
        self.now += 3000
        self.assertEqual(self.ask().json()['token'], first)
        self.assertEqual(len(self.github.mints()), 1)
        self.now += 400  # under 5 minutes left
        self.assertNotEqual(self.ask().json()['token'], first)
        self.assertEqual(len(self.github.mints()), 2)

    def test_cache_is_per_tenant(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        other = self.scm.issue(self.other.id)
        self.scope(self.other.id, ['acme/*'])
        a = self.ask().json()
        b = self.ask(other).json()
        self.assertNotEqual(a['token'], b['token'])
        self.assertEqual(b['permissions']['issues'], 'read')

    def test_jwt_is_reused_briefly(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        self.ask(body={'repo': 'acme/a'})
        self.ask(body={'repo': 'acme/b'})
        self.assertEqual(self.signs, 1)

    def test_bad_key_and_garbage(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        self.assertEqual(self.ask('scs_' + 'x' * 43).status_code, 401)
        self.assertEqual(self.ask('sms_' + 'x' * 43).status_code, 401)
        headers = {'Authorization': f'Bearer {self.token}'}
        self.assertEqual(self.client.post('/scm/v1/github/token', headers=headers,
                                          content=b'{"repo":"a/b","permissions":{}}').status_code, 400)
        self.assertEqual(self.client.post('/scm/v1/github/token', headers=headers,
                                          content=b'not json').status_code, 400)
        self.assertEqual(self.client.get('/scm/v1/github/token', headers=headers).status_code, 404)
        self.assertEqual(self.github.mints(), [])

    def test_missing_installation_is_refused_and_audited(self):
        self.scope(self.tenant.id, ['elsewhere/*'], PM)
        response = self.ask()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'installation_missing')
        self.assertEqual(self.scm.recent_mints(self.tenant.id)[0]['outcome'], 'refused')

    def test_multi_owner_scope_requires_repo(self):
        self.scope(self.tenant.id, ['acme/*', 'towNingtek/swarm'], PM)
        self.assertEqual(self.ask().json()['error']['code'], 'repo_required')
        self.assertEqual(self.ask(body={'repo': 'acme/x'}).status_code, 200)

    def test_github_refusal_is_not_relayed_verbatim(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        self.github.mint_status = 422
        response = self.ask()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('nope', response.text)
        self.github.mint_status = 500
        self.assertEqual(self.ask().status_code, 502)
        self.assertEqual(self.scm.recent_mints(self.tenant.id)[0]['outcome'], 'error')

    def test_grants_are_audited_without_token(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        token = self.ask().json()['token']
        self.ask()  # cache hit: no new audit row
        rows = self.scm.recent_mints(self.tenant.id)
        self.assertEqual([r['outcome'] for r in rows], ['granted'])
        self.assertEqual(rows[0]['installation_id'], 108813060)
        with self.core._connect() as conn:
            dump = '\n'.join(str(tuple(r)) for r in conn.execute('SELECT * FROM support_site_scm_mints'))
        self.assertNotIn(token, dump)

    def test_untrusted_peer_is_refused(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        app = create_app(SiteModelAccess(self.core), base_url='http://u.invalid/v1', api_key='k',
                         models=['m'], peer_check=lambda host: False,
                         scm_access=self.scm, scm_minters={'acme': self.minter})
        response = TestClient(app).post('/scm/v1/github/token',
                                        headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual(response.status_code, 403)

    def test_route_absent_when_not_configured(self):
        app = create_app(SiteModelAccess(self.core), base_url='http://u.invalid/v1', api_key='k',
                         models=['m'], peer_check=lambda host: True)
        response = TestClient(app).post('/scm/v1/github/token',
                                        headers={'Authorization': f'Bearer {self.token}'})
        self.assertEqual(response.status_code, 404)


class AppIdentityTests(Base):
    """Every site speaks with its own GitHub App; there is no shared default."""

    def test_site_without_app_gets_nothing(self):
        self.scm.set_repos(self.tenant.id, ['acme/*'], PM)  # no app bound
        response = self.ask()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'scm_no_app')
        self.assertEqual(self.github.mints(), [])

    def test_unknown_app_is_unavailable_not_substituted(self):
        self.scope(self.tenant.id, ['acme/*'], PM, app='someone-else')
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.github.mints(), [])
        self.assertEqual(self.scm.recent_mints(self.tenant.id)[0]['outcome'], 'error')

    def test_binding_survives_scope_change_and_can_be_removed(self):
        self.scope(self.tenant.id, ['acme/*'], PM)
        self.scm.set_repos(self.tenant.id, ['acme/widgets'])  # app omitted: kept
        self.assertEqual(self.scm.app_for(self.tenant.id), 'acme')
        self.scm.bind_app(self.tenant.id, None)
        self.assertEqual(self.ask().json()['error']['code'], 'scm_no_app')

    def test_two_sites_use_their_own_apps(self):
        second = FakeGitHub(lambda: self.now)
        signer = AppSigner('Iv1exampleclientid1', '/nonexistent.pem', clock=lambda: self.now,
                           sign=lambda m: b'sig')
        signer.jwt = (lambda inner: (lambda: 'jwt-' + inner().split('.')[1][:8]))(signer.jwt)
        other_minter = GitHubAppMinter(signer, transport=httpx.MockTransport(second), clock=lambda: self.now)
        app = create_app(SiteModelAccess(self.core), base_url='http://u.invalid/v1', api_key='k',
                         models=['m'], peer_check=lambda host: True, scm_access=self.scm,
                         scm_minters={'acme': self.minter, 'other': other_minter})
        client = TestClient(app)
        self.scope(self.tenant.id, ['acme/*'], PM, app='acme')
        other_key = self.scm.issue(self.other.id)
        self.scope(self.other.id, ['acme/*'], app='other')
        for key in (self.token, other_key):
            self.assertEqual(client.post('/scm/v1/github/token',
                                         headers={'Authorization': f'Bearer {key}'}).status_code, 200)
        self.assertEqual(len(self.github.mints()), 1)
        self.assertEqual(len(second.mints()), 1)

    def test_invalid_app_names_are_refused(self):
        for bad in ('Acme', '../x', '', 'a' * 70, 3):
            with self.assertRaises(InvalidInput):
                self.scm.set_repos(self.tenant.id, ['a/b'], app=bad)


# check_private_key() only needs this marker plus the file mode, so the fixture
# stays obviously fake. Split so no scanner reads this test file as a real key.
MARKER = b'PRIVATE' + b' KEY'


class RegistryTests(unittest.TestCase):
    def test_load_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / 'k.pem'
            key.write_bytes(MARKER)
            key.chmod(0o600)
            reg = Path(tmp) / 'apps.json'
            reg.write_text(json.dumps({'acme': {'client_id': 'Iv1exampleclientid0', 'pem_file': str(key)}}))
            reg.chmod(0o644)
            with self.assertRaises(RuntimeError):
                load_apps(reg)  # readable by others
            reg.chmod(0o600)
            self.assertEqual(load_apps(reg), {'acme': ('Iv1exampleclientid0', str(key))})
            for bad in ({'Bad Name': {'client_id': 'x', 'pem_file': str(key)}},
                        {'t': {'client_id': 'x', 'pem_file': 'k.pem'}}, {}):
                reg.write_text(json.dumps(bad))
                with self.assertRaises(RuntimeError):
                    load_apps(reg)


class KeyFileTests(unittest.TestCase):
    def test_private_key_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'k.pem'
            path.write_bytes(MARKER)
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                check_private_key(path)
            path.chmod(0o600)
            check_private_key(path)
            link = Path(tmp) / 'link.pem'
            os.symlink(path, link)
            with self.assertRaises(RuntimeError):
                check_private_key(link)

    def test_real_openssl_signature_verifies(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            key, pub = Path(tmp) / 'k.pem', Path(tmp) / 'p.pem'
            subprocess.run(['openssl', 'genrsa', '-out', str(key), '2048'], check=True, capture_output=True)
            subprocess.run(['openssl', 'rsa', '-in', str(key), '-pubout', '-out', str(pub)],
                           check=True, capture_output=True)
            token = AppSigner('Iv1exampleclientid0', key).jwt()
            header, payload, sig = token.split('.')
            import base64
            raw = base64.urlsafe_b64decode(sig + '=' * (-len(sig) % 4))
            (Path(tmp) / 's').write_bytes(raw)
            done = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(pub), '-signature',
                                   str(Path(tmp) / 's')], input=f'{header}.{payload}'.encode(),
                                  capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            self.assertEqual(claims['iss'], 'Iv1exampleclientid0')
            self.assertLessEqual(claims['exp'] - claims['iat'], 600)


if __name__ == '__main__':
    unittest.main()
