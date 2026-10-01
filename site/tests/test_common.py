import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common


class DomainTests(unittest.TestCase):
    def test_domain_is_required(self):
        with patch.object(common, "DOMAIN", ""):
            with self.assertRaises(common.SiteError):
                common.site_host("ab")

    def test_site_host(self):
        with patch.object(common, "DOMAIN", "example.com"):
            self.assertEqual(common.site_host("ab"), "ab.example.com")


class DnsTests(unittest.TestCase):
    def test_dns_off_without_token_creates_nothing(self):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": ""}), \
                patch("httpx.post", side_effect=AssertionError("network")) as post:
            self.assertIsNone(common.dns_create("ab"))
            common.dns_delete("some-id")
            post.assert_not_called()

    def test_token_without_zone_fails_closed(self):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ZONE_ID": ""}):
            with self.assertRaises(common.SiteError):
                common.dns_create("ab")

    def test_payload_prefers_tunnel(self):
        with patch.object(common, "DOMAIN", "example.com"), \
                patch.object(common, "TUNNEL_ID", "tid"), patch.object(common, "MACHINE_IP", "192.0.2.9"):
            self.assertEqual(common.dns_payload("ab")["type"], "CNAME")
        with patch.object(common, "DOMAIN", "example.com"), \
                patch.object(common, "TUNNEL_ID", ""), patch.object(common, "MACHINE_IP", "192.0.2.9"):
            payload = common.dns_payload("ab")
            self.assertEqual((payload["type"], payload["content"]), ("A", "192.0.2.9"))
        with patch.object(common, "DOMAIN", "example.com"), \
                patch.object(common, "TUNNEL_ID", ""), patch.object(common, "MACHINE_IP", ""):
            with self.assertRaises(common.SiteError):
                common.dns_payload("ab")


class StateTests(unittest.TestCase):
    def test_allocate_port_skips_recorded_ports(self):
        with tempfile.TemporaryDirectory() as root, patch.object(common, "SITES_ROOT", Path(root)):
            (Path(root) / "a").mkdir()
            (Path(root) / "a" / "site.yaml").write_text(f"port: {common.PORT_MIN}\n")
            self.assertEqual(common.allocate_port(), common.PORT_MIN + 1)

    def test_container_prefix(self):
        with patch.object(common, "CONTAINER_PREFIX", "x-"):
            self.assertEqual(common.container_name("ab"), "x-ab")

    def test_site_conf_renders_host_and_port(self):
        with patch.object(common, "DOMAIN", "example.com"):
            conf = common.render_site_conf("ab", 18001)
        self.assertIn("server_name ab.example.com;", conf)
        self.assertIn("proxy_pass http://127.0.0.1:18001;", conf)
        self.assertNotIn("$webhook_location", conf)


if __name__ == "__main__":
    unittest.main()
