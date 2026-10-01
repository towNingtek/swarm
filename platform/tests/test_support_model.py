import unittest
from support_model import DisabledModel, FakeModel, ModelUnavailable, Reply, Turn, checked_reply


class ModelBoundaryTests(unittest.TestCase):
    def test_disabled_is_fail_closed(self):
        with self.assertRaises(ModelUnavailable):
            checked_reply(DisabledModel(), [Turn('user', 'help')])

    def test_fake_is_visibly_simulated(self):
        reply = checked_reply(FakeModel(), [Turn('user', 'help')])
        self.assertTrue(reply.simulated)
        self.assertIn('TEST ONLY', reply.text)

    def test_context_and_output_are_bounded(self):
        for turns, limit in [([Turn('user', 'x' * 32001)], 512),
                             ([Turn('tool', 'secret')], 512),
                             ([Turn('user', 'help')], 2049),
                             ([Turn('user', 'help')], True), ([], 512)]:
            with self.assertRaises(ValueError):
                checked_reply(FakeModel(), turns, max_output_tokens=limit)

    def test_unknown_usage_is_not_zero(self):
        class NoUsage:
            def reply(self, *args, **kwargs):
                return Reply('hello', 'configured/provider')
        reply = checked_reply(NoUsage(), [Turn('user', 'help')])
        self.assertIsNone(reply.input_tokens)
        self.assertIsNone(reply.output_tokens)

    def test_invalid_usage_is_rejected(self):
        class Invalid:
            def reply(self, *args, **kwargs):
                return Reply('hello', 'configured/provider', -1, 5)
        with self.assertRaises(ModelUnavailable):
            checked_reply(Invalid(), [Turn('user', 'help')])


if __name__ == '__main__':
    unittest.main()
