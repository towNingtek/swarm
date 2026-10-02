"""Run: python -B -m unittest discover -s platform/tests -p test_support_core.py -v"""
import concurrent.futures
import dataclasses
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import support_core as module
from support_core import (AdminActor, Conflict, CustomerActor, InvalidInput,
                          InvalidInvite, SupportCore, Unauthorized)


PASSWORD = 'correct horse battery staple'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SupportCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'support.db')
        self.clock = Clock()
        self.core = self.reopen()
        self.admin = self.core.admin_actor('trusted-adapter-proof')
        self.tenant = self.core.create_tenant(self.admin, 'a.example', quota_mode='unlimited')

    def reopen(self):
        return SupportCore(self.path, clock=self.clock, session_ttl=100,
                           admin_verifier=lambda proof: 'operator' if proof == 'trusted-adapter-proof' else None)

    def invite(self, **kwargs):
        return self.core.issue_invite(self.admin, self.tenant.id, **kwargs)

    def activate(self, username='alice'):
        return self.core.redeem_invite(self.invite(), 'a.example', username, PASSWORD)

    def test_preview_does_not_consume_and_reuse_fails(self):
        token = self.invite()
        self.assertEqual(self.core.preview_invite(token, 'A.EXAMPLE'), self.core.preview_invite(token, 'a.example'))
        with self.core._connect() as conn:
            self.assertIsNone(conn.execute('SELECT redeemed_at FROM invites').fetchone()[0])
        grant = self.core.redeem_invite(token, 'a.example', 'Alice', PASSWORD)
        self.assertEqual(self.core.authorize_customer(grant.actor).username, 'alice')
        self.assertEqual(self.core.get_tenant(grant.actor), self.tenant)
        for operation in (lambda: self.core.preview_invite(token, 'a.example'),
                          lambda: self.core.redeem_invite(token, 'a.example', 'bob', PASSWORD)):
            with self.assertRaises(InvalidInvite):
                operation()

    def test_expired_revoked_and_wrong_host_invites(self):
        expired = self.invite(ttl=1)
        revoked = self.invite()
        wrong_host = self.invite()
        self.clock.now += 1
        self.core.revoke_invite(self.admin, revoked)
        for token, host in ((expired, 'a.example'), (revoked, 'a.example'), (wrong_host, 'b.example')):
            with self.subTest(host=host):
                with self.assertRaises(InvalidInvite):
                    self.core.preview_invite(token, host)
                with self.assertRaises(InvalidInvite):
                    self.core.redeem_invite(token, host, 'alice', PASSWORD)
        self.core.preview_invite(wrong_host, 'a.example')

    def test_password_validation_and_conflict_do_not_consume(self):
        token = self.invite()
        with self.assertRaises(InvalidInput):
            self.core.redeem_invite(token, 'a.example', 'alice', 'short')
        self.core.preview_invite(token, 'a.example')
        self.activate()
        with self.assertRaises(Conflict):
            self.core.redeem_invite(token, 'a.example', 'alice', PASSWORD)
        self.core.preview_invite(token, 'a.example')
        self.core.redeem_invite(token, 'a.example', 'bob', PASSWORD)

    def test_probing_taken_usernames_spends_the_invite(self):
        self.activate()
        token = self.invite()
        for _ in range(self.core.MAX_INVITE_CONFLICTS):
            with self.assertRaises(Conflict):
                self.core.redeem_invite(token, 'a.example', 'alice', PASSWORD)
        with self.assertRaises(InvalidInvite):
            self.core.redeem_invite(token, 'a.example', 'bob', PASSWORD)

    def test_concurrent_redemption_exactly_once_across_instances(self):
        token = self.invite()
        other = self.reopen()
        barrier = threading.Barrier(2)
        original_hash = module._password_hash

        def synchronized_hash(password):
            value = original_hash(password)
            barrier.wait(timeout=5)
            return value

        def redeem(core, username):
            try:
                return core.redeem_invite(token, 'a.example', username, PASSWORD)
            except InvalidInvite:
                return None

        with patch.object(module, '_password_hash', synchronized_hash):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(redeem, core, name) for core, name in ((self.core, 'alice'), (other, 'bob'))]
                results = [job.result(timeout=10) for job in jobs]
        self.assertEqual(sum(result is not None for result in results), 1)
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM principals').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0], 1)

    def test_recheck_expiry_and_revocation_after_hashing(self):
        original_hash = module._password_hash
        for event in ('expiry', 'revocation'):
            token = self.invite(ttl=10)

            def changed(password):
                value = original_hash(password)
                if event == 'expiry':
                    self.clock.now += 10
                else:
                    # Separate connection proves expensive hashing is outside write lock.
                    self.core.revoke_invite(self.admin, token)
                return value

            with patch.object(module, '_password_hash', changed):
                with self.assertRaises(InvalidInvite):
                    self.core.redeem_invite(token, 'a.example', event, PASSWORD)
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM principals').fetchone()[0], 0)

    def test_transaction_rolls_back_account_and_consumption_on_session_failure(self):
        token = self.invite()
        with patch.object(self.core, '_new_session', side_effect=RuntimeError('injected failure')):
            with self.assertRaises(RuntimeError):
                self.core.redeem_invite(token, 'a.example', 'alice', PASSWORD)
        self.core.preview_invite(token, 'a.example')
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM principals').fetchone()[0], 0)
        self.core.redeem_invite(token, 'a.example', 'alice', PASSWORD)

    def test_session_wrong_host_expiry_logout_and_rotation(self):
        grant = self.activate()
        with self.assertRaises(Unauthorized):
            self.core.authenticate_session(grant.token, 'b.example')
        for password in ('incorrect password', 'short'):
            with self.assertRaises(Unauthorized):
                self.core.login('a.example', 'alice', password)
        with self.assertRaises(Unauthorized):
            self.core.login('a.example', 'unknown', PASSWORD)
        rotated = self.core.login('a.example', 'alice', PASSWORD)
        with self.assertRaises(Unauthorized):
            self.core.authorize_customer(grant.actor)
        self.core.logout(rotated.actor)
        with self.assertRaises(Unauthorized):
            self.core.authenticate_session(rotated.token, 'a.example')
        fresh = self.core.login('a.example', 'alice', PASSWORD)
        self.clock.now = fresh.expires_at
        with self.assertRaises(Unauthorized):
            self.core.authenticate_session(fresh.token, 'a.example')
        with self.assertRaises(Unauthorized):
            self.core.get_tenant(fresh.actor)

    def test_disabled_unlimited_tenant_never_bypasses_auth(self):
        grant = self.activate()
        token = self.invite()
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        with self.assertRaises(Unauthorized):
            self.core.authorize_customer(grant.actor)
        with self.assertRaises(Unauthorized):
            self.core.login('a.example', 'alice', PASSWORD)
        with self.assertRaises(InvalidInvite):
            self.core.preview_invite(token, 'a.example')
        with self.assertRaises(InvalidInput):
            self.invite()

    def test_forged_and_cross_core_actors_are_rejected(self):
        grant = self.activate()
        other_tenant = self.core.create_tenant(self.admin, 'b.example')
        for actor in (self.tenant.id, self.admin,
                      CustomerActor(grant.actor.principal_id, self.tenant.id, 'a.example', grant.actor._digest, object()),
                      dataclasses.replace(grant.actor, tenant_id=other_tenant.id),
                      dataclasses.replace(grant.actor, principal_id='forged'),
                      dataclasses.replace(grant.actor, host='b.example')):
            with self.assertRaises(Unauthorized):
                self.core.get_tenant(actor)
        for actor in (grant.actor, AdminActor('operator', object()), self.reopen().admin_actor('trusted-adapter-proof')):
            with self.assertRaises(Unauthorized):
                self.core.create_tenant(actor, 'evil.example')
        with self.assertRaises(Unauthorized):
            self.reopen().authorize_customer(grant.actor)
        with self.assertRaises(Unauthorized):
            self.core.admin_actor('arbitrary-admin-cookie')
        with self.assertRaises(Unauthorized):
            SupportCore(self.path).admin_actor('anything')

    def test_customer_capability_cannot_promote_to_admin_or_change_admin_id(self):
        customer = self.activate().actor
        for forged in (AdminActor('forged', customer._issuer),
                       AdminActor(self.admin.id, customer._issuer),
                       dataclasses.replace(self.admin, id='forged')):
            with self.subTest(identity=forged.id):
                with self.assertRaises(Unauthorized):
                    self.core.create_tenant(forged, 'forged.example')
        # An already-issued second admin identity is not interchangeable either.
        core = SupportCore(self.path, admin_verifier=lambda proof: proof)
        first = core.admin_actor('first')
        second = core.admin_actor('second')
        with self.assertRaises(Unauthorized):
            core.create_tenant(dataclasses.replace(first, id=second.id), 'forged.example')
        core.create_tenant(first, 'first.example')
        core.create_tenant(second, 'second.example')

    def test_restart_persists_hashes_sessions_invites_and_revocation(self):
        grant = self.activate()
        live = self.invite()
        revoked = self.invite()
        self.core.revoke_invite(self.admin, revoked)
        reopened = self.reopen()
        actor = reopened.authenticate_session(grant.token, 'a.example')
        self.assertEqual(reopened.get_tenant(actor), self.tenant)
        reopened.preview_invite(live, 'a.example')
        with self.assertRaises(InvalidInvite):
            reopened.preview_invite(revoked, 'a.example')
        reopened.logout(actor)
        with self.assertRaises(Unauthorized):
            self.reopen().authenticate_session(grant.token, 'a.example')
        self.reopen().login('a.example', 'alice', PASSWORD)

    def test_no_plaintext_secrets_in_db_wal_or_repr(self):
        token = self.invite()
        grant = self.core.redeem_invite(token, 'a.example', 'alice', PASSWORD)
        with self.core._connect() as conn:
            row = conn.execute('SELECT password_hash FROM principals').fetchone()[0]
            self.assertTrue(row.startswith('scrypt$16384$8$1$'))
            self.assertNotIn(PASSWORD, row)
            self.assertEqual(conn.execute('SELECT digest FROM invites').fetchone()[0], hashlib.sha256(token.encode()).hexdigest())
            self.assertEqual(conn.execute('SELECT digest FROM sessions').fetchone()[0], hashlib.sha256(grant.token.encode()).hexdigest())
            self.assertEqual(conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)
            for path in Path(self.temp.name).iterdir():
                if path.is_file():
                    contents = path.read_bytes()
                    for secret in (token, grant.token, PASSWORD):
                        self.assertNotIn(secret.encode(), contents)
        self.assertNotIn(grant.token, repr(grant))
        self.assertNotIn(grant.actor._digest, repr(grant.actor))
        with self.assertRaises(InvalidInvite) as error:
            self.core.preview_invite(token, 'a.example')
        self.assertNotIn(token, str(error.exception))

    def test_scrypt_salts_are_unique(self):
        self.activate('alice')
        self.activate('bob')
        with self.core._connect() as conn:
            hashes = [row[0] for row in conn.execute('SELECT password_hash FROM principals')]
        self.assertNotEqual(*hashes)

    def test_host_lifetime_and_schema_validation(self):
        for host in ('https://a.example', 'a.example:443', 'a.example/', 'a.example.', '-a.example', 'a..example', ' a.example', 'a.example@evil'):
            with self.assertRaises(InvalidInput):
                self.core.create_tenant(self.admin, host)
        with self.assertRaises(Conflict):
            self.core.create_tenant(self.admin, 'A.EXAMPLE')
        for ttl in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(InvalidInput):
                self.invite(ttl=ttl)
        with sqlite3.connect(self.path) as conn:
            conn.execute('PRAGMA user_version=99')
        with self.assertRaises(Conflict):
            self.reopen()


