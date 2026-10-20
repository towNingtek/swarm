"""#455 EPIC 結案摘要與每月趨勢報告：swarm-run 紀錄、摘要 YAML、重跑更新同一則、criteria-defect、月報。

全部用假的 GitHub（記憶體），不碰網路。
"""
import contextlib
import io
import itertools
import unittest

import yaml

import _path  # noqa: F401
from fake_github import GO_AT, FakeGitHub

import epic_report
from epiclib import summary, swarm
import test_orchestrator as orch_t

CLOSED = "2026-10-12T10:00:00Z"
JUDGE_OK = "## 判準執行結果（runner）\n\n| # | 指令 | exit | 結果 |\n\n**全部通過，關閉。**\n"
JUDGE_BAD = "## 判準執行結果（runner）\n\n| # | 指令 | exit | 結果 |\n\n**有判準沒通過，不關閉。**\n"
TOP = ("version", "epic", "opened", "closed", "days", "subs", "runs", "totals", "criteria_defects",
       "human_interventions", "prs")


class FakeSource(FakeGitHub):
    """FakeGitHub 加上留言列表與編輯（epiclib.summary 的 src 介面）。"""
    ids = itertools.count(1000)

    def __init__(self, issues, comments=None):
        super().__init__(issues)
        self.cm = {}
        self.edits = []
        for n, bodies in (comments or {}).items():
            for b in bodies:
                self._add(n, b)

    def _add(self, n, body):
        self.cm.setdefault(int(n), []).append({"id": next(self.ids), "body": body,
                                               "created_at": "2026-10-11T00:00:00Z", "author": "x"})

    def comment(self, n, body):
        super().comment(n, body)
        self._add(n, body)

    def list_comments(self, n):
        return [dict(c) for c in self.cm.get(int(n), [])]

    def edit_comment(self, cid, body):
        self.edits.append(cid)
        for cs in self.cm.values():
            for c in cs:
                if c["id"] == cid:
                    c["body"] = body

    def prs(self, n):
        return []


def run_comment(**over):
    m = {"mode": "swarm", "engine": "claude", "model": "claude-opus-5-5", "seconds": 120.5, "agents_total": 3,
         "peak_bees": 2, "nudges": 1, "retries": 1, "conflicts": 0, "passed": 2, "stuck": 1,
         "usage": {"input_tokens": 1000, "output_tokens": 200, "cost_usd": 0.5},
         "usage_seen": ["input_tokens", "output_tokens", "cost_usd"]}
    m.update(over)
    return "## 蜂群這一輪結束（orchestrator）\n\n| 指標 | 值 |\n\n" + swarm.run_record(m, over.get("pr", "https://github.com/o/r/pull/9"))


def epic_world(epic_comments=None, extra=None, month_closed=CLOSED):
    issues = {
        1: {"title": "[EPIC] 測試", "labels": ["epic"], "type": "Epic", "go_at": GO_AT, "state": "closed",
            "closed_at": month_closed, "created_at": "2026-10-09T00:00:00Z"},
        2: {"title": "一次就過", "labels": ["by:swarm"], "parent": 1, "state": "closed"},
        3: {"title": "第二次才過", "labels": ["by:swarm"], "parent": 1, "state": "closed"},
        4: {"title": "卡住後人關", "labels": ["by:swarm", "swarm:stuck"], "parent": 1, "state": "closed"},
        5: {"title": "人做的", "labels": ["by:human"], "parent": 1, "state": "closed"},
        6: {"title": "內文寫人做", "body": "由誰做：人\n", "labels": [], "parent": 1, "state": "closed"},
    }
    comments = {
        1: epic_comments if epic_comments is not None else [run_comment()],
        2: [JUDGE_OK],
        3: [JUDGE_BAD, JUDGE_OK, "criteria-defect：搜尋條件會誤中其他 issue"],
        4: [JUDGE_BAD, "試了 2 次判準都沒通過，標 `swarm:stuck`，由人決定下一步。", "Criteria-Defect: 路徑寫錯"],
        5: ["人：看過了"],
    }
    for k, v in (extra or {}).items():
        comments.setdefault(k, []).extend(v)
    return FakeSource(issues, comments)


