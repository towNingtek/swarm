"""epic_watch CLI: Discord sender without the private reply_discord module; state per repo."""
import os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(os.path.dirname(HERE), "epic_watch.py")


class WatchCli(unittest.TestCase):
    def run_watch(self, *args, env=None):
        e = dict(os.environ); e.pop("SWARM_DISCORD_TOKEN", None); e.pop("SWARM_DISCORD_TOKEN_FILE", None)
        e.update(env or {})
        return subprocess.run([sys.executable, WATCH, *args], capture_output=True, text=True, env=e, timeout=60)

    def test_no_private_module_needed_and_no_token_is_refused(self):
        r = self.run_watch("--repo", "acme/app", "--channel", "1")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("reply_discord", r.stderr)
        self.assertIn("SWARM_DISCORD_TOKEN", r.stderr)

    def test_source_has_no_reply_discord_import(self):
        self.assertNotIn("reply_discord", open(WATCH, encoding="utf-8").read())

    def test_state_file_per_repo(self):
        src = open(WATCH, encoding="utf-8").read()
        self.assertIn('base + "-" + repo.replace("/", "-") + ".json"', src)


if __name__ == "__main__":
    unittest.main()
