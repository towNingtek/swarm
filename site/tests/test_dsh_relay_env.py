import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import dsh_sitectl as dsh


class RelayEnvTests(unittest.TestCase):
    def test_public_host_requires_explicit_peer(self):
        with tempfile.TemporaryDirectory() as root, patch.object(dsh, 'site_root', return_value=Path(root)), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(dsh.SiteError):
                dsh.write_dsh_env('test', 'test-password-long', 'customer.example')
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_verified_peer_config_is_written_without_password(self):
        with tempfile.TemporaryDirectory() as root, patch.object(dsh, 'site_root', return_value=Path(root)), patch.dict(os.environ, {'SWARM_RELAY_TRUSTED_PEERS': '192.0.2.1,::1'}):
            (Path(root)/'dsh-home').mkdir()
            target = dsh.write_dsh_env('test', 'test-password-long', 'customer.example')
            body = target.read_text()
            self.assertIn('RELAY_PUBLIC_ORIGIN=https://customer.example', body)
            self.assertIn('RELAY_TRUSTED_PEERS=192.0.2.1,::1', body)
            self.assertNotIn('test-password-long', body)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_site_gets_its_own_entry_key_never_the_master(self):
        master = 'm' * 48
        with tempfile.TemporaryDirectory() as root, patch.object(dsh, 'site_root', return_value=Path(root)), \
                patch.dict(os.environ, {'SWARM_RELAY_TRUSTED_PEERS': '192.0.2.1', 'SWARM_ENTRY_SECRET': master}):
            (Path(root)/'dsh-home').mkdir()
            body = dsh.write_dsh_env('test', 'test-password-long', 'customer.example').read_text()
        self.assertNotIn(master, body)
        self.assertIn('WEB_AUTH_ENTRY_SECRET=' + dsh.site_entry_secret(master, 'customer.example'), body)

    def test_platform_derives_the_same_entry_key(self):
        import sys
        platform_dir = Path(__file__).resolve().parents[2] / 'platform'
        if not (platform_dir / 'support_site_entry.py').exists():
            self.skipTest('platform/ not in this checkout yet')
        sys.path.insert(0, str(platform_dir))
        from support_site_entry import site_entry_secret
        master = 'm' * 48
        self.assertEqual(site_entry_secret(master, 'customer.example'),
                         dsh.site_entry_secret(master, 'customer.example'))

    def test_rejects_peer_ranges_and_env_injection(self):
        for peer in ('*', '192.0.2.0/24', '192.0.2.1\nOTHER=1', ''):
            with patch.dict(os.environ, {'SWARM_RELAY_TRUSTED_PEERS': peer}):
                with self.assertRaises(dsh.SiteError):
                    dsh.write_dsh_env('test', 'password-long', 'customer.example')


if __name__ == '__main__':
    unittest.main()
