"""執行判準、貼結果、關 sub-issue（契約第 3 節第 5–7 條、第 6 節）。

判準由這裡（一般程式）執行，不由 agent 判斷。指令照原文交給 bash，只看 exit code。
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
from dataclasses import dataclass, field

from . import criteria

COMMENT_LIMIT = 60000            # GitHub 上限 65536
SUMMARY_TAIL = 3000

_SECRET_RES = [
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(glpat-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,})"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s'\"]{12,}"),
]


def redact(text: str) -> str:
    for rx in _SECRET_RES:
        if rx.pattern.startswith(r"\b"):
            text = rx.sub("[REDACTED]", text)
        else:
            text = rx.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


@dataclass
class Row:
    cmd: str
    code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class Result:
    ok: bool
    rows: list[Row] = field(default_factory=list)
    summary: str = ""


def run_commands(cmds: list[str], cwd: str, env: dict, timeout: int = 900) -> list[Row]:
    full = dict(os.environ)
    full.update({k: str(v) for k, v in env.items()})
    rows = []
    for cmd in cmds:
        p = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, env=full, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
            rows.append(Row(cmd, p.returncode, out.decode("utf-8", "replace")))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, _ = p.communicate()
            rows.append(Row(cmd, 124, out.decode("utf-8", "replace") + f"\n[timeout：超過 {timeout} 秒，已終止]"))
    return rows


def _tail(s: str, n: int) -> str:
    return s if len(s) <= n else "…（前面省略 %d 字）\n" % (len(s) - n) + s[-n:]


def _fence(s: str) -> str:
    return "````text\n" + s.replace("````", "` ` ` `") + "\n````"


def _comment(rows: list[Row], ok: bool, go: str) -> str:
    head = ["## 判準執行結果（runner）", "",
            f"`$GO = {go}`。指令照原文執行，只看 exit code。", "",
            "| # | 指令 | exit | 結果 |", "|---|------|------|------|"]
    for i, r in enumerate(rows, 1):
        cmd = redact(r.cmd).replace("|", "\\|").replace("`", "ˋ")
        head.append(f"| {i} | `{cmd[:300]}` | exit {r.code} | {'✅' if r.ok else '❌'} |")
    head += ["", "**全部通過，關閉。**" if ok else "**有判準沒通過，不關閉。**", ""]
    text = "\n".join(head)
    budget = max(500, (COMMENT_LIMIT - len(text) - 200 * len(rows)) // max(1, len(rows)))
    for i, r in enumerate(rows, 1):
        text += f"\n<details><summary>#{i} 輸出（exit {r.code}）</summary>\n\n{_fence(_tail(redact(r.output), budget))}\n\n</details>\n"
    return text[:COMMENT_LIMIT]


def _is_epic(issue: dict) -> bool:
    return "epic" in issue.get("labels", []) or (issue.get("type") or "") == "Epic"


def judge(gh, epic: dict, issue: dict, cwd: str, go: str, repo: str,
          commands: list[str] | None = None, env: dict | None = None, timeout: int = 900) -> Result:
    if issue["number"] == epic["number"] or _is_epic(issue):
        raise ValueError("EPIC 只能由人結案，runner 不判定、不關閉 EPIC")
    cmds = criteria.parse(issue.get("body", "")).commands if commands is None else list(commands)
    if not cmds:
        gh.comment(issue["number"], "## 判準執行結果（runner）\n\n沒有可執行的判準指令，不關閉。")
        return Result(False, [], "沒有判準指令")
    e = dict(env or {})
    e.update(GO=go, REPO=repo, ISSUE=str(issue["number"]), EPIC=str(epic["number"]))
    rows = run_commands(cmds, cwd, e, timeout)
    ok = all(r.ok for r in rows)
    gh.comment(issue["number"], _comment(rows, ok, go))
    if ok:
        gh.close(issue["number"])
    bad = [f"判準 #{i}：`{redact(r.cmd)}` → exit {r.code}\n{_tail(redact(r.output), SUMMARY_TAIL)}"
           for i, r in enumerate(rows, 1) if not r.ok]
    return Result(ok, rows, "\n\n".join(bad) if bad else "全部通過")
