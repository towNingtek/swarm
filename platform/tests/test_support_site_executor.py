"""Real provisioning executor: opt-in, capability-gated, fails to reconciliation.

No test here performs real provisioning. `cmd_create` is replaced by an explicit
double so the state machine and the refusal rules can be exercised offline.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support_core import Conflict, InvalidInput, SupportCore, Unauthorized
from support_site_executor import ProvisioningDisabled, SiteProvisioner
from support_site_jobs import SupportSiteJobs


class SiteExecutorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'exec.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.worker, self.recovery = object(), object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=self.recovery)

    def provisioner(self, **kw):
        return SiteProvisioner(self.jobs, self.worker, enabled=True,
                               domain='example.test', **kw)

    def tenant(self, host):
        return self.core.create_tenant(self.admin, host)

    def test_provisioning_is_disabled_unless_explicitly_enabled(self):
        for value in (False, None, 0, 'yes', 1):
            with self.assertRaises(ProvisioningDisabled):
                SiteProvisioner(self.jobs, self.worker, enabled=value, domain='example.test')
        with self.assertRaises(InvalidInput):
            SiteProvisioner(self.jobs, 'not-a-capability', enabled=True, domain='example.test')
        with self.assertRaises(InvalidInput):
            SiteProvisioner({'jobs': 1}, self.worker, enabled=True, domain='example.test')

    def test_refuses_hosts_outside_the_configured_domain(self):
        p = self.provisioner()
        self.assertEqual(p.site_name_for('acme.example.test'), 'acme')
        for bad in ('localhost', 'acme.evil.test', 'example.test', 'a.b.example.test',
                    'ACME.example.test', '', None, 'acme.example.test.evil'):
            with self.assertRaises((InvalidInput, ValueError), msg=bad):
                p.site_name_for(bad)
        # Reserved labels are refused by the shared name validator.
        for reserved in ('admin.example.test', 'api.example.test', 'auth.example.test'):
            with self.assertRaises(ValueError):
                p.site_name_for(reserved)

    def test_onboarding_steps_follow_real_provisioning_state(self):
        """Regression: steps were hardcoded 'blocked' even after a real build."""
        from support_onboarding import SupportOnboarding

        tenant = self.core.create_tenant(self.admin, 'acme.example.test',
                                         platform_host='platform.example.test')
        invite = self.core.issue_invite(self.admin, tenant.id)
        actor = self.core.redeem_invite(invite, 'platform.example.test', 'alice',
                                        'test-password-long').actor
        onboarding = SupportOnboarding(self.core)

        def site_step():
            return next(s for s in onboarding.status(actor)['steps']
                        if s['id'] == 'site_preparation')

        self.assertEqual(site_step()['status'], 'pending')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        # Queued work is already under way from the customer's point of view.
        self.assertEqual(site_step()['status'], 'in_progress')
        lease = self.jobs.claim(self.worker, job['job_id'])
        self.assertEqual(site_step()['status'], 'in_progress')
        self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                       site_port=18042, dns_record_id=None)
        self.assertEqual(site_step()['status'], 'complete')
        # A complete site still does not mean the customer may start work.
        self.assertFalse(onboarding.status(actor)['ready_for_work'])

    def test_customer_on_a_separate_platform_host_can_see_site_status(self):
        """Regression: the customer authenticates on the platform, not the site."""
        tenant = self.core.create_tenant(self.admin, 'acme.example.test',
                                         platform_host='platform.example.test')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        invite = self.core.issue_invite(self.admin, tenant.id)
        actor = self.core.redeem_invite(invite, 'platform.example.test', 'alice',
                                        'test-password-long').actor
        self.assertEqual(actor.host, 'platform.example.test')
        # Must NOT raise merely because session host != site host.
        self.assertEqual(self.jobs.customer_status(actor)['status'], 'queued')
        lease = self.jobs.claim(self.worker, job['job_id'])
        self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                       site_port=18042, dns_record_id=None)
        view = self.jobs.customer_status(actor)
        self.assertEqual(view['status'], 'succeeded_provisioned')
        self.assertEqual(view['site_host'], 'acme.example.test')
        # A genuine identity drift is still detected.
        with self.core._connect() as conn:
            conn.execute('UPDATE tenants SET site_host=? WHERE id=?',
                         ('moved.example.test', tenant.id))
        with self.assertRaises(Conflict):
            self.jobs.customer_status(actor)

    def test_successful_provision_records_real_evidence_not_simulation(self):
        tenant = self.tenant('acme.example.test')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        p = self.provisioner()
        created = {}

        def fake_create(namespace):
            created['name'] = namespace.name
            created['engine'] = namespace.engine

        with patch.object(p, '_provision', side_effect=lambda lease: (
                fake_create(type('N', (), {'name': 'acme', 'engine': 'dsh'})()),
                ('acme', 18042, 'rec-abc', 'generated-site-password'))[1]):
            result = p.provision_one(job['job_id'])
        self.assertEqual(result['status'], 'succeeded_provisioned')
        self.assertTrue(result['provisioned'])
        self.assertFalse(result['simulated'])
        self.assertEqual((result['site_name'], result['site_port'],
                          result['dns_record_id']), ('acme', 18042, 'rec-abc'))
        self.assertEqual(created['engine'], 'dsh')
        # Customer sees a real site and its public host, never simulation.
        invite = self.core.issue_invite(self.admin, tenant.id)
        actor = self.core.redeem_invite(invite, 'acme.example.test', 'alice',
                                        'test-password-long').actor
        view = self.jobs.customer_status(actor)
        self.assertEqual(view, {'status': 'succeeded_provisioned', 'simulated': False,
                                'provisioned': True, 'site_host': 'acme.example.test'})

    def test_provision_prepares_workspace_with_tenant_template_best_effort(self):
        import dsh_sitectl
        from support_site_template import SiteTemplateChoice

        chosen = SiteTemplateChoice(self.core)
        t1, t2 = self.tenant('a.example.test'), self.tenant('b.example.test')
        chosen.set(self.admin, t2.id, 'none')
        calls = []
        p = self.provisioner(templates=chosen)
        with patch.object(dsh_sitectl, 'prepare_workspace',
                          side_effect=lambda *a, **kw: calls.append((a, kw))):
            p._prepare_workspace(t1.id, 'a', 'cloud-fast')
            p._prepare_workspace(t2.id, 'b', None)
        self.assertEqual(calls[0], (('a', 'swarm'), {'starter_model': 'cloud-fast', 'language': '繁體中文'}))
        self.assertEqual(calls[1], (('b', 'none'), {'starter_model': None, 'language': '繁體中文'}))
        # Without a template store nothing is placed beyond the guide.
        with patch.object(dsh_sitectl, 'prepare_workspace',
                          side_effect=lambda *a, **kw: calls.append((a, kw))):
            self.provisioner()._prepare_workspace(t1.id, 'a', None)
        self.assertEqual(calls[2][0], ('a', 'none'))
        # A failure never fails the (already live) site.
        with patch.object(dsh_sitectl, 'prepare_workspace', side_effect=RuntimeError('disk')):
            p._prepare_workspace(t1.id, 'a', None)

    def test_failure_always_lands_in_reconciliation_and_never_retries(self):
        tenant = self.tenant('acme.example.test')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        p = self.provisioner()
        with patch.object(p, '_provision', side_effect=RuntimeError('docker exploded')):
            with self.assertRaises(RuntimeError):
                p.provision_one(job['job_id'])
        state = self.jobs.recovery_status(self.recovery, job['job_id'])
        self.assertEqual(state['status'], 'reconciliation_required')
        self.assertEqual(state['error_code'], 'executor_failed')
        # No automatic retry: the job is no longer claimable until reconciled.
        self.assertEqual(p.run_once(), [])
        self.assertIsNone(self.jobs.claim(self.worker, job['job_id']))

    def test_evidence_must_be_well_formed(self):
        tenant = self.tenant('acme.example.test')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        lease = self.jobs.claim(self.worker, job['job_id'])
        for name, port, rec in (('ACME', 18042, 'a'), ('acme', 0, 'a'),
                                ('acme', 70000, 'a'), ('acme', '18042', 'a'),
                                ('acme', 18042, 'bad id!'), ('', 18042, 'a')):
            with self.assertRaises(InvalidInput):
                self.jobs.complete_provisioned(self.worker, lease, site_name=name,
                                               site_port=port, dns_record_id=rec)
        # A null DNS record is allowed (proxy-only deployments).
        ok = self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                            site_port=18042, dns_record_id=None)
        self.assertEqual(ok['status'], 'succeeded_provisioned')

    def test_worker_capability_is_required_for_real_completion(self):
        tenant = self.tenant('acme.example.test')
        job = self.jobs.queue(self.admin, tenant.id, 'r1')
        lease = self.jobs.claim(self.worker, job['job_id'])
        for wrong in (self.recovery, object()):
            with self.assertRaises(Unauthorized):
                self.jobs.complete_provisioned(wrong, lease, site_name='acme',
                                               site_port=18042, dns_record_id=None)

    def test_disabled_tenant_is_not_provisioned(self):
        tenant = self.tenant('acme.example.test')
        self.jobs.queue(self.admin, tenant.id, 'r1')
        self.core.set_tenant_enabled(self.admin, tenant.id, False)
        p = self.provisioner()
        self.assertEqual(p.queued_ids() if hasattr(p, 'queued_ids') else
                         self.jobs.queued_job_ids(self.worker), [])
        self.assertEqual(p.run_once(), [])


if __name__ == '__main__':
    unittest.main()
