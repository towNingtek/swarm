"""Reversible DSH site password swap; no real Docker/network."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dsh_sitectl as dsh

IMAGE = "sha256:" + "a" * 64
ENV = ("NODE_OPTIONS=--no-warnings\nWEB_AUTH_USERNAME=admin\n"
       "WEB_AUTH_PASSWORD_HASH=scrypt$old\nWEB_AUTH_MODE=always\nWEB_AUTH_ENTRY_SECRET=keep-me\n")


class SitePasswordSwapTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(dsh.common, "SITES_ROOT", self.root))
        self.home = dsh.init_dsh_home("ab")
        self.env = self.home / "dsh.env"
        self.env.write_text(ENV)
        self.env.chmod(0o600)
        self.sessions = self.home / "plugins" / "web-auth" / "sessions.json"
        self.sessions.parent.mkdir(parents=True, exist_ok=True)
        self.sessions.write_text('{"version":1,"sessions":[{"token":"old"}]}')
        dsh.save_state("ab", {"engine": "dsh", "name": "ab", "host": "ab.example.com",
                              "port": 18003, "cpus": "2", "memory": "4g", "image": "dsh-hub:latest"})
        self.commands = []

        self.target, self.seals = IMAGE, {IMAGE: "1" * 64}

        def fake_run(command):
            self.commands.append(command)
            if command[:2] == ["docker", "inspect"]:
                return IMAGE
            if command[:3] == ["docker", "image", "inspect"]:
                return self.target
            if command[:2] == ["docker", "run"] and "sha256sum" in command:
                return self.seals[command[-2]] + "  manifest"
            return ""
        self.fake_run = fake_run
        self.stack.enter_context(patch.object(dsh, "run", side_effect=fake_run))
        self.removed = []
        self.stack.enter_context(patch.object(dsh, "_docker_rm_id", side_effect=self.removed.append))
        self.started = []
        self.stack.enter_context(patch.object(
            dsh, "docker_run", side_effect=lambda *a, **k: self.started.append((a, k))))
        self.stack.enter_context(patch.object(dsh.subprocess, "run",
                                              side_effect=AssertionError("real process forbidden")))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_success_swaps_only_the_hash_and_pins_image(self):
        seen = []
        dsh.set_site_password("ab", "a-long-site-pass",
                              self_test=lambda port, pw, base_url: seen.append((port, pw, base_url)))
        text = self.env.read_text()
        self.assertNotIn("scrypt$old", text)
        new_hash = [l for l in text.splitlines() if l.startswith("WEB_AUTH_PASSWORD_HASH=")]
        self.assertEqual(len(new_hash), 1)
        self.assertTrue(new_hash[0].split("=", 1)[1].startswith("scrypt$16384$8$1$"))
        # Everything else in the env is byte-for-byte unchanged.
        strip = lambda t: [l for l in t.splitlines() if not l.startswith("WEB_AUTH_PASSWORD_HASH=")]
        self.assertEqual(strip(text), strip(ENV))
        self.assertNotIn("a-long-site-pass", text)
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.sessions.exists(), "old sessions must not outlive the password")
        (args, kwargs), = self.started
        self.assertEqual(args[1:], (18003, self.env, "2", "4g"))
        self.assertEqual(kwargs, {"image": IMAGE})
        self.assertEqual(seen, [(18003, "a-long-site-pass", "https://ab.example.com")])
        # Old container was stopped and renamed, then removed only after success.
        self.assertIn(["docker", "stop", "site-ab"], self.commands)
        renamed = [c for c in self.commands if c[:2] == ["docker", "rename"]]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(self.removed, [renamed[0][3]])
        self.assertEqual(self.output.getvalue(), "")

    def test_moves_to_current_image_only_with_identical_sealed_profile(self):
        fixed = "sha256:" + "b" * 64
        self.target, self.seals[fixed] = fixed, "1" * 64
        dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertEqual(self.started[0][1], {"image": fixed})
        self.assertEqual(dsh.load_state("ab")["image"], fixed)

    def test_upgrade_with_a_different_profile_is_refused_before_stop(self):
        other = "sha256:" + "c" * 64
        self.target, self.seals[other] = other, "2" * 64
        with self.assertRaisesRegex(dsh.SiteError, "explicit migration"):
            dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertNotIn(["docker", "stop", "site-ab"], self.commands)
        self.assertEqual(self.env.read_text(), ENV)
        self.assertEqual(self.started, [])

    def test_self_test_failure_restores_everything(self):
        before_env, before_sessions = self.env.read_bytes(), self.sessions.read_bytes()

        def failing(*_a, **_k):
            raise dsh.SiteError("login failed")
        with self.assertRaisesRegex(dsh.SiteError, "restored to the old password"):
            dsh.set_site_password("ab", "a-long-site-pass", self_test=failing)
        self.assertEqual(self.env.read_bytes(), before_env)
        self.assertEqual(self.sessions.read_bytes(), before_sessions)
        previous = [c for c in self.commands if c[:2] == ["docker", "rename"]][0][3]
        # New container removed, old one renamed back and started.
        self.assertEqual(self.removed, ["site-ab"])
        self.assertEqual(self.commands[-2:], [["docker", "rename", previous, "site-ab"],
                                              ["docker", "start", "site-ab"]])

    def test_failed_rename_restarts_the_old_container(self):
        def fake_run(command):
            if command[:2] == ["docker", "rename"]:
                self.commands.append(command)
                raise dsh.SiteError("rename failed")
            return self.fake_run(command)
        with patch.object(dsh, "run", side_effect=fake_run):
            with self.assertRaises(dsh.SiteError):
                dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertEqual(self.commands[-1], ["docker", "start", "site-ab"])
        self.assertEqual(self.env.read_text(), ENV)
        self.assertEqual(self.started, [])

    def test_rejected_before_any_effect(self):
        for bad in ("short", "x" * 11, "x" * 1025, "line\nbreak-long-enough", "nul\x00-long-enough", 123):
            with self.subTest(bad=bad), self.assertRaises(dsh.SiteError):
                dsh.set_site_password("ab", bad, self_test=lambda *a, **k: None)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.env.read_text(), ENV)

    def test_unpinnable_image_or_bad_env_refused_before_stop(self):
        with patch.object(dsh, "run", side_effect=lambda c: self.commands.append(c) or "dsh-hub:latest"):
            with self.assertRaisesRegex(dsh.SiteError, "pin"):
                dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.env.write_text(ENV + "WEB_AUTH_PASSWORD_HASH=scrypt$dup\n")
        self.commands.clear()
        with self.assertRaisesRegex(dsh.SiteError, "exactly one"):
            dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertNotIn(["docker", "stop", "site-ab"], self.commands)

    def test_wrong_engine_or_deleted_refused(self):
        for state in ({"engine": "opencode"}, {"engine": "dsh", "deleted": True}):
            dsh.save_state("ab", state)
            with self.subTest(state=state), self.assertRaises(dsh.SiteError):
                dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertEqual(self.commands, [])

    def test_general_replacement_still_refused(self):
        import argparse
        for command in (dsh.cmd_update_resources, dsh.cmd_reset_password):
            with self.assertRaisesRegex(dsh.SiteError, "replacement disabled"):
                command(argparse.Namespace(name="ab"))


if __name__ == "__main__":
    unittest.main()


class ContainerHomeTests(unittest.TestCase):
    """The folder picker opens at HOME; it must be a writable, persistent dir."""

    def test_docker_run_sets_writable_persistent_home(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(dsh.common, "SITES_ROOT", Path(tmp)), \
                patch.object(dsh, "run") as run:
            home = dsh.init_dsh_home("ab")
            env = home / "dsh.env"
            env.write_text("WEB_AUTH_PASSWORD_HASH=scrypt$x\n")
            dsh.docker_run("ab", 18003, env, "2", "4g", image="sha256:" + "a" * 64)
            args = run.call_args.args[0]
            self.assertIn(f"HOME={dsh.WORKSPACE_IN_CONTAINER}", args)
            workspace = Path(tmp) / "ab" / "workspace"
            self.assertIn(f"{workspace}:{dsh.WORKSPACE_IN_CONTAINER}", args)
            self.assertTrue(workspace.is_dir())
            self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
            self.assertNotEqual(dsh.WORKSPACE_IN_CONTAINER, "/home/dsh/dsh-home")
