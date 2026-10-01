"""safe_fs never follows a symlink planted inside a customer-writable root."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

import safe_fs


class SafeFsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'root'
        (self.root / 'a').mkdir(parents=True)
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        (self.outside / 'secret').write_text('platform secret')

    def test_read_write_roundtrip_and_modes(self):
        safe_fs.write_atomic(self.root, 'a/f.txt', 'hi', 0o600)
        self.assertEqual(safe_fs.read_text(self.root, 'a/f.txt'), 'hi')
        self.assertEqual(stat.S_IMODE((self.root / 'a/f.txt').stat().st_mode), 0o600)
        safe_fs.write_atomic(self.root, 'a/f.txt', 'again', 0o644, keep_mode=True)
        self.assertEqual(stat.S_IMODE((self.root / 'a/f.txt').stat().st_mode), 0o600)
        safe_fs.create_new(self.root, 'b/c/new.txt', 'x')
        with self.assertRaises(FileExistsError):
            safe_fs.create_new(self.root, 'b/c/new.txt', 'y')
        self.assertEqual(sorted(safe_fs.list_dir(self.root, 'b/c')), ['new.txt'])
        self.assertEqual([p for p in os.listdir(self.root / 'a')], ['f.txt'])  # no temp left

    def test_symlinked_file_is_neither_read_nor_overwritten(self):
        (self.root / 'a' / 'link').symlink_to(self.outside / 'secret')
        with self.assertRaises(safe_fs.UnsafePath):
            safe_fs.read_text(self.root, 'a/link')
        with self.assertRaises(safe_fs.UnsafePath):
            safe_fs.write_atomic(self.root, 'a/link', 'pwned')
        self.assertEqual((self.outside / 'secret').read_text(), 'platform secret')
        self.assertEqual(safe_fs.kind(self.root, 'a/link'), 'link')

    def test_symlinked_directory_is_never_entered(self):
        (self.root / 'dir').symlink_to(self.outside)
        for call in (lambda: safe_fs.read_text(self.root, 'dir/secret'),
                     lambda: safe_fs.write_atomic(self.root, 'dir/new', 'x'),
                     lambda: safe_fs.create_new(self.root, 'dir/new', 'x'),
                     lambda: safe_fs.list_dir(self.root, 'dir'),
                     lambda: safe_fs.unlink(self.root, 'dir/secret')):
            with self.assertRaises(OSError):
                call()
        self.assertIsNone(safe_fs.kind(self.root, 'dir/secret'))
        self.assertEqual(sorted(os.listdir(self.outside)), ['secret'])
        self.assertEqual((self.outside / 'secret').read_text(), 'platform secret')

    def test_rejects_escaping_paths_and_specials(self):
        for rel in ('../outside/secret', '/etc/passwd', 'a/../../outside/secret', ''):
            with self.assertRaises(OSError):
                safe_fs.read_text(self.root, rel)
        os.mkfifo(self.root / 'a' / 'fifo')
        with self.assertRaises(safe_fs.UnsafePath):
            safe_fs.read_text(self.root, 'a/fifo')   # must not block on a FIFO
        (self.root / 'big').write_bytes(b'x' * 11)
        with self.assertRaises(safe_fs.UnsafePath):
            safe_fs.read_bytes(self.root, 'big', limit=10)


if __name__ == '__main__':
    unittest.main()
