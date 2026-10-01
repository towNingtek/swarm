"""The onboarding page's read-only view of a site office."""
import os
import tempfile
import unittest
from pathlib import Path

from support_site_office import MAX_BYTES, SiteOffice, read_office

REGISTRY = '''# comment
hives:
  - id: shop
    path: hives/shop
    enabled: false
    agents:
      pm:
        model: ""
        morning_digest: "0 8 * * *"
      rd:
        model: opus
  - id: legacy
    path: hives/legacy
    agents: [pm, reviewer]
  - id: "../escape"
    agents: [pm]
'''
PROJECT = '''project:
  id: shop
  name: 小明咖啡
  description: 賣咖啡
scm:
  type: github
  org: someone
  token_file: .keys/github.token
discord:
  server_id: "123"
reports:
  morning_digest:
    skill: calendar
'''
PATCH = '''plugins:
- id: other
  config:
    model: not-this
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: platform-starter
    model: cloud-fast
    apiKey: sk-should-never-leak
'''


class OfficeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sites = Path(tmp.name)
        self.site = self.sites / 'ab'
        self.ws = self.site / 'workspace'
        (self.ws / '.swarm').mkdir(parents=True)
        (self.ws / 'hives' / 'shop' / '.keys').mkdir(parents=True)
        (self.site / 'dsh-home' / 'profiles' / 'web').mkdir(parents=True)
        (self.ws / '.dsh-template.json').write_text('{"template": "swarm", "files": {}}')
        (self.ws / '.swarm' / 'registry.yaml').write_text(REGISTRY)
        (self.ws / 'hives' / 'shop' / 'project.yaml').write_text(PROJECT)
        (self.ws / 'hives' / 'shop' / '.keys' / 'github.token').write_text('ghp_secret')
        (self.site / 'dsh-home' / 'profiles' / 'web' / 'cordis.patch.yml').write_text(PATCH)

    def test_classic_registry_is_summarised(self):
        office = read_office(self.site)
        self.assertEqual(office['template'], 'swarm')
        self.assertEqual(office['registry'], 'ok')
        self.assertEqual([h['id'] for h in office['hives']], ['shop', 'legacy'])
        shop = office['hives'][0]
        self.assertEqual(shop['name'], '小明咖啡')
        self.assertTrue(shop['exists'])
        self.assertFalse(shop['enabled'])
        self.assertEqual(shop['scm'], 'github')
        self.assertEqual(shop['channels'], ['discord'])
        [job] = shop['schedules']
        self.assertEqual((job['name'], job['skill'], job['state']),
                         ('morning_digest', 'calendar', 'disabled'))
        pm, rd = shop['roles']
        self.assertEqual(pm['schedules'], [{'name': 'morning_digest', 'cron': '0 8 * * *'}])
        self.assertIsNone(pm['model'])
        self.assertEqual(rd['model'], 'opus')
        legacy = office['hives'][1]
        self.assertEqual([r['id'] for r in legacy['roles']], ['pm', 'reviewer'])
        self.assertFalse(legacy['exists'])

    def test_only_provider_and_model_of_the_default_are_read(self):
        office = read_office(self.site)
        self.assertEqual(office['default_model'],
                         {'provider': 'platform-starter', 'model': 'cloud-fast', 'starter': True})
        text = repr(office)
        for secret in ('sk-should-never-leak', 'ghp_secret', 'token_file', 'someone', '123'):
            self.assertNotIn(secret, text)

    def test_symlinks_are_never_followed(self):
        secret = self.sites / 'secret.yaml'
        secret.write_text('hives:\n  - id: stolen\n')
        registry = self.ws / '.swarm' / 'registry.yaml'
        registry.unlink()
        registry.symlink_to(secret)
        self.assertEqual(read_office(self.site)['registry'], 'missing')
        registry.unlink()
        (self.ws / '.swarm').rmdir()
        other = self.sites / 'other'
        (other / '.swarm').mkdir(parents=True)
        (other / '.swarm' / 'registry.yaml').write_text('hives:\n  - id: stolen\n')
        (self.ws / '.swarm').symlink_to(other / '.swarm')
        self.assertEqual(read_office(self.site)['hives'], [])

    def test_oversized_and_broken_files_are_not_guessed(self):
        (self.ws / '.swarm' / 'registry.yaml').write_text('#' * (MAX_BYTES + 1))
        self.assertEqual(read_office(self.site)['registry'], 'missing')
        (self.ws / '.swarm' / 'registry.yaml').write_text('hives: [unclosed\n')
        office = read_office(self.site)
        self.assertEqual((office['registry'], office['hives']), ('unreadable', []))

    def test_empty_starter_registry_is_ok(self):
        (self.ws / '.swarm' / 'registry.yaml').write_text('# only comments\nhives: []\n')
        office = read_office(self.site)
        self.assertEqual((office['registry'], office['hives']), ('ok', []))

    def test_host_mapping_is_strict(self):
        office = SiteOffice(self.sites, 'example.com')
        self.assertEqual(office.for_host('ab.example.com')['template'], 'swarm')
        for host in ('ab.evil.cc', '../ab.example.com', 'x.ab.example.com', None, 'nope.example.com'):
            self.assertIsNone(office.for_host(host), host)
        os.symlink(self.site, self.sites / 'cd')
        self.assertIsNone(office.for_host('cd.example.com'))


if __name__ == '__main__':
    unittest.main()


