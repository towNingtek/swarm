"""The Copilot reads real state, proposes the next step, and acts only on confirmation."""
import tempfile
import unittest
from pathlib import Path

from support_core import Conflict, InvalidInput, SupportCore
from support_model_settings import CustomerModelSettings
from support_onboarding import SupportOnboarding
from support_onboarding_copilot import ACTION_ACKNOWLEDGE_RULES, OnboardingCopilot
from support_site_jobs import SupportSiteJobs

PLATFORM = 'platform.example'


class FakeOffice:
    """Stands in for SiteOffice: the summary a real site would yield."""
    def __init__(self):
        self.value = {'template': 'swarm', 'registry': 'ok', 'hives': [],
                      'default_model': {'provider': 'platform-starter', 'model': 'cloud-fast',
                                        'starter': True}}
        self.hosts = []

    def for_host(self, host):
        self.hosts.append(host)
        return self.value


class CopilotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = SupportCore(str(Path(tmp.name) / 'c.sqlite'),
                                admin_verifier=lambda x: 'admin' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.settings = CustomerModelSettings(self.core, catalog=('model-a', 'model-b'))
        self.office = FakeOffice()
        self.onboarding = SupportOnboarding(self.core, model_settings=self.settings,
                                            office=self.office)
        self.copilot = OnboardingCopilot(self.onboarding)
        self.worker = object()
        self.jobs = SupportSiteJobs(self.core, worker_capability=self.worker,
                                    recovery_capability=object())
        self.tenant = self.core.create_tenant(self.admin, 'acme.example',
                                              platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, self.tenant.id)
        self.actor = self.core.redeem_invite(token, PLATFORM, 'alice', 'password-1').actor

    # -- it knows where the customer actually is --------------------------

    def test_first_step_is_rules_and_acting_completes_it(self):
        step = self.copilot.next_step(self.actor)
        self.assertEqual(step['action']['kind'], ACTION_ACKNOWLEDGE_RULES)
        self.assertFalse(self.copilot.situation(self.actor)['rules_acknowledged'])

        after = self.copilot.perform(self.actor, **self._confirm(step['action']))
        self.assertTrue(self.copilot.situation(self.actor)['rules_acknowledged'])
        # It immediately reports the genuinely next step, not a success banner.
        self.assertIsNone(after['action'])
        self.assertIn('管理員', after['message'])

    def test_after_setup_it_reports_build_state_without_inventing_progress(self):
        self._complete_setup()
        self.assertIn('管理員', self.copilot.next_step(self.actor)['message'])
        self.assertIsNone(self.copilot.next_step(self.actor)['action'])

        job = self.jobs.queue(self.admin, self.tenant.id, 'r1')
        self.assertIn('數分鐘', self.copilot.next_step(self.actor)['message'])

        lease = self.jobs.claim(self.worker, job['job_id'])
        self.jobs.fail(self.worker, lease, 'executor_failed')
        message = self.copilot.next_step(self.actor)['message']
        self.assertIn('不會自動重試', message)
        self.assertIsNone(self.copilot.next_step(self.actor)['action'])

    def _provision(self):
        job = self.jobs.queue(self.admin, self.tenant.id, 'r1')
        lease = self.jobs.claim(self.worker, job['job_id'])
        self.jobs.complete_provisioned(self.worker, lease, site_name='acme',
                                       site_port=18042, dns_record_id=None)

    def test_office_is_read_only_after_real_provisioning(self):
        self._complete_setup()
        self.copilot.next_step(self.actor)
        self.assertEqual(self.office.hosts, [])
        self._provision()
        step = self.copilot.next_step(self.actor)
        self.assertEqual(self.office.hosts[-1], 'acme.example')
        self.assertIn('acme.example', step['message'])
        self.assertIn('第一間公司', step['message'])
        self.assertIsNone(step['action'])

    def test_it_follows_the_office_then_the_model(self):
        self._complete_setup()
        self._provision()
        self.office.value['hives'] = [{
            'id': 'shop', 'name': '小店', 'enabled': False, 'exists': True, 'scm': 'none',
            'channels': [], 'description': None,
            'schedules': [{'role': 'pm', 'name': 'digest', 'cron': '0 8 * * *', 'state': 'disabled'}],
            'roles': [{'id': 'pm', 'model': None, 'enabled': True,
                       'schedules': [{'name': 'digest', 'cron': '0 8 * * *'}]}]}]
        self.assertIn('起步模型', self.copilot.next_step(self.actor)['message'])
        situation = self.copilot.situation(self.actor)
        self.assertEqual(situation['hives'][0]['schedules'], 1)
        self.office.value['default_model'] = {'provider': 'openrouter', 'model': 'x',
                                              'starter': False}
        step = self.copilot.next_step(self.actor)
        self.assertIn('都設定好了', step['message'])
        self.assertIn('acme.example', step['message'])

    def test_template_none_skips_the_office_step(self):
        self._complete_setup()
        self._provision()
        self.office.value.update(template=None, registry='missing')
        steps = {s['id']: s for s in self.onboarding.status(self.actor)['steps']}
        self.assertEqual(steps['office']['status'], 'skipped')

    # -- it cannot be talked into doing anything else ---------------------

    def test_only_allowlisted_actions_exist(self):
        for kind in ('run_shell', 'delete_tenant', 'reveal_password', 'queue_site', ''):
            with self.assertRaises(InvalidInput, msg=kind):
                self.copilot.perform(self.actor, kind, {}, 'any-token')

    def test_a_forged_or_altered_proposal_is_refused(self):
        action = self.copilot.next_step(self.actor)['action']
        with self.assertRaises(Conflict):
            self.copilot.perform(self.actor, action['kind'], action['arguments'], 'forged')
        # Arguments are bound to the token: swapping them invalidates it.
        tampered = dict(action['arguments'], rules_version='other')
        with self.assertRaises(Conflict):
            self.copilot.perform(self.actor, action['kind'], tampered, action['token'])
        self.assertFalse(self.copilot.situation(self.actor)['rules_acknowledged'])

    def test_model_selection_is_no_longer_an_action(self):
        with self.assertRaises(InvalidInput):
            self.copilot.perform(self.actor, 'select_model', {}, 'any-token')

    def test_proposals_from_another_process_are_not_accepted(self):
        action = self.copilot.next_step(self.actor)['action']
        other = OnboardingCopilot(self.onboarding)
        with self.assertRaises(Conflict):
            other.perform(self.actor, **self._confirm(action))

    def test_it_never_sees_another_customer(self):
        other = self.core.create_tenant(self.admin, 'other.example', platform_host=PLATFORM)
        token = self.core.issue_invite(self.admin, other.id)
        bob = self.core.redeem_invite(token, PLATFORM, 'bob', 'password-1').actor
        self.copilot.perform(self.actor, **self._confirm(
            self.copilot.next_step(self.actor)['action']))
        # Alice acknowledged; Bob's own situation is untouched.
        self.assertFalse(self.copilot.situation(bob)['rules_acknowledged'])

    # -- as a room participant --------------------------------------------

    def test_room_copilot_answers_from_real_state_and_offers_the_action(self):
        """The Copilot is a member of the support room, not a separate panel."""
        from support_copilot import SupportCopilot
        from support_model import FakeModel
        from support_rooms import SupportRooms

        rooms = SupportRooms(self.core)
        room = rooms.create_room(self.admin, self.tenant.id, mode='ai')
        assistant = SupportCopilot(rooms, FakeModel(), onboarding_copilot=self.copilot)
        rooms.post_message(self.actor, room['id'], 'm1', '我下一步要做什麼？')
        result = assistant.respond(self.actor, room['id'], requested=True)

        self.assertIsNotNone(result['message'])
        # The reply carries the next allowed action, computed from state.
        self.assertEqual(result['suggestion']['action']['kind'], ACTION_ACKNOWLEDGE_RULES)
        # And the model was given the customer's real state as a system turn.
        state = assistant._state_context(self.actor)
        self.assertIn('規則尚未確認', state)
        self._complete_setup()
        self._provision()
        state = assistant._state_context(self.actor)
        self.assertIn('platform-starter/cloud-fast', state)
        self.assertIn('沒有排程中心', state)
        # It must never leak another tenant's state.
        self.assertNotIn('other.example', state)

    def test_copilot_is_instructed_to_answer_in_traditional_chinese(self):
        """Owner requirement: the real model drifted into Simplified Chinese."""
        from support_copilot import SYSTEM_PROMPT
        self.assertTrue(SYSTEM_PROMPT.startswith('LANGUAGE:'))
        self.assertIn('Traditional Chinese', SYSTEM_PROMPT)
        self.assertIn('Never use Simplified Chinese', SYSTEM_PROMPT)

    def test_state_context_is_absent_without_the_onboarding_copilot(self):
        from support_copilot import SupportCopilot
        from support_model import FakeModel
        from support_rooms import SupportRooms

        plain = SupportCopilot(SupportRooms(self.core), FakeModel())
        self.assertIsNone(plain._state_context(self.actor))

    # -- helpers ----------------------------------------------------------

    def _confirm(self, action):
        return {'kind': action['kind'], 'arguments': action['arguments'],
                'token': action['token']}

    def _complete_setup(self):
        self.copilot.perform(self.actor, **self._confirm(
            self.copilot.next_step(self.actor)['action']))


if __name__ == '__main__':
    unittest.main()
