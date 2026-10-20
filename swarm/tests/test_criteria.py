"""#394 判準解析與凍結。契約第 3 節。"""
import subprocess
import tempfile
import unittest

import _path  # noqa: F401
from fake_github import AFTER, BEFORE, GO_AT, criteria_body

from epiclib import criteria


class Parse(unittest.TestCase):
    def test_extracts_commands_in_order(self):
        body = criteria_body("g", ["`true`", "`test -f a.txt`"], ["`false`"])
        c = criteria.parse(body)
        self.assertTrue(c.present)
        self.assertEqual(c.commands, ["true", "test -f a.txt"])
        self.assertTrue(c.all_commands)

    def test_only_fixed_table_counts(self):
        # 「❌ 還沒好」表裡的指令不是通過條件
        body = criteria_body("g", ["`true`"], ["`echo nope`", "`false`"])
        self.assertEqual(criteria.parse(body).commands, ["true"])

    def test_escaped_pipe_becomes_pipe(self):
        body = criteria_body("g", [r"`echo a \| grep -q a`"])
        self.assertEqual(criteria.parse(body).commands, ["echo a | grep -q a"])

    def test_note_after_command_is_ignored(self):
        body = criteria_body("g", ["`! false`（說明文字）"])
        self.assertEqual(criteria.parse(body).commands, ["! false"])

    def test_double_backtick_span_keeps_inner_backtick(self):
        body = criteria_body("g", ["`` test \"$(echo `x`)\" = x ``"])
        self.assertEqual(criteria.parse(body).commands, ['test "$(echo `x`)" = x'])

    def test_missing_section(self):
        c = criteria.parse("## 目標\n\n沒有判準\n")
        self.assertFalse(c.present)
        self.assertEqual(c.commands, [])

    def test_section_without_rows_is_not_present(self):
        body = "## 驗收判準\n\n### ✅ 修好了 —— 全部成立\n| # | 怎麼驗 | 預期 |\n|---|---|---|\n"
        self.assertFalse(criteria.parse(body).present)

    def test_human_row_makes_not_all_commands(self):
        c = criteria.parse(criteria_body("g", ["`true`", "人：看過畫面"]))
        self.assertTrue(c.present)
        self.assertFalse(c.all_commands)
        self.assertEqual(c.non_commands, ["人：看過畫面"])

    def test_section_ends_at_next_h2(self):
        body = criteria_body("g", ["`true`"]) + "\n## 其他\n\n| 1 | `rm -rf /` | x |\n"
        self.assertEqual(criteria.parse(body).commands, ["true"])


class Check(unittest.TestCase):
    """check(issue) → (ok, reason)：swarm 能不能用這張的判準。"""

    def issue(self, body, labels=("by:swarm",)):
        return {"number": 9, "body": body, "labels": list(labels)}

    def test_ok(self):
        ok, why = criteria.check(self.issue(criteria_body("g", ["`true`"])))
        self.assertTrue(ok, why)

    def test_no_criteria(self):
        ok, why = criteria.check(self.issue("## 目標\n"))
        self.assertFalse(ok)
        self.assertIn("驗收判準", why)

    def test_needs_criteria_label(self):
        ok, why = criteria.check(self.issue(criteria_body("g", ["`true`"]), ("by:swarm", "needs-criteria")))
        self.assertFalse(ok)
        self.assertIn("needs-criteria", why)

    def test_non_command_row_means_human(self):
        ok, why = criteria.check(self.issue(criteria_body("g", ["`true`", "人：看過"])))
        self.assertFalse(ok)
        self.assertIn("by:human", why)


class Freeze(unittest.TestCase):
    def sub(self, **kw):
        it = {"number": 7, "body": criteria_body("g", ["`true`"]), "labels": ["by:swarm"],
              "created_at": BEFORE, "last_edited_at": None}
        it.update(kw)
        return it

    def test_untouched_since_go_is_frozen(self):
        ok, why = criteria.frozen(self.sub(), GO_AT)
        self.assertTrue(ok, why)

    def test_edited_before_go_is_frozen(self):
        self.assertTrue(criteria.frozen(self.sub(last_edited_at=BEFORE), GO_AT)[0])

    def test_edited_after_go_is_not_frozen(self):
        ok, why = criteria.frozen(self.sub(last_edited_at=AFTER), GO_AT)
        self.assertFalse(ok)
        self.assertIn("swarm:go", why)

    def test_created_after_go_is_not_frozen(self):
        self.assertFalse(criteria.frozen(self.sub(created_at=AFTER), GO_AT)[0])

    def test_no_go_is_not_frozen(self):
        self.assertFalse(criteria.frozen(self.sub(), None)[0])

    def test_snapshot_detects_change(self):
        a = self.sub()
        snap = criteria.snapshot(a)
        self.assertEqual(snap["commands"], ["true"])
        self.assertTrue(criteria.same(a, snap))
        b = self.sub(body=criteria_body("g", ["`false`"]))
        self.assertFalse(criteria.same(b, snap))

    def test_snapshot_ignores_edits_outside_criteria(self):
        a = self.sub()
        snap = criteria.snapshot(a)
        b = self.sub(body="多了一段說明\n\n" + a["body"])
        self.assertTrue(criteria.same(b, snap))


class GoCommit(unittest.TestCase):
    """$GO ＝ 放行當下預設分支的 commit。"""

    def git(self, d, *a, date=None):
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True, env=env).stdout.strip()

    def test_commit_at_go_time(self):
        with tempfile.TemporaryDirectory() as d:
            self.git(d, "init", "-q")                      # 主機 git 2.25 沒有 init -b
            self.git(d, "checkout", "-q", "-b", "master")
            self.git(d, "commit", "-q", "--allow-empty", "-m", "before", date=BEFORE)
            want = self.git(d, "rev-parse", "HEAD")
            self.git(d, "commit", "-q", "--allow-empty", "-m", "after", date=AFTER)
            self.assertEqual(criteria.go_commit(d, GO_AT, ref="master"), want)


if __name__ == "__main__":
    unittest.main()