def summary_yaml(body):
    m = summary._SUMMARY_RE.search(body)
    assert m, body
    return yaml.safe_load(m.group(1))


class RunRecord(unittest.TestCase):
    def test_parsable_and_complete(self):
        c = run_comment()
        self.assertIn("<!-- swarm-run", c)
        (r,) = summary.parse_runs([{"body": c}])
        for k in summary.RUN_FIELDS:
            self.assertIn(k, r)
        self.assertEqual((r["agents"], r["peak"], r["input_tokens"], r["cost_usd"], r["pr"]),
                         (3, 2, 1000, 0.5, "https://github.com/o/r/pull/9"))

    def test_unreported_usage_is_null(self):
        c = run_comment(usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, usage_seen=[], pr="")
        (r,) = summary.parse_runs([{"body": c}])
        self.assertIsNone(r["input_tokens"])
        self.assertIsNone(r["cost_usd"])
        self.assertIsNone(r["pr"])

    def test_orchestrator_field_names(self):
        self.assertEqual(swarm.RUN_FIELDS, summary.RUN_FIELDS)

    def test_legacy_table_backfill(self):
        body = ("## 蜂群這一輪結束（orchestrator）\n\n| 指標 | 值 |\n|---|---|\n| 模式 | swarm |\n"
                "| 引擎／模型 | claude / opus |\n| 時間（秒） | 284.1 |\n| agent 個數（總數） | 5 |\n"
                "| 同時最多（峰值） | 2 |\n| 推一把次數 | 0 |\n| 重試次數 | 1 |\n| 合併衝突 | 0 |\n"
                "| 判準通過／卡住 | 5 / 0 |\n")
        (r,) = summary.parse_runs([{"body": body, "created_at": "2026-10-18T09:00:00Z"}])
        self.assertEqual((r["seconds"], r["agents"], r["retries"], r["passed"], r["engine"]), (284.1, 5, 1, 5, "claude"))
        self.assertIsNone(r["input_tokens"])        # 舊表格沒有用量列：null，不猜 0
        self.assertIsNone(r["pr"])


class OrchestratorWritesRunRecord(orch_t.Base):
    def test_epic_comment_has_swarm_run(self):
        gh = orch_t.make_world(n_parallel=2)
        self.run_epic(gh, open_pr=lambda *a: "https://github.com/o/r/pull/7")
        end = [c for c in gh.comments[1] if c.startswith("## 蜂群這一輪結束")]
        self.assertEqual(len(end), 1)
        self.assertIn("<!-- swarm-run", end[0])
        (r,) = summary.parse_runs([{"body": end[0]}])
        self.assertEqual((r["mode"], r["engine"], r["agents"], r["passed"], r["stuck"], r["pr"]),
                         ("swarm", "claude", 2, 2, 0, "https://github.com/o/r/pull/7"))
        self.assertIsNone(r["input_tokens"])        # 假的蜂沒回報用量


