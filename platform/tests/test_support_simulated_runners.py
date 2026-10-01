"""Fixture-only simulated runners: opt-in, capability-gated, never default."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import InvalidInput, SupportCore, Unauthorized
from support_chat_app import create_customer_chat_app
from support_model_settings import CustomerModelSettings
from support_rooms import SupportRooms
from support_simulated_runners import (SimulatedModelVerifier, SimulatedSiteExecutor,
                                       install_simulated_model_probe)
from support_site_jobs import SupportSiteJobs


class SimulatedRunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'sim.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'one.example')
        self.rooms = SupportRooms(self.core)

    def customer(self, app):
        invite = self.core.issue_invite(self.admin, self.tenant.id)
        client = TestClient(app, base_url='https://one.example')
        self.addCleanup(client.close)
        origin = {'origin': 'https://one.example'}
        self.assertEqual(client.post('/customer/activate', headers=origin, json={
            'invite': invite, 'username': 'alice', 'password': 'test-password-long'}).status_code, 201)
        return client, origin

    def test_default_customer_app_has_no_probe_route_and_no_probe_flag(self):
        app = create_customer_chat_app(self.core, self.rooms, 'https://one.example',
                                       model_catalog=('fake-model-a',))
        client, origin = self.customer(app)
        self.assertFalse(client.get('/customer/model-settings').json()['simulated_probe_available'])
        self.assertEqual(client.post('/customer/model-settings/simulated-probe', headers=origin,
                                     json={'expected_revision': 0}).status_code, 404)

    def test_construction_requires_trusted_parts(self):
        cap = object()
        settings = CustomerModelSettings(self.core, catalog=('fake-model-a',), verifier_capability=cap)
        with self.assertRaises(InvalidInput):
            SimulatedModelVerifier(settings, 'cap')
        with self.assertRaises(InvalidInput):
            SimulatedModelVerifier({'catalog': ()}, cap)
        jobs = SupportSiteJobs(self.core, worker_capability=cap, recovery_capability=object())
        with self.assertRaises(InvalidInput):
            SimulatedSiteExecutor(jobs, 'cap')
        # Wrong capability: verifier exists but store refuses every probe.
        wrong = SimulatedModelVerifier(settings, object())
        app = create_customer_chat_app(self.core, self.rooms, 'https://one.example', model_settings=settings)
        with self.assertRaises(ValueError):
            create_customer_chat_app(self.core, self.rooms, 'https://one.example',
                                     model_settings=settings, model_catalog=('x',))
        other = SupportCore(str(Path(self.core.db_path).with_name('other.sqlite')))
        with self.assertRaises(ValueError):
            create_customer_chat_app(self.core, self.rooms, 'https://one.example',
                                     model_settings=CustomerModelSettings(other))
        client, origin = self.customer(app)
        self.assertEqual(client.post('/customer/model-settings', headers=origin, json={
            'selection_id': 'fake-model-a', 'expected_revision': 0}).status_code, 200)
        from support_app import COOKIE
        actor = self.core.authenticate_session(client.cookies.get(COOKIE), 'one.example')
        with self.assertRaises(Unauthorized):
            wrong.probe(actor, 1)
        self.assertEqual(client.get('/customer/model-settings').json()['state'], 'pending')

    def test_probe_route_is_bound_to_caller_and_revision(self):
        cap = object()
        settings = CustomerModelSettings(self.core, catalog=('fake-model-a', 'fake-model-b'),
                                         verifier_capability=cap)
        app = create_customer_chat_app(self.core, self.rooms, 'https://one.example', model_settings=settings)
        verifier = SimulatedModelVerifier(settings, cap, passing_selections=('fake-model-a',))
        install_simulated_model_probe(app, verifier)
        with self.assertRaises(ValueError):
            install_simulated_model_probe(app, verifier)
        client, origin = self.customer(app)
        self.assertTrue(client.get('/customer/model-settings').json()['simulated_probe_available'])
        # Unconfigured: nothing to probe.
        self.assertEqual(client.post('/customer/model-settings/simulated-probe', headers=origin,
                                     json={'expected_revision': 0}).status_code, 409)
        client.post('/customer/model-settings', headers=origin,
                    json={'selection_id': 'fake-model-b', 'expected_revision': 0})
        for body in ({'expected_revision': 0}, {'expected_revision': 1, 'passed': True},
                     {'expected_revision': '1'}, {}):
            self.assertIn(client.post('/customer/model-settings/simulated-probe', headers=origin,
                                      json=body).status_code, (400, 409), body)
        failed = client.post('/customer/model-settings/simulated-probe', headers=origin,
                             json={'expected_revision': 1}).json()
        self.assertEqual((failed['state'], failed['simulated']), ('failed', True))
        self.assertEqual(client.post('/customer/model-settings', headers=origin, json={
            'selection_id': 'fake-model-a', 'expected_revision': 1}).status_code, 200)
        passed = client.post('/customer/model-settings/simulated-probe', headers=origin,
                             json={'expected_revision': 2}).json()
        self.assertEqual((passed['state'], passed['verified_for_work']), ('simulated_pass', False))
        status = client.get('/customer/onboarding/status').json()
        self.assertFalse(status['ready_for_work'])
        # A simulated pass must not claim verification or unlock the site.
        self.assertEqual({s['id']: s['status'] for s in status['steps']}['own_model'], 'blocked')
        self.assertFalse(status['model_settings']['verified_for_work'])
        self.assertEqual(client.post('/customer/logout', headers=origin, json={}).status_code, 200)
        self.assertEqual(client.post('/customer/model-settings/simulated-probe', headers=origin,
                                     json={'expected_revision': 2}).status_code, 401)

    def test_executor_processes_only_queued_enabled_jobs(self):
        worker, recovery = object(), object()
        jobs = SupportSiteJobs(self.core, worker_capability=worker, recovery_capability=recovery)
        executor = SimulatedSiteExecutor(jobs, worker)
        self.assertEqual(executor.run_once(), [])
        other = self.core.create_tenant(self.admin, 'two.example')
        first = jobs.queue(self.admin, self.tenant.id, 'r1')
        second = jobs.queue(self.admin, other.id, 'r2')
        self.core.set_tenant_enabled(self.admin, other.id, False)
        with self.assertRaises(Unauthorized):
            jobs.queued_job_ids(recovery)
        self.assertEqual(executor.run_once(), [first['job_id']])
        self.assertEqual(jobs.worker_status(worker, first['job_id'])['status'], 'succeeded_simulated')
        self.core.set_tenant_enabled(self.admin, other.id, True)
        # Re-enable bumped the lifecycle: the queued job is fenced, not executed.
        self.assertEqual(executor.run_once(), [])
        self.assertEqual(jobs.recovery_status(recovery, second['job_id'])['status'], 'reconciliation_required')


if __name__ == '__main__':
    unittest.main()