class PasswordPolicyTests(unittest.TestCase):
    """Owner set the floor at 8; the boundary must be enforced exactly."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core = SupportCore(str(Path(self.temp.name) / 'pw.db'),
                                admin_verifier=lambda p: 'operator')
        self.admin = self.core.admin_actor('ok')
        self.tenant = self.core.create_tenant(self.admin, 'a.example')

    def activate(self, password, username='alice'):
        token = self.core.issue_invite(self.admin, self.tenant.id)
        return self.core.redeem_invite(token, 'a.example', username, password)

    def test_eight_bytes_is_accepted_and_seven_is_not(self):
        self.assertEqual(self.activate('12345678').actor.tenant_id, self.tenant.id)
        for short in ('1234567', '', 'a'):
            with self.assertRaises(InvalidInput, msg=short):
                self.activate(short, 'bob')
        # Multi-byte characters are measured in UTF-8 bytes, not code points:
        # three Chinese characters are 9 bytes, two are 6.
        self.assertEqual(self.activate('密碼設定', 'carol').actor.tenant_id, self.tenant.id)
        with self.assertRaises(InvalidInput):
            self.activate('密碼', 'dave')
        with self.assertRaises(InvalidInput):
            self.activate('x' * 1025, 'erin')


class PlatformHostTests(unittest.TestCase):
    """One platform authority serves many tenants whose sites may not exist yet."""

    PLATFORM = 'platform.example'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'platform.db')
        self.clock = Clock()
        self.core = SupportCore(self.path, clock=self.clock, session_ttl=100,
                                admin_verifier=lambda p: 'operator' if p == 'ok' else None)
        self.admin = self.core.admin_actor('ok')
        self.a = self.core.create_tenant(self.admin, 'alpha.example', platform_host=self.PLATFORM)
        self.b = self.core.create_tenant(self.admin, 'beta.example', platform_host=self.PLATFORM)

    def activate(self, tenant, username):
        token = self.core.issue_invite(self.admin, tenant.id)
        return self.core.redeem_invite(token, self.PLATFORM, username, PASSWORD)

    def test_activation_and_login_happen_on_the_platform_not_the_future_site(self):
        grant = self.activate(self.a, 'alice')
        self.assertEqual(grant.actor.host, self.PLATFORM)
        self.assertEqual(grant.actor.tenant_id, self.a.id)
        # The customer's own site host must NOT authenticate: it may not exist.
        for host in ('alpha.example', 'beta.example', 'evil.example'):
            with self.assertRaises((InvalidInvite, Unauthorized), msg=host):
                self.core.login(host, 'alice', PASSWORD)
        again = self.core.login(self.PLATFORM, 'alice', PASSWORD)
        self.assertEqual(again.actor.tenant_id, self.a.id)
        self.assertEqual(self.core.authenticate_session(again.token, self.PLATFORM).tenant_id, self.a.id)
        with self.assertRaises(Unauthorized):
            self.core.authenticate_session(again.token, 'alpha.example')

    def test_same_username_across_tenants_is_refused_by_the_database(self):
        self.activate(self.a, 'alice')
        # Without a platform-scoped constraint this second activation would make
        # login ambiguous and could authenticate the wrong tenant's customer.
        with self.assertRaises(Conflict):
            self.activate(self.b, 'alice')
        self.assertEqual(self.core.login(self.PLATFORM, 'alice', PASSWORD).actor.tenant_id, self.a.id)
        # A different username in the other tenant still works.
        self.assertEqual(self.activate(self.b, 'bob').actor.tenant_id, self.b.id)

    def test_invite_preview_reports_the_future_site_host(self):
        token = self.core.issue_invite(self.admin, self.a.id)
        preview = self.core.preview_invite(token, self.PLATFORM)
        self.assertEqual(preview.site_host, 'alpha.example')
        self.assertEqual(preview.tenant_id, self.a.id)
        with self.assertRaises(InvalidInvite):
            self.core.preview_invite(token, 'alpha.example')

    def test_v1_database_upgrades_without_moving_anyone(self):
        legacy = str(Path(self.temp.name) / 'legacy.db')
        core = SupportCore(legacy, clock=self.clock, admin_verifier=lambda p: 'operator')
        with core._connect() as conn:  # simulate a v1 database
            conn.execute('PRAGMA user_version=1')
        reopened = SupportCore(legacy, clock=self.clock, admin_verifier=lambda p: 'operator')
        admin = reopened.admin_actor('ok')
        tenant = reopened.create_tenant(admin, 'legacy.example')
        # Default platform host stays the site host: no silent authority change.
        self.assertEqual(tenant.platform_host, 'legacy.example')
        token = reopened.issue_invite(admin, tenant.id)
        self.assertEqual(reopened.redeem_invite(token, 'legacy.example', 'alice',
                                                PASSWORD).actor.host, 'legacy.example')


if __name__ == '__main__':
    unittest.main()