class Summary(unittest.TestCase):
    def setUp(self):
        self.gh = epic_world()
        self.s, self.what = summary.summarize(self.gh, 1)
        self.body = self.gh.cm[1][-1]["body"]
        self.y = summary_yaml(self.body)

    def test_yaml_version_1(self):
        self.assertEqual(self.y["version"], 1)
        self.assertEqual(self.y["epic"], 1)

    def test_fields_complete(self):
        for k in TOP:
            self.assertIn(k, self.y)
        for k in ("total", "swarm", "human", "passed", "stuck", "human_closed"):
            self.assertIn(k, self.y["subs"])
        for k in ("agents", "peak", "seconds", "input_tokens", "output_tokens", "cost_usd", "nudges", "retries",
                  "conflicts"):
            self.assertIn(k, self.y["totals"])

    def test_human_table(self):
        self.assertTrue(self.body.startswith("## 結案摘要"))
        self.assertIn("| 項目 | 值 |", self.body)

    def test_subs_counts(self):
        sb = self.y["subs"]
        self.assertEqual(sb["total"], 5)
        self.assertEqual(sb["swarm"], 3)
        self.assertEqual(sb["human"], 2)            # by:human label＋內文「由誰做：人」
        self.assertEqual(sb["passed"], 2)
        self.assertEqual(sb["stuck"], 1)
        self.assertEqual(sb["human_closed"], 3)     # #4（卡住後人關）、#5、#6
        self.assertEqual(sb["first_pass"], 1)
        self.assertEqual(self.y["human_interventions"], 1)

    def test_totals_and_prs(self):
        t = self.y["totals"]
        self.assertEqual((t["agents"], t["peak"], t["input_tokens"], t["cost_usd"]), (3, 2, 1000, 0.5))
        self.assertEqual(self.y["prs"], ["https://github.com/o/r/pull/9"])
        self.assertEqual(self.y["days"], 2.0)

    def test_criteria_defect_counted(self):
        self.assertEqual(self.y["criteria_defects"], 2)

    def test_rerun_updates_same_comment(self):
        self.assertEqual(self.what, "created")
        n_before = len(self.gh.cm[1])
        self.gh._add(3, "criteria-defect：又一個")
        s2, what = summary.summarize(self.gh, 1)
        self.assertEqual(what, "updated")
        self.assertEqual(len(self.gh.cm[1]), n_before)
        marked = [c for c in self.gh.cm[1] if "<!-- swarm-epic-summary" in c["body"]]
        self.assertEqual(len(marked), 1)
        self.assertEqual(summary_yaml(marked[0]["body"])["criteria_defects"], 3)
        self.assertEqual(summary.summarize(self.gh, 1)[1], "unchanged")

    def test_summary_not_counted_as_defect(self):
        summary.summarize(self.gh, 1)
        self.assertEqual(summary.collect(self.gh, 1)["criteria_defects"], 2)

    def test_summary_round_trips(self):
        self.assertEqual(summary.parse_summary(self.body)["subs"], self.y["subs"])

    def test_notify_text(self):
        t = summary.notify_text(self.s, "o/r")
        for must in ("#1", "[EPIC] 測試", "通過 2", "卡住 1", "agent 3", "https://github.com/o/r/issues/1"):
            self.assertIn(must, t)


class CountDefects(unittest.TestCase):
    """#505：只算行首的 `criteria-defect：` 標記，句中提到不算。"""

    def n(self, *bodies):
        return summary.count_defects([{"body": b} for b in bodies])

    def test_line_start_fullwidth_colon(self):
        self.assertEqual(self.n("criteria-defect：判準 1 寫錯"), 1)

    def test_line_start_after_other_lines(self):
        self.assertEqual(self.n("驗收\ncriteria-defect：判準 1 寫錯"), 1)

    def test_list_item_ascii_colon(self):
        self.assertEqual(self.n("- criteria-defect: 路徑寫錯"), 1)

    def test_prefixes_and_case(self):
        for b in ("  criteria-defect：x", "* criteria-defect: x", "> criteria-defect：x", "Criteria-Defect: x",
                  "> - criteria-defect：x"):
            self.assertEqual(self.n(b), 1, b)

    def test_mention_not_counted(self):
        for b in ("判準寫錯（criteria-defect）：#448、#451", "這不是 criteria-defect：只是提到",
                  "`criteria-defect`", "criteria-defect 的格式見文件", "see criteria-defect: here"):
            self.assertEqual(self.n(b), 0, b)

    def test_two_markers_one_comment_counts_once(self):
        self.assertEqual(self.n("criteria-defect：判準 1\n- criteria-defect: 判準 2"), 1)

    def test_each_comment_counts(self):
        self.assertEqual(self.n("criteria-defect：a", "提到 criteria-defect 而已", "- criteria-defect: b"), 2)

    def test_machine_summary_not_counted(self):
        body = summary.HEAD + "\n\ncriteria-defect：x\n\n<!-- " + summary.MARK + "\nversion: 1\n-->"
        self.assertEqual(self.n(body), 0)
        self.assertEqual(self.n("criteria-defect：x\n<!-- " + summary.RUN_MARK + "\nmode: swarm\n-->"), 0)

    def test_empty(self):
        self.assertEqual(summary.count_defects([{"body": None}, {}]), 0)


