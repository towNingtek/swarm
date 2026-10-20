"""#395 可接取判斷。契約第 5 節：全部條件成立才接，任一條不符就跳過並寫出原因。"""
import unittest

import _path  # noqa: F401
from fake_github import AFTER, world

from epiclib import takeable


class Takeable(unittest.TestCase):
    def check(self, gh, n):
        return takeable.check(gh.issue(1), gh.issue(n))

    def test_first_in_chain_is_takeable(self):
        ok, why = self.check(world(), 2)
        self.assertTrue(ok, why)

    def test_blocked_by_open_issue(self):
        ok, why = self.check(world(), 3)
        self.assertFalse(ok)
        self.assertIn("#2", why)

    def test_blocker_closed_unlocks(self):
        gh = world(i2={"state": "closed"})
        self.assertTrue(self.check(gh, 3)[0])

    def test_closed_issue(self):
        ok, why = self.check(world(i2={"state": "closed"}), 2)
        self.assertFalse(ok)
        self.assertIn("closed", why)

    def test_by_human(self):
        ok, why = self.check(world(), 5)
        self.assertFalse(ok)
        self.assertIn("by:human", why)

    def test_no_executor_label(self):
        ok, why = self.check(world(i2={"labels": []}), 2)
        self.assertFalse(ok)
        self.assertIn("by:swarm", why)

    def test_both_executor_labels(self):
        ok, why = self.check(world(i2={"labels": ["by:swarm", "by:human"]}), 2)
        self.assertFalse(ok)
        self.assertIn("剛好", why)

    def test_epic_without_go(self):
        ok, why = self.check(world(i1={"labels": ["epic"], "go_at": None}), 2)
        self.assertFalse(ok)
        self.assertIn("swarm:go", why)

    def test_stuck(self):
        ok, why = self.check(world(i2={"labels": ["by:swarm", "swarm:stuck"]}), 2)
        self.assertFalse(ok)
        self.assertIn("swarm:stuck", why)

    def test_needs_criteria(self):
        ok, why = self.check(world(i2={"labels": ["by:swarm", "needs-criteria"]}), 2)
        self.assertFalse(ok)
        self.assertIn("needs-criteria", why)

    def test_criteria_edited_after_go(self):
        ok, why = self.check(world(i2={"last_edited_at": AFTER}), 2)
        self.assertFalse(ok)
        self.assertIn("swarm:go", why)

    def test_not_a_sub_of_this_epic(self):
        gh = world(i2={"parent": None})
        ok, why = self.check(gh, 2)
        self.assertFalse(ok)
        self.assertIn("#1", why)

    def test_pick_lists_takeable_and_reasons(self):
        gh = world()
        take, skipped = takeable.pick(gh.issue(1), gh.subs(1))
        self.assertEqual([i["number"] for i in take], [2])
        self.assertEqual(sorted(skipped), [3, 4, 5])
        self.assertTrue(all(skipped.values()))


if __name__ == "__main__":
    unittest.main()
