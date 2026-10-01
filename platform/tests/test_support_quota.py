"""Pure local tests: no providers, credentials, network, or live model."""
import concurrent.futures
import dataclasses
import tempfile
import threading
import unittest
from datetime import datetime, timezone

from support_core import SupportCore, Unauthorized, Conflict, InvalidInput
from support_quota import SupportQuota, QuotaDenied


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
        self.core = SupportCore(self.tmp.name + '/test.db', clock=lambda: self.now,
                                admin_verifier=lambda assertion: 'admin' if assertion == 'ok' else None,
                                session_ttl=10000000)
        self.admin = self.core.admin_actor('ok')
        self.tenant = self.core.create_tenant(self.admin, 'acme.test', quota_mode='unlimited')
        self.actor = self.customer(self.tenant, 'alice')
        self.other_tenant = self.core.create_tenant(self.admin, 'other.test')
        self.other = self.customer(self.other_tenant, 'bob')
        self.q = SupportQuota(self.core)

    def customer(self, tenant, name):
        invite = self.core.issue_invite(self.admin, tenant.id)
        return self.core.redeem_invite(invite, tenant.site_host, name, 'test-password-long').actor

    def cap(self, limit=100):
        self.q.set_policy(self.admin, self.tenant.id, 'capped', limit)

    def test_disabled_default_metadata_and_admin_auth(self):
        with self.assertRaises(QuotaDenied):
            self.q.reserve(self.actor, 'a', 1)
        with self.assertRaises(Unauthorized):
            self.q.set_policy(self.actor, self.tenant.id, 'unlimited')
        self.q.set_policy(self.admin, self.tenant.id, 'unlimited')
        r = self.q.reserve(self.actor, 'a', 1000)
        self.q.set_policy(self.admin, self.tenant.id, 'disabled')
        with self.assertRaises(QuotaDenied):
            self.q.reserve(self.actor, 'b', 1)
        self.assertEqual(self.q.cancel(self.actor, r), 'cancelled')
        self.q.set_policy(self.admin, self.tenant.id, 'unlimited')
        self.core.set_tenant_enabled(self.admin, self.tenant.id, False)
        with self.assertRaises(Unauthorized):
            self.q.reserve(self.actor, 'c', 1)

    def test_retries_conflicts_digest_and_no_double_charge(self):
        self.cap()
        r = self.q.reserve(self.actor, 'req', 80)
        self.assertEqual(r, self.q.reserve(self.actor, 'req', 80, reservation=r))
        with self.assertRaises(Conflict):
            self.q.reserve(self.actor, 'req', 80)
        with self.assertRaises(Conflict):
            self.q.reserve(self.actor, 'req', 81, reservation=r)
        with self.core._connect() as conn:
            row = conn.execute('SELECT * FROM support_quota_ledger').fetchone()
            self.assertNotIn(r.token, tuple(row))
        self.assertEqual(self.q.settle(self.actor, r, 60), 'settled')
        self.assertEqual(self.q.settle(self.actor, r, 60), 'settled')
        with self.assertRaises(Conflict):
            self.q.settle(self.actor, r, 59)
        with self.assertRaises(Conflict):
            self.q.reserve(self.actor, 'req', 80, reservation=r)
        self.q.reserve(self.actor, 'remaining', 40)
        with self.assertRaises(QuotaDenied):
            self.q.reserve(self.actor, 'over', 1)

    def test_unknown_overage_cancel_and_finalization_conflicts(self):
        self.cap()
        r = self.q.reserve(self.actor, 'unknown', 100)
        self.assertEqual(self.q.settle(self.actor, r, None), 'held')
        self.assertEqual(self.q.settle(self.actor, r, None), 'held')
        with self.assertRaises(Conflict):
            self.q.settle(self.actor, r, 101)
        with self.assertRaises(QuotaDenied):
            self.q.reserve(self.actor, 'blocked', 1)
        self.assertEqual(self.q.cancel(self.actor, r), 'cancelled')
        self.assertEqual(self.q.cancel(self.actor, r), 'cancelled')
        with self.assertRaises(Conflict):
            self.q.settle(self.actor, r, 0)
        self.q.reserve(self.actor, 'freed', 100)

    def test_tenant_principal_capability_and_session_boundaries(self):
        self.cap()
        r = self.q.reserve(self.actor, 'a', 10)
        peer = self.customer(self.tenant, 'peer')
        for actor in (self.other, peer, dataclasses.replace(self.actor, tenant_id=self.other_tenant.id)):
            with self.assertRaises(Unauthorized):
                self.q.cancel(actor, r)
        with self.assertRaises(Unauthorized):
            self.q.settle(self.actor, 'A' * 43, 1)
        self.core.logout(self.actor)
        for action in (lambda: self.q.cancel(self.actor, r),
                       lambda: self.q.reserve(self.actor, 'a', 10, reservation=r)):
            with self.assertRaises(Unauthorized):
                action()

    def test_month_bucket_and_global_outstanding(self):
        self.cap()
        self.q = SupportQuota(self.core, max_outstanding=2)
        jan = self.q.reserve(self.actor, 'jan', 100)
        self.now = datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp()
        feb = self.q.reserve(self.actor, 'feb', 100)
        self.assertEqual((jan.month, feb.month), ('2026-01', '2026-02'))
        self.q.set_policy(self.admin, self.tenant.id, 'unlimited')
        with self.assertRaises(QuotaDenied):
            self.q.reserve(self.actor, 'outstanding', 1)
        self.q.settle(self.actor, jan, 50)
        self.q.reserve(self.actor, 'new-slot', 1)

    def test_atomic_race_budget_across_instances(self):
        self.cap(100)
        barrier = threading.Barrier(12)
        ledgers = [SupportQuota(self.core) for _ in range(12)]
        def reserve(index):
            barrier.wait()
            try:
                return ledgers[index].reserve(self.actor, str(index), 30)
            except QuotaDenied:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(r is not None for r in results), 3)
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT sum(estimate) FROM support_quota_ledger').fetchone()[0], 90)

    def test_outstanding_and_finish_races(self):
        self.q = SupportQuota(self.core, max_outstanding=1)
        self.q.set_policy(self.admin, self.tenant.id, 'unlimited')
        barrier = threading.Barrier(6)
        def admit(index):
            barrier.wait()
            try:
                return self.q.reserve(self.actor, 'race-' + str(index), 10)
            except QuotaDenied:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            grants = [r for r in pool.map(admit, range(6)) if r]
        self.assertEqual(len(grants), 1)
        barrier = threading.Barrier(2)
        def finish(cancel):
            barrier.wait()
            try:
                return self.q.cancel(self.actor, grants[0]) if cancel else self.q.settle(self.actor, grants[0], 7)
            except Conflict:
                return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, (True, False)))
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(sum(r in ('cancelled', 'settled') for r in results), 1)

    def test_validation_and_persistence(self):
        self.cap()
        for value in (True, 0, -1, 1.5, 10**20):
            with self.assertRaises(InvalidInput):
                self.q.reserve(self.actor, 'bad', value)
        r = self.q.reserve(self.actor, 'persistent', 100)
        reopened = SupportQuota(self.core)
        with self.assertRaises(QuotaDenied):
            reopened.reserve(self.actor, 'no-more', 1)
        self.assertEqual(reopened.settle(self.actor, r, 100), 'settled')


if __name__ == '__main__':
    unittest.main()