class Missing(unittest.TestCase):
    def test_missing_values_are_null(self):
        gh = epic_world(epic_comments=[])
        gh.issues[1]["closed_at"] = None
        gh.issues[1]["go_at"] = None
        s, _ = summary.summarize(gh, 1)
        y = summary_yaml(gh.cm[1][-1]["body"])
        self.assertEqual(y["runs"], [])
        self.assertIsNone(y["closed"])
        self.assertIsNone(y["days"])
        for k, v in y["totals"].items():
            self.assertIsNone(v, k)

    def test_partial_runs_total_null(self):
        legacy = "## 這一輪結束（runner）\n\n模型：`claude/opus`\n\n| sub-issue | 狀態 | 這一輪 |\n|---|---|---|\n| #2 x | closed | 通過並關閉 |\n"
        gh = epic_world(epic_comments=[legacy, run_comment()])
        y = summary.collect(gh, 1)
        self.assertEqual(len(y["runs"]), 2)
        self.assertEqual(y["runs"][0]["passed"], 1)
        self.assertIsNone(y["totals"]["agents"])    # 第一次執行沒有 agent 數：加總不猜
        self.assertEqual(y["totals"]["peak"], None)


def fake_summary(n, closed, total, swarm_n, first, stuck, agents, tokens, defects, interventions, days):
    return {"version": 1, "epic": n, "closed": closed, "days": days,
            "subs": {"total": total, "swarm": swarm_n, "human": total - swarm_n, "passed": first, "stuck": stuck,
                     "human_closed": 0, "first_pass": first},
            "totals": {"agents": agents, "peak": 2, "seconds": 1, "input_tokens": tokens, "output_tokens": 0,
                       "cost_usd": None, "nudges": 0, "retries": 0, "conflicts": 0},
            "criteria_defects": defects, "human_interventions": interventions, "runs": [], "prs": []}


class MonthlyReport(unittest.TestCase):
    def setUp(self):
        self.sums = [
            fake_summary(10, "2026-09-05T00:00:00Z", 4, 4, 2, 2, 8, 4000, 1, 2, 2.0),
            fake_summary(20, "2026-10-03T00:00:00Z", 5, 4, 3, 1, 6, 2000, 0, 1, 1.0),
            fake_summary(21, "2026-10-20T00:00:00Z", 6, 6, 6, 0, 6, None, 1, 0, 3.0),
            fake_summary(30, "2026-11-01T00:00:00Z", 1, 1, 1, 0, 1, 1, 0, 0, 1.0),
        ]
        self.r = summary.report("2026-10", self.sums)

    def test_one_pass_and_stuck_rate(self):
        c = self.r["cur"]
        self.assertEqual(c["epics"], 2)
        self.assertAlmostEqual(c["one_pass_rate"], 0.9)       # 9／10
        self.assertAlmostEqual(c["stuck_rate"], 0.1)          # 1／10
        self.assertAlmostEqual(c["swarm_share"], round(10 / 11, 3))
        self.assertAlmostEqual(c["agents_per_sub"], 1.2)      # 12／10
        self.assertAlmostEqual(c["tokens_per_sub"], 500)      # 只算有回報的 EPIC：2000／4
        self.assertIsNone(c["cost_per_sub"])
        self.assertEqual((c["criteria_defects"], c["human_interventions"], c["days_to_close"]), (1, 1, 2.0))

    def test_compared_with_previous_month(self):
        self.assertEqual(self.r["prev_month"], "2026-09")
        cmp = self.r["compare"]
        self.assertAlmostEqual(cmp["one_pass_rate"]["prev"], 0.5)
        self.assertAlmostEqual(cmp["one_pass_rate"]["delta"], 0.4)
        self.assertAlmostEqual(cmp["stuck_rate"]["delta"], -0.4)
        self.assertEqual(cmp["human_interventions"]["delta"], -1)
        self.assertIsNone(cmp["cost_per_sub"]["delta"])

    def test_render(self):
        t = summary.render_report(self.r, "o/r")
        for must in ("2026-10", "2026-09", "一次通過率：90.0%", "上月 50.0%", "卡住率：10.0%", "#20", "#21"):
            self.assertIn(must, t)
        self.assertNotIn("#30", t)

    def test_empty_previous_month_is_null(self):
        r = summary.report("2026-09", self.sums)
        self.assertIsNone(r["prev"]["one_pass_rate"])
        self.assertIsNone(r["prev"]["criteria_defects"])
        self.assertIsNone(r["compare"]["criteria_defects"]["delta"])

    def test_prev_month_wraps_year(self):
        self.assertEqual(summary.prev_month("2027-01"), "2026-12")

    def test_reads_posted_summaries(self):
        gh = epic_world()
        summary.summarize(gh, 1)
        (s,) = [summary.parse_summary(c["body"]) for c in gh.cm[1] if summary.parse_summary(c["body"])]
        r = summary.report("2026-10", [s])
        self.assertAlmostEqual(r["cur"]["one_pass_rate"], round(1 / 3, 3))
        self.assertAlmostEqual(r["cur"]["stuck_rate"], round(1 / 3, 3))


