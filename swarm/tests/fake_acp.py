"""假的 DSH ACP：在 JSON-RPC 層面模擬，所以協定細節（model 值是 JSON 字串、逾時送 session/cancel）都測得到。

真實版是 `epiclib.agent.DshAcp`，兩者提供同一組方法：
  rpc(method, params, timeout) -> result dict（逾時 raise TimeoutError）
  notify(method, params)
  subscribe(session_id, fn) / unsubscribe(session_id)
"""
from __future__ import annotations


class FakeAcp:
    def __init__(self, reply: str = "完成", mode: str = "ok"):
        self.reply, self.mode = reply, mode
        self.calls: list[tuple[str, dict]] = []
        self.subs = {}
        self.n = 0

    def rpc(self, method, params, timeout=60):
        self.calls.append((method, params))
        if method == "session/new":
            self.n += 1
            return {"sessionId": f"s{self.n}"}
        if method == "session/prompt":
            if self.mode == "hang":
                raise TimeoutError("session/prompt timeout")
            fn = self.subs.get(params["sessionId"])
            for part in (self.reply[: len(self.reply) // 2], self.reply[len(self.reply) // 2:]):
                if fn:
                    fn({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "（想法不回傳）"}})
                    fn({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": part}})
            return {"stopReason": "end_turn"}
        return {}

    def notify(self, method, params):
        self.calls.append((method, params))

    def subscribe(self, sid, fn):
        self.subs[sid] = fn

    def unsubscribe(self, sid):
        self.subs.pop(sid, None)

    def methods(self):
        return [m for m, _ in self.calls]

    def params(self, method):
        return [p for m, p in self.calls if m == method]
