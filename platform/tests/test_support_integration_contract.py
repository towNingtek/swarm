"""Offline app-composition contracts; never starts a server or mutates host routing."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from support_core import SupportCore
from support_rooms import SupportRooms
from support_chat_app import create_admin_chat_app, create_customer_chat_app


class IntegrationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = SupportCore(Path(self.tmp.name) / 'contract.sqlite',
                                admin_verifier=lambda value: 'operator' if value == 'approved-fixture' else None)
        self.admin = self.core.admin_actor('approved-fixture')
        self.tenant = self.core.create_tenant(self.admin, 'customer.example')
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.admin, self.tenant.id)['id']

    def test_customer_admin_factories_are_not_composed_or_cookie_interchangeable(self):
        customer = create_customer_chat_app(self.core, self.rooms, 'https://customer.example')
        admin = create_admin_chat_app(self.core, self.rooms, 'https://mother.example')
        customer_paths = {route.path for route in customer.routes}
        admin_paths = {route.path for route in admin.routes}
        self.assertIn('/customer/activate', customer_paths)
        self.assertIn('/customer/rooms/{room_id}/respond', customer_paths)
        self.assertNotIn('/admin/rooms', customer_paths)
        self.assertIn('/admin/rooms/{room_id}/control', admin_paths)
        self.assertNotIn('/customer/activate', admin_paths)
        with TestClient(admin, base_url='https://mother.example') as client:
            response = client.get('/admin/rooms', headers={'origin':'https://mother.example'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        with TestClient(customer, base_url='https://customer.example') as client:
            response = client.get('/customer/me', headers={
                'origin':'https://customer.example', 'cookie':'swarm_admin_session=admin-token'})
            self.assertNotEqual(response.status_code, 200)

    def test_each_factory_requires_its_exact_origin(self):
        for factory, origin, wrong in (
            (create_customer_chat_app, 'https://customer.example', 'https://mother.example'),
            (create_admin_chat_app, 'https://mother.example', 'https://customer.example')):
            app = factory(self.core, self.rooms, origin)
            with TestClient(app, base_url=wrong) as client:
                response = client.get('/customer/me' if factory is create_customer_chat_app else '/admin/rooms',
                                      headers={'origin':wrong})
                self.assertEqual(response.status_code, 403)

    def test_no_factory_claims_to_mount_public_nginx_or_provisioning(self):
        customer = create_customer_chat_app(self.core, self.rooms, 'https://customer.example')
        admin = create_admin_chat_app(self.core, self.rooms, 'https://mother.example')
        for app in (customer, admin):
            paths = {route.path for route in app.routes}
            self.assertFalse(any(path.startswith('/nginx') or path.startswith('/provision') for path in paths))
        self.assertFalse(any('/token' in route.path for route in customer.routes))


if __name__ == '__main__':
    unittest.main()
