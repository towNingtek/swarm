"""#416 orchestrator：廣度、深度、驗收、收成果、benchmark 記錄。

orchestrator 用假的蜂（FakeBee）與假的 GitHub，不叫任何模型、不碰真的 repo 遠端；
git 合併用暫存的本機 repo 實跑。
"""
import os
import subprocess
import tempfile
import threading
import time
import unittest

import _path  # noqa: F401
from fake_github import GO_AT, FakeGitHub, criteria_body

from epiclib import swarm


def sh(d, *a):
    return subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True).stdout.strip()


def make_world(n_parallel=3, chain=False):
    ok = criteria_body("做事", ["`test -f done-$ISSUE`"])
    issues = {1: {"title": "[EPIC] 測試", "body": "## 目標\n\nx\n", "labels": ["epic", "swarm:go"],
                  "type": "Epic", "go_at": GO_AT}}
    for i in range(2, 2 + n_parallel):
        issues[i] = {"title": f"工作 {i}", "body": ok, "labels": ["by:swarm"], "parent": 1,
                     "blocked_by": [i - 1] if chain and i > 2 else []}
    return FakeGitHub(issues)


class FakeBee:
    """假的蜂：每次 send 依劇本回覆；可以寫檔、可以等一下，記錄同時有幾隻在跑。"""
    live = 0
    peak = 0
    lock = threading.Lock()

    def __init__(self, engine, model, script):
        self.engine, self.model, self.script = engine, model, list(script)
        self.sent = []

    def start(self, workdir, prompt):
        self.workdir = workdir
        with FakeBee.lock:
            FakeBee.live += 1
            FakeBee.peak = max(FakeBee.peak, FakeBee.live)
        return self.send(prompt)

    def send(self, text):
        self.sent.append(text)
        step = self.script.pop(0) if self.script else {"reply": "完成。"}
        time.sleep(step.get("sleep", 0.05))
        for f, content in step.get("write", {}).items():
            with open(os.path.join(self.workdir, f.replace("$ISSUE", os.path.basename(self.workdir).split("-")[-1])), "w") as fh:
                fh.write(content)
        return swarm.Turn(text=step.get("reply", ""), timed_out=step.get("timeout", False))

    def stop(self):
        with FakeBee.lock:
            FakeBee.live -= 1


