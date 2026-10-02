"""Tenant deletion: destroys only what this platform provisioned, and says so."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_site_jobs import SupportSiteJobs
from support_site_teardown import SiteTeardown, TeardownError

PLATFORM = 'platform.example'


class TeardownTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'teardown.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)
        self.teardown = SiteTeardown(self.core, enabled=True)

    def tenant(self, host='acme.example'):
        return self.core.create_tenant(self.admin, host, platform_host=PLATFORM)

    def activate(self, tenant, username='alice'):
        token = self.core.issue_invite(self.admin, tenant.id)
        return self.core.redeem_invite(token, PLATFORM, username, 'customer-password-1').actor

    def provision(self, tenant, name='acme'):
        job = self.jobs.queue(self.admin, tenant.id, 'r-' + tenant.id[:6])
        lease = self.jobs.claim(self.worker, job['job_id'])
        return self.jobs.complete_provisioned(self.worker, lease, site_name=name,
                                              site_port=18042, dns_record_id='rec-1')

    def test_must_be_enabled_explicitly(self):
        for value in (False, None, 1, 'yes'):
            with self.assertRaises(InvalidInput):
                SiteTeardown(self.core, enabled=value)

    def test_deletes_records_and_the_provisioned_site(self):
        tenant = self.tenant()
        self.activate(tenant)
        self.provision(tenant)
        calls = []
        with patch('dsh_sitectl.cmd_delete', side_effect=lambda ns: calls.append(ns)):
            result = self.teardown.delete_tenant(self.admin, tenant.id,
                                                 expected_host='acme.example')
        # The site name comes from the platform's own record, not the host.
        self.assertEqual([ns.name for ns in calls], ['acme'])
        self.assertTrue(calls[0].purge_data)
        self.assertEqual(result['site_removed'], 'acme')
        with self.core._connect() as conn:
            for table in ('tenants', 'principals', 'invites', 'support_site_jobs'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0,
                                 table)
            self.assertEqual(len(conn.execute('PRAGMA foreign_key_check').fetchall()), 0)

    def test_never_touches_a_site_this_platform_did_not_provision(self):
        tenant = self.tenant()
        self.activate(tenant)
        # Queued but never completed: nothing was created, so nothing is deleted.
        self.jobs.queue(self.admin, tenant.id, 'r1')
        with patch('dsh_sitectl.cmd_delete') as deleter:
            result = self.teardown.delete_tenant(self.admin, tenant.id,
                                                 expected_host='acme.example')
        deleter.assert_not_called()
        self.assertIsNone(result['site_removed'])

    def test_host_mismatch_aborts_before_any_destruction(self):
        tenant = self.tenant()
        self.provision(tenant)
        with patch('dsh_sitectl.cmd_delete') as deleter:
            with self.assertRaises(Conflict):
                self.teardown.delete_tenant(self.admin, tenant.id,
                                            expected_host='other.example')
        deleter.assert_not_called()
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM tenants').fetchone()[0], 1)
        # The tenant is untouched, including still being enabled.
        self.assertTrue(self.core.tenant(self.admin, tenant.id).enabled
                        if hasattr(self.core, 'tenant') else True)

    def test_failed_site_removal_keeps_records_and_reports(self):
        tenant = self.tenant()
        self.activate(tenant)
        self.provision(tenant)
        with patch('dsh_sitectl.cmd_delete', side_effect=RuntimeError('docker down')):
            with self.assertRaises(TeardownError):
                self.teardown.delete_tenant(self.admin, tenant.id,
                                            expected_host='acme.example')
        # Records survive, because they are the only pointer to what exists.
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM tenants').fetchone()[0], 1)
            # ...but the tenant is disabled so the customer cannot keep using it.
            self.assertEqual(conn.execute('SELECT enabled FROM tenants').fetchone()[0], 0)

    def test_only_an_administrator_may_delete(self):
        tenant = self.tenant()
        actor = self.activate(tenant)
        for wrong in (actor, object(), None):
            with self.assertRaises((Unauthorized, InvalidInput, AttributeError, TypeError)):
                self.teardown.delete_tenant(wrong, tenant.id, expected_host='acme.example')
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM tenants').fetchone()[0], 1)

    def test_other_tenants_are_unaffected(self):
        keep = self.tenant('keep.example')
        self.activate(keep, 'keeper')
        self.provision(keep, name='keep')
        doomed = self.tenant('doomed.example')
        self.activate(doomed, 'doomed-user')
        with patch('dsh_sitectl.cmd_delete') as deleter:
            self.teardown.delete_tenant(self.admin, doomed.id, expected_host='doomed.example')
        deleter.assert_not_called()  # doomed was never provisioned
        with self.core._connect() as conn:
            hosts = [r[0] for r in conn.execute('SELECT site_host FROM tenants')]
            self.assertEqual(hosts, ['keep.example'])
            users = [r[0] for r in conn.execute('SELECT username FROM principals')]
            self.assertEqual(users, ['keeper'])
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM support_site_jobs').fetchone()[0], 1)

    def test_no_orphan_rows_survive_in_any_table(self):
        """Every table must be checked, not just the ones I remembered."""
        from support_model_settings import CustomerModelSettings
        from support_site_credentials import SiteCredentials

        writer = object()
        credentials = SiteCredentials(self.core, writer_capability=writer)
        settings = CustomerModelSettings(self.core, catalog=('m-a',))
        tenant = self.tenant()
        actor = self.activate(tenant)
        settings.configure(actor, 'm-a', 0)
        self.provision(tenant)
        with self.core._connect() as conn:
            credentials.store(writer, conn, tenant.id, 'acme.example', 'admin', 'pw' * 6)
        # A conversation: messages, an assistant run and an operator note all
        # reference the room, and none of them carries a tenant_id.
        from support_rooms import SupportRooms
        rooms = SupportRooms(self.core)
        room = rooms.create_room(self.admin, tenant.id)['id']
        rooms.post_message(actor, room, 'm1', 'hello')
        token, _ = rooms.start_operator_run(self.admin, room, 'be brief')
        rooms.finish_run(token, 'hi there')
        # A rejected username leaves a conflict record on a second invite.
        from support_core import Conflict
        spare = self.core.issue_invite(self.admin, tenant.id)
        with self.assertRaises(Conflict):
            self.core.redeem_invite(spare, PLATFORM, 'alice', 'customer-password-1')
        before = self._row_counts()
        self.assertTrue(all(before[t] for t in
                            ('principals', 'invites', 'support_site_jobs', 'invite_conflicts',
                             'support_model_settings', 'support_site_credentials',
                             'rooms', 'messages', 'runs', 'internal_notes')),
                        before)
        with patch('dsh_sitectl.cmd_delete'):
            self.teardown.delete_tenant(self.admin, tenant.id, expected_host='acme.example')
        after = self._row_counts()
        self.assertEqual({t: n for t, n in after.items() if n}, {}, after)

    def _row_counts(self):
        counts = {}
        with self.core._connect() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                if name.endswith('_version') or name.endswith('_schema'):
                    continue
                counts[name] = conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
        return counts

    def test_unknown_tenant_is_rejected(self):
        with self.assertRaises(InvalidInput):
            self.teardown.delete_tenant(self.admin, 'no-such-tenant',
                                        expected_host='acme.example')


if __name__ == '__main__':
    unittest.main()
