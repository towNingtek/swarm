"""The site image has two builds, chosen by the DSH_MAJOR build arg.

The default (DSH_MAJOR=0.1) is what production sites run and must not move:
Node 22, DSH 0.1.7-rc.2, plugin versions from dsh-profile/package.json.
DSH_MAJOR=0.2 is the test-site build: Node 24, DSH 0.2.1, plugin versions from
dsh-profile/package.dsh02.json (dsh-plugin-subscriptions 0.9.12+ needs DSH 0.2).
The two package files differ only in the subscriptions plugin.
"""
import json
import re
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
DOCKERFILE = SITE / "Dockerfile"
PACKAGE = SITE / "dsh-profile" / "package.json"
PACKAGE_DSH02 = SITE / "dsh-profile" / "package.dsh02.json"
SUBSCRIPTIONS = "dsh-plugin-subscriptions"


def version_tuple(spec: str) -> tuple[int, ...]:
    return tuple(int(p) for p in spec.lstrip("^~").split("-")[0].split("."))


def parse_dockerfile(text: str) -> tuple[dict[str, str], dict[str, dict]]:
    """Return the global ARG defaults and each named stage's base, ARGs and package COPY."""
    joined = re.sub(r"\\\n", " ", text)
    global_args: dict[str, str] = {}
    stages: dict[str, dict] = {}
    current = None
    for raw in joined.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?$", line, re.I)
        if m:
            current = {"from": m.group(1), "args": {}, "packages": []}
            stages[m.group(2) or f"#{len(stages)}"] = current
            continue
        m = re.match(r"ARG\s+(\w+)(?:=(\S*))?$", line)
        if m:
            target = global_args if current is None else current["args"]
            target[m.group(1)] = m.group(2)
            continue
        m = re.match(r"COPY\s+(.*)$", line)
        if m and current is not None:
            srcs = m.group(1).split()[:-1]
            current["packages"] += [s for s in srcs if re.search(r"dsh-profile/package[^/]*\.json$", s)]
    return global_args, stages


def build(dsh_major: str) -> dict:
    """Resolve what `docker build --build-arg DSH_MAJOR=<dsh_major>` uses."""
    global_args, stages = parse_dockerfile(DOCKERFILE.read_text())
    final = list(stages.values())[-1]
    name = final["from"].replace("${DSH_MAJOR}", dsh_major).replace("$DSH_MAJOR", dsh_major)
    stage = stages[name]
    node = re.fullmatch(r"node:(\d+)-[\w.-]+", stage["from"])
    assert node, stage["from"]
    package = stage["packages"] + final["packages"]
    assert len(package) == 1, package
    return {
        "node": node.group(1),
        "dsh": stage["args"].get("DSH_VERSION"),
        "package": SITE.parent / package[0],
        "global_args": global_args,
    }


class Dsh02ImageTests(unittest.TestCase):
    def test_default_build_is_unchanged(self):
        default = build(parse_dockerfile(DOCKERFILE.read_text())[0]["DSH_MAJOR"])
        self.assertEqual(default["node"], "22")
        self.assertEqual(default["dsh"], "0.1.7-rc.2")
        self.assertEqual(default["package"], PACKAGE)
        self.assertEqual(json.loads(PACKAGE.read_text())["dependencies"][SUBSCRIPTIONS], "0.9.11")

    def test_default_dsh_major_is_0_1(self):
        self.assertEqual(parse_dockerfile(DOCKERFILE.read_text())[0].get("DSH_MAJOR"), "0.1")
        self.assertEqual(build("0.1")["dsh"], "0.1.7-rc.2")

    def test_dsh02_build(self):
        dsh02 = build("0.2")
        self.assertEqual(dsh02["node"], "24")
        self.assertRegex(dsh02["dsh"], r"^0\.2\.1(-rc\.\d+)?$")
        self.assertEqual(dsh02["package"], PACKAGE_DSH02)
        pinned = json.loads(PACKAGE_DSH02.read_text())["dependencies"][SUBSCRIPTIONS]
        self.assertGreaterEqual(version_tuple(pinned), (0, 9, 12))

    # Plugins whose 0.2 build may be newer than the 0.1 one: the subscriptions plugin (0.9.12 needs
    # DSH 0.2) and share-room (#47: 0.3.2 adds DSH 0.2 support; the 0.1 sites stay on 0.3.1 until they switch).
    MAY_DIFFER = {SUBSCRIPTIONS: (0, 9, 12), "dsh-share-room": (0, 3, 2)}

    def test_package_files_differ_only_in_subscriptions(self):
        a = json.loads(PACKAGE.read_text())
        b = json.loads(PACKAGE_DSH02.read_text())
        self.assertEqual(a["dsh"], b["dsh"])
        a_deps = {k: v for k, v in a["dependencies"].items() if k not in self.MAY_DIFFER}
        b_deps = {k: v for k, v in b["dependencies"].items() if k not in self.MAY_DIFFER}
        self.assertEqual(a_deps, b_deps)
        for name, least in self.MAY_DIFFER.items():
            # The 0.2 build never goes backwards from the 0.1 build, and has at least the 0.2-ready version.
            self.assertGreaterEqual(version_tuple(b["dependencies"][name]), version_tuple(a["dependencies"][name]), name)
            self.assertGreaterEqual(version_tuple(b["dependencies"][name]), least, name)
        self.assertEqual(set(a["dependencies"]), set(b["dependencies"]))
        self.assertEqual({k: v for k, v in a.items() if k != "dependencies"},
                         {k: v for k, v in b.items() if k != "dependencies"})

    def test_manifest_records_installed_dsh(self):
        # DSH_VERSION is only set inside the chosen dsh-* stage, so the seal step
        # must read the installed version instead of the (empty) build arg.
        text = re.sub(r"\\\n", " ", DOCKERFILE.read_text())
        seal = [l for l in text.splitlines() if "writeProfileManifest(" in l]
        self.assertEqual(len(seal), 1)
        self.assertIn("@deepseek-ai/dsh/package.json", seal[0])


if __name__ == "__main__":
    unittest.main()
