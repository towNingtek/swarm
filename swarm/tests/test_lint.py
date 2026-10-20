"""Issue 品質檢查（template 第二道把關）。範本是純 Markdown：docs/templates/*.md。"""
import os
import re
import unittest

import _path  # noqa: F401
from fake_github import FakeGitHub

from epiclib import lint

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CRIT = """## 驗收判準

### ✅ 修好了 —— 全部成立
| # | 怎麼驗 | 預期 |
|---|--------|------|
| 1 | `python3 -m unittest scheduler/tests/epic/test_lint.py` | exit 0 |
| 2 | `grep -q needs-criteria scheduler/epiclib/lint.py` | exit 0 |

### ❌ 還沒好 —— 任一成立（附現況實測）
| # | 怎麼驗 | 目前（2026-10-18 實測） |
|---|--------|------------------------|
| 1 | 同上 | `No module named epiclib.lint` |"""


def sub(by="swarm", what="加 issue 品質檢查", scope="scheduler/epiclib/**\ndocs/*.md", crit=CRIT):
    by_line = f"由誰做：{by}\n<!-- 說明 -->\n\n" if by is not None else ""
    return (f"<!-- 範本說明 -->\n\n{by_line}## 要做什麼\n<!-- 一兩句話 -->\n{what}\n\n"
            f"## 可以修改的範圍\n<!-- 每行一個 -->\n```\n{scope}\n```\n\n## 依賴\n無\n\n{crit}\n")


EPIC = ("## 目標\n<!-- 寫結果 -->\nWolffia 後端改由 swarm 平台執行。\n\n## 範圍\n**要做**\n- 搬 API\n\n**不做**\n- 改看板\n\n"
        "## 整體驗收\n<!-- 給人結案看 -->\n- [ ] 正式網址操作一次\n\n## 硬期限\n無\n\n## 背景與參考\n")


def template(name):
    text = open(os.path.join(ROOT, "swarm", "templates", name), encoding="utf-8").read()
    return re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)       # 去掉 GitHub/GitLab front matter


class Sub(unittest.TestCase):
    def test_good_issue_passes_and_reads_by_line(self):
        r = lint.lint({"body": sub(), "labels": []})
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(lint.declared_by(sub()), "by:swarm")
        self.assertEqual(lint.declared_by(sub(by="人")), "by:human")
        self.assertEqual(lint.scope_paths(sub()), ["scheduler/epiclib/**", "docs/*.md"])

    def test_by_missing_or_conflicting(self):
        self.assertFalse(lint.lint({"body": sub(by=None), "labels": []}).ok)
        self.assertFalse(lint.lint({"body": sub(by="誰"), "labels": []}).ok)
        self.assertFalse(lint.lint({"body": sub(by=None), "labels": ["by:swarm", "by:human"]}).ok)
        self.assertTrue(lint.lint({"body": sub(by=None), "labels": ["by:swarm"]}).ok)   # 舊 issue：只有 label

    def test_comment_text_is_ignored(self):
        body = sub(by=None) + "\n<!-- 由誰做：swarm -->\n"
        self.assertIsNone(lint.declared_by(body))

    def test_placeholder_fails(self):
        crit = CRIT.replace("`python3 -m unittest scheduler/tests/epic/test_lint.py`", "`指令`") \
                   .replace("| 2 | `grep -q needs-criteria scheduler/epiclib/lint.py` | exit 0 |\n", "")
        r = lint.lint({"body": sub(crit=crit), "labels": []})
        self.assertTrue(any("佔位" in p for p in r.problems))

    def test_non_command_row_fails_for_swarm_only(self):
        crit = CRIT.replace("`grep -q needs-criteria scheduler/epiclib/lint.py`", "登入後點點看")
        self.assertTrue(any("不是指令" in p for p in lint.lint({"body": sub(crit=crit), "labels": []}).problems))
        self.assertTrue(lint.lint({"body": sub(by="人", crit=crit), "labels": []}).ok)

    def test_trivial_commands_fail(self):
        crit = CRIT.replace("`python3 -m unittest scheduler/tests/epic/test_lint.py`", "`true`") \
                   .replace("`grep -q needs-criteria scheduler/epiclib/lint.py`", "`echo ok`")
        self.assertTrue(any("一定會成功" in p for p in lint.lint({"body": sub(crit=crit), "labels": []}).problems))

    def test_scope_missing_warns_and_confined(self):
        body = sub().split("## 可以修改的範圍")[0] + "## 依賴\n無\n\n" + CRIT
        r = lint.lint({"body": body, "labels": []})
        self.assertTrue(r.ok)
        self.assertTrue(any("範圍" in w for w in r.warnings))
        for bad in ("/etc/passwd", "../other-repo/x", "~/.ssh/id_rsa"):
            self.assertTrue(any("絕對路徑" in p for p in lint.lint({"body": sub(scope=bad), "labels": []}).problems), bad)

    def test_missing_criteria(self):
        self.assertTrue(any("驗收判準" in p for p in lint.lint({"body": sub(crit="還沒想好"), "labels": []}).problems))

    def test_form_style_headings_also_work(self):
        body = "### 由誰做\n\nswarm\n\n" + sub(by=None).replace("## 要做什麼", "### 要做什麼")
        self.assertTrue(lint.lint({"body": body, "labels": ["by:swarm"]}).ok)


