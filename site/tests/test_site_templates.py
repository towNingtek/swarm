import json
import os
import tempfile
import unittest
from pathlib import Path

import site_templates as st


def values(**kw):
    return st.guide_values(**kw)


class RenderTests(unittest.TestCase):
    def test_sections_and_vars(self):
        v = values(starter_model="cloud-fast")
        self.assertEqual(st.render("a{{#starter}} {{starter_model}}{{/starter}}{{^starter}}x{{/starter}}", v),
                         "a cloud-fast")
        self.assertEqual(st.render("{{#starter}}y{{/starter}}{{^starter}}n{{/starter}}", values()), "n")

    def test_unknown_or_unbalanced_refused(self):
        for text in ("{{nope}}", "{{#nope}}x{{/nope}}", "{{#starter}}x", "{{starter}}"):
            with self.assertRaises(st.TemplateError):
                st.render(text, values())

    def test_guide_values_validated(self):
        with self.assertRaises(st.TemplateError):
            values(language="中\n{{x}}")


class RealTemplateTests(unittest.TestCase):
    """The shipped templates themselves must render and hold no secrets."""

    def test_every_shipped_template_loads(self):
        ids = [t["id"] for t in st.list_templates()]
        self.assertIn("swarm", ids)
        self.assertEqual(ids[0], st.NONE)
        for tid in ids[1:]:
            for v in (values(starter_model="cloud-fast"), values()):
                meta, files = st.load_template(tid, v)
                self.assertTrue(files)
                for rel, text in files.items():
                    self.assertNotIn("{{", text, rel)
                    self.assertFalse(any(p in {".keys", "keys"} for p in Path(rel).parts), rel)

    def test_platform_guide_renders_both_ways(self):
        with tempfile.TemporaryDirectory() as d:
            st.write_platform_guide(Path(d), values(starter_model="cloud-fast"))
            text = (Path(d) / "AGENTS.md").read_text()
            self.assertIn("cloud-fast", text)
            self.assertIn("繁體中文", text)
            st.write_platform_guide(Path(d), values(language="English"))
            text = (Path(d) / "AGENTS.md").read_text()
            self.assertNotIn("cloud-fast", text)
            self.assertIn("沒有平台提供的起步模型", text)
            self.assertIn("English", text)

    def test_swarm_wires_keys_and_local_issues(self):
        # The always-loaded guide must point at the tools and skills that ship.
        _, files = st.load_template("swarm", values(starter_model="cloud-fast"))
        guide = files["AGENTS.md"]
        for rel in (".swarm/set-key.mjs", ".swarm/key-test.mjs", ".swarm/notify.mjs",
                    ".swarm/check-office.mjs", ".swarm/git-credential.mjs", ".swarm/keys.mjs", ".swarm/office-setup.mjs",
                    ".dsh/skills/connect-github/SKILL.md", ".dsh/skills/connect-discord/SKILL.md",
                    ".dsh/skills/office-setup/SKILL.md"):
            self.assertIn(rel, files)
        for word in ("set-key.mjs", "check-office.mjs", "office-setup.mjs", "connect-github",
                     "交接", "申請", "github.token", "discord-webhook.url", "狀態："):
            self.assertIn(word, guide)
        self.assertIn("office-setup.mjs", files[".dsh/skills/hive-new/SKILL.md"])
        # AGENTS.md is loaded on every turn: keep it small.
        self.assertLess(len(guide.encode()), 9000)

    def test_swarm_mentions_no_deployment_specific_word(self):
        # The repo's hashed deny-list (scripts/denylist.sha256) holds the
        # deployment's companies, people and domains without naming them here.
        import importlib.util
        script = Path(__file__).resolve().parents[2] / "scripts" / "check-denylist.py"
        spec = importlib.util.spec_from_file_location("check_denylist", script)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        hashes = checker.load_hashes()
        self.assertTrue(hashes)
        _, files = st.load_template("swarm", values(starter_model="cloud-fast"))
        for rel, text in files.items():
            self.assertEqual(checker.scan_text(rel, text, hashes), [], rel)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "tpl"
        self.ws = base / "ws"
        self.ws.mkdir()
        t = self.root / "demo"
        (t / "docs").mkdir(parents=True)
        (t / "template.yaml").write_text("id: demo\nname: Demo\nversion: 1\ndescription: d\ndirs: [hives]\n")
        (t / "AGENTS.md").write_text("hi {{language}}\n")
        (t / "docs" / "a.md").write_text("a\n")

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, tid="demo"):
        return st.apply_template(self.ws, tid, values(), root=self.root)

    def test_first_apply_creates_and_records(self):
        out = self.apply()
        self.assertEqual(sorted(out["created"]), ["AGENTS.md", "docs/a.md"])
        self.assertEqual((self.ws / "AGENTS.md").read_text(), "hi 繁體中文\n")
        self.assertTrue((self.ws / "hives").is_dir())
        record = json.loads((self.ws / st.RECORD).read_text())
        self.assertEqual((record["template"], record["version"]), ("demo", 1))

    def test_customer_edits_and_deletions_stick(self):
        self.apply()
        (self.ws / "AGENTS.md").write_text("mine\n")
        (self.ws / "docs" / "a.md").unlink()
        out = self.apply()
        self.assertEqual(out["created"], [])
        self.assertEqual((self.ws / "AGENTS.md").read_text(), "mine\n")
        self.assertFalse((self.ws / "docs" / "a.md").exists())

    def test_untouched_files_upgrade_edited_files_stay(self):
        self.apply()
        (self.ws / "docs" / "a.md").write_text("customer\n")
        (self.root / "demo" / "AGENTS.md").write_text("v2 {{language}}\n")
        (self.root / "demo" / "docs" / "a.md").write_text("a v2\n")
        out = self.apply()
        self.assertEqual(out["upgraded"], ["AGENTS.md"])
        self.assertEqual((self.ws / "AGENTS.md").read_text(), "v2 繁體中文\n")
        self.assertEqual((self.ws / "docs" / "a.md").read_text(), "customer\n")
        # Deleted stays deleted even when the template changes it.
        (self.ws / "AGENTS.md").unlink()
        (self.root / "demo" / "AGENTS.md").write_text("v3\n")
        self.assertEqual(self.apply()["upgraded"], [])
        self.assertFalse((self.ws / "AGENTS.md").exists())

    def test_new_file_in_template_is_added_once(self):
        self.apply()
        (self.root / "demo" / "b.md").write_text("b\n")
        self.assertEqual(self.apply()["created"], ["b.md"])

    def test_preexisting_customer_file_not_overwritten(self):
        (self.ws / "AGENTS.md").write_text("theirs\n")
        out = self.apply()
        self.assertIn("AGENTS.md", out["kept"])
        self.assertEqual((self.ws / "AGENTS.md").read_text(), "theirs\n")

    def test_different_template_refused(self):
        self.apply()
        other = self.root / "other"
        other.mkdir()
        (other / "template.yaml").write_text("id: other\nname: O\nversion: 1\ndescription: d\n")
        (other / "x.md").write_text("x\n")
        with self.assertRaises(st.TemplateError):
            self.apply("other")

    def test_none_places_nothing(self):
        self.assertEqual(self.apply(st.NONE)["created"], [])
        self.assertEqual(os.listdir(self.ws), [])

    def test_secret_paths_refused_before_any_write(self):
        for rel in (".keys/token", "hives/x/.keys/a", "keys/a", ".env", "cert.pem",
                    "id_ed25519", ".credentials.yaml"):
            with self.subTest(rel=rel):
                bad = self.root / "bad"
                if bad.exists():
                    import shutil
                    shutil.rmtree(bad)
                (bad / Path(rel).parent).mkdir(parents=True, exist_ok=True)
                (bad / "template.yaml").write_text("id: bad\nname: B\nversion: 1\ndescription: d\n")
                (bad / "ok.md").write_text("ok\n")
                (bad / rel).write_text("x\n")
                with self.assertRaises(st.TemplateError):
                    st.apply_template(self.ws, "bad", values(), root=self.root)
                self.assertFalse((self.ws / "ok.md").exists())

    def test_secret_content_refused(self):
        for text in ("key sk-" + "a" * 30, "sms_" + "b" * 43, "ghp_" + "c" * 36,
                     "-----BEGIN OPENSSH " + "PRIVATE KEY-----", "AKIA" + "A" * 16):
            with self.subTest(text=text[:8]):
                (self.root / "demo" / "docs" / "a.md").write_text(text)
                with self.assertRaises(st.TemplateError):
                    self.apply()
                self.assertFalse((self.ws / "AGENTS.md").exists())

    def test_symlinks_refused(self):
        (self.root / "demo" / "link.md").symlink_to("/etc/passwd")
        with self.assertRaises(st.TemplateError):
            self.apply()
        (self.root / "demo" / "link.md").unlink()
        (self.ws / "docs").symlink_to("/tmp")
        with self.assertRaises(st.TemplateError):
            self.apply()

    def test_git_init_marker(self):
        (self.root / "demo" / "template.yaml").write_text(
            "id: demo\nname: Demo\nversion: 1\ndescription: d\ngit_init: true\n")
        self.apply()
        self.assertTrue((self.ws / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
