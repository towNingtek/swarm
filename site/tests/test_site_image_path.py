"""The site image puts the persistent workspace bin on PATH.

Tools a site installs for itself (opencode for the platform's classic scheduler, uv, cloudflared, ...) live
in /home/dsh/workspace/.local/bin, the persistent workspace, so they survive image updates. Without it on
PATH, `docker exec <site> opencode` answered "opencode: not found" and every classic run of the yillkid site
failed from 2026-10-26 (56 runs in a row) although the binary was there.

  cd site && PYTHONPATH=.:tests python3 -B -m unittest test_site_image_path
"""
import re
import unittest
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
WORKSPACE_BIN = "/home/dsh/workspace/.local/bin"


def final_path():
    """PATH as the image sets it last (ENV lines, continuations joined)."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    found = None
    for line in text.splitlines():
        if not line.startswith("ENV "):
            continue
        for match in re.finditer(r"(?:^|\s)PATH=(\S+)", line[4:]):
            found = match.group(1)
    return found


class SiteImagePathTest(unittest.TestCase):
    def test_workspace_bin_is_on_path(self):
        path = final_path()
        self.assertIsNotNone(path, "the image must set PATH")
        self.assertIn(WORKSPACE_BIN, path.split(":"))

    def test_image_tools_come_first(self):
        parts = final_path().split(":")
        self.assertLess(parts.index("/usr/local/bin"), parts.index(WORKSPACE_BIN),
                        "a tool in the workspace must not shadow the image's own (node, dsh)")

    def test_system_dirs_kept(self):
        parts = final_path().split(":")
        for d in ("/usr/local/bin", "/usr/bin", "/bin"):
            self.assertIn(d, parts)


if __name__ == "__main__":
    unittest.main()
