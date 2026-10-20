"""DSH ACP agent 執行器（契約第 3 節第 5 條、第 4、5 節）。

- 模型由啟動時指定（例如 ["claude","claude-opus-5-5"]），沒指定就拒絕，不猜預設值。
- 一張 sub-issue 一次嘗試＝一個新的 ACP session；只回傳 agent_message_chunk 的文字。
- agent 沒有關 issue 的權限：DSH 行程拿不到任何 GitHub 憑證（agent_env），
  要求權限的請求一律拒絕（permission_response）。判定與關閉由 judge 做。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass

NODE = os.environ.get("EPIC_NODE", "node")
DSH = os.environ.get("EPIC_DSH", "dsh")
OVERLAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsh-epic-overlay.yml")
WRONG = "判準有誤："
_GH_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "NODE_OPTIONS")


@dataclass
class Result:
    text: str
    timed_out: bool = False
    criteria_wrong: str | None = None
    tokens: int = 0


def _wrong(text: str) -> str | None:
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(WRONG):
            return s[len(WRONG):].strip() or "（沒有說明）"
    return None


def build_prompt(epic: dict, issue: dict, commands: list[str], downstream: list[dict], prev: str | None = None) -> str:
    cmds = "\n".join(f"{i}. `{c}`" for i, c in enumerate(commands, 1))
    down = "\n".join(f"- #{d['number']} {d.get('title', '')}" for d in downstream) or "（沒有）"
    s = f"""你是 swarm 的執行者，要完成 GitHub issue #{issue['number']}「{issue.get('title', '')}」。
工作目錄是這個 EPIC 的 git worktree（branch），直接在裡面修改、commit。

# EPIC #{epic['number']}「{epic.get('title', '')}」的目標
{epic.get('body', '')[:6000]}

# 這張 issue 的內文
{issue.get('body', '')[:20000]}

# 驗收判準（已凍結；做完後由 runner 照原文執行，exit code 全部是 0 才算完成）
{cmds}

# 下游（被這張擋住的 issue）：不要動到它們依賴的介面或資料格式
{down}

