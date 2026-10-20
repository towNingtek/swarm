"""#485 gh_budget：額度低於 20% 自動跳過並通知 Discord。

假的 gh：PATH 最前面放一支腳本，回應由環境變數 FAKE_RL 決定（"limit remaining resetAt"，或 "fail"）；
另外記下每次被呼叫的參數到 FAKE_GH_LOG。假的 Discord：注入的 post 函式只記錄不連網。
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _path  # noqa: F401
from fake_github import world

import epic_autostart as auto
import gh_budget

ROOT = os.path.dirname(_path.SCHEDULER)
FETCH = os.path.join(ROOT, "plugins", "swarm-board", "wolffia", "fetch-project.py")
RESET = "2026-10-19T03:00:00Z"

FAKE_GH = r'''#!%s
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
rl = os.environ.get("FAKE_RL", "fail")
if rl == "fail":
    sys.stderr.write("error connecting to api.github.com\n"); sys.exit(1)
q = next(a[6:] for a in sys.argv if a.startswith("query="))
if "rateLimit" not in q:
    sys.stderr.write("fake gh: only rateLimit\n"); sys.exit(1)
limit, remaining, reset = rl.split()
print(json.dumps({"data": {"rateLimit": {"limit": int(limit), "remaining": int(remaining), "resetAt": reset}}}))
''' % sys.executable


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FakeDiscord:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, token, channel, content):
        if self.fail:
            raise OSError("discord down")
        self.sent.append((token, channel, content))


class Base(unittest.TestCase):
    def setUp(self):
        self._ch, gh_budget.CHANNEL = gh_budget.CHANNEL, "4242"
        self.addCleanup(setattr, gh_budget, "CHANNEL", self._ch)
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        bindir = os.path.join(t, "bin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH)
        os.chmod(gh, 0o755)
        self.log = os.path.join(t, "gh.log")
        self.token = os.path.join(t, "discord.token")
        with open(self.token, "w") as f:
            f.write("TOKEN123\n")
        self.state = os.path.join(t, "home", ".local", "share", "swarm-epic", "gh-budget.json")
        self.env = {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""), "FAKE_GH_LOG": self.log,
                    "HOME": os.path.join(t, "home")}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        os.environ.pop(gh_budget.TOKEN_ENV, None)
        self.discord = FakeDiscord()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def rl(self, spec):
        os.environ["FAKE_RL"] = spec

    def check(self, **kw):
        kw.setdefault("post", self.discord)
        kw.setdefault("state_file", self.state)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            b = gh_budget.check(**kw)
        return b, err.getvalue()

    def gh_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(x) for x in f]


class Check(Base):
    def test_half_left_is_fine(self):
        self.rl(f"5000 2500 {RESET}")
        b, _ = self.check(token_file=self.token)
        self.assertFalse(b["low"])
        self.assertAlmostEqual(b["ratio"], 0.5)
        self.assertEqual(self.discord.sent, [])
        self.assertFalse(os.path.exists(self.state))

    def test_uses_ratelimit_query(self):
        self.rl(f"5000 2500 {RESET}")
        self.check()
        (args,) = self.gh_calls()
        self.assertEqual(args[:2], ["api", "graphql"])
        self.assertIn("query={rateLimit{limit remaining resetAt}}", args)

    def test_ten_percent_is_low(self):
        self.rl(f"5000 500 {RESET}")
        b, _ = self.check(token_file=self.token)
        self.assertTrue(b["low"])
        self.assertEqual(b["reset_at"], RESET)

    def test_threshold_is_strict(self):
        self.rl(f"5000 1000 {RESET}")
        self.assertFalse(self.check()[0]["low"])
        self.rl(f"5000 999 {RESET}")
        self.assertTrue(self.check()[0]["low"])

    def test_low_notifies_once_per_reset(self):
        self.rl(f"5000 500 {RESET}")
        for _ in range(3):
            self.check(token_file=self.token, who="epic_autostart")
        self.assertEqual(len(self.discord.sent), 1)
        token, channel, content = self.discord.sent[0]
        self.assertEqual((token, channel), ("TOKEN123", "4242"))
        self.assertIn(RESET, content)
        self.assertIn("500/5000", content)
        self.assertEqual(json.loads(read(self.state))["notified_reset_at"], RESET)
        # 新的一個 resetAt 再發一次
        self.rl("5000 400 2026-10-19T04:00:00Z")
        self.check(token_file=self.token)
        self.assertEqual(len(self.discord.sent), 2)

    def test_default_state_file_under_home(self):
        os.environ[gh_budget.TOKEN_ENV] = self.token
        self.rl(f"5000 500 {RESET}")
        b, _ = self.check(state_file=gh_budget.STATE_FILE)
        self.assertTrue(b["notified"])
        self.assertEqual(json.loads(read(self.state))["notified_reset_at"], RESET)

    def test_token_from_env(self):
        os.environ[gh_budget.TOKEN_ENV] = self.token
        self.rl(f"5000 500 {RESET}")
        b, _ = self.check()
        self.assertTrue(b["notified"])
        self.assertEqual(len(self.discord.sent), 1)

    def test_no_token_prints_only(self):
        self.rl(f"5000 500 {RESET}")
        b, err = self.check()
        self.assertTrue(b["low"])
        self.assertFalse(b["notified"])
        self.assertEqual(self.discord.sent, [])
        self.assertIn("額度偏低", err)
        self.assertIn(RESET, err)

    def test_discord_failure_does_not_raise_and_retries(self):
        self.rl(f"5000 500 {RESET}")
        b, err = self.check(token_file=self.token, post=FakeDiscord(fail=True))
        self.assertTrue(b["low"])
        self.assertFalse(b["notified"])
        self.assertIn("Discord 通知失敗", err)
        self.check(token_file=self.token)
        self.assertEqual(len(self.discord.sent), 1)

    def test_query_failure_is_not_low(self):
        for spec in ("fail", "0 0 x"):
            self.rl(spec)
            b, err = self.check(token_file=self.token)
            self.assertFalse(b["low"])
            self.assertTrue(b["error"])
            self.assertIn("照常執行", err)
        self.assertEqual(self.discord.sent, [])

    def test_missing_gh_is_not_low(self):
        b, _ = self.check(fetch=lambda: gh_budget.fetch_rate(gh=os.path.join(self.tmp.name, "nope")))
        self.assertFalse(b["low"])


class Autostart(Base):
    def run_auto(self, budget):
        launched = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = auto.main(["--repo", "o/r", "--lock-dir", os.path.join(self.tmp.name, "locks"),
                            "--log-dir", os.path.join(self.tmp.name, "auto")],
                           gh=self.gh, launcher=lambda *a: launched.append(a), budget=budget)
        self.assertEqual(rc, 0)
        return out.getvalue(), launched

    def setUp(self):
        super().setUp()
        self.gh = world()

    def budget(self):
        return gh_budget.check(who="epic_autostart", post=self.discord, state_file=self.state)

    def test_half_left_runs(self):
        self.rl(f"5000 2500 {RESET}")
        out, launched = self.run_auto(self.budget)
        self.assertEqual(len(launched), 1)
        self.assertIn("啟動 #1", out)

    def test_ten_percent_skips(self):
        self.rl(f"5000 500 {RESET}")
        calls0 = getattr(self.gh, "gql_calls", 0)
        out, launched = self.run_auto(self.budget)
        self.assertEqual(launched, [])
        self.assertEqual(out.strip(), f"額度偏低，跳過到 {RESET}")
        self.assertEqual(getattr(self.gh, "gql_calls", 0), calls0)

    def test_query_failure_runs(self):
        self.rl("fail")
        out, launched = self.run_auto(self.budget)
        self.assertEqual(len(launched), 1)

    def test_default_budget_is_gh_budget(self):
        self.rl(f"5000 500 {RESET}")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("epiclib.github.GitHub", side_effect=AssertionError("不該查 GitHub")):
            rc = auto.main(["--repo", "o/r", "--lock-dir", os.path.join(self.tmp.name, "locks"),
                            "--log-dir", os.path.join(self.tmp.name, "auto")])
        self.assertEqual(rc, 0)
        self.assertIn(f"額度偏低，跳過到 {RESET}", out.getvalue())


class Fetch(Base):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp.name, "data")
        os.makedirs(self.out)
        self.old = os.path.join(self.out, "p3.js")
        with open(self.old, "w") as f:
            f.write("OLD")

    def run_fetch(self):
        env = dict(os.environ)
        env.pop(gh_budget.TOKEN_ENV, None)
        conf = os.path.join(self.tmp.name, "projects.json")
        with open(conf, "w", encoding="utf-8") as f:
            json.dump({"default": 2, "projects": [2, 3]}, f)
        env["WOLFFIA_PROJECTS"], env["WOLFFIA_ORG"] = conf, "acme"
        return subprocess.run([sys.executable, FETCH, self.out], capture_output=True, text=True, env=env,
                              timeout=60)

    def test_ten_percent_skips_and_keeps_data(self):
        self.rl(f"5000 500 {RESET}")
        r = self.run_fetch()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"額度偏低，跳過到 {RESET}", r.stdout)
        self.assertIn("不發 Discord", r.stderr)          # 沒設 token：只印不發
        self.assertEqual(read(self.old), "OLD")
        self.assertEqual(sorted(os.listdir(self.out)), ["p3.js"])
        self.assertEqual(len(self.gh_calls()), 1)         # 只有 rateLimit 那一次

    def _load(self):
        spec = importlib.util.spec_from_file_location("fetch_project", FETCH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_half_left_fetches(self):
        self.rl(f"5000 2500 {RESET}")
        m = self._load()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(m.budget_low())

    def test_query_failure_fetches(self):
        self.rl("fail")
        m = self._load()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(m.budget_low())
        # 真的跑：rateLimit 失敗也照常往下抓（假 gh 讓抓取失敗 → 舊資料保留、exit 1，與 #485 前相同）
        r = self.run_fetch()
        self.assertNotIn("額度偏低", r.stdout)
        self.assertGreater(len(self.gh_calls()), 2)
        self.assertEqual(read(self.old), "OLD")


if __name__ == "__main__":
    unittest.main()
