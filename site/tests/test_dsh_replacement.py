"""Fail-closed replacement coverage for swarm-next#9; no real Docker/network."""
import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dsh_sitectl as dsh


class ReplacementSafetyTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(dsh.common, "SITES_ROOT", self.root))
        self.args = argparse.Namespace(name="test-site", password="new-secret", cpus="8", memory="16g")
        home = dsh.init_dsh_home(self.args.name)
        (home / "dsh.env").write_text("WEB_AUTH_PASSWORD_HASH=old-hash\nOTHER=keep\n")
        (home / "dsh.env").chmod(0o600)
        dsh.save_state(self.args.name, {"engine": "dsh", "image": "custom@sha256:pinned",
                                      "port": 18000, "cpus": "1", "memory": "1g"})
        self.process = self.stack.enter_context(patch.object(
            dsh.subprocess, "run", side_effect=AssertionError("real process forbidden")))
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mode)
                for p in self.root.rglob("*") if p.is_file()}

    def test_replacement_commands_refuse_before_any_mutation(self):
        for command in (dsh.cmd_update_resources, dsh.cmd_reset_password):
            with self.subTest(command=command.__name__):
                before = self.snapshot()
                with patch.object(dsh, "save_state") as save, \
                     patch.object(dsh, "docker_rm") as remove, \
                     patch.object(dsh, "docker_run") as create, \
                     patch.object(dsh, "generate_password") as password:
                    with self.assertRaisesRegex(dsh.SiteError, "replacement disabled"):
                        command(self.args)
                self.assertEqual(self.snapshot(), before)
                for mock in (save, remove, create, password, self.process):
                    mock.assert_not_called()
                self.assertEqual(self.output.getvalue(), "")

    def test_missing_env_does_not_trigger_destructive_replacement(self):
        (self.root / self.args.name / "dsh-home" / "dsh.env").unlink()
        before = self.snapshot()
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_reset_password(self.args)
        self.assertEqual(self.snapshot(), before)
        self.process.assert_not_called()

    def test_wrong_engine_and_deleted_sites_rejected_before_process(self):
        for state in ({"engine": "opencode"}, {"engine": "dsh", "deleted": True}):
            dsh.save_state(self.args.name, state)
            for command in (dsh.cmd_start, dsh.cmd_stop,
                            dsh.cmd_reset_password, dsh.cmd_update_resources):
                with self.subTest(state=state, command=command.__name__):
                    with self.assertRaises(dsh.SiteError):
                        command(self.args)
        self.process.assert_not_called()

    def test_invalid_name_is_rejected_before_loading_state(self):
        self.args.name = "../unsafe"
        with patch.object(dsh, "load_state") as load:
            for command in (dsh.cmd_start, dsh.cmd_stop,
                            dsh.cmd_reset_password, dsh.cmd_update_resources):
                with self.assertRaises((ValueError, dsh.SiteError)):
                    command(self.args)
            load.assert_not_called()
        self.process.assert_not_called()

    def test_start_stop_surface_docker_failure(self):
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 1, "", "daemon failure")
        before = self.snapshot()
        for command in (dsh.cmd_start, dsh.cmd_stop):
            with self.assertRaises(dsh.SiteError):
                command(self.args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.output.getvalue(), "")

    def test_start_stop_success_use_checked_command(self):
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        for command, action in ((dsh.cmd_start, "start"), (dsh.cmd_stop, "stop")):
            command(self.args)
            self.assertEqual(self.process.call_args.args[0],
                             ["docker", action, dsh.container_name(self.args.name)])


if __name__ == "__main__":
    unittest.main()
