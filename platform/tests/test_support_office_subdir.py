"""An office in a sub-folder named by the platform's site.yaml (scheduler.office)."""
import tempfile
import unittest
from pathlib import Path

from support_site_office import office_dir, office_workspace, read_office
from support_site_line import keys_dir


class OfficeSubdir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.site = self.root / 'acme'
        (self.site / 'workspace' / '.swarm').mkdir(parents=True)
        (self.site / 'workspace' / '.swarm' / 'registry.yaml').write_text('hives: []\n')
        office = self.site / 'workspace' / 'office' / '.swarm'
        office.mkdir(parents=True)
        (office / 'registry.yaml').write_text(
            'hives:\n- id: shop\n  enabled: true\n  agents:\n    pm:\n      daily: "0 8 * * *"\n')
        (self.site / 'workspace' / 'office' / 'hives' / 'shop').mkdir(parents=True)
        (self.site / 'workspace' / 'office' / 'hives' / 'shop' / 'project.yaml').write_text(
            'name: Shop\nreports:\n  daily:\n    context: say hi\n')

    def test_default_is_the_workspace(self):
        self.assertEqual(office_dir(self.site), '')
        self.assertEqual(office_workspace(self.site), self.site / 'workspace')
        self.assertEqual(read_office(self.site)['hives'], [])
        self.assertEqual(keys_dir(self.root, 'acme'), '/home/dsh/workspace/.swarm/keys')

    def test_site_yaml_names_the_office_folder(self):
        (self.site / 'site.yaml').write_text('scheduler:\n  office: office\n')
        self.assertEqual(office_dir(self.site), 'office')
        hives = read_office(self.site)['hives']
        self.assertEqual([h['id'] for h in hives], ['shop'])
        self.assertEqual(hives[0]['schedules'][0]['state'], 'ready')
        self.assertEqual(keys_dir(self.root, 'acme'), '/home/dsh/workspace/office/.swarm/keys')

    def test_bad_names_fall_back_to_the_workspace(self):
        for bad in ('../etc', '/abs', 'a/b', 'UPPER', ''):
            (self.site / 'site.yaml').write_text(f'scheduler:\n  office: "{bad}"\n')
            self.assertEqual(office_dir(self.site), '', bad)

    def test_scheduler_runs_in_the_office_folder(self):
        from support_site_office import SiteOffice
        from support_site_scheduler import SiteScheduler
        (self.site / 'site.yaml').write_text('scheduler:\n  office: office\n')
        s = SiteScheduler.__new__(SiteScheduler)
        s.office = SiteOffice(self.root, 'example.com')
        self.assertEqual(s._workdir('acme'), '/home/dsh/workspace/office')
        self.assertEqual(s._workdir('../x'), '/home/dsh/workspace')


if __name__ == '__main__':
    unittest.main()
