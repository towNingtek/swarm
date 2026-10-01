"""Offline metadata-to-chat journey; does not provision or reach any real site."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import SupportCore
from support_rooms import SupportRooms
from support_quota import SupportQuota
from support_chat_app import create_admin_chat_app, create_customer_chat_app
from support_management import install_management
from support_model import FakeModel


class OnboardingJourneyTests(unittest.TestCase):
    def test_atomic_metadata_invite_customer_ai_human(self):
        with tempfile.TemporaryDirectory() as root:
            core = SupportCore(Path(root) / 'db', admin_verifier=lambda x: 'operator' if x == 'fixture' else None)
            rooms, quota = SupportRooms(core), SupportQuota(core)
            create_body = {'client_request_id': 'journey-one', 'host': 'customer.example',
                           'policy': {'mode': 'unlimited'}}
            admin_app = create_admin_chat_app(core, rooms, 'https://mother.example',
                request_assertion=lambda r: 'fixture' if r.headers.get('x-test-admin') == 'yes' else None)
            install_management(admin_app, core, rooms, quota)
            customer_app = create_customer_chat_app(core, rooms, 'https://customer.example', model=FakeModel())
            ah = {'origin': 'https://mother.example', 'x-test-admin': 'yes'}
            ch = {'origin': 'https://customer.example'}
            with TestClient(admin_app, base_url='https://mother.example') as admin, TestClient(customer_app, base_url='https://customer.example') as customer:
                initialized = admin.post('/admin/tenants', headers=ah, json=create_body)
                self.assertIn(initialized.status_code, (200, 201))
                created = initialized.json()
                self.assertTrue(created['metadata_only'])
                self.assertEqual(created['provisioning'], 'not_provisioned')
                tenant_id, room_id = created['tenant']['id'], created['room']['id']
                replay = admin.post('/admin/tenants', headers=ah, json=create_body)
                self.assertEqual(replay.json(), created)
                issued = admin.post(f'/admin/tenants/{tenant_id}/invites', headers=ah, json={'ttl_seconds': 300})
                self.assertEqual(issued.status_code, 201)
                invite = issued.json()['token']
                activation = customer.post('/customer/activate', headers=ch, json={
                    'invite': invite, 'username': 'customer', 'password': 'fixture-long-password'})
                self.assertEqual(activation.status_code, 201)
                path = '/customer/rooms/' + room_id
                sent = customer.post(path + '/messages', headers=ch, json={'client_message_id': 'm1', 'body': 'Help with setup'})
                self.assertEqual(sent.status_code, 200)
                response = customer.post(path + '/respond', headers=ch, json={'requested': True})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['simulated'])
                human = admin.post('/admin/rooms/' + room_id + '/messages', headers=ah,
                    json={'client_message_id': 'human-1', 'body': 'I can help.'})
                self.assertEqual(human.status_code, 200)
                history = customer.get(path).json()['messages']
                self.assertEqual([m['sender_kind'] for m in history], ['customer', 'ai', 'human'])
                self.assertNotIn(invite, str(history))
                reuse = customer.post('/customer/activate', headers=ch, json={
                    'invite': invite, 'username': 'other', 'password': 'fixture-long-password'})
                self.assertNotEqual(reuse.status_code, 201)


if __name__ == '__main__':
    unittest.main()
