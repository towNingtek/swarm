"""#517 docs/skills/epic/SKILL.md 的文件結構：front matter、七個節依序、引用的檔案、lint 的 --file。"""
import os
import re
import unittest

import _path  # noqa: F401

ROOT = os.path.dirname(_path.SCHEDULER)
SKILL = os.path.join(ROOT, "swarm", "skills", "epic", "SKILL.md")
SECTIONS = ["意圖", "規模判斷", "聚合", "拆 sub-issue", "判準先實測", "lint", "放行"]
REFS = ["swarm/templates/epic.md", "swarm/templates/sub-issue.md", "swarm/epic_lint.py"]


def _read():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


class SkillDoc(unittest.TestCase):
    def setUp(self):
        self.text = _read()

    def front_matter(self):
        m = re.match(r"---\n(.*?)\n---\n", self.text, re.S)
        self.assertIsNotNone(m, "開頭要有 front matter")
        return m.group(1)

    def test_front_matter_name(self):
        self.assertRegex(self.front_matter(), r"(?m)^name:\s*epic\s*$")

    def test_front_matter_description(self):
        self.assertRegex(self.front_matter(), r"(?m)^description:\s*\S+")

    def test_sections_in_order(self):
        heads = [h.strip() for h in re.findall(r"(?m)^##\s+(.+)$", self.text)]
        pos = []
        for s in SECTIONS:
            idx = [i for i, h in enumerate(heads) if re.fullmatch(r"(\d+\.\s*)?" + re.escape(s), h)]
            self.assertTrue(idx, f"缺少節標題：{s}")
            pos.append(idx[0])
        self.assertEqual(pos, sorted(pos), "節標題順序不對")

    def test_references(self):
        for r in REFS:
            self.assertIn(r, self.text)
            self.assertTrue(os.path.exists(os.path.join(ROOT, r)), f"引用的檔案不存在：{r}")

    def test_lint_file_flag(self):
        self.assertRegex(self.text, r"epic_lint\.py\s+--file")

    def test_counter_examples(self):
        for n in ("#448", "#476"):
            self.assertIn(n, self.text)

    def test_auto_merge(self):
        self.assertIn("swarm:auto-merge", self.text)


if __name__ == "__main__":
    unittest.main()
