"""#474 epic_autostart：已放行且有可接的 EPIC 自動在背景啟動 orchestrator。#484：少查詢。

假的 GitHub（fake_github.world）、假的啟動器；鎖用真的 flock（與 epiclib/round 同一把）。
"""
import contextlib
import io
import json
import os
import signal
import sys
import tempfile
import time
import unittest

import _path  # noqa: F401
from fake_github import GO_AT, FakeGitHub, world

import epic_autostart as auto


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, log_path, lock_fd):
        self.calls.append((cmd, log_path))


class Autostart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock_dir = os.path.join(self.tmp.name, "locks")
        self.log_dir = os.path.join(self.tmp.name, "autostart")
        self.launch = FakeLauncher()

    def tearDown(self):
        self.tmp.cleanup()

    def run_auto(self, gh, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = auto.main(["--repo", "o/r", "--lock-dir", self.lock_dir, "--log-dir", self.log_dir, *extra],
                           gh=gh, launcher=self.launch)
        self.assertEqual(rc, 0)
        return out.getvalue()

    def test_go_and_takeable_starts(self):
        out = self.run_auto(world(), "--engine", "codex", "--max-bees", "2")
        self.assertEqual(len(self.launch.calls), 1)
        cmd, log = self.launch.calls[0]
        self.assertEqual(cmd[1:], [auto.EPIC_RUN, "--repo", "o/r", "--epic", "1", "--swarm", "--engine", "codex",
                                   "--max-bees", "2"])
        self.assertEqual(log, os.path.join(self.log_dir, "epic1.log"))
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("檢查 1 個 EPIC", out)
        self.assertIn("啟動 #1", out)

    def test_default_engine_claude(self):
        self.run_auto(world())
        cmd, _ = self.launch.calls[0]
        self.assertEqual(cmd[cmd.index("--engine") + 1], "claude")
        self.assertNotIn("--max-bees", cmd)

    def test_not_released_not_started(self):
        # 沒有 swarm:go 的 EPIC 不在那一次查詢的結果裡，不檢查
        out = self.run_auto(world(i1={"labels": ["epic"], "go_at": None}))
        self.assertEqual(self.launch.calls, [])
        self.assertIn("檢查 0 個 EPIC", out)

    def test_label_without_go_time_skipped(self):
        out = self.run_auto(world(i1={"go_at": None}))
        self.assertEqual(self.launch.calls, [])
        self.assertIn("#1（沒有 swarm:go）", out)

    def test_nothing_takeable_not_started(self):
        gh = world(i2={"state": "closed"}, i3={"state": "closed"}, i4={"state": "closed"})
        out = self.run_auto(gh)
        self.assertEqual(self.launch.calls, [])
        self.assertIn("#1（沒有 open、by:swarm 且未 stuck 的 sub-issue）", out)

    def test_already_running_not_started_again(self):
        held = auto.try_lock(self.lock_dir, 1)
        self.assertIsNotNone(held)
        try:
            out = self.run_auto(world())
        finally:
            os.close(held)
        self.assertEqual(self.launch.calls, [])
        self.assertIn("#1（已經在跑）", out)

    def test_same_lock_file_as_round(self):
        # epiclib/round.run_round 用 <lock_dir>/epic-<N>.lock
        held = auto.try_lock(self.lock_dir, 7)
        try:
            self.assertTrue(os.path.exists(os.path.join(self.lock_dir, "epic-7.lock")))
        finally:
            os.close(held)

    def test_dry_run_does_not_start(self):
        out = self.run_auto(world(), "--dry-run")
        self.assertEqual(self.launch.calls, [])
        self.assertIn("dry-run", out)
        self.assertIn("#1", out)
        # dry-run 不拿著鎖
        fd = auto.try_lock(self.lock_dir, 1)
        self.assertIsNotNone(fd)
        os.close(fd)

    def test_mixed_summary(self):
        gh = world(i10={"title": "[EPIC] 沒放行", "labels": ["epic"], "type": "Epic"})
        out = self.run_auto(gh)
        self.assertIn("檢查 1 個 EPIC", out)
        self.assertIn("啟動 #1", out)
        self.assertNotIn("#10", out)

    def test_returns_immediately_and_child_holds_lock(self):
        # 真的啟動器、假的 epic_run（睡 30 秒）：main 要立刻返回，子行程活著時鎖被拿著
        slow = os.path.join(self.tmp.name, "slow_epic_run.py")
        with open(slow, "w") as f:
            f.write("import time\ntime.sleep(30)\n")
        procs = []

        def real(cmd, log_path, fd):
            procs.append(auto.launch(cmd, log_path, fd))

        t0 = time.monotonic()
        rep = auto.run(world(), "o/r", lock_dir=self.lock_dir, log_dir=self.log_dir, launcher=real, script=slow)
        elapsed = time.monotonic() - t0
        try:
            self.assertEqual(rep["started"], [1])
            self.assertLess(elapsed, 5)
            self.assertIsNone(procs[0].poll())                       # 還在跑，沒有等它
            self.assertNotEqual(os.getsid(procs[0].pid), os.getsid(0))  # start_new_session
            self.assertTrue(os.path.exists(os.path.join(self.log_dir, "epic1.log")))
            self.assertIsNone(auto.try_lock(self.lock_dir, 1))       # 子行程繼承鎖
            rep2 = auto.run(world(), "o/r", lock_dir=self.lock_dir, log_dir=self.log_dir, launcher=real, script=slow)
            self.assertEqual(rep2["started"], [])
            self.assertEqual(rep2["skipped"], {1: "已經在跑"})
        finally:
            for p in procs:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        fd = auto.try_lock(self.lock_dir, 1)                         # 子行程結束，鎖自動釋放
        self.assertIsNotNone(fd)
        os.close(fd)


class FewerQueries(unittest.TestCase):
    """#484：先一次查詢篩選，必要時才逐張檢查；輸出含 GraphQL 呼叫次數。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock_dir = os.path.join(self.tmp.name, "locks")
        self.launch = FakeLauncher()

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, gh):
        return auto.run(gh, "o/r", lock_dir=self.lock_dir, log_dir=os.path.join(self.tmp.name, "log"),
                        launcher=self.launch)

    def issue_reads(self, gh, n=None):
        return [r for r in gh.reads if r[0] == "issue" and (n is None or r[1] == n)]

    def test_nothing_to_do_uses_one_query(self):
        gh = world(i1={"labels": ["epic"], "go_at": None})       # 沒有放行的 EPIC
        rep = self.go(gh)
        self.assertEqual(gh.gql_calls, 1)
        self.assertEqual(gh.reads, [("go_epics",)])
        self.assertEqual(rep["calls"], 1)
        self.assertEqual(self.launch.calls, [])

    def test_no_epics_uses_one_query(self):
        gh = FakeGitHub({})
        rep = self.go(gh)
        self.assertEqual(rep, {"checked": 0, "started": [], "skipped": {}, "calls": 1})

    def test_epic_review_not_checked(self):
        gh = world(i1={"labels": ["epic", "swarm:go", "epic:review"]})
        rep = self.go(gh)
        self.assertEqual(gh.reads, [("go_epics",)])
        self.assertEqual(rep["calls"], 1)
        self.assertEqual(rep["started"], [])
        self.assertNotIn(1, rep["skipped"])

    def test_only_human_not_checked(self):
        gh = world(i2={"state": "closed"}, i3={"state": "closed"}, i4={"state": "closed"})   # 只剩 #5 by:human
        rep = self.go(gh)
        self.assertEqual(self.issue_reads(gh), [])
        self.assertEqual(rep["calls"], 1)
        self.assertEqual(self.launch.calls, [])
        self.assertIn("by:swarm", rep["skipped"][1])

    def test_all_stuck_not_checked(self):
        stuck = {"labels": ["by:swarm", "swarm:stuck"]}
        gh = world(i2=stuck, i3=stuck, i4=stuck)
        rep = self.go(gh)
        self.assertEqual(self.issue_reads(gh), [])
        self.assertEqual(rep["calls"], 1)
        self.assertEqual(self.launch.calls, [])
        self.assertIn("stuck", rep["skipped"][1])

    def test_takeable_still_starts_and_checks_only_candidates(self):
        gh = world(i10={"title": "[EPIC] 審查中", "labels": ["epic", "swarm:go", "epic:review"], "type": "Epic",
                        "go_at": GO_AT},
                   i11={"title": "審查中的子項", "labels": ["by:swarm"], "parent": 10})
        rep = self.go(gh)
        self.assertEqual(rep["started"], [1])
        self.assertEqual(len(self.launch.calls), 1)
        # 只逐張查 EPIC #1 的候選（#2 #3 #4），不查 by:human 的 #5、也不查 epic:review 的 #10/#11
        self.assertEqual(sorted(r[1] for r in self.issue_reads(gh)), [2, 3, 4])
        self.assertEqual(rep["calls"], 1 + 3)

    def test_stuck_mixed_checks_only_unstuck(self):
        gh = world(i2={"labels": ["by:swarm", "swarm:stuck"]})
        rep = self.go(gh)
        self.assertEqual(sorted(r[1] for r in self.issue_reads(gh)), [3, 4])
        self.assertEqual(rep["started"], [])
        self.assertEqual(rep["skipped"][1], "沒有可接的 sub-issue")

    def test_output_has_query_count(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            auto.main(["--repo", "o/r", "--lock-dir", self.lock_dir, "--log-dir", self.tmp.name, "--dry-run"],
                      gh=world(), launcher=self.launch)
        self.assertIn("GraphQL 呼叫 4 次", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            auto.main(["--repo", "o/r", "--lock-dir", self.lock_dir, "--log-dir", self.tmp.name],
                      gh=FakeGitHub({}), launcher=self.launch)
        self.assertIn("GraphQL 呼叫 1 次", out.getvalue())


class RealGitHubGoEpics(unittest.TestCase):
    """GitHub.go_epics：一次 GraphQL，把回應轉成摘要。"""

    def test_one_graphql_call_and_shape(self):
        from epiclib.github import GitHub
        gh = GitHub("o/r")
        seen = []

        def fake_run(*args, input=None):
            seen.append(args)
            return json.dumps({"data": {"search": {"nodes": [
                {"number": 9, "state": "OPEN", "labels": {"nodes": [{"name": "epic"}, {"name": "swarm:go"}]},
                 "timelineItems": {"nodes": [
                     {"createdAt": "2026-10-01T00:00:00Z", "label": {"name": "swarm:go"}},
                     {"createdAt": "2026-10-02T00:00:00Z", "label": {"name": "epic"}},
                     {"createdAt": "2026-10-03T00:00:00Z", "label": {"name": "swarm:go"}}, {}]},
                 "subIssues": {"nodes": [{"number": 10, "state": "CLOSED", "labels": {"nodes": [{"name": "by:swarm"}]}}]}},
                {},
            ]}}})

        gh._run = fake_run
        got = gh.go_epics()
        self.assertEqual(len(seen), 1)
        self.assertEqual(gh.gql_calls, 1)
        q = " ".join(seen[0])
        for part in ("repo:o/r", "is:open", "label:epic", 'label:"swarm:go"', '-label:"epic:review"'):
            self.assertIn(part, q)
        self.assertEqual(got, [{"number": 9, "state": "open", "labels": ["epic", "swarm:go"],
                                "go_at": "2026-10-03T00:00:00Z",
                                "subs": [{"number": 10, "state": "closed", "labels": ["by:swarm"]}],
                                "truncated": False}])

    def run_go(self, node):
        from epiclib.github import GitHub
        gh = GitHub("o/r")
        gh._run = lambda *a, input=None: json.dumps({"data": {"search": {"nodes": [node]}}})
        return gh.go_epics()[0]

    @staticmethod
    def node(subs=50, sub_total=None, labels=2, label_total=None, sub_label_total=1, tl_total=1, go=True):
        names = ["epic", "swarm:go"] + [f"l{i}" for i in range(labels - 2)]
        ev = [{"createdAt": "2026-10-01T00:00:00Z", "label": {"name": "swarm:go" if go else "x"}}]
        return {"number": 9, "state": "OPEN",
                "labels": {"totalCount": label_total or labels, "nodes": [{"name": x} for x in names]},
                "timelineItems": {"totalCount": tl_total, "nodes": ev},
                "subIssues": {"totalCount": sub_total or subs, "nodes": [
                    {"number": 100 + i, "state": "OPEN",
                     "labels": {"totalCount": sub_label_total, "nodes": [{"name": "by:swarm"}]}}
                    for i in range(subs)]}}

    def test_cost_limits_in_query(self):
        # #488：cost 52 → 11 的上限
        import re
        from epiclib.github import Q_GO_EPICS as q
        for part in ("search(query:$s,type:ISSUE,first:20)", "labels(first:10)", "last:20)",
                     "subIssues(first:50)"):
            self.assertIn(part, q)
        self.assertLessEqual(max(int(x) for x in re.findall(r"(?:first|last):(\d+)", q)), 50)
        self.assertEqual(q.count("totalCount"), 4)

    def test_not_truncated_at_limit(self):
        self.assertFalse(self.run_go(self.node(subs=50))["truncated"])

    def test_truncated_when_subs_over_limit(self):
        got = self.run_go(self.node(subs=50, sub_total=51))
        self.assertTrue(got["truncated"])
        self.assertEqual(len(got["subs"]), 50)

    def test_truncated_when_labels_over_limit(self):
        self.assertTrue(self.run_go(self.node(subs=1, labels=10, label_total=11))["truncated"])

    def test_truncated_when_sub_labels_over_limit(self):
        self.assertTrue(self.run_go(self.node(subs=1, sub_label_total=11))["truncated"])

    def test_timeline_truncation_only_matters_without_go(self):
        # 最近 20 個 LABELED 事件裡有 swarm:go：最新的那次就在裡面，不必退回
        self.assertFalse(self.run_go(self.node(subs=1, tl_total=21))["truncated"])
        self.assertTrue(self.run_go(self.node(subs=1, tl_total=21, go=False))["truncated"])


class LimitedFake(FakeGitHub):
    """模擬 Q_GO_EPICS 的上限：sub-issue 只回前 SUBS 張、labels 只回前 LABELS 個，超過就標 truncated。"""
    SUBS, LABELS = 50, 10

    def go_epics(self):
        out = []
        for e in super().go_epics():
            cut = len(e["subs"]) > self.SUBS or len(e["labels"]) > self.LABELS
            e["subs"], e["labels"] = e["subs"][:self.SUBS], e["labels"][:self.LABELS]
            out.append({**e, "truncated": cut})
        return out


class FallbackWhenTruncated(unittest.TestCase):
    """#488：摘要被上限截斷時，epic_autostart 退回逐張檢查，不漏判。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.launch = FakeLauncher()

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, gh):
        return auto.run(gh, "o/r", lock_dir=os.path.join(self.tmp.name, "locks"),
                        log_dir=os.path.join(self.tmp.name, "log"), launcher=self.launch)

    @staticmethod
    def big(takeable_last=True):
        # EPIC #1 底下 #2..#52 共 51 張；前 50 張（#2..#51）都是 by:human，只有第 51 張 #52 可接
        over = {f"i{k}": {"labels": ["by:human"], "parent": 1} for k in range(2, 52)}
        ok = world().issues[2]["body"]
        over["i52"] = {"title": "第 51 張", "body": ok, "labels": ["by:swarm"] if takeable_last else ["by:human"],
                       "parent": 1}
        return LimitedFake(world(**over).issues)

    def test_subs_at_limit_falls_back_and_starts(self):
        gh = self.big()
        rep = self.go(gh)
        self.assertEqual(rep["started"], [1])                  # 只看摘要會漏掉 #52
        reads = [r[1] for r in gh.reads if r[0] == "issue"]
        self.assertEqual(reads[0], 1)                          # 先重查 EPIC 本身
        self.assertEqual(sorted(reads[1:]), list(range(2, 53)))  # 再逐張檢查全部 sub-issue
        self.assertEqual(rep["calls"], 1 + 1 + 51)

    def test_subs_at_limit_nothing_takeable(self):
        rep = self.go(self.big(takeable_last=False))
        self.assertEqual(rep["started"], [])
        self.assertIn("逐張檢查", rep["skipped"][1])

    def test_without_fallback_would_miss(self):
        # 對照：不標 truncated 的截斷摘要會漏判，證明上面是靠退回逐張檢查才接到 #52
        gh = self.big()
        gh.go_epics = lambda: [{**e, "truncated": False} for e in LimitedFake.go_epics(gh)]
        self.assertEqual(self.go(gh)["started"], [])

    def test_labels_over_limit_falls_back(self):
        # swarm:go 排在第 11 個 label 之後：摘要裡看不到，逐張檢查才不會誤判成「沒有 swarm:go」
        labels = ["epic"] + [f"area:{i}" for i in range(10)] + ["swarm:go"]
        gh = LimitedFake(world(i1={"labels": labels}).issues)
        rep = self.go(gh)
        self.assertEqual(rep["started"], [1])
        self.assertNotIn(1, rep["skipped"])

    def test_under_limit_uses_summary(self):
        gh = LimitedFake(world().issues)
        rep = self.go(gh)
        self.assertEqual(rep["started"], [1])
        self.assertEqual(sorted(r[1] for r in gh.reads if r[0] == "issue"), [2, 3, 4])   # 沒有重查 EPIC、#5


if __name__ == "__main__":
    unittest.main()
