"""蜂的 adapter：用各家官方 CLI 當「蜂」（#416）。

共同介面（orchestrator 只用這三個）：
  start(workdir, prompt) -> Turn    開一個新 session 做事
  send(text)             -> Turn    在同一個 session 接著說（推一把用）
  stop()                            結束（收掉程序）

每次呼叫都是一個獨立的 CLI 程序，用 session id 接續同一段對話：
- claude：`claude -p <prompt> --output-format json` 取得 session_id；之後 `--resume <id>`。
  登入用站台主人自己的 claude.ai 登入（官方 CLI；不經手 token）。
- codex：`codex exec --json <prompt>` 取得 thread id；之後 `codex exec resume <id> <text>`。
- opencode：`opencode run --format json <prompt>`；之後 `--session <id>`。

指令在 workdir（蜂自己的 worktree）執行；逾時就殺掉程序並回傳 timed_out。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess

from .swarm import Turn


def _run(cmd, cwd, timeout, stdin=None):
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         start_new_session=True, text=True)
    try:
        out, err = p.communicate(input=stdin, timeout=timeout)
        return p.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return 124, out or "", err or "", True


class _CliBee:
    def __init__(self, model: str, timeout: int = 1800):
        self.model, self.timeout = model, timeout
        self.usage = {}
        self.session = None
        self.workdir = None

    def start(self, workdir: str, prompt: str) -> Turn:
        self.workdir = workdir
        return self._call(prompt, first=True)

    def send(self, text: str) -> Turn:
        return self._call(text, first=False)

    def stop(self):
        pass


class ClaudeBee(_CliBee):
    """官方 Claude Code CLI，無人值守（--dangerously-skip-permissions；只在蜂自己的 worktree）。"""

    def _call(self, text, first):
        cmd = ["claude", "-p", "--output-format", "json", "--dangerously-skip-permissions"]
        if self.model:
            cmd += ["--model", self.model]
        if not first and self.session:
            cmd += ["--resume", self.session]
        code, out, err, to = _run(cmd + [text], self.workdir, self.timeout)
        try:
            d = json.loads(out.strip().splitlines()[-1])
            self.session = d.get("session_id") or self.session
            u = d.get("usage") or {}
            usage = {"input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                     + (u.get("cache_creation_input_tokens") or 0),
                     "output_tokens": u.get("output_tokens") or 0,
                     "cost_usd": d.get("total_cost_usd") or 0}
            return Turn(text=str(d.get("result") or ""), timed_out=to, usage=usage)
        except (ValueError, IndexError):
            return Turn(text=(out or err)[-4000:], timed_out=to)


class CodexBee(_CliBee):
    """官方 Codex CLI（codex exec），寫入限在 worktree（--sandbox workspace-write）。"""

    def _call(self, text, first):
        base = ["codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "workspace-write"]
        if self.model:
            base += ["--model", self.model]
        cmd = base + (["resume", self.session, text] if (not first and self.session) else [text])
        code, out, err, to = _run(cmd, self.workdir, self.timeout)
        last = ""
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            tid = ev.get("thread_id") or (ev.get("thread") or {}).get("id")
            if tid:
                self.session = tid
            if ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
                cu = ev["usage"]
                self.usage = {"input_tokens": cu.get("input_tokens") or 0, "output_tokens": cu.get("output_tokens") or 0}
            item = ev.get("item") or {}
            if item.get("type") in ("agent_message", "assistant_message") and item.get("text"):
                last = item["text"]
        return Turn(text=last or (out or err)[-4000:], timed_out=to, usage=getattr(self, "usage", {}) or {})


class OpencodeBee(_CliBee):
    """opencode（免費模型／NVIDIA 等 API key）。"""

    def _call(self, text, first):
        cmd = ["opencode", "run", "--format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        if not first and self.session:
            cmd += ["--session", self.session]
        code, out, err, to = _run(cmd + [text], self.workdir, self.timeout)
        texts = []
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            sid = ev.get("sessionID") or (ev.get("part") or {}).get("sessionID")
            if sid:
                self.session = sid
            part = ev.get("part") or {}
            if part.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
        return Turn(text=(texts[-1] if texts else (out or err)[-4000:]), timed_out=to)


ENGINES = {"claude": ClaudeBee, "codex": CodexBee, "opencode": OpencodeBee}


def factory(engine: str, model: str, timeout: int):
    cls = ENGINES[engine]
    return lambda issue_n: cls(model, timeout)
