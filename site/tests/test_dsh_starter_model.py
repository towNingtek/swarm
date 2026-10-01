"""Starter model wiring into a site's DSH files; no Docker/network."""
import contextlib
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import dsh_sitectl as dsh

TOKEN = "sms_" + "A" * 43
ROTATED = "sms_" + "B" * 43
URL = "http://172.17.0.1:8212/v1"
PATCH = "- id: locale\n  config:\n    preference: zh-TW\n"


class StarterModelTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.object(dsh.common, "SITES_ROOT", self.root))
        self.home = dsh.init_dsh_home("ab")
        dsh.save_state("ab", {"engine": "dsh", "name": "ab", "host": "ab.example.com"})
        self.patch = self.home / "profiles" / "web" / "cordis.patch.yml"
        self.patch.parent.mkdir(parents=True, exist_ok=True)
        self.patch.write_text(PATCH)
        self.patch.chmod(0o600)
        self.creds = self.home / ".credentials.yaml"
        self.creds.write_text("version: 1\nrecords:\n  client-connection/x:\n    kind: grant\n")

    def test_writes_route_default_and_token_only_in_credentials(self):
        dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        text = self.patch.read_text()
        entries = yaml.safe_load(text)
        self.assertEqual(entries[0]["id"], "locale")  # site overrides kept
        route = entries[1]["config"]["providers"][dsh.STARTER_ROUTE]
        self.assertEqual(route["baseURL"], URL)
        self.assertEqual(route["apiKeyEnv"], dsh.STARTER_KEY_REF)
        self.assertEqual(entries[2]["config"], {"provider": dsh.STARTER_ROUTE, "model": "cloud-fast"})
        self.assertNotIn(TOKEN, text)
        creds = yaml.safe_load(self.creds.read_text())
        self.assertEqual(creds["refs"][dsh.STARTER_KEY_REF], TOKEN)
        self.assertIn("client-connection/x", creds["records"])  # other records kept
        self.assertEqual(stat.S_IMODE(self.creds.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.patch.stat().st_mode), 0o600)
        self.assertFalse(self.patch.with_name("cordis.patch.yml.lock").exists())

    def test_idempotent_and_rotates(self):
        dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        first = self.patch.read_text()
        dsh.enable_starter_model("ab", ROTATED, base_url=URL, models=["cloud-fast"])
        self.assertEqual(self.patch.read_text(), first)
        self.assertEqual(first.count(dsh.STARTER_MARKER), 1)
        self.assertEqual(yaml.safe_load(self.creds.read_text())["refs"][dsh.STARTER_KEY_REF], ROTATED)

    def test_customer_model_choice_survives_rerun(self):
        dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        # What DSH writes when the customer picks their own model in the UI.
        text = self.patch.read_text().replace(
            f"provider: {dsh.STARTER_ROUTE}\n    model: cloud-fast",
            "provider: deepseek-official\n    model: deepseek-flash\n    reasoningEffort: high")
        self.patch.write_text(text)
        dsh.enable_starter_model("ab", ROTATED, base_url=URL, models=["cloud-fast"])
        entries = yaml.safe_load(self.patch.read_text())
        defaults = [e for e in entries if e["id"] == "agent-default-model"]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["config"]["provider"], "deepseek-official")

    def test_migrates_old_layout_keeping_choice(self):
        old = (PATCH + "\n" + dsh.STARTER_MARKER + "\n- id: llm-pi-ai\n  config: {}\n"
               "- id: agent-default-model\n  name: '@deepseek-ai/dsh-agent-default-model'\n"
               "  config:\n    provider: openrouter\n    model: x\n\n" + dsh.STARTER_END + "\n")
        self.patch.write_text(old)
        dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        text = self.patch.read_text()
        entries = yaml.safe_load(text)
        self.assertEqual([e["id"] for e in entries], ["locale", "llm-pi-ai", "agent-default-model"])
        self.assertEqual(entries[2]["config"]["provider"], "openrouter")
        block = text[text.index(dsh.STARTER_MARKER):text.index(dsh.STARTER_END)]
        self.assertNotIn("agent-default-model", block)

    def test_prepare_workspace_places_guide_and_template(self):
        import json
        out = dsh.prepare_workspace("ab", "swarm", starter_model="cloud-fast")
        guide = (self.home / "AGENTS.md").read_text()
        self.assertIn("cloud-fast", guide)
        ws = self.root / "ab" / "workspace"
        self.assertIn("INITIAL.md", out["created"])
        self.assertTrue((ws / ".dsh" / "skills" / "hive-new" / "SKILL.md").is_file())
        self.assertTrue((ws / ".git").is_dir())
        self.assertTrue((ws / "hives").is_dir())
        self.assertEqual(json.loads((ws / ".dsh-template.json").read_text())["template"], "swarm")
        self.assertFalse(any(p.name in {".keys", "keys"} for p in ws.rglob("*")))
        (ws / "INITIAL.md").write_text("mine")
        again = dsh.prepare_workspace("ab", "swarm", starter_model=None)
        self.assertEqual(again["created"], [])
        self.assertEqual((ws / "INITIAL.md").read_text(), "mine")
        self.assertNotIn("cloud-fast", (self.home / "AGENTS.md").read_text())

    def test_rejects_bad_inputs_and_damaged_block(self):
        for token in ("", "platform-key", "sms_short"):
            with self.assertRaises(dsh.SiteError):
                dsh.enable_starter_model("ab", token, base_url=URL, models=["cloud-fast"])
        for url in ("file:///etc", "http://x/v1\n- id: evil"):
            with self.assertRaises(dsh.SiteError):
                dsh.enable_starter_model("ab", TOKEN, base_url=url, models=["cloud-fast"])
        with self.assertRaises(dsh.SiteError):
            dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["a\n- id: evil"])
        self.patch.write_text(PATCH + dsh.STARTER_MARKER + "\n")
        with self.assertRaises(dsh.SiteError):
            dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        self.assertNotIn(dsh.STARTER_KEY_REF, self.creds.read_text().split("records")[0] + "")

    def test_waits_for_dsh_lock(self):
        lock = self.patch.with_name("cordis.patch.yml.lock")
        lock.write_text("1\n")
        with patch("time.monotonic", side_effect=[0, 0, 100]), patch("time.sleep"):
            with self.assertRaises(dsh.SiteError):
                dsh.enable_starter_model("ab", TOKEN, base_url=URL, models=["cloud-fast"])
        self.assertTrue(lock.exists())  # someone else's lock is never removed


if __name__ == "__main__":
    unittest.main()
