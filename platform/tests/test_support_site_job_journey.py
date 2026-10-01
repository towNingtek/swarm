"""HTTP metadata queue -> worker simulation -> customer progress, no real site."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import SupportCore, Unauthorized
from support_rooms import SupportRooms
from support_quota import SupportQuota
from support_chat_app import create_admin_chat_app, create_customer_chat_app
from support_management import install_management
from support_site_jobs import SupportSiteJobs
from support_ui import install_customer_ui


class SiteJobJourneyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / 'journey.sqlite')
        self.now = [1000]
        self.core = SupportCore(self.db, clock=lambda:self.now[0], admin_verifier=lambda x:'operator' if x == 'trusted-test' else None)
        self.rooms = SupportRooms(self.core)
        app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example',
            request_assertion=lambda r:'trusted-test' if r.headers.get('x-test-admin') == 'yes' else None)
        install_management(app, self.core, self.rooms, SupportQuota(self.core), enable_site_job_queue=True)
        self.admin = TestClient(app, base_url='https://mother.example')
        self.addCleanup(self.admin.close)
        self.ah = {'origin':'https://mother.example', 'x-test-admin':'yes'}
        created = self.admin.post('/admin/tenants', headers=self.ah,
                                 json={'client_request_id':'create-one', 'host':'one.example'})
        self.assertEqual(created.status_code, 200)
        self.tenant_id = created.json()['tenant']['id']
        invite = self.admin.post('/admin/tenants/'+self.tenant_id+'/invites', headers=self.ah,
                                 json={'ttl_seconds':600}).json()['token']
        customer = create_customer_chat_app(self.core, self.rooms, 'https://one.example')
        install_customer_ui(customer)
        self.customer = TestClient(customer, base_url='https://one.example')
        self.addCleanup(self.customer.close)
        self.assertEqual(self.customer.post('/customer/activate', headers={'origin':'https://one.example'},
            json={'invite':invite,'username':'alice','password':'test-password-long'}).status_code, 201)
        self.endpoint = '/admin/tenants/'+self.tenant_id+'/site-jobs'

    def status(self):
        response = self.customer.get('/customer/onboarding/status')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ready_for_work'])
        return response.json()['site_job']

    def queue(self):
        response = self.admin.post(self.endpoint, headers=self.ah, json={'client_request_id':'queue-one'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['metadata_only'])
        self.assertFalse(response.json()['executor_connected'])
        return response.json()['job']

    def test_queue_claim_crash_reconcile_and_simulated_completion_never_ready(self):
        self.assertEqual(self.status()['status'], 'not_requested')
        job = self.queue()
        self.assertEqual(self.queue(), job)
        self.assertEqual(self.status()['status'], 'queued')
        capability, recovery = object(), object()
        worker = SupportSiteJobs(self.core, worker_capability=capability, recovery_capability=recovery)
        lease = worker.claim(capability, job['job_id'], lease_seconds=10)
        self.assertEqual(self.status()['status'], 'running')
        self.now[0] += 11
        self.assertEqual(self.status()['status'], 'reconciliation_required')
        # Restart worker/core: storage survives, but no automatic side-effect retry.
        reopened = SupportCore(self.db, clock=lambda:self.now[0])
        worker = SupportSiteJobs(reopened, worker_capability=capability, recovery_capability=recovery)
        self.assertIsNone(worker.claim(capability, job['job_id']))
        view = worker.worker_status(capability, job['job_id'])
        # The worker capability alone cannot attest reconciliation.
        with self.assertRaises(Unauthorized):
            worker.reconcile(capability, job['job_id'], view['epoch'], outcome='retry_safe')
        worker.reconcile(recovery, job['job_id'], view['epoch'], outcome='retry_safe')
        lease = worker.claim(capability, job['job_id'])
        worker.complete_simulated(capability, lease)
        self.assertEqual(self.status(), {'status':'succeeded_simulated','simulated':True,'provisioned':False})
        # Suspension then re-enable voids even simulated success for the customer.
        admin = self.core.admin_actor('trusted-test')
        self.core.set_tenant_enabled(admin, self.tenant_id, False)
        self.assertEqual(self.customer.get('/customer/onboarding/status').status_code, 401)
        self.core.set_tenant_enabled(admin, self.tenant_id, True)
        self.assertEqual(self.status()['status'], 'reconciliation_required')

    def test_management_factory_queue_is_disabled_by_default(self):
        app = create_admin_chat_app(self.core, self.rooms, 'https://mother.example',
            request_assertion=lambda r: 'trusted-test')
        install_management(app, self.core, self.rooms, SupportQuota(self.core))
        with TestClient(app, base_url='https://mother.example') as client:
            self.assertEqual(client.post(self.endpoint, headers=self.ah,
                                         json={'client_request_id':'default-off'}).status_code, 404)
        self.assertEqual(self.status()['status'], 'not_requested')

    def test_http_cannot_select_resource_identity_or_complete_job(self):
        self.assertEqual(self.admin.post(self.endpoint, json={'client_request_id':'x'},
                                        headers={'origin':'https://mother.example'}).status_code, 401)
        for extra in ({'site_host':'victim.example'}, {'status':'ready'}, {'worker_capability':'x'}):
            self.assertEqual(self.admin.post(self.endpoint, headers=self.ah,
                json={'client_request_id':'x', **extra}).status_code, 400)
        self.assertEqual(self.customer.post('/customer/site-jobs', headers={'origin':'https://one.example'},
                                           json={'status':'ready'}).status_code, 404)
        job = self.queue()
        self.assertNotIn('site_id', self.status())
        self.assertNotIn('job_id', self.status())
        self.assertEqual(self.admin.post(self.endpoint+'/complete', headers=self.ah, json={}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
