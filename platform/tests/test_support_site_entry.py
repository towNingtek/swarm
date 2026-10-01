"""Single-use site entry tickets: scoped, short-lived, unforgeable."""
import tempfile, unittest
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_site_entry import SiteEntry
from support_site_jobs import SupportSiteJobs

SECRET = 'x' * 48
PLATFORM = 'platform.example'


class SiteEntryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'e.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)
        self.entry = SiteEntry(self.core, entry_secret=SECRET)
        self.tenant = self.core.create_tenant(self.admin, 'acme.example', platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, self.tenant.id)
        self.actor = self.core.redeem_invite(token, PLATFORM, 'alice', 'password-1').actor

    def provision(self):
        job = self.jobs.queue(self.admin, self.tenant.id, 'r1')
        lease = self.jobs.claim(self.worker, job['job_id'])
        self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                       site_port=18042, dns_record_id=None)

    def test_secret_must_be_strong(self):
        for bad in ('', 'short', 'x' * 31, None, 12345):
            with self.assertRaises(InvalidInput):
                SiteEntry(self.core, entry_secret=bad)

    def test_ticket_only_after_real_provisioning(self):
        with self.assertRaises(Conflict):
            self.entry.entry_url(self.actor)     # nothing built yet
        self.jobs.queue(self.admin, self.tenant.id, 'r1')
        with self.assertRaises(Conflict):
            self.entry.entry_url(self.actor)     # queued is not built
        self.provision()
        url = self.entry.entry_url(self.actor)
        self.assertTrue(url.startswith('https://acme.example/auth/enter?ticket='))

    def test_ticket_is_unique_short_lived_and_carries_no_secret(self):
        self.provision()
        first = parse_qs(urlsplit(self.entry.entry_url(self.actor)).query)['ticket'][0]
        second = parse_qs(urlsplit(self.entry.entry_url(self.actor)).query)['ticket'][0]
        self.assertNotEqual(first, second)        # never reuse a nonce
        expiry, nonce, sig = first.split('.')
        import time
        self.assertLessEqual(int(expiry) / 1000 - time.time(), 61)
        self.assertNotIn(SECRET, first)
        self.assertNotIn('password', first.lower())

    def test_ticket_verifies_only_with_that_sites_own_key(self):
        import base64, hashlib, hmac
        from support_site_entry import site_entry_secret

        self.provision()
        expiry, nonce, sig = parse_qs(urlsplit(self.entry.entry_url(self.actor)).query)['ticket'][0].split('.')

        def signed(key):
            mac = hmac.new(key.encode(), f'{expiry}.{nonce}'.encode(), hashlib.sha256).digest()
            return base64.urlsafe_b64encode(mac).decode().rstrip('=')

        own = site_entry_secret(SECRET, 'acme.example')
        self.assertEqual(sig, signed(own))
        # The master secret itself and another site's key do not verify it,
        # and one site's key reveals nothing usable for another site.
        self.assertNotEqual(sig, signed(SECRET))
        self.assertNotEqual(own, site_entry_secret(SECRET, 'other.example'))
        self.assertNotIn(SECRET, own)
        with self.assertRaises(InvalidInput):
            site_entry_secret(SECRET, 'Acme.example')

    def test_disabled_tenant_cannot_enter(self):
        self.provision()
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        # Disabling a tenant also invalidates its sessions, so this is refused
        # even earlier than the provisioning check.
        with self.assertRaises((Conflict, Unauthorized)):
            self.entry.entry_url(self.actor)

    def test_host_change_invalidates_entry(self):
        self.provision()
        with self.core._connect() as conn:
            conn.execute('UPDATE tenants SET site_host=? WHERE id=?',
                         ('moved.example', self.tenant.id))
        # The job still describes the old site: refuse rather than send the
        # customer to a host this platform did not provision.
        with self.assertRaises(Conflict):
            self.entry.entry_url(self.actor)

    def test_other_tenant_gets_its_own_site_only(self):
        self.provision()
        other = self.core.create_tenant(self.admin, 'other.example', platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, other.id)
        bob = self.core.redeem_invite(token, PLATFORM, 'bob', 'password-1').actor
        with self.assertRaises(Conflict):
            self.entry.entry_url(bob)            # bob's site is not provisioned
        self.assertIn('acme.example', self.entry.entry_url(self.actor))


if __name__ == '__main__':
    unittest.main()
