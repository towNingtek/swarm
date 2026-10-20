"""#399 一輪主程式與 EPIC 總結。契約第 5、6 節。

run_round(gh, epic_n, model, work, lock_dir) -> Report
  work(epic, issue) -> "done" | "stuck"    （正式版接 retry.work + agent + judge）
"""
import os
import subprocess
import sys
import tempfile
import threading
import unittest

import _path  # noqa: F401
from fake_github import world

from epiclib import round as epic_round

MODEL = ["claude", "claude-opus-5-5"]


class Round(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.lock = self.d.name
        self.order = []

    def tearDown(self):
        self.d.cleanup()

    def worker(self, gh, outcome=None):
        outcome = outcome or {}

        def work(epic, issue):
            n = issue["number"]
            self.order.append(n)
            r = outcome.get(n, "done")
            if r == "done":
                gh.close(n)
            else:
                gh.add_label(n, "swarm:stuck")
            return r
        return work

    def test_follows_chain_until_nothing_takeable(self):
        gh = world()
        rep = epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertEqual(self.order, [2, 3, 4])
        self.assertEqual(sorted(rep.done), [2, 3, 4])
        self.assertIn(5, rep.skipped)

    def test_start_comment_names_model(self):
        gh = world()
        epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertIn("claude-opus-5-5", gh.comments[1][0])

    def test_model_required(self):
        gh = world()
        with self.assertRaises(ValueError):
            epic_round.run_round(gh, 1, None, self.worker(gh), self.lock)
        self.assertEqual(gh.writes(), [])

    def test_not_an_epic(self):
        gh = world()
        with self.assertRaises(ValueError):
            epic_round.run_round(gh, 2, MODEL, self.worker(gh), self.lock)

    def test_stuck_stops_the_chain(self):
        gh = world()
        rep = epic_round.run_round(gh, 1, MODEL, self.worker(gh, {3: "stuck"}), self.lock)
        self.assertEqual(self.order, [2, 3])
        self.assertEqual(rep.stuck, [3])
        self.assertNotIn("epic:review", gh.issues[1]["labels"])

    def test_each_issue_at_most_once_per_round(self):
        gh = world()

        def work(epic, issue):          # 回報 done 卻沒關：不能一直重跑
            self.order.append(issue["number"])
            return "done"
        epic_round.run_round(gh, 1, MODEL, work, self.lock)
        self.assertEqual(self.order, [2])

    def test_all_closed_posts_summary_and_review(self):
        gh = world(i5={"state": "closed"})
        epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertIn("epic:review", gh.issues[1]["labels"])
        last = gh.comments[1][-1]
        for n in ("#2", "#3", "#4", "#5"):
            self.assertIn(n, last)

    def test_human_sub_open_means_no_review(self):
        gh = world()
        epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertNotIn("epic:review", gh.issues[1]["labels"])
        self.assertIn("#5", gh.comments[1][-1])

    def test_never_closes_epic(self):
        gh = world(i5={"state": "closed"})
        epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertEqual(gh.issues[1]["state"], "open")
        self.assertNotIn(1, gh.closed_calls())

    def test_no_takeable_still_reports(self):
        gh = world(i1={"labels": ["epic"], "go_at": None})
        rep = epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        self.assertEqual(self.order, [])
        self.assertEqual(rep.done, [])
        self.assertIn("swarm:go", gh.comments[1][-1])

    def test_same_epic_cannot_run_twice(self):
        gh = world()
        entered, release = threading.Event(), threading.Event()

        def slow(epic, issue):
            entered.set()
            release.wait(5)
            gh.close(issue["number"])
            return "done"
        t = threading.Thread(target=epic_round.run_round, args=(gh, 1, MODEL, slow, self.lock))
        t.start()
        self.assertTrue(entered.wait(5))
        try:
            with self.assertRaises(epic_round.Busy):
                epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)
        finally:
            release.set()
            t.join(5)
        epic_round.run_round(gh, 1, MODEL, self.worker(gh), self.lock)   # 跑完後鎖會釋放


class Cli(unittest.TestCase):
    def test_help(self):
        here = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(os.path.dirname(here), "epic_run.py")
        r = subprocess.run([sys.executable, script, "--help"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        for opt in ("--repo", "--epic", "--model"):
            self.assertIn(opt, r.stdout)


if __name__ == "__main__":
    unittest.main()
