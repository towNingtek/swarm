"""#397 agent 執行器（DSH ACP，用啟動時選的模型）。契約第 3 節第 5 條、第 4、5 節。"""
import json
import os
import unittest

import _path  # noqa: F401
from fake_acp import FakeAcp
from fake_github import criteria_body, world

from epiclib import agent

MODEL = ["claude", "claude-opus-5-5"]


class Prompt(unittest.TestCase):
    def setUp(self):
        gh = world(i2={"body": criteria_body("把 A 做出來", ["`test -f A`"])})
        self.epic, self.issue = gh.issue(1), gh.issue(2)
        self.down = [gh.issue(3)]
        self.p = agent.build_prompt(self.epic, self.issue, ["test -f A"], self.down)

    def test_contains_issue_body(self):
        self.assertIn("把 A 做出來", self.p)

    def test_contains_frozen_commands(self):
        self.assertIn("test -f A", self.p)

    def test_contains_epic_goal(self):
        self.assertIn("整體目標：讓測試世界運作", self.p)

    def test_contains_downstream(self):
        self.assertIn("#3", self.p)
        self.assertIn("第二步", self.p)

    def test_states_rules(self):
        for must in ("不要關閉", "不要修改判準", "判準有誤："):
            self.assertIn(must, self.p)

    def test_previous_failure_included(self):
        p = agent.build_prompt(self.epic, self.issue, ["test -f A"], self.down, prev="上一次的失敗輸出XYZ")
        self.assertIn("上一次的失敗輸出XYZ", p)


class Run(unittest.TestCase):
    def test_model_is_required(self):
        acp = FakeAcp()
        for bad in (None, [], ""):
            with self.assertRaises(ValueError):
                agent.run(acp, bad, "/tmp", "做事", timeout=5)
        self.assertEqual(acp.calls, [])

    def test_sets_model_as_json_string(self):
        acp = FakeAcp()
        agent.run(acp, MODEL, "/tmp", "做事", timeout=5)
        (p,) = acp.params("session/set_config_option")
        self.assertEqual(p["configId"], "model")
        self.assertIsInstance(p["value"], str)
        self.assertEqual(json.loads(p["value"]), MODEL)

    def test_order_and_close(self):
        acp = FakeAcp()
        agent.run(acp, MODEL, "/work", "做事", timeout=5)
        m = acp.methods()
        self.assertEqual(m[0], "session/new")
        self.assertEqual(acp.params("session/new")[0]["cwd"], "/work")
        self.assertLess(m.index("session/set_config_option"), m.index("session/prompt"))
        self.assertEqual(m[-1], "session/close")

    def test_returns_message_text_only(self):
        acp = FakeAcp(reply="這是回覆")
        r = agent.run(acp, MODEL, "/tmp", "做事", timeout=5)
        self.assertEqual(r.text, "這是回覆")
        self.assertNotIn("想法", r.text)
        self.assertFalse(r.timed_out)

    def test_timeout_cancels(self):
        acp = FakeAcp(mode="hang")
        r = agent.run(acp, MODEL, "/tmp", "做事", timeout=1)
        self.assertTrue(r.timed_out)
        self.assertIn("session/cancel", acp.methods())
        self.assertEqual(acp.methods()[-1], "session/close")

    def test_criteria_wrong_marker(self):
        r = agent.run(FakeAcp(reply="看過了\n判準有誤：第 1 列檔名不對"), MODEL, "/tmp", "做事", timeout=5)
        self.assertEqual(r.criteria_wrong, "第 1 列檔名不對")
        self.assertIsNone(agent.run(FakeAcp(), MODEL, "/tmp", "做事", timeout=5).criteria_wrong)


class NoCloseRights(unittest.TestCase):
    """agent 沒有關 issue 的權限：它的行程拿不到 GitHub 憑證。"""

    def test_env_strips_github_credentials(self):
        base = {"PATH": "/usr/bin", "HOME": "/home/x", "GH_TOKEN": "a", "GITHUB_TOKEN": "b",
                "GH_ENTERPRISE_TOKEN": "c", "NODE_OPTIONS": "--x", "GH_CONFIG_DIR": "/tmp/x/.config/gh"}
        env = agent.agent_env(base, "/tmp/epic-dsh-home")
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "NODE_OPTIONS"):
            self.assertNotIn(k, env)
        self.assertNotEqual(env["GH_CONFIG_DIR"], "/tmp/x/.config/gh")
        self.assertTrue(os.path.isdir(env["GH_CONFIG_DIR"]))
        self.assertEqual(os.listdir(env["GH_CONFIG_DIR"]), [])
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["DSH_HOME"], "/tmp/epic-dsh-home")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_permission_requests_are_rejected(self):
        opts = [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]
        self.assertEqual(agent.permission_response({"options": opts}),
                         {"outcome": {"outcome": "selected", "optionId": "r"}})
        self.assertEqual(agent.permission_response({"options": [{"optionId": "a", "kind": "allow_once"}]}),
                         {"outcome": {"outcome": "cancelled"}})


if __name__ == "__main__":
    unittest.main()