class EditorTests(unittest.TestCase):
    def setUp(self):
        from support_site_office import OfficeEditor
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.site = Path(tmp.name) / 'ab'
        self.ws = self.site / 'workspace'
        (self.ws / '.swarm').mkdir(parents=True)
        (self.ws / 'hives' / 'shop').mkdir(parents=True)
        (self.ws / '.swarm' / 'registry.yaml').write_text(
            '# 格式說明\n# 保留我\nhives:\n  - id: shop\n    path: hives/shop\n    enabled: false\n'
            '    agents:\n      pm:\n        model: ""\n      rd:\n        model: opus\n')
        (self.ws / 'hives' / 'shop' / 'project.yaml').write_text(
            'project:\n  id: shop\n  name: 小店\nscm:\n  type: none\nreports: {}\n')
        self.editor = OfficeEditor(self.site, clock=iter(range(1000, 2000)).__next__)

    def rev(self):
        return read_office(self.site)['revision']

    def save(self, **kw):
        args = dict(original='', name='digest', role='pm', cron='0 8 * * *',
                    skill='', context='每天早上整理昨天的待辦')
        args.update(kw)
        return self.editor.save_schedule(self.rev(), 'shop', **args)

    def test_round_trip_in_classic_layout(self):
        import yaml
        office = self.save()
        [item] = office['hives'][0]['schedules']
        self.assertEqual((item['role'], item['name'], item['cron'], item['state']),
                         ('pm', 'digest', '0 8 * * *', 'disabled'))
        registry_text = (self.ws / '.swarm' / 'registry.yaml').read_text()
        self.assertTrue(registry_text.startswith('# 格式說明\n# 保留我\n'))
        registry = yaml.safe_load(registry_text)
        self.assertEqual(registry['hives'][0]['agents']['pm'], {'model': '', 'digest': '0 8 * * *'})
        self.assertEqual(registry['hives'][0]['agents']['rd'], {'model': 'opus'})
        project = yaml.safe_load((self.ws / 'hives' / 'shop' / 'project.yaml').read_text())
        self.assertEqual(project['reports']['digest']['context'], '每天早上整理昨天的待辦')
        self.assertEqual(project['project']['name'], '小店')
        # Enabling the hive makes a complete schedule 'ready' (would run).
        office = self.editor.set_hive_enabled(self.rev(), 'shop', True)
        self.assertEqual(office['hives'][0]['schedules'][0]['state'], 'ready')
        # Rename + move role + change time; the old name disappears everywhere.
        office = self.save(original='digest', name='weekly', role='rd', cron='0 9 * * mon')
        names = [(s['role'], s['name'], s['cron']) for s in office['hives'][0]['schedules']]
        self.assertEqual(names, [('rd', 'weekly', '0 9 * * mon')])
        self.assertNotIn('digest', (self.ws / '.swarm' / 'registry.yaml').read_text())
        office = self.editor.delete_schedule(self.rev(), 'shop', 'weekly')
        self.assertEqual(office['hives'][0]['schedules'], [])
        backups = os.listdir(self.ws / '.swarm' / '.backups')
        self.assertTrue(any(b.endswith('-registry.yaml') for b in backups))

    def test_stale_revision_is_refused(self):
        from support_site_office import OfficeConflict
        old = self.rev()
        self.save()
        with self.assertRaises(OfficeConflict):
            self.editor.delete_schedule(old, 'shop', 'digest')

    def test_invalid_input_is_refused(self):
        from support_site_office import OfficeError
        bad = [dict(cron='0 9 * * 1'), dict(cron='61 * * * *'), dict(cron='* * *'),
               dict(cron='0 8 * * *; rm'), dict(name='Bad Name'), dict(name='model'),
               dict(role='ghost'), dict(skill='../x'), dict(context='', skill=''),
               dict(context='key sk-abcdefghijklmnopqrstuvwxyz'), dict(context='x' * 2001)]
        for kw in bad:
            with self.assertRaises(OfficeError, msg=kw):
                self.save(**kw)
        with self.assertRaises(OfficeError):
            self.editor.save_schedule(self.rev(), 'other', original='', name='a', role='pm',
                                      cron='0 8 * * *', skill='', context='x')
        self.save()
        with self.assertRaises(OfficeError):
            self.save()                     # duplicate name

    def test_symlinked_project_is_never_written(self):
        from support_site_office import OfficeError
        outside = self.site.parent / 'outside.yaml'
        outside.write_text('reports: {}\n')
        target = self.ws / 'hives' / 'shop' / 'project.yaml'
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(OfficeError):
            self.save()
        self.assertEqual(outside.read_text(), 'reports: {}\n')

    def test_orphans_are_reported_like_the_classic_scheduler(self):
        (self.ws / '.swarm' / 'registry.yaml').write_text(
            'hives:\n  - id: shop\n    enabled: true\n    agents:\n      pm:\n'
            '        lonely: "0 8 * * *"\n        heartbeat: "*/30 * * * *"\n')
        (self.ws / 'hives' / 'shop' / 'project.yaml').write_text(
            'reports:\n  untimed:\n    skill: calendar\n')
        states = {s['name']: s['state'] for s in read_office(self.site)['hives'][0]['schedules']}
        self.assertEqual(states, {'lonely': 'missing_definition', 'untimed': 'missing_time'})

    def test_classic_top_level_list_registry_is_read(self):
        (self.ws / '.swarm' / 'registry.yaml').write_text(
            '- id: shop\n  enabled: false\n  agents:\n    pm:\n      model: sonnet\n')
        self.assertEqual([h['id'] for h in read_office(self.site)['hives']], ['shop'])
        office = self.save()
        self.assertEqual(office['hives'][0]['schedules'][0]['name'], 'digest')
