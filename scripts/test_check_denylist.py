import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_denylist", Path(__file__).with_name("check-denylist.py"))
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)


class DenylistTest(unittest.TestCase):
    def setUp(self):
        self.hashes = {cd.digest("acme-secret.test"), cd.digest("mallory")}

    def scan(self, text, rel="x.md"):
        return cd.scan_text(rel, text, self.hashes)

    def test_clean(self):
        self.assertEqual(self.scan("site at app.example.com for alice"), [])

    def test_domain_and_subdomain(self):
        self.assertTrue(self.scan("see https://acme-secret.test/x"))
        self.assertTrue(self.scan("see https://platform.acme-secret.test/x"))

    def test_case_insensitive_and_pieces(self):
        self.assertTrue(self.scan("owner: Mallory"))
        self.assertTrue(self.scan("user mallory-bot pushed"))
        self.assertTrue(self.scan("mail mallory@example.com"))

    def test_report_hides_word(self):
        out = self.scan("owner: mallory")
        self.assertNotIn("mallory", "".join(out))

    def test_generic_patterns(self):
        self.assertTrue(self.scan("cd /home/bob/work"))
        self.assertTrue(self.scan("-----BEGIN OPENSSH PRIVATE KEY-----"))
        self.assertTrue(self.scan("channel 123456789012345678"))
        self.assertEqual(self.scan("version 1.2.3 port 8080 /home/ alone"), [])
        self.assertEqual(self.scan("-v data:/home/dsh/workspace"), [])

    def test_public_allow_is_exact(self):
        allow = ["https://github.com/mallory/public-plugin"]
        ok = cd.scan_text("x.md", "see https://github.com/mallory/public-plugin", self.hashes, allow)
        self.assertEqual(ok, [])
        bad = cd.scan_text("x.md", "see https://github.com/mallory/private-repo", self.hashes, allow)
        self.assertTrue(bad)

    def test_generic_allowlist(self):
        self.assertEqual(self.scan("/home/bob/", rel="scripts/check-denylist.py"), [])


if __name__ == "__main__":
    unittest.main()
