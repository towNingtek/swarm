"""Offline DSH allocation regressions: real processes, no Docker or sockets."""
import argparse
import contextlib
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import dsh_sitectl as dsh


def create_worker(root, name, entered, release, allocated, result, fail=False, stage="docker"):
    dsh.common.SITES_ROOT = Path(root)
    original = dsh.allocate_free_port

    def allocate():
        allocated.set()
        return original()

    def docker(*args, **kwargs):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("test release timeout")
        if fail:
            raise RuntimeError("injected Docker failure")

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(dsh, "allocate_free_port", allocate))
        stack.enter_context(patch.object(dsh, "_port_free", return_value=True))
        stack.enter_context(patch.object(dsh, "write_dsh_env", return_value=Path(root) / "fake.env"))
        stack.enter_context(patch.object(dsh, "docker_run", docker if stage == "docker" else lambda *a, **kw: None))
        for target in ("proxy_add", "proxy_remove", "docker_rm"):
            stack.enter_context(patch.object(dsh, target))
        stack.enter_context(patch.object(dsh, "_dsh_self_test", docker if stage == "probe" else lambda *a, **kw: None))
        stack.enter_context(patch.object(dsh.common, "dns_create", return_value="fake-id"))
        stack.enter_context(patch.object(dsh.common, "dns_delete"))
        stack.enter_context(patch.object(dsh.subprocess, "run", side_effect=AssertionError("external process forbidden")))
        try:
            dsh.cmd_create(argparse.Namespace(name=name, password="fake", cpus="1", memory="1g"))
            result.put((name, dsh.load_state(name)["port"]))
        except Exception as exc:
            result.put((name, str(exc)))


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "sites"
        self.patch = patch.object(dsh.common, "SITES_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def concurrent_create(self, fail=False, stage="docker"):
        ctx = multiprocessing.get_context("spawn")
        first_entered, second_entered = ctx.Event(), ctx.Event()
        release, free = ctx.Event(), ctx.Event()
        first_allocated, second_allocated = ctx.Event(), ctx.Event()
        result = ctx.Queue()
        free.set()
        first = ctx.Process(target=create_worker, args=(str(self.root), "first-site", first_entered, release, first_allocated, result, fail, stage))
        second = ctx.Process(target=create_worker, args=(str(self.root), "second-site", second_entered, free, second_allocated, result))
        processes = (first, second)
        try:
            first.start()
            self.assertTrue(first_entered.wait(10))
            # Reservation is already published before Docker while the lock stays held.
            self.assertIsInstance(dsh.load_state("first-site")["port"], int)
            second.start()
            self.assertFalse(second_allocated.wait(0.5), "allocation escaped create lock")
            release.set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            values = dict(result.get(timeout=2) for _ in processes)
            if fail:
                self.assertEqual(values["first-site"], "injected Docker failure")
                self.assertFalse((self.root / "first-site").exists())
                self.assertEqual(values["second-site"], dsh.common.PORT_MIN)
            else:
                self.assertNotEqual(values["first-site"], values["second-site"])
        finally:
            release.set()
            for process in processes:
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(5)
            result.close()
            result.join_thread()

    def test_concurrent_creates_reserve_distinct_ports(self):
        self.concurrent_create()

    def test_lock_spans_post_docker_authentication_probe(self):
        self.concurrent_create(stage="probe")

    def test_exception_releases_lock_after_rollback(self):
        self.concurrent_create(fail=True)

    def test_subprocess_lock_blocks_and_releases(self):
        code = "import dsh_sitectl as d; from pathlib import Path; import sys; d.common.SITES_ROOT=Path(sys.argv[1]); print('ready', flush=True);\nwith d._create_lock(): print('acquired', flush=True)"
        with dsh._create_lock():
            lock = self.root.parent / ".sites.dsh-create.lock"
            inode = lock.stat().st_ino
            self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
            child = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.root)], cwd=Path(__file__).parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), "ready")
            with self.assertRaises(subprocess.TimeoutExpired):
                child.communicate(timeout=0.2)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, stderr)
        self.assertIn("acquired", stdout)
        self.assertEqual(lock.stat().st_ino, inode)

    def test_symlink_lock_rejected_without_touching_target(self):
        target = Path(self.temp.name) / "target"
        target.write_text("unchanged")
        (self.root.parent / ".sites.dsh-create.lock").symlink_to(target)
        with self.assertRaises(dsh.SiteError), dsh._create_lock():
            self.fail("symlink accepted")
        self.assertEqual(target.read_text(), "unchanged")

    def test_symlink_sites_root_and_ancestor_rejected(self):
        real = Path(self.temp.name) / "real"
        real.mkdir()
        self.root.symlink_to(real, target_is_directory=True)
        with self.assertRaises(dsh.SiteError), dsh._create_lock():
            self.fail("root symlink accepted")
        with patch.object(dsh.common, "SITES_ROOT", self.root / "nested"):
            with self.assertRaises(OSError), dsh._create_lock():
                self.fail("ancestor symlink accepted")

    def test_nonprivate_and_hardlinked_lock_rejected(self):
        lock = self.root.parent / ".sites.dsh-create.lock"
        lock.touch(mode=0o644)
        with self.assertRaises(dsh.SiteError), dsh._create_lock():
            self.fail("public lock accepted")
        lock.chmod(0o600)
        os.link(lock, self.root.parent / "alias")
        with self.assertRaises(dsh.SiteError), dsh._create_lock():
            self.fail("hardlinked lock accepted")

    def test_atomic_failures_preserve_state_and_clean_temporary(self):
        (self.root / "test-site").mkdir(parents=True)
        dsh.save_state("test-site", {"port": 18000, "engine": "dsh"})
        path = dsh.state_path("test-site")
        before = path.read_bytes()
        for target in ("fsync", "replace"):
            with self.subTest(target=target), patch.object(dsh.os, target, side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    dsh.save_state("test-site", {"port": 18001})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])
        with patch.object(dsh.yaml, "safe_dump", side_effect=ValueError("serialization")):
            with self.assertRaises(ValueError):
                dsh.save_state("test-site", {})
        self.assertEqual(path.read_bytes(), before)
        inode = path.stat().st_ino
        dsh.save_state("test-site", {"port": 18001, "engine": "dsh"})
        self.assertNotEqual(path.stat().st_ino, inode)
        self.assertEqual(dsh.load_state("test-site")["port"], 18001)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
