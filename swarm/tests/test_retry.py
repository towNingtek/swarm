"""#398 重試與卡住。契約第 4 節。

work(gh, epic, issue, attempt, check, max_tries=3)
  attempt(prev) -> agent.Result      prev ＝ 上一次失敗的判準輸出（第一次是 None）
  check()      -> judge.Result       由 runner 跑判準；通過時 judge 已關閉 issue
回傳 "done" 或 "stuck"。
"""
import unittest
from types import SimpleNamespace as NS

import _path  # noqa: F401
from fake_github import world

from epiclib import retry


def agent_result(text="完成", criteria_wrong=None, timed_out=False):
    return NS(text=text, criteria_wrong=criteria_wrong, timed_out=timed_out)


class Retry(unittest.TestCase):
    def setUp(self):
        self.gh = world()
        self.epic, self.issue = self.gh.issue(1), self.gh.issue(2)
        self.prevs = []

    def attempt(self, results=None):
        results = list(results or [])

        def f(prev):
            self.prevs.append(prev)
            return results.pop(0) if results else agent_result()
        return f

    def check(self, oks):
        oks = list(oks)

        def f():
            ok = oks.pop(0)
            if ok:
                self.gh.close(2)
            return NS(ok=ok, summary=f"判準輸出-{len(oks)}")
        return f

    def test_pass_first_time(self):
        self.assertEqual(retry.work(self.gh, self.epic, self.issue, self.attempt(), self.check([True])), "done")
        self.assertEqual(self.prevs, [None])
        self.assertNotIn(("add_label", 2, "swarm:stuck"), self.gh.calls)

    def test_pass_on_third(self):
        r = retry.work(self.gh, self.epic, self.issue, self.attempt(), self.check([False, False, True]))
        self.assertEqual(r, "done")
        self.assertEqual(len(self.prevs), 3)
        self.assertIsNone(self.prevs[0])
        self.assertIn("判準輸出-2", self.prevs[1])
        self.assertIn("判準輸出-1", self.prevs[2])

    def test_three_failures_mark_stuck(self):
        r = retry.work(self.gh, self.epic, self.issue, self.attempt(), self.check([False, False, False]))
        self.assertEqual(r, "stuck")
        self.assertEqual(len(self.prevs), 3)
        self.assertIn(("add_label", 2, "swarm:stuck"), self.gh.calls)
        self.assertEqual(self.gh.issues[2]["state"], "open")

    def test_stuck_does_not_switch_to_human(self):
        retry.work(self.gh, self.epic, self.issue, self.attempt(), self.check([False] * 3))
        labels = self.gh.issues[2]["labels"]
        self.assertIn("by:swarm", labels)
        self.assertNotIn("by:human", labels)
        self.assertFalse([c for c in self.gh.calls if c[0] == "remove_label"])

    def test_each_attempt_is_commented(self):
        retry.work(self.gh, self.epic, self.issue, self.attempt(), self.check([False] * 3))
        text = "\n".join(self.gh.comments.get(2, []))
        for k in ("1/3", "2/3", "3/3"):
            self.assertIn(k, text)

    def test_criteria_wrong_stops_immediately(self):
        r = retry.work(self.gh, self.epic, self.issue,
                       self.attempt([agent_result(criteria_wrong="第 1 列不對")]), self.check([True]))
        self.assertEqual(r, "stuck")
        self.assertEqual(len(self.prevs), 1)
        self.assertIn(("add_label", 2, "swarm:stuck"), self.gh.calls)
        self.assertIn("第 1 列不對", "\n".join(self.gh.comments[2]))
        self.assertNotIn(("close", 2), self.gh.calls)

    def test_timeout_counts_as_attempt(self):
        r = retry.work(self.gh, self.epic, self.issue,
                       self.attempt([agent_result(timed_out=True)]), self.check([False, True]))
        self.assertEqual(r, "done")
        self.assertEqual(len(self.prevs), 2)


if __name__ == "__main__":
    unittest.main()
