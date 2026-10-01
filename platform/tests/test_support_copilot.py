import tempfile
import unittest
from pathlib import Path
from support_core import SupportCore
from support_rooms import SupportRooms
from support_model import FakeModel, ModelUnavailable
from support_copilot import SupportCopilot


class CopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core = SupportCore(Path(self.tmp.name)/'db', admin_verifier=lambda x: 'operator' if x == 'test' else None)
        self.admin = self.core.admin_actor('test')
        self.tenant = self.core.create_tenant(self.admin, 'customer.example', quota_mode='unlimited')
        token = self.core.issue_invite(self.admin, self.tenant.id)
        self.customer = self.core.redeem_invite(token, 'customer.example', 'customer', 'test-password-long').actor
        self.rooms = SupportRooms(self.core)
        self.room = self.rooms.create_room(self.admin, self.tenant.id)['id']
        self.rooms.post_message(self.customer, self.room, 'hello', 'How do I configure my model?')

    def test_customer_ai_and_human_share_persistent_history(self):
        result = SupportCopilot(self.rooms, FakeModel()).respond(self.customer, self.room)
        self.assertTrue(result['simulated'])
        self.assertFalse(result['discarded'])
        self.rooms.post_message(self.admin, self.room, 'human-1', 'I can help you with setup.')
        messages = self.rooms.read_room(self.customer, self.room)['messages']
        self.assertEqual([m['sender_kind'] for m in messages], ['customer', 'ai', 'human'])
        self.assertEqual(messages[-1]['sender_id'], 'operator')

    def test_notes_excluded_and_takeover_discards_inflight_reply(self):
        self.rooms.add_note(self.admin, self.room, 'PRIVATE-NOTE')
        owner = self
        class Delayed(FakeModel):
            def reply(self, turns, **kwargs):
                owner.assertNotIn('PRIVATE-NOTE', str(turns))
                owner.rooms.set_mode(owner.admin, owner.room, 'human', expected_epoch=0)
                return super().reply(turns, **kwargs)
        result = SupportCopilot(self.rooms, Delayed()).respond(self.customer, self.room)
        self.assertTrue(result['discarded'])
        self.assertEqual(len(self.rooms.read_room(self.customer, self.room)['messages']), 1)

    def test_long_history_is_bounded_and_latest_question_survives(self):
        for index in range(10):
            self.rooms.post_message(self.customer, self.room, 'large-' + str(index), 'x' * 7000)
        self.rooms.post_message(self.customer, self.room, 'latest', 'LATEST QUESTION')
        owner = self
        class Inspect(FakeModel):
            def reply(self, turns, **kwargs):
                owner.assertLessEqual(sum(len(t.content) for t in turns), 30000)
                owner.assertEqual(turns[-1].content, 'LATEST QUESTION')
                return super().reply(turns, **kwargs)
        self.assertFalse(SupportCopilot(self.rooms, Inspect()).respond(self.customer, self.room)['discarded'])

    def test_staff_messages_are_marked_as_platform_side_not_questions(self):
        self.rooms.admin_reply(self.admin, self.room, 'h1', '幫我解釋你有多少 llm')
        seen = []
        class Inspect(FakeModel):
            def reply(self, turns, **kwargs):
                seen.extend(turns)
                return super().reply(turns, **kwargs)
        SupportCopilot(self.rooms, Inspect()).respond(self.customer, self.room, requested=True)
        staff = [t for t in seen if '幫我解釋' in t.content]
        self.assertEqual([(t.role, t.content) for t in staff], [('assistant', '（平台人員）幫我解釋你有多少 llm')])
        self.assertEqual(seen[-1].role, 'assistant')  # the staff line is last, not a user turn

    def test_operator_instruction_is_private_and_the_ai_answers_the_customer(self):
        seen = []
        class Inspect(FakeModel):
            def reply(self, turns, **kwargs):
                seen.extend(turns)
                from support_model import Reply
                return Reply('（平台人員）到設定頁填入金鑰。', 'test/fake', 1, 1, True)
        self.rooms.set_mode(self.admin, self.room, 'human', expected_epoch=0)
        result = SupportCopilot(self.rooms, Inspect()).operator_respond(self.admin, self.room, '說明怎麼設定模型')
        self.assertEqual(seen[-1].role, 'system')
        self.assertIn('說明怎麼設定模型', seen[-1].content)
        # The staff marker is never copied into the AI's own message.
        self.assertEqual(result['message']['body'], '到設定頁填入金鑰。')
        self.assertEqual(result['message']['sender_kind'], 'ai')
        public = self.rooms.read_room(self.customer, self.room)
        self.assertNotIn('說明怎麼設定模型', str(public))
        with self.assertRaises(Exception):
            SupportCopilot(self.rooms, Inspect()).operator_respond(self.customer, self.room, '偽造')

    def test_default_has_no_live_model(self):
        with self.assertRaises(ModelUnavailable):
            SupportCopilot(self.rooms).respond(self.customer, self.room)
        # Failure releases the worker lease, rather than blocking the room.
        result = SupportCopilot(self.rooms, FakeModel()).respond(self.customer, self.room)
        self.assertFalse(result['discarded'])


if __name__ == '__main__':
    unittest.main()
