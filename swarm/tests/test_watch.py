"""#400 cron 觀察、通知 Discord。契約第 7 節：只讀取、只通知，不啟動任何東西。

scan(gh, state, now, send) -> state
  state：可存成 JSON 的 dict（cron 每次讀進、寫回）
  send(text)：發一則 Discord 訊息
"""
import json
import unittest
from datetime import datetime, timedelta, timezone

import _path  # noqa: F401
from fake_github import world

from epiclib import watch

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


class Watch(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def scan(self, gh, state, t):
        return watch.scan(gh, state, t, self.sent.append)

    def test_quiet_world_sends_nothing(self):
        gh = world()
        self.scan(gh, {}, T0)
        self.assertEqual(self.sent, [])

    def test_review_notifies(self):
        gh = world(i1={"labels": ["epic", "epic:review"]})
        self.scan(gh, {}, T0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("#1", self.sent[0])
        self.assertIn("epic:review", self.sent[0])

    def test_blocking_stuck_notifies_with_what_it_blocks(self):
        gh = world(i2={"labels": ["by:swarm", "swarm:stuck"]})
        self.scan(gh, {}, T0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("#2", self.sent[0])
        self.assertIn("#3", self.sent[0])

    def test_non_blocking_stuck_is_silent(self):
        gh = world(i4={"labels": ["by:swarm", "swarm:stuck"]})
        self.scan(gh, {}, T0)
        self.assertEqual(self.sent, [])

    def test_stuck_blocking_only_closed_is_silent(self):
        gh = world(i2={"labels": ["by:swarm", "swarm:stuck"]}, i3={"state": "closed"})
        self.scan(gh, {}, T0)
        self.assertEqual(self.sent, [])

    def test_then_daily(self):
        gh = world(i1={"labels": ["epic", "epic:review"]})
        st = self.scan(gh, {}, T0)
        st = self.scan(gh, json.loads(json.dumps(st)), T0 + timedelta(hours=1))
        st = self.scan(gh, st, T0 + timedelta(hours=23))
        self.assertEqual(len(self.sent), 1)
        st = self.scan(gh, st, T0 + timedelta(hours=24, minutes=1))
        self.assertEqual(len(self.sent), 2)
        self.scan(gh, st, T0 + timedelta(hours=25))
        self.assertEqual(len(self.sent), 2)

    def test_resolved_then_again_notifies_immediately(self):
        gh = world(i1={"labels": ["epic", "epic:review"]})
        st = self.scan(gh, {}, T0)
        gh.issues[1]["labels"] = ["epic"]
        st = self.scan(gh, st, T0 + timedelta(hours=1))
        gh.issues[1]["labels"] = ["epic", "epic:review"]
        self.scan(gh, st, T0 + timedelta(hours=2))
        self.assertEqual(len(self.sent), 2)

    def test_closed_epic_ignored(self):
        gh = world(i1={"labels": ["epic", "epic:review"], "state": "closed"})
        self.scan(gh, {}, T0)
        self.assertEqual(self.sent, [])

    def test_read_only(self):
        gh = world(i1={"labels": ["epic", "epic:review"]}, i2={"labels": ["by:swarm", "swarm:stuck"]})
        self.scan(gh, {}, T0)
        self.assertEqual(gh.writes(), [])
        self.assertEqual(len(self.sent), 2)

    def test_send_failure_retries_next_time(self):
        gh = world(i1={"labels": ["epic", "epic:review"]})

        def boom(text):
            raise RuntimeError("discord down")
        st = watch.scan(gh, {}, T0, boom)
        self.scan(gh, st, T0 + timedelta(minutes=10))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
