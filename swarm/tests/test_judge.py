"""#396 執行判準、貼結果、關 sub-issue。契約第 3 節第 5–7 條、第 6 節。"""
import os
import tempfile
import unittest

import _path  # noqa: F401
from fake_github import criteria_body, world

from epiclib import judge


# Fake GitHub token for the redaction test (built at runtime; not a real credential).
FAKE_PAT = "ghp_" + "abcdEFGH" * 4 + "1234"

class Run(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.cwd = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def test_exit_code_decides(self):
        rows = judge.run_commands(["true", "false", "exit 3"], self.cwd, {})
        self.assertEqual([r.code for r in rows], [0, 1, 3])
        self.assertEqual([r.ok for r in rows], [True, False, False])

    def test_runs_verbatim_in_shell_and_cwd(self):
        open(os.path.join(self.cwd, "a.txt"), "w").write("hello\n")
        rows = judge.run_commands(["grep -q hello a.txt && ! test -f b.txt"], self.cwd, {})
        self.assertTrue(rows[0].ok)

    def test_captures_stdout_and_stderr(self):
        rows = judge.run_commands(["echo out; echo err >&2; exit 1"], self.cwd, {})
        self.assertIn("out", rows[0].output)
        self.assertIn("err", rows[0].output)

    def test_env_vars_available(self):
        env = {"GO": "abc123", "REPO": "o/r", "ISSUE": "7", "EPIC": "1"}
        rows = judge.run_commands(['test "$GO/$REPO/$ISSUE/$EPIC" = "abc123/o/r/7/1"'], self.cwd, env)
        self.assertTrue(rows[0].ok, rows[0].output)

    def test_timeout_is_failure(self):
        rows = judge.run_commands(["sleep 5"], self.cwd, {}, timeout=1)
        self.assertFalse(rows[0].ok)
        self.assertIn("timeout", rows[0].output.lower())


class Judge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.cwd = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def go(self, gh, n=2):
        return judge.judge(gh, gh.issue(1), gh.issue(n), self.cwd, go="abc", repo="o/r")

    def test_all_pass_closes_and_comments(self):
        gh = world(i2={"body": criteria_body("g", ["`true`", "`echo hi`"])})
        res = self.go(gh)
        self.assertTrue(res.ok)
        self.assertEqual(gh.closed_calls(), [2])
        c = "\n".join(gh.comments[2])
        self.assertIn("true", c)
        self.assertIn("echo hi", c)
        self.assertIn("hi", c)

    def test_any_failure_does_not_close(self):
        gh = world(i2={"body": criteria_body("g", ["`true`", "`echo boom; false`"])})
        res = self.go(gh)
        self.assertFalse(res.ok)
        self.assertEqual(gh.closed_calls(), [])
        c = "\n".join(gh.comments[2])
        self.assertIn("boom", c)
        self.assertIn("exit 1", c)

    def test_every_row_runs_even_after_failure(self):
        gh = world(i2={"body": criteria_body("g", ["`false`", "`echo second-row`"])})
        res = self.go(gh)
        self.assertEqual(len(res.rows), 2)
        self.assertIn("second-row", "\n".join(gh.comments[2]))

    def test_long_output_truncated(self):
        gh = world(i2={"body": criteria_body("g", ["`yes x \\| head -c 200000`"])})
        self.go(gh)
        self.assertLess(max(len(c) for c in gh.comments[2]), 65536)

    def test_secrets_redacted(self):
        # a fake GitHub token, assembled at runtime so secret scanners do not flag the source
        gh = world(i2={"body": criteria_body("g", ["`echo " + FAKE_PAT + "`"])})
        self.go(gh)
        self.assertNotIn(FAKE_PAT, "\n".join(gh.comments[2]))

    def test_never_closes_epic(self):
        gh = world()
        with self.assertRaises(ValueError):
            judge.judge(gh, gh.issue(1), gh.issue(1), self.cwd, go="abc", repo="o/r")
        self.assertEqual(gh.closed_calls(), [])

    def test_uses_snapshot_not_current_body(self):
        gh = world(i2={"body": criteria_body("g", ["`false`"])})
        res = judge.judge(gh, gh.issue(1), gh.issue(2), self.cwd, go="abc", repo="o/r", commands=["true"])
        self.assertTrue(res.ok)

    def test_no_commands_is_not_pass(self):
        gh = world(i2={"body": "## 目標\n"})
        res = self.go(gh)
        self.assertFalse(res.ok)
        self.assertEqual(gh.closed_calls(), [])


if __name__ == "__main__":
    unittest.main()
