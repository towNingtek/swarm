"""Isolated site network: fail closed, reversible move, per-site entry key.

No real Docker or network: `run` is faked and records every command.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dsh_sitectl as dsh
import test_dsh_site_password as password_fixture

ENV = password_fixture.ENV

MASTER = "m" * 48
GOOD = {"allow": {"172.17.0.1:8212": True},
        "block": {f"{h}:{p}": False for h, p in dsh.ISOLATION_MUST_BLOCK},
        "dns": True, "internet": True}
NETWORK = [{"IPAM": {"Config": [{"Subnet": dsh.SITE_SUBNET, "Gateway": dsh.SITE_GATEWAY}]},
            "Options": {"com.docker.network.bridge.enable_icc": "false",
                        "com.docker.network.bridge.name": dsh.SITE_BRIDGE}}]


def fake_docker(probe, network=NETWORK, log=None):
    def run(command):
        if log is not None:
            log.append(command)
        if command[:3] == ["docker", "network", "inspect"]:
            return json.dumps(network)
        if command[:2] == ["docker", "run"] and "--rm" in command:
            return "noise\n" + json.dumps(probe)
        return ""
    return run


class IsolationProbeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(dsh, "SITE_NETWORK", "swarm-sites")
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, probe, network=NETWORK):
        with patch.object(dsh, "run", side_effect=fake_docker(probe, network)):
            return dsh.verify_site_isolation("img")

    def test_passes_only_when_every_sampled_target_is_blocked(self):
        self.assertEqual(self.check(GOOD), GOOD)
        for target in GOOD["block"]:
            leak = json.loads(json.dumps(GOOD))
            leak["block"][target] = True
            with self.subTest(target=target), self.assertRaisesRegex(dsh.SiteError, "not isolated"):
                self.check(leak)

    def test_relay_and_internet_are_required(self):
        for key, value in (("allow", {"172.17.0.1:8212": False}), ("allow", {}),
                           ("internet", False), ("dns", False)):
            bad = dict(GOOD, **{key: value})
            with self.subTest(key=key), self.assertRaises(dsh.SiteError):
                self.check(bad)

    def test_garbage_or_foreign_network_refused(self):
        with patch.object(dsh, "run", side_effect=lambda c: "[]" if "inspect" in c else "not json"):
            with self.assertRaises(dsh.SiteError):
                dsh.verify_site_isolation("img")
        icc_on = json.loads(json.dumps(NETWORK))
        icc_on[0]["Options"]["com.docker.network.bridge.enable_icc"] = "true"
        with self.assertRaisesRegex(dsh.SiteError, "other settings"):
            self.check(GOOD, icc_on)

    def test_probe_runs_unprivileged_on_the_site_network(self):
        log = []
        with patch.object(dsh, "run", side_effect=fake_docker(GOOD, log=log)):
            dsh.verify_site_isolation("img")
        probe = [c for c in log if c[:2] == ["docker", "run"]][0]
        for flag in (["--network", "swarm-sites"], ["--cap-drop", "ALL"], ["--dns", "1.1.1.1"]):
            self.assertIn(flag, [probe[i:i + 2] for i in range(len(probe) - 1)])


class DockerRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.stack = patch.object(dsh.common, "SITES_ROOT", Path(tmp.name))
        self.stack.start()
        self.addCleanup(self.stack.stop)
        self.env = dsh.init_dsh_home("ab") / "dsh.env"

    def start(self, network, peers, probe=GOOD):
        self.env.write_text(f"WEB_AUTH_PASSWORD_HASH=scrypt$x\nRELAY_TRUSTED_PEERS={peers}\n")
        log = []
        with patch.object(dsh, "SITE_NETWORK", network), \
                patch.object(dsh, "run", side_effect=fake_docker(probe, log=log)):
            dsh.docker_run("ab", 18003, self.env, "2", "4g", image="img")
        return [c for c in log if c[:3] == ["docker", "run", "-d"]]

    def test_unrestricted_only_on_the_isolated_network(self):
        (site,) = self.start("swarm-sites", dsh.SITE_GATEWAY)
        self.assertIn("DSH_PERMISSION_MODE=danger-full-access", site)
        self.assertEqual(site[site.index("--network") + 1], "swarm-sites")
        self.assertIn("1.1.1.1", site)
        for hardening in ("no-new-privileges", "ALL", "127.0.0.1:18003:3080"):
            self.assertIn(hardening, site)
        (legacy,) = self.start("", "172.17.0.1")
        self.assertNotIn("--network", legacy)
        self.assertFalse(any("DSH_PERMISSION_MODE" in arg for arg in legacy))

    def test_leaky_network_or_wrong_peer_starts_nothing(self):
        leak = json.loads(json.dumps(GOOD))
        leak["block"]["169.254.169.254:80"] = True
        with self.assertRaises(dsh.SiteError):
            self.start("swarm-sites", dsh.SITE_GATEWAY, leak)
        with self.assertRaisesRegex(dsh.SiteError, "gateway"):
            self.start("swarm-sites", "172.17.0.1")


class IsolateSiteTests(unittest.TestCase):
    """Reuses the password-swap fixture (fake docker, temp site), not its tests."""

    def setUp(self):
        password_fixture.SitePasswordSwapTests.setUp(self)
        self.env.write_text(ENV + "DSH_TRUSTED_HOST=ab.example.com\nRELAY_TRUSTED_PEERS=172.17.0.1\n")
        for p in (patch.object(dsh, "SITE_NETWORK", "swarm-sites"),
                  patch.dict(dsh.os.environ, {"SWARM_ENTRY_SECRET": MASTER})):
            p.start()
            self.addCleanup(p.stop)

    def test_moves_with_gateway_peer_and_own_key_keeping_everything_else(self):
        seen = []
        dsh.isolate_site("ab", self_test=lambda port, pw, **k: seen.append((port, pw, k)))
        text = self.env.read_text()
        key = dsh.site_entry_secret(MASTER, "ab.example.com")
        self.assertIn(f"RELAY_TRUSTED_PEERS={dsh.SITE_GATEWAY}\n", text)
        self.assertIn(f"WEB_AUTH_ENTRY_SECRET={key}\n", text)
        self.assertNotIn("keep-me", text)
        self.assertNotIn(MASTER, text)
        self.assertIn("WEB_AUTH_PASSWORD_HASH=scrypt$old\n", text)   # password untouched
        self.assertEqual(text.count("RELAY_TRUSTED_PEERS="), 1)
        self.assertTrue(self.sessions.exists(), "logins survive a network move")
        self.assertEqual(seen, [(18003, None, {"base_url": "https://ab.example.com", "entry_key": key})])
        self.assertEqual(self.started[0][1], {"image": "sha256:" + "a" * 64})

    def test_host_comes_from_site_state_not_the_site_writable_env(self):
        # Code in site ab rewrote its own dsh.env to name another site.
        self.env.write_text(ENV + "DSH_TRUSTED_HOST=cd.example.com\n"
                            "RELAY_PUBLIC_ORIGIN=https://cd.example.com\nRELAY_TRUSTED_PEERS=172.17.0.1\n")
        dsh.isolate_site("ab", self_test=lambda *a, **k: None)
        text = self.env.read_text()
        self.assertIn("WEB_AUTH_ENTRY_SECRET=" + dsh.site_entry_secret(MASTER, "ab.example.com") + "\n", text)
        self.assertNotIn(dsh.site_entry_secret(MASTER, "cd.example.com"), text)
        self.assertNotIn(dsh.site_entry_secret(MASTER, "cd.example.com"), repr(self.commands))
        self.assertIn("DSH_TRUSTED_HOST=ab.example.com\n", text)
        self.assertIn("RELAY_PUBLIC_ORIGIN=https://ab.example.com\n", text)

    def test_an_indented_duplicate_identity_line_is_refused(self):
        self.env.write_text(ENV + "DSH_TRUSTED_HOST=cd.example.com\n  DSH_TRUSTED_HOST=ab.example.com\n"
                            "RELAY_TRUSTED_PEERS=172.17.0.1\n")
        with self.assertRaisesRegex(dsh.SiteError, "more than one"):
            dsh.isolate_site("ab", self_test=lambda *a, **k: None)
        self.assertNotIn(["docker", "stop", "site-ab"], self.commands)

    def test_failed_self_test_restores_old_env_and_container(self):
        before = self.env.read_bytes()

        def failing(*_a, **_k):
            raise dsh.SiteError("entry failed")
        with self.assertRaisesRegex(dsh.SiteError, "restored on its old network"):
            dsh.isolate_site("ab", self_test=failing)
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual(self.commands[-1], ["docker", "start", "site-ab"])

    def test_requires_network_and_master(self):
        with patch.object(dsh, "SITE_NETWORK", ""), self.assertRaises(dsh.SiteError):
            dsh.isolate_site("ab", self_test=lambda *a, **k: None)
        with patch.dict(dsh.os.environ, {"SWARM_ENTRY_SECRET": ""}), self.assertRaises(dsh.SiteError):
            dsh.isolate_site("ab", self_test=lambda *a, **k: None)
        self.assertNotIn(["docker", "stop", "site-ab"], self.commands)


class EntryTicketLoginTests(unittest.TestCase):
    def test_ticket_matches_the_site_plugin_format(self):
        import base64, hashlib, hmac, time
        key = "k" * 64
        expiry, nonce, sig = dsh._entry_ticket(key).split(".")
        self.assertTrue(0 < int(expiry) - time.time() * 1000 <= 60_000)
        mac = hmac.new(key.encode(), f"{expiry}.{nonce}".encode(), hashlib.sha256).digest()
        self.assertEqual(sig, base64.urlsafe_b64encode(mac).decode().rstrip("="))

    def test_self_test_needs_a_password_or_a_key(self):
        with self.assertRaisesRegex(dsh.SiteError, "entry key"):
            dsh._dsh_self_test(18003, None, entry_key=None, transport_factory=object)


if __name__ == "__main__":
    unittest.main()
