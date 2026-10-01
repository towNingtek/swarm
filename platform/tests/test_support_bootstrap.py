"""Offline SQLite bootstrap acceptance; no services or network."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from support_bootstrap import SupportBootstrap
from support_core import SupportCore, Conflict, InvalidInput, Unauthorized
from support_quota import SupportQuota, QuotaDenied
from support_rooms import SupportRooms


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / 'test.sqlite')
        self.core = SupportCore(self.path, admin_verifier=lambda x: x if x in ('admin', 'other') else None)
        self.admin = self.core.admin_actor('admin')
        self.rooms, self.quota = SupportRooms(self.core), SupportQuota(self.core)
        self.bootstrap = SupportBootstrap(self.core, self.rooms, self.quota)
        self.body = {'client_request_id': 'create-1', 'host': 'NEW.example'}

    def counts(self):
        with self.core._connect() as conn:
            return [conn.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in
                    ('tenants', 'rooms', 'support_quota_policies', 'support_bootstrap_requests')]

    def test_default_disabled_policy_and_stable_replay_after_mutation_reopen(self):
        result = self.bootstrap.create(self.admin, self.body)
        self.assertTrue(result['metadata_only'])
        self.assertEqual(result['provisioning'], 'not_provisioned')
        self.assertEqual(result['tenant']['host'], 'new.example')
        self.assertTrue(result['tenant']['enabled'])
        self.assertEqual(result['policy'], {'mode': 'disabled', 'monthly_limit': None})
        grant = self.core.redeem_invite(self.core.issue_invite(self.admin, result['tenant']['id']),
                                      'new.example', 'alice', 'test-password-long')
        with self.assertRaises(QuotaDenied):
            self.quota.reserve(grant.actor, 'disabled', 1)
        self.quota.set_policy(self.admin, result['tenant']['id'], 'unlimited')
        self.rooms.set_mode(self.admin, result['room']['id'], 'human', expected_epoch=0)
        self.core.set_tenant_enabled(self.admin, result['tenant']['id'], False)
        core = SupportCore(self.path, admin_verifier=lambda x: 'admin')
        bootstrap = SupportBootstrap(core, SupportRooms(core), SupportQuota(core))
        self.assertEqual(bootstrap.create(core.admin_actor(None), {**self.body, 'host': 'new.example',
                         'room_mode': 'ai', 'policy': {'mode': 'disabled'}}), result)
        self.assertEqual(self.counts(), [1, 1, 1, 1])

    def test_all_midtransaction_failures_rollback_and_retry(self):
        for stage in ('before_room', 'before_policy', 'before_receipt', 'before_commit'):
            with self.subTest(stage=stage):
                def fail(current):
                    if current == stage:
                        raise RuntimeError('fault')
                with patch.object(self.bootstrap, '_checkpoint', side_effect=fail):
                    with self.assertRaises(RuntimeError):
                        self.bootstrap.create(self.admin, self.body)
                self.assertEqual(self.counts(), [0, 0, 0, 0])
        self.bootstrap.create(self.admin, self.body)
        self.assertEqual(self.counts(), [1, 1, 1, 1])

    def test_two_independent_cores_thread_race(self):
        barrier = threading.Barrier(2)
        def create(_):
            core = SupportCore(self.path, admin_verifier=lambda _: 'admin')
            bootstrap = SupportBootstrap(core, SupportRooms(core), SupportQuota(core))
            actor = core.admin_actor(None)
            barrier.wait(timeout=10)
            return bootstrap.create(actor, self.body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.counts(), [1, 1, 1, 1])

    def test_payload_host_and_admin_conflicts(self):
        result = self.bootstrap.create(self.admin, self.body)
        for changed in ({'host': 'different.example'}, {'room_mode': 'human'},
                        {'policy': {'mode': 'unlimited'}}, {'client_request_id': 'different'}):
            with self.assertRaises(Conflict):
                self.bootstrap.create(self.admin, {**self.body, **changed})
        with self.assertRaises(Conflict):
            self.bootstrap.create(self.core.admin_actor('other'), self.body)
        self.assertEqual(self.bootstrap.create(self.admin, self.body), result)
        self.assertEqual(self.counts(), [1, 1, 1, 1])

    def test_admin_revalidated_customers_and_forgery_denied_even_on_replay(self):
        result = self.bootstrap.create(self.admin, self.body)
        customer = self.core.redeem_invite(self.core.issue_invite(self.admin, result['tenant']['id']),
                                         'new.example', 'alice', 'test-password-long').actor
        other = self.core.create_tenant(self.admin, 'other.example')
        foreign = self.core.redeem_invite(self.core.issue_invite(self.admin, other.id),
                                        'other.example', 'bob', 'test-password-long').actor
        for actor in (None, customer, foreign, replace(self.admin, id='forged')):
            for body in (self.body, {**self.body, 'client_request_id': 'new', 'host': 'third.example'}):
                with self.assertRaises(Unauthorized):
                    self.bootstrap.create(actor, body)
        with patch.object(self.core, '_admin', side_effect=Unauthorized):
            with self.assertRaises(Unauthorized):
                self.bootstrap.create(self.admin, self.body)

    def test_explicit_unlimited_capped_and_invalid_payload(self):
        for index, policy in enumerate(({'mode': 'unlimited'}, {'mode': 'capped', 'monthly_limit': 0})):
            result = self.bootstrap.create(self.admin, {'client_request_id': str(index),
                                           'host': f'{index}.example', 'policy': policy})
            self.assertEqual(result['policy']['mode'], policy['mode'])
        for change in ({'host': 'https://bad.example'}, {'host': 'bad.example:443'},
                       {'host': 'bad.example/path'}, {'host': 'bad.example.'}, {'host': None},
                       {'room_mode': []}, {'enabled': True}, {'client_request_id': ''},
                       {'policy': {'mode': 'capped', 'monthly_limit': True}},
                       {'policy': {'mode': 'capped', 'monthly_limit': 1.0}},
                       {'policy': {'mode': 'capped', 'monthly_limit': -1}},
                       {'policy': {'mode': 'capped', 'monthly_limit': 10**12 + 1}},
                       {'policy': {'mode': 'unlimited', 'monthly_limit': None}}, {'policy': None}):
            with self.assertRaises(InvalidInput):
                self.bootstrap.create(self.admin, {**self.body, **change})
        self.assertEqual(self.counts(), [2, 2, 2, 2])

    def test_no_existing_schema_recreation_and_version_guard(self):
        statements = []
        original = self.core._connect
        from contextlib import contextmanager
        @contextmanager
        def traced():
            with original() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        with patch.object(self.core, '_connect', traced):
            SupportBootstrap(self.core, self.rooms, self.quota)
        ddl = [s for s in statements if s.startswith(('CREATE', 'ALTER', 'DROP'))]
        self.assertTrue(all('support_bootstrap_' in s for s in ddl))
        with self.core._connect() as conn:
            conn.execute('UPDATE support_bootstrap_schema SET version=99')
        with self.assertRaises(Conflict):
            SupportBootstrap(self.core, self.rooms, self.quota)

    def test_missing_prerequisite_does_not_recreate_schema(self):
        import sqlite3
        with self.core._connect() as conn:
            conn.execute('DROP TABLE support_quota_policies')
        with self.assertRaises(sqlite3.OperationalError):
            SupportBootstrap(self.core, self.rooms, self.quota)
        with self.core._connect() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='support_quota_policies'").fetchone())


if __name__ == '__main__':
    unittest.main()
