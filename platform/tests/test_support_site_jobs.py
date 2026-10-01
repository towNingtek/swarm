"""Pure temporary-SQLite provisioning intent tests, no executor/infra calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from support_core import SupportCore, Unauthorized, Conflict, InvalidInput
from support_site_jobs import SupportSiteJobs


class SiteJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'jobs.sqlite')
        self.now = [1000.0]
        self.core = SupportCore(self.db, clock=lambda: self.now[0], admin_verifier=lambda a: a if a in ('admin','other') else None)
        self.admin = self.core.admin_actor('admin')
        self.a = self.core.create_tenant(self.admin, 'a.example')
        self.b = self.core.create_tenant(self.admin, 'b.example')
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)
        self.actor = self.customer(self.a)
        self.other = self.customer(self.b)

    def customer(self, tenant):
        invite = self.core.issue_invite(self.admin, tenant.id)
        grant = self.core.redeem_invite(invite, tenant.site_host, 'alice', 'a-long-test-password')
        if tenant.id == self.a.id:
            self.customer_token = grant.token
        return grant.actor

    def queue(self):
        return self.jobs.queue(self.admin, self.a.id, 'req-1')

    def test_request_replay_conflict_and_unique_ownership(self):
        first = self.queue()
        self.assertEqual(first, self.queue())
        self.assertEqual(first, self.jobs.queue(self.admin, self.a.id, 'req-2'))
        self.assertEqual(first['site_host'], 'a.example')
        self.assertEqual(first['site_id'], 'site-' + self.a.id)
        for admin, tenant in [(self.admin, self.b.id),(self.core.admin_actor('other'), self.a.id)]:
            with self.assertRaises(Conflict):
                self.jobs.queue(admin, tenant, 'req-1')
        with self.core._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM support_site_jobs').fetchone()[0], 1)
        lease = self.jobs.claim(self.worker, first['job_id'])
        self.jobs.complete_simulated(self.worker, lease)
        self.assertEqual(self.queue()['status'], 'succeeded_simulated')

    def test_concurrent_queue_and_claim_only_one_winner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            queued = list(pool.map(lambda i:self.jobs.queue(self.admin,self.a.id,'r'+str(i)), range(8)))
        self.assertEqual(len({r['job_id'] for r in queued}), 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            leases = list(pool.map(lambda _:self.jobs.claim(self.worker,queued[0]['job_id']), range(8)))
        self.assertEqual(sum(lease is not None for lease in leases), 1)

    def test_expired_claim_requires_reconciliation_before_retry(self):
        job = self.queue()['job_id']
        old = self.jobs.claim(self.worker, job, lease_seconds=5)
        self.now[0] += 5
        self.assertEqual(self.jobs.customer_status(self.actor)['status'], 'reconciliation_required')
        self.assertIsNone(self.jobs.claim(self.worker, job))
        status = self.jobs.worker_status(self.worker, job)
        self.assertEqual(status['status'], 'reconciliation_required')
        self.assertGreater(status['epoch'], old.epoch)
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker, old)
        with self.assertRaises(Unauthorized):  # worker cannot attest reconciliation
            self.jobs.reconcile(self.worker, job, status['epoch'], outcome='retry_safe')
        with self.assertRaises(Conflict):
            self.jobs.reconcile(self.recovery, job, old.epoch, outcome='retry_safe')
        self.jobs.reconcile(self.recovery, job, status['epoch'], outcome='retry_safe')
        new = self.jobs.claim(self.worker, job)
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker, old)
        self.jobs.complete_simulated(self.worker, new)
        self.assertEqual(self.jobs.customer_status(self.actor), {'status':'succeeded_simulated','simulated':True,'provisioned':False})
        self.assertIsNone(self.jobs.claim(self.worker, job))

    def test_expired_completion_commits_fence_even_when_rejected(self):
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker, job, lease_seconds=1)
        self.now[0] += 2
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker, lease)
        with self.core._connect() as conn:
            row = conn.execute('SELECT status,epoch FROM support_site_jobs').fetchone()
            self.assertEqual(row['status'], 'reconciliation_required')
            self.assertGreater(row['epoch'], lease.epoch)

    def test_reopened_db_preserves_intent_and_requires_new_capability(self):
        job = self.queue()['job_id']
        self.jobs.claim(self.worker, job, lease_seconds=5)
        fresh_core = SupportCore(self.db, clock=lambda:self.now[0])
        readonly = SupportSiteJobs(fresh_core)
        actor = fresh_core.authenticate_session(self.customer_token, 'a.example')
        self.assertEqual(readonly.customer_status(actor), {'status':'running','simulated':False,'provisioned':False})
        with self.assertRaises(Unauthorized):
            readonly.claim(None, job)
        new_worker, new_recovery = object(), object()
        fresh = SupportSiteJobs(fresh_core, worker_capability=new_worker, recovery_capability=new_recovery)
        with self.assertRaises(Unauthorized):
            fresh.claim(self.worker, job)
        self.now[0] += 6
        self.assertIsNone(fresh.claim(new_worker, job))
        state = fresh.worker_status(new_worker, job)
        self.assertEqual(state['status'], 'reconciliation_required')
        fresh.reconcile(new_recovery,job,state['epoch'],outcome='failed')
        self.assertEqual(readonly.customer_status(actor), {'status':'failed','simulated':False,'provisioned':False})

    def test_cross_tenant_capabilities_and_redacted_status(self):
        self.assertEqual(self.jobs.customer_status(self.actor), {'status':'not_requested','simulated':False,'provisioned':False})
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker, job)
        with self.assertRaises(Unauthorized):
            self.jobs.queue(self.actor, self.b.id, 'attacker')
        with self.assertRaises(Unauthorized):
            self.jobs.customer_status(replace(self.actor, tenant_id=self.b.id))
        with self.assertRaises(Unauthorized):
            self.jobs.claim(object(), job)
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker, replace(lease,tenant_id=self.b.id))
        bjob = self.jobs.queue(self.admin,self.b.id,'b')['job_id']
        blease = self.jobs.claim(self.worker,bjob)
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker, replace(lease, job_id=bjob,tenant_id=self.b.id,site_id=blease.site_id,site_host=blease.site_host))
        # Customer view stays redacted: no job/site ids, ports, or DNS evidence.
        self.assertEqual(set(self.jobs.customer_status(self.actor)),
                         {'status','simulated','provisioned'})
        self.assertEqual(self.jobs.customer_status(self.other), {'status':'running','simulated':False,'provisioned':False})
        self.jobs.complete_simulated(self.worker, lease)
        self.assertFalse(self.jobs.customer_status(self.other)['simulated'])

    def test_disabled_tenant_and_revoked_customer_fail_closed(self):
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker,job)
        self.core.logout(self.actor)
        with self.assertRaises(Unauthorized):
            self.jobs.customer_status(self.actor)
        self.core.set_tenant_enabled(self.admin,self.a.id,False)
        for operation in (lambda:self.queue(),lambda:self.jobs.claim(self.worker,job),
                          lambda:self.jobs.complete_simulated(self.worker,lease),
                          lambda:self.jobs.worker_status(self.worker,job)):
            with self.assertRaises(Unauthorized):
                operation()
        # Recovery-only authority may inspect and close a suspended job, never requeue it.
        with self.assertRaises(Unauthorized):
            self.jobs.recovery_status(self.worker,job)
        view = self.jobs.recovery_status(self.recovery,job)
        self.assertEqual((view['status'],view['tenant_enabled'],view['identity_matches']),
                         ('reconciliation_required',False,True))
        with self.assertRaises(Unauthorized):
            self.jobs.reconcile(self.recovery,job,view['epoch'],outcome='retry_safe')
        # Re-enable BEFORE lease expiry: lifecycle bump still fences the old lease.
        self.core.set_tenant_enabled(self.admin,self.a.id,True)
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker,lease)
        self.assertIsNone(self.jobs.claim(self.worker,job))
        state = self.jobs.worker_status(self.worker,job)
        self.assertEqual(state['status'],'reconciliation_required')
        self.jobs.reconcile(self.recovery,job,state['epoch'],outcome='retry_safe')
        self.assertEqual(self.jobs.worker_status(self.worker,job)['status'],'queued')
        # No-op set_tenant_enabled(True) again must not bump lifecycle.
        self.core.set_tenant_enabled(self.admin,self.a.id,True)
        self.assertEqual(self.jobs.worker_status(self.worker,job)['status'],'queued')

    def test_suspension_voids_simulated_success_and_customer_view(self):
        job = self.queue()['job_id']
        self.jobs.complete_simulated(self.worker,self.jobs.claim(self.worker,job))
        self.assertEqual(self.jobs.customer_status(self.actor)['status'],'succeeded_simulated')
        self.core.set_tenant_enabled(self.admin,self.a.id,False)
        self.core.set_tenant_enabled(self.admin,self.a.id,True)
        self.assertEqual(self.jobs.customer_status(self.actor)['status'],'reconciliation_required')
        self.assertEqual(self.jobs.worker_status(self.worker,job)['status'],'reconciliation_required')

    def test_schema_v1_upgrade_invalidates_prior_intent(self):
        job = self.queue()['job_id']
        self.jobs.complete_simulated(self.worker,self.jobs.claim(self.worker,job))
        with self.core._connect() as conn:
            # Rebuild the table exactly as schema v1 (no lifecycle column) with
            # the completed row copied over, then mark the version as 1.
            conn.execute('BEGIN IMMEDIATE')
            requests = conn.execute('SELECT * FROM support_site_job_requests').fetchall()
            rows = conn.execute('''SELECT tenant_id,job_id,site_id,site_host,status,epoch,lease_until,
                lease_digest,error_code,created_at,updated_at FROM support_site_jobs''').fetchall()
            conn.execute('DROP TABLE support_site_job_requests')
            conn.execute('DROP TABLE support_site_jobs')
            conn.execute('''CREATE TABLE support_site_jobs (
                tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                job_id TEXT NOT NULL UNIQUE, site_id TEXT NOT NULL UNIQUE,
                site_host TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                epoch INTEGER NOT NULL DEFAULT 0, lease_until REAL, lease_digest TEXT,
                error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
            conn.execute('''CREATE TABLE support_site_job_requests (
                request_id TEXT PRIMARY KEY, admin_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL REFERENCES support_site_jobs(tenant_id))''')
            conn.executemany('INSERT INTO support_site_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                             [tuple(r) for r in rows])
            conn.executemany('INSERT INTO support_site_job_requests VALUES (?,?,?)',
                             [tuple(r) for r in requests])
            conn.execute('DELETE FROM support_site_jobs_version')
            conn.execute('INSERT INTO support_site_jobs_version VALUES (1)')
        upgraded = SupportSiteJobs(SupportCore(self.db, clock=lambda:self.now[0]),
                                   worker_capability=self.worker, recovery_capability=self.recovery)
        self.assertEqual(upgraded.worker_status(self.worker,job)['status'],'reconciliation_required')
        with self.assertRaises(InvalidInput):
            SupportSiteJobs(self.core, worker_capability=self.worker, recovery_capability=self.worker)

    def test_safe_errors_and_partial_effect_failure_reconciliation(self):
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker,job)
        for value in ('secret-password-123',RuntimeError('secret'),None):
            with self.assertRaises(InvalidInput):
                self.jobs.fail(self.worker,lease,value)
        state = self.jobs.fail(self.worker,lease,'executor_failed')
        self.assertEqual(state['status'],'reconciliation_required')
        self.assertIsNone(self.jobs.claim(self.worker,job))
        with self.assertRaises(Conflict):
            self.jobs.complete_simulated(self.worker,lease)
        self.jobs.reconcile(self.recovery,job,state['epoch'],outcome='failed')
        self.assertEqual(self.jobs.worker_status(self.worker,job)['error_code'],'reconciliation_failed')
        with self.core._connect() as conn:
            data = str(list(conn.iterdump()))
        self.assertNotIn(lease.token,data)
        self.assertNotIn('secret-password-123',data)
        self.assertNotIn('ready_real',data)

    def test_read_only_handle_disables_every_worker_transition(self):
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker,job)
        readonly = SupportSiteJobs(self.core)
        for operation in (
            lambda:readonly.claim(None,job),
            lambda:readonly.worker_status(None,job),
            lambda:readonly.complete_simulated(None,lease),
            lambda:readonly.fail(None,lease,'executor_failed'),
            lambda:readonly.reconcile(None,job,lease.epoch,outcome='retry_safe'),
        ):
            with self.assertRaises(Unauthorized):
                operation()
        self.assertEqual(readonly.customer_status(self.actor)['status'],'running')

    def test_concurrent_reconciliation_fences_second_operator(self):
        job = self.queue()['job_id']
        lease = self.jobs.claim(self.worker,job)
        state = self.jobs.fail(self.worker,lease,'executor_failed')
        def reconcile(_):
            try:
                self.jobs.reconcile(self.recovery,job,state['epoch'],outcome='retry_safe')
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reconcile,range(2))),1)
        self.assertEqual(self.jobs.worker_status(self.worker,job)['status'],'queued')

    def test_invalid_inputs_and_site_host_mutation(self):
        with self.assertRaises(InvalidInput):
            SupportSiteJobs(self.core,worker_capability='browser-token')
        with self.assertRaises(InvalidInput):
            self.jobs.queue(self.admin,self.a.id,'\nsecret')
        job = self.queue()['job_id']
        for value in (True,0,-1,float('inf'),float('nan'),3601):
            with self.assertRaises(InvalidInput):
                self.jobs.claim(self.worker,job,lease_seconds=value)
        with self.core._connect() as conn:
            conn.execute('UPDATE tenants SET site_host=? WHERE id=?',('changed.example',self.a.id))
        with self.assertRaises(Conflict):
            self.jobs.claim(self.worker,job)


if __name__ == '__main__':
    unittest.main()