# 規則
- 你的任務是讓上面每一條判準指令都通過；做法要真的完成 issue 的目標，不能只是讓指令剛好通過。
- 不要關閉 issue，也不要宣告完成；判定由 runner 做。
- 不要修改判準，也不要修改測試目錄（scheduler/tests/epic/ 等判準用到的測試）。
- 如果你認為判準本身有錯，就不要動手，回覆一行「{WRONG}<原因>」然後停止。
- 改完請 `git add` 並 `git commit`（訊息以 `[epic#{epic['number']}] #{issue['number']}` 開頭）；不要 push。
- 最後用幾句話說明你做了什麼。
"""
    if prev:
        s += f"\n# 上一次嘗試沒通過，判準輸出如下（這次要換個做法）\n{prev[-12000:]}\n"
    return s


def agent_env(base: dict, dsh_home: str) -> dict:
    env = {k: v for k, v in base.items() if k not in _GH_ENV}
    empty = tempfile.mkdtemp(prefix="epic-nogh-")
    env.update(GH_CONFIG_DIR=empty, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false",
               GCM_INTERACTIVE="never", DSH_HOME=dsh_home)
    return env


def permission_response(params: dict) -> dict:
    opt = [o for o in params.get("options", []) if str(o.get("kind", "")).startswith("reject")]
    if opt:
        return {"outcome": {"outcome": "selected", "optionId": opt[0]["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


def run(acp, model, cwd: str, prompt: str, timeout: int = 1800) -> Result:
    if not model or not isinstance(model, (list, tuple)) or len(model) != 2:
        raise ValueError('要在啟動時指定模型，例如 ["claude","claude-opus-5-5"]；不猜預設值')
    sid = acp.rpc("session/new", {"cwd": cwd, "mcpServers": []}, 120)["sessionId"]
    out, used = [], [0]

    def on(u):
        k = u.get("sessionUpdate")
        if k == "agent_message_chunk" and (u.get("content") or {}).get("type") == "text":
            out.append(u["content"]["text"])
        elif k == "usage_update":
            used[0] = u.get("used") or used[0]
    acp.subscribe(sid, on)
    timed_out = False
    try:
        acp.rpc("session/set_config_option", {"sessionId": sid, "configId": "model",
                                              "value": json.dumps(list(model), separators=(",", ":"))}, 60)
        try:
            acp.rpc("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]}, timeout)
        except TimeoutError:
            timed_out = True
            acp.notify("session/cancel", {"sessionId": sid})
    finally:
        acp.unsubscribe(sid)
        try:
            acp.rpc("session/close", {"sessionId": sid}, 30)
        except Exception:
            pass
    text = "".join(out).strip()
    return Result(text, timed_out, _wrong(text), used[0])


class DshAcp:
    """真實的 DSH ACP 連線（JSON-RPC over stdio）。介面和 tests/epic/fake_acp.FakeAcp 相同。"""

    def __init__(self, dsh_home: str, log_path: str | None = None):
        self.dsh_home = dsh_home
        self.log_path = log_path or os.path.join(dsh_home, "epic-dsh.stderr.log")
        self.p = None
        self.n, self.pend, self.subs = 0, {}, {}
        self.wlock = threading.Lock()

    def start(self):
        os.makedirs(self.dsh_home, exist_ok=True)
        self._ensure_home()
        env = agent_env(dict(os.environ), self.dsh_home)
        self.p = subprocess.Popen([NODE, DSH, "--profile", "acp", "--patch", OVERLAY],
                                  cwd=os.path.dirname(os.path.dirname(os.path.dirname(DSH))), env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(self.log_path, "ab"))
        threading.Thread(target=self._read, daemon=True).start()
        self.rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, 120)
        return self

    def _ensure_home(self):
        """訂閱登入沿用 DSH GUI 的那一份（symlink），不另外登入，也不複製 token。"""
        plug = os.path.join(self.dsh_home, "plugins")
        os.makedirs(plug, exist_ok=True)
        link = os.path.join(plug, "subscriptions")
        src = os.environ.get("EPIC_SUBSCRIPTIONS", "")  # the DSH GUI's subscriptions plugin dir; unset = no link
        if src and not os.path.lexists(link):
            os.symlink(src, link)
        st = os.path.join(self.dsh_home, "settings.yaml")
        if not os.path.exists(st):
            with open(st, "w") as f:
                f.write("permission:\n  defaultPreset: workspace-write\n")

    def _read(self):
        for line in self.p.stdout:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "id" in m and "method" not in m:
                w = self.pend.pop(m["id"], None)
                if w:
                    w["r"] = m
                    w["e"].set()
            elif m.get("method") == "session/update":
                f = self.subs.get(m["params"].get("sessionId"))
                if f:
                    f(m["params"]["update"])
            elif m.get("method") == "session/request_permission":
                self._write({"jsonrpc": "2.0", "id": m["id"], "result": permission_response(m["params"])})
            elif "id" in m:
                self._write({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "not supported"}})
        for w in list(self.pend.values()):
            w["r"] = {"error": {"message": "dsh exited"}}
            w["e"].set()

    def _write(self, obj):
        with self.wlock:
            self.p.stdin.write((json.dumps(obj) + "\n").encode())
            self.p.stdin.flush()

    def rpc(self, method, params, timeout=60):
        with self.wlock:
            self.n += 1
            i = self.n
        w = self.pend[i] = {"e": threading.Event()}
        self._write({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        if not w["e"].wait(timeout):
            self.pend.pop(i, None)
            raise TimeoutError(method + " timeout")
        if "error" in w["r"]:
            raise RuntimeError("%s: %s" % (method, w["r"]["error"].get("message")))
        return w["r"].get("result") or {}

    def notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def subscribe(self, sid, fn):
        self.subs[sid] = fn

    def unsubscribe(self, sid):
        self.subs.pop(sid, None)

    def close(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
