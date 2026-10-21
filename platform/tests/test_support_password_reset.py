"""swarm#580: operator-issued password reset links."""
import os, tempfile, unittest, warnings
warnings.filterwarnings('ignore')
from support_core import SupportCore, InvalidInput, SupportError, Unauthorized


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now = [1_800_000_000.0]
        self.core = SupportCore(os.path.join(self.tmp.name, 'db.sqlite'), clock=lambda: self.now[0],
                                admin_verifier=lambda v: 'admin' if v == 't' else None)
        self.admin = self.core.admin_actor('t')
        self.t = self.core.create_tenant(self.admin, 'acme.example.cc', platform_host='p.example.cc')
        self.other = self.core.create_tenant(self.admin, 'beta.example.cc', platform_host='p.example.cc')
        g = self.core.redeem_invite(self.core.issue_invite(self.admin, self.t.id), 'p.example.cc', 'alice', 'old-password-1')
        self.old_session = g.token
        self.acct = self.core.list_accounts(self.admin, self.t.id)[0]

    def reset(self, ttl=3600):
        return self.core.issue_password_reset(self.admin, self.t.id, self.acct['id'], ttl=ttl)

    def test_accounts_list_shows_names_only(self):
        a = self.core.list_accounts(self.admin, self.t.id)
        self.assertEqual([x['username'] for x in a], ['alice'])
        self.assertNotIn('password_hash', a[0]); self.assertFalse(any('scrypt' in str(v) for v in a[0].values()))
        self.assertEqual(self.core.list_accounts(self.admin, self.other.id), [])

    def test_reset_sets_new_password_and_ends_old_sessions(self):
        tok = self.reset()
        self.assertEqual(self.core.preview_password_reset(tok, 'p.example.cc'), 'alice')
        grant = self.core.redeem_password_reset(tok, 'p.example.cc', 'new-password-2')
        self.assertTrue(grant.token)
        with self.assertRaises(Unauthorized):
            self.core.authenticate_session(self.old_session, 'p.example.cc')      # old session ended
        with self.assertRaises(Unauthorized):
            self.core.login('p.example.cc', 'alice', 'old-password-1')            # old password refused
        self.core.login('p.example.cc', 'alice', 'new-password-2')                # new one works

    def test_single_use(self):
        tok = self.reset()
        self.core.redeem_password_reset(tok, 'p.example.cc', 'new-password-2')
        with self.assertRaises(SupportError):
            self.core.redeem_password_reset(tok, 'p.example.cc', 'another-password-3')

    def test_expires(self):
        tok = self.reset(ttl=60)
        self.now[0] += 61
        with self.assertRaises(SupportError):
            self.core.redeem_password_reset(tok, 'p.example.cc', 'new-password-2')

    def test_other_links_for_the_account_are_voided(self):
        a, b = self.reset(), self.reset()
        self.core.redeem_password_reset(a, 'p.example.cc', 'new-password-2')
        with self.assertRaises(SupportError):
            self.core.redeem_password_reset(b, 'p.example.cc', 'another-password-3')

    def test_wrong_platform_host_refused(self):
        tok = self.reset()
        with self.assertRaises(SupportError):
            self.core.redeem_password_reset(tok, 'evil.example.cc', 'new-password-2')

    def test_only_admin_issues_and_only_for_that_tenant(self):
        with self.assertRaises(SupportError):
            self.core.issue_password_reset(object(), self.t.id, self.acct['id'])
        with self.assertRaises(InvalidInput):
            self.core.issue_password_reset(self.admin, self.other.id, self.acct['id'])  # account of another tenant

    def test_short_password_refused(self):
        tok = self.reset()
        with self.assertRaises(Exception):
            self.core.redeem_password_reset(tok, 'p.example.cc', 'short')
        self.core.redeem_password_reset(tok, 'p.example.cc', 'long-enough-1')   # link still usable after a refused try

    def test_only_digest_stored(self):
        tok = self.reset()
        with self.core._connect() as c:
            rows = c.execute('SELECT * FROM password_resets').fetchall()
        self.assertFalse(any(tok in str(tuple(r)) for r in rows))


if __name__ == '__main__':
    unittest.main()
