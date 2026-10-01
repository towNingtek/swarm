"""Pure lifecycle regression tests for towNingtek/swarm-next#9.

All external effects are mocked; only TemporaryDirectory files are touched.
"""
import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dsh_sitectl as dsh


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "test-site"
        self.stack.enter_context(patch.object(dsh.common, "SITES_ROOT", Path(self.temp)))
        # Explicit test fixture; never infer a real Docker gateway from host state.
        self.stack.enter_context(patch.dict(dsh.os.environ, {'SWARM_RELAY_TRUSTED_PEERS': '192.0.2.1'}))
        self.mocks = {}
        for target in ("allocate_free_port", "docker_run", "docker_rm", "proxy_add",
                       "proxy_remove", "_dsh_self_test"):
            self.mocks[target] = self.stack.enter_context(patch.object(dsh, target))
        self.mocks["allocate_free_port"].return_value = 18000
        for target in ("dns_create", "dns_delete"):
            self.mocks[target] = self.stack.enter_context(patch.object(dsh.common, target))
        self.mocks["dns_create"].return_value = "owned-dns-id"
        # Fail closed if a test accidentally reaches an unmocked process.
        self.process = self.stack.enter_context(patch.object(
            dsh.subprocess, "run", side_effect=AssertionError("real process forbidden")))
        self.args = argparse.Namespace(name="test-site", password="test-password",
                                       cpus="1", memory="1g", purge_data=False)
        self.output = self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def seed(self, **state):
        dsh.init_dsh_home(self.args.name)
        (self.root / "dsh-home" / "secret").write_text("keep me")
        dsh.save_state(self.args.name, {"engine": "dsh", "port": 18000,
                                      "dns_record_id": "owned-dns-id", **state})

    def test_successful_create(self):
        dsh.cmd_create(self.args)
        self.assertTrue((self.root / "dsh-home" / "dsh.env").exists())
        self.assertEqual(dsh.load_state(self.args.name)["dns_record_id"], "owned-dns-id")
        self.mocks["dns_delete"].assert_not_called()

    def test_create_local_failures_remove_entire_owned_root(self):
        for target in ("init_dsh_home", "write_dsh_env", "save_state"):
            with self.subTest(target=target):
                with patch.object(dsh, target, side_effect=OSError(target)):
                    with self.assertRaises(OSError):
                        dsh.cmd_create(self.args)
                self.assertFalse(self.root.exists())
                self.mocks["docker_rm"].assert_not_called()
                self.mocks["dns_delete"].assert_not_called()

    def test_create_existing_directory_is_never_removed(self):
        self.seed()
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_create(self.args)
        self.assertEqual((self.root / "dsh-home" / "secret").read_text(), "keep me")
        self.mocks["docker_run"].assert_not_called()

    def test_create_mkdir_race_does_not_remove_foreign_directory(self):
        original = Path.mkdir
        def race(path, *args, **kwargs):
            if path == self.root:
                original(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, "mkdir", race):
            with self.assertRaises(FileExistsError):
                dsh.cmd_create(self.args)
        self.assertTrue(self.root.exists())

    def test_dns_success_followed_by_state_save_failure(self):
        original = dsh.save_state
        count = 0
        def fail_second(name, state):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("save DNS state failed")
            return original(name, state)
        with patch.object(dsh, "save_state", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "save DNS"):
                dsh.cmd_create(self.args)
        self.mocks["dns_delete"].assert_called_once_with("owned-dns-id")
        self.mocks["docker_rm"].assert_called_once_with(self.args.name)
        self.assertFalse(self.root.exists())

    def test_dns_success_followed_by_state_load_failure(self):
        with patch.object(dsh, "load_state", side_effect=OSError("load failed")):
            with self.assertRaises(OSError):
                dsh.cmd_create(self.args)
        self.mocks["dns_delete"].assert_called_once_with("owned-dns-id")
        self.assertFalse(self.root.exists())

    def test_docker_failure_does_not_remove_foreign_named_container(self):
        self.mocks["docker_run"].side_effect = dsh.SiteError("name conflict")
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_create(self.args)
        self.mocks["docker_rm"].assert_not_called()
        self.assertFalse(self.root.exists())

    def test_partial_docker_creation_removes_only_cidfile_container(self):
        def partial(*args, cid_file, **kwargs):
            cid_file.write_text("owned-container-id")
            raise dsh.SiteError("start failed")
        self.mocks["docker_run"].side_effect = partial
        with patch.object(dsh, "_docker_rm_id") as remove:
            with self.assertRaises(dsh.SiteError):
                dsh.cmd_create(self.args)
        remove.assert_called_once_with("owned-container-id")
        self.mocks["docker_rm"].assert_not_called()
        self.assertFalse(self.root.exists())

    def test_dns_create_failure_does_not_delete_unknown_dns(self):
        self.mocks["dns_create"].side_effect = dsh.SiteError("DNS failed")
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_create(self.args)
        self.mocks["dns_delete"].assert_not_called()
        self.mocks["docker_rm"].assert_called_once()
        self.assertFalse(self.root.exists())

    def test_proxy_failure_does_not_remove_unacquired_proxy(self):
        self.mocks["proxy_add"].side_effect = dsh.SiteError("proxy conflict")
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_create(self.args)
        self.mocks["proxy_remove"].assert_not_called()
        self.mocks["dns_delete"].assert_called_once()
        self.assertFalse(self.root.exists())

    def test_self_test_failure_cleans_all_resources(self):
        self.mocks["_dsh_self_test"].side_effect = dsh.SiteError("health failed")
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_create(self.args)
        for target in ("proxy_remove", "dns_delete", "docker_rm"):
            self.mocks[target].assert_called_once()
        self.assertFalse(self.root.exists())

    def test_rollback_cleanup_failure_is_reported_and_other_cleanup_continues(self):
        for target in ("proxy_remove", "dns_delete", "docker_rm"):
            with self.subTest(target=target):
                self.mocks["_dsh_self_test"].side_effect = dsh.SiteError("health failed")
                self.mocks[target].side_effect = OSError("cleanup failed")
                with self.assertRaisesRegex(dsh.SiteError, "rollback incomplete") as caught:
                    dsh.cmd_create(self.args)
                self.assertIsInstance(caught.exception.__cause__, dsh.SiteError)
                self.assertTrue(self.root.exists())
                self.assertNotIn("站台建立完成", self.output.getvalue())
                for cleanup in ("proxy_remove", "dns_delete", "docker_rm"):
                    self.mocks[cleanup].assert_called_once()
                    self.mocks[cleanup].reset_mock(side_effect=True)
                import shutil
                shutil.rmtree(self.root)

    def test_rollback_directory_cleanup_failure_is_reported(self):
        self.mocks["_dsh_self_test"].side_effect = dsh.SiteError("health failed")
        with patch.object(dsh.shutil, "rmtree", side_effect=OSError("remove failed")):
            with self.assertRaisesRegex(dsh.SiteError, "rollback incomplete.*data"):
                dsh.cmd_create(self.args)
        self.assertTrue(self.root.exists())
        self.assertNotIn("站台建立完成", self.output.getvalue())

    def test_delete_preserves_data_and_marks_tombstone(self):
        self.seed()
        dsh.cmd_delete(self.args)
        state = dsh.load_state(self.args.name)
        self.assertTrue(state["deleted"])
        self.assertNotIn("dns_record_id", state)
        self.assertTrue((self.root / "dsh-home" / "secret").exists())
        dsh.cmd_delete(self.args)
        self.mocks["dns_delete"].assert_called_once()

    def test_delete_purges_all_data(self):
        self.seed()
        self.args.purge_data = True
        dsh.cmd_delete(self.args)
        self.assertFalse(self.root.exists())
        self.assertIn("含資料", self.output.getvalue())

    def test_delete_without_dns_id_does_not_call_dns(self):
        self.seed(dns_record_id="")
        dsh.cmd_delete(self.args)
        self.mocks["dns_delete"].assert_not_called()

    def test_delete_rejects_other_engine_and_invalid_name(self):
        self.seed(engine="opencode")
        with self.assertRaises(dsh.SiteError):
            dsh.cmd_delete(self.args)
        self.args.name = "../unsafe"
        with self.assertRaises((dsh.SiteError, ValueError)):
            dsh.cmd_delete(self.args)
        self.mocks["docker_rm"].assert_not_called()
        self.assertTrue(self.root.exists())

    def test_delete_external_failures_never_purge_or_mark_deleted(self):
        for purge in (False, True):
            for target in ("docker_rm", "proxy_remove", "dns_delete"):
                with self.subTest(purge=purge, target=target):
                    self.seed()
                    self.args.purge_data = purge
                    self.mocks[target].side_effect = OSError("cleanup failed")
                    with self.assertRaisesRegex(dsh.SiteError, "Delete incomplete"):
                        dsh.cmd_delete(self.args)
                    self.assertTrue(self.root.exists())
                    self.assertFalse(dsh.load_state(self.args.name).get("deleted", False))
                    self.assertNotIn("已刪除", self.output.getvalue())
                    for cleanup in ("docker_rm", "proxy_remove", "dns_delete"):
                        self.mocks[cleanup].assert_called_once()
                        self.mocks[cleanup].reset_mock(side_effect=True)

    def test_delete_local_cleanup_failures_do_not_report_success(self):
        self.seed()
        with patch.object(dsh, "save_state", side_effect=OSError("save failed")):
            with self.assertRaises(OSError):
                dsh.cmd_delete(self.args)
        self.args.purge_data = True
        with patch.object(dsh.shutil, "rmtree", side_effect=OSError("purge failed")):
            with self.assertRaises(OSError):
                dsh.cmd_delete(self.args)
        self.assertNotIn("已刪除", self.output.getvalue())
        self.assertTrue(self.root.exists())

    def test_docker_cleanup_checks_return_code(self):
        self.process.side_effect = None
        for error in ("permission denied", "Cannot connect to the Docker daemon"):
            self.process.return_value = subprocess.CompletedProcess([], 1, "", error)
            with self.assertRaises(dsh.SiteError):
                dsh._docker_rm_id("owned-id")
        self.process.return_value = subprocess.CompletedProcess(
            [], 1, "", "Error response from daemon: No such container: owned-id")
        dsh._docker_rm_id("owned-id")
        self.process.return_value = subprocess.CompletedProcess([], 0, "owned-id", "")
        dsh._docker_rm_id("owned-id")


if __name__ == "__main__":
    unittest.main()
