"""The site image ships the standard tools every site relies on.

The site runs unprivileged and cannot install into system directories, so
anything the bundled plugins execute must be in the image itself. The board
(plugins/swarm-board) runs `gh` for its GitHub status, Projects fetch and
status/EPIC actions; without it every site shows「尚未連接 GitHub」.
"""
import re
import unittest
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
STANDARD = {"git", "gh", "python3", "ca-certificates"}


def base_apt_packages(text: str) -> set[str]:
    # Join continuation lines, then read the first apt-get install in the image.
    joined = re.sub(r"\\\n", " ", text)
    for line in joined.splitlines():
        m = re.search(r"apt-get install\s+(.*?)(?:&&|$)", line)
        if m:
            return {w for w in m.group(1).split() if not w.startswith("-")}
    return set()


class StandardPackagesTests(unittest.TestCase):
    def test_base_image_installs_standard_packages(self):
        packages = base_apt_packages(DOCKERFILE.read_text())
        self.assertTrue(STANDARD <= packages, f"missing: {sorted(STANDARD - packages)}")

    def test_board_still_needs_gh(self):
        # If the board stops running gh, this standard package can be revisited.
        board = Path(__file__).resolve().parents[2] / "plugins" / "swarm-board" / "wolffia"
        used = (board / "site-plugin" / "lib" / "index.js").read_text()
        self.assertIn("execFile('gh'", used)

    def test_board_still_fetches_with_python3(self):
        # The board runs fetch-project.py with python3 for every Projects refresh.
        board = Path(__file__).resolve().parents[2] / "plugins" / "swarm-board" / "wolffia"
        used = (board / "site-plugin" / "lib" / "index.js").read_text()
        self.assertIn("'python3'", used)
        self.assertTrue((board / "fetch-project.py").read_text().startswith("#!/usr/bin/env python3"))


if __name__ == "__main__":
    unittest.main()