class ReportSource(FakeSource):
    """月報用的假 GitHub：EPIC #1（10 月結案）＋固定 issue #512。"""

    def closed_epics(self, since=None):
        return [n for n, it in self.issues.items() if "epic" in it["labels"] and it["state"] == "closed"]


class ReportToIssue(unittest.TestCase):
    """#514：--issue N 把月報留言到 issue N，同月份更新同一則。"""

    def setUp(self):
        self.gh = epic_world()
        self.gh.__class__ = ReportSource
        self.gh.issues[512] = dict(self.gh.issues[5], title="月報", labels=[], parent=None, state="open")

    def run_report(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = epic_report.main(["--repo", "o/r", *args], src=self.gh)
        self.assertEqual(rc, 0)
        return out.getvalue()

    def marked(self, month):
        return [c for c in self.gh.cm.get(512, [])
                if f"<!-- swarm-monthly-report month={month} -->" in c["body"]]

    def test_issue_creates_comment_with_month_marker(self):
        self.run_report("--month", "2026-10", "--issue", "512")
        (c,) = self.gh.cm[512]
        self.assertIn("<!-- swarm-monthly-report month=2026-10 -->", c["body"])
        self.assertIn("2026-10", c["body"])
        self.assertIn(("comment", 512), self.gh.calls)

    def test_same_month_updates_same_comment(self):
        self.run_report("--month", "2026-10", "--issue", "512")
        (c,) = self.gh.cm[512]
        cid = c["id"]
        self.gh._add(3, "criteria-defect：又一個")       # 內容變了，重跑要更新
        self.gh.cm[1] = [x for x in self.gh.cm[1] if not summary.parse_summary(x["body"])]
        self.run_report("--month", "2026-10", "--issue", "512")
        self.assertEqual(len(self.gh.cm[512]), 1)
        self.assertEqual(self.gh.edits, [cid])
        self.assertEqual(len(self.marked("2026-10")), 1)
        self.run_report("--month", "2026-10", "--issue", "512")      # 內容一樣：不動
        self.assertEqual((len(self.gh.cm[512]), self.gh.edits), (1, [cid]))

    def test_other_month_creates_new_comment(self):
        self.run_report("--month", "2026-10", "--issue", "512")
        self.run_report("--month", "2026-11", "--issue", "512")
        self.assertEqual(len(self.gh.cm[512]), 2)
        self.assertEqual(self.gh.edits, [])
        self.assertEqual((len(self.marked("2026-10")), len(self.marked("2026-11"))), (1, 1))

    def test_other_comments_untouched(self):
        self.gh._add(512, "人：10 月月報手動貼的")
        self.run_report("--month", "2026-10", "--issue", "512")
        self.assertEqual(len(self.gh.cm[512]), 2)
        self.assertEqual(self.gh.edits, [])

    def test_without_issue_writes_nothing(self):
        before = {k: [dict(c) for c in v] for k, v in self.gh.cm.items()}
        out = self.run_report("--month", "2026-10")
        self.assertIn("2026-10", out)
        self.assertEqual(self.gh.cm, before)
        self.assertEqual(self.gh.edits, [])
        self.assertFalse([c for c in self.gh.calls if c[0] == "comment"])


if __name__ == "__main__":
    unittest.main()
