"""Per-site extra packages: derived image, reversible swap; no real Docker."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dsh_sitectl as dsh

BASE = "sha256:" + "a" * 64
BUILT = "sha256:" + "b" * 64
ENV = "WEB_AUTH_PASSWORD_HASH=scrypt$x\nWEB_AUTH_ENTRY_SECRET=k\n"


class SitePackagesTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(dsh.common, "SITES_ROOT", root))
        home = dsh.init_dsh_home("ab")
        self.env = home / "dsh.env"
        self.env.write_text(ENV)
        dsh.save_state("ab", {"engine": "dsh", "name": "ab", "host": "ab.example.com",
                              "port": 18003, "cpus": "2", "memory": "4g", "image": "swarm-site:latest"})
        self.running = BASE
        self.commands = []

        def fake_run(command):
            self.commands.append(command)
            if command[:2] == ["docker", "inspect"]:
                return self.running
            if command[:3] == ["docker", "image", "inspect"]:
                return "sha256:" + "f" * 64  # shared image moved on: must be ignored
            return ""
        self.stack.enter_context(patch.object(dsh, "run", side_effect=fake_run))
        self.stack.enter_context(patch.object(dsh, "_docker_rm_id"))
        self.started = []
        self.stack.enter_context(patch.object(
            dsh, "docker_run", side_effect=lambda *a, **k: self.started.append(k["image"])))
        self.stack.enter_context(patch.object(dsh.subprocess, "run",
                                              side_effect=AssertionError("real process forbidden")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.builds = []

    def builder(self, name, base, packages):
        self.builds.append((name, base, packages))
        return BUILT

    def test_builds_on_the_running_image_and_records_packages(self):
        out = dsh.set_site_packages("ab", ["openssh-client", "curl", "curl"],
                                    self_test=lambda *a, **k: None, builder=self.builder)
        self.assertEqual(self.builds, [("ab", BASE, ["curl", "openssh-client"])])
        self.assertEqual(self.started, [BUILT])
        state = dsh.load_state("ab")
        self.assertEqual((state["image"], state["base_image"], state["packages"]),
                         (BUILT, BASE, ["curl", "openssh-client"]))
        self.assertEqual(out["packages"], ["curl", "openssh-client"])
        self.assertEqual(self.env.read_text(), ENV)

    def test_later_change_rebuilds_from_the_recorded_base_not_the_derived_image(self):
        dsh.set_site_packages("ab", ["curl"], self_test=lambda *a, **k: None, builder=self.builder)
        self.running = BUILT
        dsh.set_site_packages("ab", ["jq"], self_test=lambda *a, **k: None, builder=self.builder)
        self.assertEqual(self.builds[-1], ("ab", BASE, ["jq"]))

    def test_empty_list_returns_to_the_base_image_without_building(self):
        dsh.set_site_packages("ab", ["curl"], self_test=lambda *a, **k: None, builder=self.builder)
        self.running = BUILT
        dsh.set_site_packages("ab", [], self_test=lambda *a, **k: None, builder=self.builder)
        self.assertEqual(self.started[-1], BASE)
        self.assertEqual(dsh.load_state("ab")["packages"], [])

    def test_failed_build_stops_nothing(self):
        def broken(*_a):
            raise dsh.SiteError("package build failed: E: Unable to locate package nope")
        with self.assertRaisesRegex(dsh.SiteError, "build failed"):
            dsh.set_site_packages("ab", ["nope"], self_test=lambda *a, **k: None, builder=broken)
        self.assertNotIn(["docker", "stop", "site-ab"], self.commands)
        self.assertEqual(self.started, [])
        self.assertNotIn("packages", dsh.load_state("ab"))

    def test_failed_self_test_restores_old_container_and_state(self):
        def failing(*_a, **_k):
            raise dsh.SiteError("login failed")
        with self.assertRaisesRegex(dsh.SiteError, "old packages"):
            dsh.set_site_packages("ab", ["curl"], self_test=failing, builder=self.builder)
        self.assertEqual(self.commands[-1], ["docker", "start", "site-ab"])
        self.assertNotIn("packages", dsh.load_state("ab"))

    def test_package_names_cannot_smuggle_options_or_shell(self):
        for bad in (["-o", "x"], ["curl; rm -rf /"], ["curl=1.0"], ["../x"], ["Curl"], "curl",
                    [1], ["a" * 64], [f"p{i}" for i in range(41)]):
            with self.subTest(bad=bad), self.assertRaises(dsh.SiteError):
                dsh.set_site_packages("ab", bad, self_test=lambda *a, **k: None, builder=self.builder)
        self.assertEqual(self.builds, [])
        self.assertEqual(self.commands, [])

    def test_dockerfile_installs_as_root_and_runs_unprivileged(self):
        with patch.object(dsh, "CONTAINER_USER", "1005:1006"):
            text = dsh._site_dockerfile("dsh-site-base:x", ["curl", "openssh-client"])
        lines = text.splitlines()
        self.assertEqual(lines[0], "FROM dsh-site-base:x")
        self.assertEqual(lines[-1], "USER dsh")
        self.assertIn("--no-install-recommends curl openssh-client", text)
        self.assertIn("site:x:1005:1006::/home/dsh/workspace:/bin/sh", text)

    def test_password_change_keeps_a_package_site_on_its_own_image(self):
        dsh.set_site_packages("ab", ["curl"], self_test=lambda *a, **k: None, builder=self.builder)
        self.running = BUILT
        dsh.set_site_password("ab", "a-long-site-pass", self_test=lambda *a, **k: None)
        self.assertEqual(self.started[-1], BUILT)
        self.assertEqual(dsh.load_state("ab")["packages"], ["curl"])


if __name__ == "__main__":
    unittest.main()
