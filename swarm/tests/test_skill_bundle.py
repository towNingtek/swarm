"""#549 站台外掛內附的 epic skill（wolffia/site-plugin/skills/epic）：
SKILL.md 不依賴 repo 路徑；內附的 lint 不靠 scheduler/ 也能獨立跑，結果和 repo 的 lint 一致。"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _path  # noqa: F401

from epiclib import lint as repo_lint

ROOT = os.path.dirname(_path.SCHEDULER)
SKILL_DIR = os.path.join(ROOT, "plugins", "swarm-board", "wolffia", "site-plugin", "skills", "epic")
LINT = os.path.join(SKILL_DIR, "lint", "epic_lint.py")
REPO_PATHS = ["swarm/skills/epic/SKILL.md", "swarm/epic_lint.py", "swarm/templates/"]

EPIC_OK = ("## 目標\n站台的分析對話能用 epic skill 開 EPIC。\n\n## 範圍\n**要做**\n- 內附 skill\n\n**不做**\n- 改 runner\n\n"
           "## 整體驗收\n- [ ] 在站台開一張 EPIC\n\n## 硬期限\n無\n\n## 背景與參考\n")
EPIC_MISSING = "## 目標\n只寫了目標。\n\n## 背景與參考\n"
SUB_OK = """由誰做：swarm

## 要做什麼
內附 lint。

## 可以修改的範圍
```
wolffia/site-plugin/skills/**
```

## 依賴
無

## 驗收判準

### ✅ 修好了 —— 全部成立
| # | 怎麼驗 | 預期 |
|---|--------|------|
| 1 | `python3 -m unittest test_skill_bundle` | exit 0 |
"""


def run_lint(body, labels):
    """在一個空的暫存目錄、乾淨的環境（沒有 PYTHONPATH、-I）跑內附的 lint，確定沒有借用 scheduler/epiclib。"""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "draft.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(body)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        p = subprocess.run([sys.executable, "-I", "-B", LINT, "--file", f, "--labels", labels],
                           cwd=d, env=env, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


class SkillBundle(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8") as f:
            self.skill = f.read()

    def test_files(self):
        for rel in ["SKILL.md", "templates/epic.md", "templates/sub-issue.md", "lint/epic_lint.py",
                    "lint/epiclib/__init__.py", "lint/epiclib/lint.py", "lint/epiclib/criteria.py"]:
            self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, rel)), rel)

    def test_name(self):
        m = re.match(r"---\n(.*?)\n---\n", self.skill, re.S)
        self.assertIsNotNone(m, "開頭要有 front matter")
        self.assertRegex(m.group(1), r"(?m)^name:\s*epic\s*$")
        self.assertRegex(m.group(1), r"(?m)^description:\s*\S+")

    def test_no_repo_paths(self):
        for p in REPO_PATHS:
            self.assertNotIn(p, self.skill)

    def test_points_into_skill_dir(self):
        for p in ["templates/epic.md", "templates/sub-issue.md"]:
            self.assertIn(p, self.skill)
        self.assertRegex(self.skill, r"lint/epic_lint\.py\s+--file")

    def test_templates_match_repo(self):
        for name in ["epic.md", "sub-issue.md"]:
            with open(os.path.join(ROOT, "swarm", "templates", name), encoding="utf-8") as a, \
                 open(os.path.join(SKILL_DIR, "templates", name), encoding="utf-8") as b:
                self.assertEqual(a.read(), b.read(), f"templates/{name} 和 swarm/templates/{name} 不一致")

    def test_good_epic_passes(self):
        code, out = run_lint(EPIC_OK, "epic")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_epic_missing_sections_fails(self):
        code, out = run_lint(EPIC_MISSING, "epic")
        self.assertEqual(code, 1, out)
        self.assertIn("缺「範圍」", out)
        self.assertIn("缺「整體驗收」", out)

    def test_sub_issue(self):
        self.assertEqual(run_lint(SUB_OK, "by:swarm")[0], 0)
        bad = SUB_OK.replace("`python3 -m unittest test_skill_bundle`", "看起來對")
        code, out = run_lint(bad, "by:swarm")
        self.assertEqual(code, 1, out)

    def test_bundled_template_runs(self):
        code, out = run_lint(open(os.path.join(SKILL_DIR, "templates", "epic.md"), encoding="utf-8").read(), "epic")
        self.assertIn(code, (0, 1), out)
        self.assertNotIn("Traceback", out)

    def test_same_verdict_as_repo_lint(self):
        cases = [(EPIC_OK, ["epic"]), (EPIC_MISSING, ["epic"]), (SUB_OK, ["by:swarm"]),
                 (SUB_OK.replace("由誰做：swarm", ""), []), (SUB_OK.replace("`python3 -m unittest test_skill_bundle`", "`true`"), ["by:swarm"])]
        for body, labels in cases:
            want = repo_lint.lint({"body": body, "labels": labels})
            code, out = run_lint(body, ",".join(labels))
            self.assertEqual(code, 0 if want.ok else 1, out)
            for p in want.problems:
                self.assertIn(p, out)

    def test_missing_file_is_exit_2(self):
        p = subprocess.run([sys.executable, "-I", "-B", LINT, "--file", "/nonexistent/draft.md"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