class Base(unittest.TestCase):
    def setUp(self):
        FakeBee.live = FakeBee.peak = 0
        self.d = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.d.name, "repo")
        os.makedirs(self.repo)
        sh(self.repo, "init", "-q")
        sh(self.repo, "symbolic-ref", "HEAD", "refs/heads/master")     # old git: no `init -b`
        sh(self.repo, "config", "user.email", "t@t"); sh(self.repo, "config", "user.name", "t")
        open(os.path.join(self.repo, "README"), "w").write("x\n")
        sh(self.repo, "add", "-A"); sh(self.repo, "commit", "-qm", "init")
        self.cfg = swarm.Config(max_bees=2, per_engine={"claude": 2, "codex": 1, "opencode": 2},
                                max_minutes=5, max_nudges=2, max_tries=2,
                                stop_patterns=swarm.DEFAULT_STOP_PATTERNS)
        self.policy = swarm.Policy(allowed={"claude", "codex", "opencode"}, deny_prefixes=["claude-code/"])
        self.scripts = {}

    def tearDown(self):
        self.d.cleanup()

    def bee_factory(self, engine, model):
        def make(issue_n):
            return FakeBee(engine, model, self.scripts.get(issue_n, [{"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]))
        return make

    def run_epic(self, gh, engine="claude", model="claude-opus-5-5", **kw):
        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg,
                                  policy=self.policy, engine=engine, model=model,
                                  bee_factory=self.bee_factory(engine, model),
                                  state_dir=os.path.join(self.d.name, "state"), push=False, **kw)
        return orch.run()


class Breadth(Base):
    def test_never_more_than_max_bees_or_per_engine(self):
        gh = make_world(n_parallel=5)
        for n in range(2, 7):
            self.scripts[n] = [{"sleep": 0.3, "write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        rep = self.run_epic(gh)
        self.assertLessEqual(FakeBee.peak, 2)                      # max_bees
        self.assertEqual(rep.metrics["peak_bees"], FakeBee.peak)
        self.assertGreaterEqual(FakeBee.peak, 2)                   # it did go parallel
        self.cfg.max_bees = 5
        gh = make_world(n_parallel=4)
        FakeBee.peak = 0
        for n in range(2, 6):
            self.scripts[n] = [{"sleep": 0.3, "write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        self.run_epic(gh, engine="codex", model="gpt-5")
        self.assertLessEqual(FakeBee.peak, 1)                      # per_engine codex = 1

    def test_blocked_issues_wait_for_dependencies(self):
        gh = make_world(n_parallel=3, chain=True)
        order = []
        orig = self.bee_factory

        def factory(engine, model):
            mk = orig(engine, model)

            def make(n):
                order.append(n)
                return mk(n)
            return make
        self.bee_factory = factory
        rep = self.run_epic(gh)
        self.assertEqual(order, [2, 3, 4])                          # each waited for the previous
        self.assertEqual(rep.metrics["peak_bees"], 1)


class Depth(Base):
    def test_question_at_the_end_is_a_stop_and_gets_nudged(self):
        gh = make_world(n_parallel=1)
        self.scripts[2] = [{"reply": "第 1 階段已完成。要不要繼續第 2 階段？"},
                           {"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        rep = self.run_epic(gh)
        self.assertEqual(gh.issue(2)["state"], "closed")
        self.assertEqual(rep.metrics["nudges"], 1)
        self.assertTrue(swarm.is_stop("今天已經很晚了，要不要先到這裡？", self.cfg.stop_patterns))
        self.assertTrue(swarm.is_stop("Shall I continue with step 2?", self.cfg.stop_patterns))
        self.assertFalse(swarm.is_stop("完成：三個檔案都已更新，測試通過。", self.cfg.stop_patterns))

    def test_after_max_nudges_it_counts_as_a_failed_try(self):
        gh = make_world(n_parallel=1)
        self.scripts[2] = [{"reply": "要繼續嗎？"}] * 10
        rep = self.run_epic(gh)
        self.assertIn("swarm:stuck", gh.issue(2)["labels"])
        self.assertEqual(rep.metrics["per_issue"][2]["tries"], self.cfg.max_tries)
        self.assertEqual(rep.metrics["nudges"], self.cfg.max_nudges * self.cfg.max_tries)


class Criteria(Base):
    def test_issue_is_not_closed_when_criteria_fail_even_if_bee_says_done(self):
        gh = make_world(n_parallel=1)
        self.scripts[2] = [{"reply": "全部完成了！"}] * 4          # never writes the file
        rep = self.run_epic(gh)
        self.assertEqual(gh.issue(2)["state"], "open")
        self.assertIn("swarm:stuck", gh.issue(2)["labels"])
        self.assertEqual(rep.done, [])


class Collect(Base):
    def test_merge_conflict_marks_stuck(self):
        gh = make_world(n_parallel=2)
        # both bees write the same file differently; the criteria still pass for each
        self.scripts[2] = [{"write": {"done-$ISSUE": "ok", "shared.txt": "A\n"}, "reply": "完成。"}]
        self.scripts[3] = [{"write": {"done-$ISSUE": "ok", "shared.txt": "B\n"}, "reply": "完成。"}]
        rep = self.run_epic(gh)
        stuck = [n for n in (2, 3) if "swarm:stuck" in gh.issue(n)["labels"]]
        self.assertEqual(len(stuck), 1)
        self.assertTrue(any("衝突" in c for c in gh.comments.get(stuck[0], [])))
        self.assertEqual(len(rep.done), 1)

    def test_all_pass_opens_one_summary_pr_and_review(self):
        gh = make_world(n_parallel=3)
        prs = []
        rep = self.run_epic(gh, open_pr=lambda branch, title, body: prs.append((branch, title, body)) or "https://pr/1")
        self.assertEqual(sorted(rep.done), [2, 3, 4])
        self.assertTrue(rep.review)
        self.assertIn("epic:review", gh.issue(1)["labels"])
        self.assertEqual(len(prs), 1)
        branch, title, body = prs[0]
        self.assertEqual(branch, "epic/1")
        self.assertIn("agent", body)                                 # benchmark table in the PR
        files = sh(self.repo, "ls-tree", "--name-only", "epic/1").split()
        self.assertEqual(sorted(f for f in files if f.startswith("done-")), ["done-2", "done-3", "done-4"])


class Policy(Base):
    def test_policy_blocks_models_not_allowed(self):
        gh = make_world(n_parallel=1)
        with self.assertRaises(swarm.PolicyError):
            self.run_epic(gh, engine="opencode", model="claude-code/haiku")
        with self.assertRaises(swarm.PolicyError):
            self.run_epic(gh, engine="nope", model="x")
        self.assertEqual(gh.writes(), [])


class Metrics(Base):
    def test_benchmark_metrics_are_recorded(self):
        gh = make_world(n_parallel=2)
        self.scripts[2] = [{"reply": "要繼續嗎？"}, {"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        rep = self.run_epic(gh)
        m = rep.metrics
        for k in ("seconds", "agents_total", "peak_bees", "nudges", "per_issue", "passed", "engine", "model"):
            self.assertIn(k, m)
        self.assertEqual(m["agents_total"], 2)
        self.assertEqual(m["passed"], 2)
        self.assertTrue(os.path.isfile(rep.metrics_path))


class Single(Base):
    """A 組：一個 session 一次做完整個 EPIC，之後逐張跑判準。"""

    def test_single_session_does_all_then_judges_each(self):
        gh = make_world(n_parallel=3)
        made = []

        class OneBee(FakeBee):
            def send(self, text):
                self.sent.append(text)
                for n in (2, 3):                       # does two of three
                    open(os.path.join(self.workdir, f"done-{n}"), "w").write("ok")
                return swarm.Turn(text="全部完成。", usage={"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.5})

        def factory(engine, model):
            def make(n):
                b = OneBee(engine, model, [])
                made.append(b)
                return b
            return make
        self.bee_factory = factory
        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg, policy=self.policy,
                                  engine="claude", model="m", bee_factory=self.bee_factory("claude", "m"),
                                  state_dir=os.path.join(self.d.name, "state"), push=False)
        rep = orch.run_single()
        self.assertEqual(len(made), 1)                                  # one session only
        self.assertIn("#2", made[0].sent[0]) ; self.assertIn("#4", made[0].sent[0])   # whole EPIC in one prompt
        self.assertEqual(sorted(rep.done), [2, 3])
        self.assertEqual(rep.stuck, [4])
        self.assertIn("swarm:stuck", gh.issue(4)["labels"])
        self.assertEqual(gh.issue(2)["state"], "closed")
        m = rep.metrics
        self.assertEqual((m["mode"], m["agents_total"], m["peak_bees"]), ("single", 1, 1))
        self.assertEqual(m["usage"]["input_tokens"], 100)
        self.assertFalse(rep.review)                                    # not all closed
        files = sh(self.repo, "ls-tree", "--name-only", "epic/1").split()
        self.assertIn("done-2", files)


class Usage(Base):
    def test_usage_is_summed_over_bees_and_nudges(self):
        gh = make_world(n_parallel=1)

        class UBee(FakeBee):
            def send(self, text):
                t = super().send(text)
                t.usage = {"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.1}
                return t
        self.scripts[2] = [{"reply": "要繼續嗎？"}, {"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        orig = self.bee_factory
        self.bee_factory = lambda e, m: (lambda n: UBee(e, m, self.scripts.get(n, [])))
        rep = self.run_epic(gh)
        self.assertEqual(rep.metrics["usage"]["input_tokens"], 20)     # start + one nudge
        self.assertAlmostEqual(rep.metrics["usage"]["cost_usd"], 0.2)


class Running(Base):
    """#457：開工加 swarm:running 並留言引擎；通過、卡住、蜂出錯都一定移除。"""

    def watching_factory(self, gh, seen, boom=()):
        """蜂開工那一刻記下這張有沒有 swarm:running；boom 裡的 issue 蜂直接出錯。"""
        orig = self.bee_factory

        def factory(engine, model):
            mk = orig(engine, model)

            def make(n):
                b = mk(n)
                start = b.start

                def watched(workdir, prompt):
                    seen.setdefault(n, []).append("swarm:running" in gh.issue(n)["labels"])
                    if n in boom:
                        raise RuntimeError("蜂炸了")
                    return start(workdir, prompt)
                b.start = watched
                return b
            return make
        return factory

    def started_comments(self, gh, n):
        return [c for c in gh.comments.get(n, []) if c.startswith("🐝 開工")]

    def test_running_while_working_and_removed_when_passed(self):
        gh = make_world(n_parallel=2)
        seen = {}
        self.bee_factory = self.watching_factory(gh, seen)
        rep = self.run_epic(gh, engine="codex", model="gpt-5")
        self.assertEqual(sorted(rep.done), [2, 3])
        self.assertEqual(seen, {2: [True], 3: [True]})
        for n in (2, 3):
            self.assertNotIn("swarm:running", gh.issue(n)["labels"])
            self.assertIn(("add_label", n, "swarm:running"), gh.calls)
            self.assertIn(("remove_label", n, "swarm:running"), gh.calls)
            c = self.started_comments(gh, n)
            self.assertEqual(len(c), 1)
            self.assertIn("引擎 codex", c[0])
            self.assertRegex(c[0], r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")    # ISO start time
        # added before the bee started, removed after
        i_add = gh.calls.index(("add_label", 2, "swarm:running"))
        i_rm = gh.calls.index(("remove_label", 2, "swarm:running"))
        self.assertLess(i_add, i_rm)

    def test_removed_when_stuck(self):
        gh = make_world(n_parallel=1)
        self.scripts[2] = [{"reply": "全部完成了！"}] * 4          # criteria never pass
        seen = {}
        self.bee_factory = self.watching_factory(gh, seen)
        rep = self.run_epic(gh)
        self.assertEqual(rep.stuck, [2])
        self.assertEqual(seen[2], [True] * self.cfg.max_tries)
        self.assertIn("swarm:stuck", gh.issue(2)["labels"])
        self.assertNotIn("swarm:running", gh.issue(2)["labels"])
        self.assertIn(("remove_label", 2, "swarm:running"), gh.calls)

    def test_removed_when_bee_crashes(self):
        gh = make_world(n_parallel=2)
        seen = {}
        self.bee_factory = self.watching_factory(gh, seen, boom={2})
        rep = self.run_epic(gh)
        self.assertEqual(rep.stuck, [2])
        self.assertEqual(rep.done, [3])
        self.assertEqual(seen[2], [True])
        self.assertTrue(any("出錯" in c for c in gh.comments[2]))
        for n in (2, 3):
            self.assertNotIn("swarm:running", gh.issue(n)["labels"])
            self.assertIn(("remove_label", n, "swarm:running"), gh.calls)

    def test_single_marks_whole_batch_and_removes(self):
        gh = make_world(n_parallel=3)
        seen = {}

        class OneBee(FakeBee):
            def send(self, text):
                for n in (2, 3, 4):
                    seen[n] = "swarm:running" in gh.issue(n)["labels"]
                for n in (2, 3):                       # does two of three
                    open(os.path.join(self.workdir, f"done-{n}"), "w").write("ok")
                return swarm.Turn(text="全部完成。")

        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg, policy=self.policy,
                                  engine="opencode", model="m", bee_factory=lambda n: OneBee("opencode", "m", []),
                                  state_dir=os.path.join(self.d.name, "state"), push=False)
        rep = orch.run_single()
        self.assertEqual(rep.stuck, [4])
        self.assertEqual(seen, {2: True, 3: True, 4: True})
        for n in (2, 3, 4):
            self.assertNotIn("swarm:running", gh.issue(n)["labels"])
            self.assertIn(("remove_label", n, "swarm:running"), gh.calls)
            c = self.started_comments(gh, n)
            self.assertEqual(len(c), 1)
            self.assertIn("引擎 opencode", c[0])

    def test_single_removes_even_when_bee_crashes(self):
        gh = make_world(n_parallel=2)

        class Boom(FakeBee):
            def send(self, text):
                raise RuntimeError("蜂炸了")

        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg, policy=self.policy,
                                  engine="claude", model="m", bee_factory=lambda n: Boom("claude", "m", []),
                                  state_dir=os.path.join(self.d.name, "state"), push=False)
        with self.assertRaises(RuntimeError):
            orch.run_single()
        for n in (2, 3):
            self.assertIn(("add_label", n, "swarm:running"), gh.calls)
            self.assertNotIn("swarm:running", gh.issue(n)["labels"])


class ProjectStatus(Base):
    """#475：開工→「進行中 (7 天一輪)」、EPIC review→「審核後請關閉」；卡住不改回；set_status 失敗不影響結果。"""

    def test_running_sets_status_and_review_sets_epic_status(self):
        gh = make_world(n_parallel=2)
        rep = self.run_epic(gh)
        self.assertTrue(rep.review)
        for n in (2, 3):
            self.assertEqual(gh.status_calls(n), [("set_status", n, swarm.STATUS_RUNNING)])
            self.assertEqual(swarm.STATUS_RUNNING, "進行中 (7 天一輪)")
            # 開工時（加 swarm:running 時）就設定，在蜂關掉這張之前
            self.assertLess(gh.calls.index(("set_status", n, swarm.STATUS_RUNNING)), gh.calls.index(("close", n)))
        self.assertEqual(gh.status_calls(1), [("set_status", 1, swarm.STATUS_RUNNING), ("set_status", 1, swarm.STATUS_REVIEW)])
        self.assertEqual(swarm.STATUS_REVIEW, "審核後請關閉")
        self.assertGreater(gh.calls.index(("set_status", 1, swarm.STATUS_REVIEW)),
                           gh.calls.index(("add_label", 1, "epic:review")))

    def test_stuck_does_not_change_status_back(self):
        gh = make_world(n_parallel=1)
        self.scripts[2] = [{"reply": "全部完成了！"}] * 4          # criteria never pass
        rep = self.run_epic(gh)
        self.assertEqual(rep.stuck, [2])
        self.assertFalse(rep.review)
        self.assertEqual(gh.status_calls(2), [("set_status", 2, swarm.STATUS_RUNNING)])
        self.assertEqual(gh.status[2], swarm.STATUS_RUNNING)
        self.assertEqual(gh.status_calls(1), [("set_status", 1, swarm.STATUS_RUNNING)])   # EPIC not in review

    def test_set_status_failure_does_not_block_work_or_review(self):
        gh = make_world(n_parallel=2)

        def boom(n, name):
            gh.calls.append(("set_status", int(n), name))
            raise RuntimeError("Resource not accessible by integration")
        gh.set_status = boom
        rep = self.run_epic(gh)
        self.assertEqual(sorted(rep.done), [2, 3])
        self.assertEqual(rep.stuck, [])
        self.assertTrue(rep.review)
        self.assertIn("epic:review", gh.issue(1)["labels"])
        for n in (2, 3):
            self.assertEqual(gh.issue(n)["state"], "closed")
            self.assertEqual(len([c for c in gh.comments.get(n, []) if c.startswith("🐝 開工")]), 1)
        self.assertEqual(len(gh.status_calls()), 4)            # 2 bees + EPIC running + EPIC review

    def test_single_sets_status(self):
        gh = make_world(n_parallel=2)

        class OneBee(FakeBee):
            def send(self, text):
                for n in (2, 3):
                    seen[n] = gh.status.get(n)
                    open(os.path.join(self.workdir, f"done-{n}"), "w").write("ok")
                return swarm.Turn(text="全部完成。")
        seen = {}
        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg, policy=self.policy,
                                  engine="claude", model="m", bee_factory=lambda n: OneBee("claude", "m", []),
                                  state_dir=os.path.join(self.d.name, "state"), push=False)
        rep = orch.run_single()
        self.assertTrue(rep.review)
        self.assertEqual(seen, {2: swarm.STATUS_RUNNING, 3: swarm.STATUS_RUNNING})
        self.assertEqual(gh.status_calls(1), [("set_status", 1, swarm.STATUS_RUNNING), ("set_status", 1, swarm.STATUS_REVIEW)])


class EpicStatus(Base):
    """#497：第一隻蜂開工時 EPIC 也改成「進行中 (7 天一輪)」，一輪只改一次。"""

    def single(self, gh):
        script = [{"write": {f"done-{n}": "ok" for n in gh.issues if n != 1}, "reply": "全部完成。"}]
        orch = swarm.Orchestrator(gh, epic=1, repo_dir=self.repo, base="master", cfg=self.cfg, policy=self.policy,
                                  engine="claude", model="m", bee_factory=lambda n: FakeBee("claude", "m", script),
                                  state_dir=os.path.join(self.d.name, "state"), push=False)
        return orch.run_single()

    def test_first_bee_sets_epic_running(self):
        gh = make_world(n_parallel=1)
        self.run_epic(gh)
        self.assertEqual(gh.status_calls(1)[0], ("set_status", 1, swarm.STATUS_RUNNING))
        self.assertEqual(swarm.STATUS_RUNNING, "進行中 (7 天一輪)")
        # 在第一隻蜂開工（設 sub-issue 狀態）之前或同時，而不是等到結束
        self.assertLess(gh.calls.index(("set_status", 1, swarm.STATUS_RUNNING)),
                        gh.calls.index(("set_status", 2, swarm.STATUS_RUNNING)))

    def test_many_bees_change_epic_only_once(self):
        gh = make_world(n_parallel=4, chain=True)               # bees start in several waves
        rep = self.run_epic(gh)
        self.assertEqual(sorted(rep.done), [2, 3, 4, 5])
        self.assertEqual(gh.status_calls(1).count(("set_status", 1, swarm.STATUS_RUNNING)), 1)
        gh = make_world(n_parallel=4)                           # bees start in parallel
        self.run_epic(gh)
        self.assertEqual(gh.status_calls(1).count(("set_status", 1, swarm.STATUS_RUNNING)), 1)

    def test_all_pass_epic_still_ends_in_review(self):
        gh = make_world(n_parallel=2)
        rep = self.run_epic(gh)
        self.assertTrue(rep.review)
        self.assertEqual(gh.status_calls(1), [("set_status", 1, swarm.STATUS_RUNNING),
                                              ("set_status", 1, swarm.STATUS_REVIEW)])
        self.assertEqual(gh.status[1], swarm.STATUS_REVIEW)

    def test_single_sets_epic_running_once(self):
        gh = make_world(n_parallel=2)
        rep = self.single(gh)
        self.assertTrue(rep.review)
        self.assertEqual(gh.status_calls(1), [("set_status", 1, swarm.STATUS_RUNNING),
                                              ("set_status", 1, swarm.STATUS_REVIEW)])
        self.assertLess(gh.calls.index(("set_status", 1, swarm.STATUS_RUNNING)),
                        gh.calls.index(("set_status", 2, swarm.STATUS_RUNNING)))

    def test_nothing_takeable_does_not_change_epic(self):
        gh = make_world(n_parallel=1)
        gh.issues[2]["labels"].append("swarm:stuck")
        rep = self.run_epic(gh)
        self.assertEqual(rep.done, [])
        self.assertEqual(gh.status_calls(1), [])
        gh = make_world(n_parallel=1)
        gh.issues[2]["labels"].append("swarm:stuck")
        self.single(gh)
        self.assertEqual(gh.status_calls(1), [])

    def test_epic_already_in_review_is_not_changed(self):
        gh = make_world(n_parallel=1)
        gh.issues[1]["labels"].append("epic:review")
        self.run_epic(gh)
        self.assertNotIn(("set_status", 1, swarm.STATUS_RUNNING), gh.status_calls(1))
        gh = make_world(n_parallel=1)
        gh.issues[1]["labels"].append("epic:review")
        self.single(gh)
        self.assertNotIn(("set_status", 1, swarm.STATUS_RUNNING), gh.status_calls(1))

    def test_epic_status_failure_only_warns(self):
        gh = make_world(n_parallel=1)
        orig = gh.set_status

        def boom(n, name):
            if int(n) == 1:
                gh.calls.append(("set_status", 1, name))
                raise RuntimeError("no project")
            return orig(n, name)
        gh.set_status = boom
        rep = self.run_epic(gh)
        self.assertEqual(rep.done, [2])
        self.assertTrue(rep.review)


class GitHubSetStatus(unittest.TestCase):
    """真實 GitHub.set_status：只更新有 Status 欄位且有這個選項的 project。"""

    def test_updates_only_projects_with_the_option(self):
        from epiclib import github
        g = github.GitHub("o/r")
        sent = []
        items = {"repository": {"issue": {"projectItems": {"nodes": [
            {"id": "I1", "project": {"id": "P1", "title": "看板", "field": {"id": "F1", "options": [
                {"id": "O1", "name": "待辦"}, {"id": "O2", "name": "進行中 (7 天一輪)"}]}}},
            {"id": "I2", "project": {"id": "P2", "title": "沒有 Status", "field": None}},
            {"id": "I3", "project": {"id": "P3", "title": "沒有這個選項", "field": {"id": "F3", "options": [
                {"id": "O9", "name": "Done"}]}}},
        ]}}}}

        def gql(q, **v):
            if "updateProjectV2ItemFieldValue" in q:
                sent.append(v)
                return {}
            return items
        g._gql = gql
        self.assertEqual(g.set_status(5, "進行中 (7 天一輪)"), ["看板"])
        self.assertEqual(sent, [{"p": "P1", "i": "I1", "f": "F1", "v": "O2"}])


class AutoMerge(Base):
    """#465：EPIC 帶 swarm:auto-merge 且四條件成立才自動合併總 PR；不論如何 EPIC 都加 epic:review。"""

    SCOPE = "\n## 可以修改的範圍\n```\ndone-*\nsrc/**\n```\n"

    def world(self, auto=True, n_parallel=2, scope=SCOPE):
        gh = make_world(n_parallel=n_parallel)
        if auto:
            gh.issues[1]["labels"].append("swarm:auto-merge")
        for n in range(2, 2 + n_parallel):
            gh.issues[n]["body"] += scope
        return gh

    def run_auto(self, gh, mergeable=True, merge_ok=True):
        self.pr = {"opened": [], "merged": [], "comments": [], "asked": []}

        def open_pr(branch, title, body):
            self.pr["opened"].append((branch, title, body))
            return "https://github.com/o/r/pull/9"

        def pr_mergeable(url):
            self.pr["asked"].append(url)
            return mergeable

        def merge_pr(url):
            self.pr["merged"].append(url)
            return merge_ok, "" if merge_ok else "boom"

        return self.run_epic(gh, open_pr=open_pr, pr_mergeable=pr_mergeable, merge_pr=merge_pr,
                             comment_pr=lambda url, body: self.pr["comments"].append((url, body)))

    def epic_comments(self, gh, word):
        return [c for c in gh.comments.get(1, []) if word in c]

    def assert_not_merged(self, gh, rep, *why):
        self.assertEqual(self.pr["merged"], [])
        self.assertEqual(self.pr["comments"], [])
        self.assertFalse(rep.metrics["auto_merged"])
        self.assertIn("epic:review", gh.issue(1)["labels"])             # 照舊等人
        self.assertTrue(rep.review)
        notes = self.epic_comments(gh, "不自動合併")
        self.assertEqual(len(notes), 1)
        for w in why:
            self.assertIn(w, notes[0])
            self.assertTrue(any(w in r for r in rep.metrics["auto_merge_reasons"]))

    def test_without_label_does_not_merge(self):
        gh = self.world(auto=False)
        rep = self.run_auto(gh)
        self.assertEqual(len(self.pr["opened"]), 1)
        self.assertEqual(self.pr["merged"], [])
        self.assertEqual(self.pr["asked"], [])
        self.assertFalse(rep.metrics["auto_merged"])
        self.assertIn("swarm:auto-merge", rep.metrics["auto_merge_reasons"][0])
        self.assertIn("epic:review", gh.issue(1)["labels"])
        self.assertEqual(self.epic_comments(gh, "自動合併（`swarm:auto-merge`）"), [])

    def test_label_and_all_conditions_merge_and_comment(self):
        gh = self.world()
        self.scripts[2] = [{"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]
        rep = self.run_auto(gh)
        self.assertEqual(sorted(rep.done), [2, 3])
        self.assertEqual(self.pr["merged"], ["https://github.com/o/r/pull/9"])
        self.assertTrue(rep.metrics["auto_merged"])
        self.assertEqual(len(rep.metrics["auto_merge_reasons"]), 4)
        self.assertEqual(len(self.pr["comments"]), 1)
        url, body = self.pr["comments"][0]
        self.assertEqual(url, "https://github.com/o/r/pull/9")
        self.assertIn("自動合併", body)
        self.assertIn("可以修改的範圍", body)                         # 寫明理由
        notes = self.epic_comments(gh, "自動合併（`swarm:auto-merge`）")
        self.assertEqual(len(notes), 1)
        self.assertIn("pull/9", notes[0])
        # 合併後 EPIC 仍加 epic:review，由人驗收結案
        self.assertIn("epic:review", gh.issue(1)["labels"])
        self.assertTrue(rep.review)
        self.assertEqual(gh.issue(1)["state"], "open")

    def test_metrics_record_auto_merged(self):
        gh = self.world()
        rep = self.run_auto(gh)
        import json
        saved = json.load(open(rep.metrics_path, encoding="utf-8"))
        self.assertIs(saved["auto_merged"], True)
        self.assertTrue(saved["auto_merge_reasons"])
        summary = [c for c in gh.comments[1] if c.startswith("## 蜂群這一輪結束")]
        self.assertEqual(len(summary), 1)
        self.assertIn("auto_merged", summary[0])
        self.assertIn("true", summary[0])
        gh = self.world(auto=False)
        rep = self.run_auto(gh)
        saved = json.load(open(rep.metrics_path, encoding="utf-8"))
        self.assertIs(saved["auto_merged"], False)
        self.assertTrue(saved["auto_merge_reasons"])
        summary = [c for c in gh.comments[1] if c.startswith("## 蜂群這一輪結束")][0]
        self.assertIn("auto_merged", summary)
        self.assertIn("false", summary)

    def test_file_outside_scope_does_not_merge(self):
        gh = self.world()
        self.scripts[2] = [{"write": {"done-$ISSUE": "ok", "README": "changed\n"}, "reply": "完成。"}]
        rep = self.run_auto(gh)
        self.assertEqual(sorted(rep.done), [2, 3])
        self.assert_not_merged(gh, rep, "`README` 不在任何 sub-issue")

    def test_glob_scope_matches_nested_files(self):
        gh = self.world()
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        orig = self.bee_factory

        def factory(engine, model):
            mk = orig(engine, model)

            def make(n):
                b = mk(n)
                start = b.start

                def go(workdir, prompt):
                    os.makedirs(os.path.join(workdir, "src", "a"), exist_ok=True)
                    open(os.path.join(workdir, "src", "a", f"x{n}.py"), "w").write("x\n")
                    return start(workdir, prompt)
                b.start = go
                return b
            return make
        self.bee_factory = factory
        rep = self.run_auto(gh)
        self.assertTrue(rep.metrics["auto_merged"], rep.metrics["auto_merge_reasons"])

    def test_protected_paths_do_not_merge_even_if_in_scope(self):
        for path in (".github/workflows/ci.yml", "scheduler/model-policy.yaml"):
            with self.subTest(path=path):
                scope = self.SCOPE.replace("src/**", ".github/**\nscheduler/**")
                gh = self.world(scope=scope)
                d, f = os.path.split(path)
                self.scripts = {2: [{"write": {"done-$ISSUE": "ok"}, "reply": "完成。"}]}
                orig = self.bee_factory

                def factory(engine, model, d=d, f=f, orig=orig):
                    mk = orig(engine, model)

                    def make(n):
                        b = mk(n)
                        start = b.start

                        def go(workdir, prompt):
                            if n == 2:
                                os.makedirs(os.path.join(workdir, d), exist_ok=True)
                                open(os.path.join(workdir, d, f), "w").write("loosened\n")
                            return start(workdir, prompt)
                        b.start = go
                        return b
                    return make
                self.bee_factory = factory
                rep = self.run_auto(gh)
                self.bee_factory = orig
                self.assertEqual(sorted(rep.done), [2, 3])
                self.assert_not_merged(gh, rep, f"`{path}`", "不准自己放寬規則")
                sh(self.repo, "branch", "-D", "epic/1")                  # fresh EPIC branch for the next case
                for br in sh(self.repo, "branch", "--format=%(refname:short)").split():
                    if br.startswith("epic/1-bee-"):
                        sh(self.repo, "branch", "-D", br)

    def test_stuck_sub_issue_does_not_merge(self):
        gh = self.world(n_parallel=2)
        # #3 was closed earlier but still carries swarm:stuck (e.g. a person closed it after the bee gave up)
        gh.issues[3]["labels"].append("swarm:stuck")
        gh.issues[3]["state"] = "closed"
        rep = self.run_auto(gh)
        self.assertEqual(rep.done, [2])
        self.assert_not_merged(gh, rep, "#3 有 `swarm:stuck`")

    def test_not_by_swarm_does_not_merge(self):
        gh = self.world(n_parallel=2)
        gh.issues[3]["labels"] = ["by:human"]
        gh.issues[3]["state"] = "closed"
        rep = self.run_auto(gh)
        self.assert_not_merged(gh, rep, "#3 不是由 swarm 做的")

    def test_not_mergeable_does_not_merge(self):
        gh = self.world()
        rep = self.run_auto(gh, mergeable=False)
        self.assertEqual(self.pr["asked"], ["https://github.com/o/r/pull/9"])
        self.assert_not_merged(gh, rep, "不可合併")

    def test_unknown_mergeable_or_no_pr_does_not_merge(self):
        gh = self.world()
        rep = self.run_auto(gh, mergeable=None)
        self.assert_not_merged(gh, rep, "無法確認")
        gh = self.world()
        rep = self.run_epic(gh, open_pr=lambda b, t, body: "(--no-push)",
                            pr_mergeable=lambda u: True, merge_pr=lambda u: (True, ""))
        self.assertFalse(rep.metrics["auto_merged"])
        self.assertTrue(any("沒有總 PR" in r for r in rep.metrics["auto_merge_reasons"]))
        self.assertIn("epic:review", gh.issue(1)["labels"])

    def test_merge_failure_is_reported_not_merged(self):
        gh = self.world()
        rep = self.run_auto(gh, merge_ok=False)
        self.assertEqual(self.pr["merged"], ["https://github.com/o/r/pull/9"])   # tried
        self.assertFalse(rep.metrics["auto_merged"])
        self.assertTrue(any("合併失敗" in r for r in rep.metrics["auto_merge_reasons"]))
        self.assertEqual(len(self.epic_comments(gh, "不自動合併")), 1)
        self.assertIn("epic:review", gh.issue(1)["labels"])

    def test_in_scope_globs(self):
        f = swarm.Orchestrator._in_scope
        self.assertTrue(f("docs/orchestrator.md", ["docs/orchestrator.md"]))
        self.assertTrue(f("docs/a/b.md", ["docs/*.md"]))
        self.assertTrue(f("docs/a/b.md", ["docs/"]))
        self.assertTrue(f("docs/a/b.md", ["./docs/**"]))
        self.assertFalse(f("docs2/x", ["docs/"]))
        self.assertFalse(f("README", ["docs/**", "done-*"]))


class Config(unittest.TestCase):
    def test_load_yaml(self):
        here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cfg = swarm.Config.load(os.path.join(here, "swarm.yaml"))
        self.assertGreaterEqual(cfg.max_bees, 1)
        self.assertGreaterEqual(cfg.max_nudges, 1)
        pol = swarm.Policy.load(os.path.join(here, "model-policy.yaml"))
        self.assertIn("claude", pol.allowed)
        with self.assertRaises(swarm.PolicyError):
            pol.check("opencode", "claude-code/haiku")


if __name__ == "__main__":
    unittest.main()