class Epic(unittest.TestCase):
    def test_good_epic(self):
        self.assertTrue(lint.lint({"body": EPIC, "labels": ["epic"]}).ok)

    def test_missing_acceptance(self):
        body = EPIC.replace("- [ ] 正式網址操作一次", "")
        r = lint.lint({"body": body, "labels": ["epic"]})
        self.assertTrue(any("整體驗收" in p for p in r.problems))


class Templates(unittest.TestCase):
    """範本本身（未填）必須擋得下來，填好之後要能過。"""

    def test_blank_sub_template_is_blocked(self):
        r = lint.lint({"body": template("sub-issue.md"), "labels": []})
        self.assertFalse(r.ok)
        self.assertTrue(any("佔位" in p for p in r.problems))
        self.assertEqual(lint.declared_by(template("sub-issue.md")), "by:swarm")

    def test_filled_sub_template_passes(self):
        body = template("sub-issue.md").replace("`指令`", "`python3 -m unittest scheduler/tests/epic/test_lint.py`") \
                                      .replace("scheduler/example/**", "scheduler/epiclib/**")
        self.assertTrue(lint.lint({"body": body, "labels": []}).ok)

    def test_blank_epic_template_is_blocked_and_filled_passes(self):
        blank = template("epic.md")
        r = lint.lint({"body": blank, "labels": ["epic"]})
        self.assertFalse(r.ok)
        self.assertTrue(any("目標" in p for p in r.problems))
        filled = blank.replace("## 目標\n", "## 目標\nWolffia 改由平台執行。\n", 1).replace("- [ ] \n", "- [ ] 正式網址操作一次\n")
        self.assertTrue(lint.lint({"body": filled, "labels": ["epic"]}).ok, lint.lint({"body": filled, "labels": ["epic"]}).problems)

    def test_platform_copies_match_the_source(self):
        for name in ("epic.md", "sub-issue.md"):
            src = open(os.path.join(ROOT, "swarm", "templates", name), encoding="utf-8").read()
            for d in (".github/ISSUE_TEMPLATE",):
                with open(os.path.join(ROOT, d, name), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), src, f"{d}/{name} 和 docs/templates/{name} 不一致")


class Apply(unittest.TestCase):
    def test_adds_by_label_and_needs_criteria_then_removes(self):
        gh = FakeGitHub({7: {"number": 7, "title": "t", "body": sub(crit="還沒想好"), "labels": []}})
        _, action = lint.apply(gh, 7, gh.issue(7))
        self.assertEqual(action, "label by:swarm, add needs-criteria")
        self.assertEqual(sorted(gh.issue(7)["labels"]), ["by:swarm", "needs-criteria"])
        gh.issues[7]["body"] = sub()
        _, action = lint.apply(gh, 7, gh.issue(7))
        self.assertEqual(action, "remove needs-criteria")
        self.assertEqual(gh.issue(7)["labels"], ["by:swarm"])

    def test_by_change_swaps_label(self):
        gh = FakeGitHub({7: {"number": 7, "title": "t", "body": sub(by="人"), "labels": ["by:swarm"]}})
        lint.apply(gh, 7, gh.issue(7))
        self.assertEqual(gh.issue(7)["labels"], ["by:human"])

    def test_unrelated_and_closed_issues_are_skipped(self):
        gh = FakeGitHub({8: {"number": 8, "title": "[run] log", "body": "x", "labels": ["run-log"]},
                         9: {"number": 9, "title": "t", "body": sub(), "labels": [], "state": "closed"}})
        for n in (8, 9):
            _, action = lint.apply(gh, n, gh.issue(n))
            self.assertTrue(action.startswith("skip"))
        self.assertEqual(gh.writes(), [])

    def test_dry_run_writes_nothing(self):
        gh = FakeGitHub({7: {"number": 7, "title": "t", "body": sub(crit="x"), "labels": []}})
        lint.apply(gh, 7, gh.issue(7), dry_run=True)
        self.assertEqual(gh.writes(), [])


if __name__ == "__main__":
    unittest.main()
