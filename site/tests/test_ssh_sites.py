"""SWARM_SSH_SITES: operator-approved sites pinned to a fixed address on the site network."""
import unittest
from unittest import mock

import dsh_sitectl as d


class SshSites(unittest.TestCase):
    def test_parse_keeps_only_addresses_in_the_site_subnet(self):
        with mock.patch.object(d, 'SITE_SUBNET', '172.29.10.0/24'), mock.patch.object(d, 'SITE_GATEWAY', '172.29.10.1'):
            got = d._ssh_sites('acme=172.29.10.4, beta=10.0.0.5, gamma=172.29.10.1, bad, delta=not-an-ip, eps=172.29.10.255')
        self.assertEqual(got, {'acme': '172.29.10.4'})

    def test_empty_means_no_site(self):
        self.assertEqual(d._ssh_sites(''), {})

    def _argv(self, name):
        env = d.site_root(name)
        with mock.patch.object(d, 'SITE_NETWORK', 'swarm-sites'), \
             mock.patch.object(d, 'SSH_SITES', {'acme': '172.29.10.4'}), \
             mock.patch.object(d, 'verify_site_isolation'), \
             mock.patch.object(d, '_env_value', return_value=d.SITE_GATEWAY), \
             mock.patch.object(d, '_workspace_dir', return_value='/tmp/ws'), \
             mock.patch.object(d, 'run') as run:
            d.docker_run(name, 18001, env / 'dsh.env', '1', '1g')
        return run.call_args_list[0].args[0]

    def test_pinned_site_gets_its_address(self):
        argv = self._argv('acme')
        self.assertEqual(argv[argv.index('--ip') + 1], '172.29.10.4')

    def test_other_sites_get_no_fixed_address(self):
        self.assertNotIn('--ip', self._argv('beta'))


if __name__ == '__main__':
    unittest.main()
