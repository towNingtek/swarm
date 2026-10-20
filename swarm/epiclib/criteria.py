"""判準解析與凍結（契約第 3 節）。

格式沿用 docs/issue-acceptance.md：`## 驗收判準` 底下的「### ✅ 修好了」表。
每一列的「怎麼驗」欄若以 code span 開頭，就是一條指令；否則是要人判斷的列。
只看 exit code，「預期」欄 runner 不讀。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

SECTION = "驗收判準"
FIXED = "✅"


@dataclass
class Criteria:
    present: bool = False
    commands: list[str] = field(default_factory=list)
    non_commands: list[str] = field(default_factory=list)

    @property
    def all_commands(self) -> bool:
        return self.present and not self.non_commands


def _section(body: str) -> str | None:
    lines = (body or "").splitlines()
    out, inside = [], False
    for ln in lines:
        if re.match(r"^##\s", ln):
            if inside:
                break
            inside = SECTION in ln
            continue
        if inside:
            out.append(ln)
    return "\n".join(out) if inside or out else None


def _fixed_table(sec: str) -> list[str]:
    rows, inside = [], False
    for ln in sec.splitlines():
        if re.match(r"^###\s", ln):
            if inside:
                break
            inside = FIXED in ln
            continue
        if inside and ln.strip().startswith("|"):
            rows.append(ln.strip())
    return rows


def _cells(row: str) -> list[str]:
    """以未跳脫的 | 切欄；code span 裡的 | 也要寫成 \\|（GFM 規則）。"""
    parts, cur, i = [], "", 0
    while i < len(row):
        ch = row[i]
        if ch == "\\" and i + 1 < len(row) and row[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if ch == "|":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur)
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def _code_span(cell: str) -> str | None:
    m = re.match(r"^(`+)", cell)
    if not m:
        return None
    ticks = m.group(1)
    rest = cell[len(ticks):]
    end = re.search(r"(?<!`)" + ticks + r"(?!`)", rest)
    if not end:
        return None
    code = rest[: end.start()]
    if len(code) >= 2 and code[0] == " " and code[-1] == " " and code.strip():
        code = code[1:-1]
    return code


def parse(body: str) -> Criteria:
    sec = _section(body)
    if sec is None:
        return Criteria()
    c = Criteria()
    for row in _fixed_table(sec):
        cells = _cells(row)
        if len(cells) < 2:
            continue
        if set("".join(cells)) <= set("-: "):
            continue                      # 分隔列
        how = cells[1]
        if how == "怎麼驗":
            continue                      # 表頭
        cmd = _code_span(how)
        if cmd is not None and cmd.strip():
            c.commands.append(cmd)
        else:
            c.non_commands.append(how)
    c.present = bool(c.commands or c.non_commands)
    return c


def check(issue: dict) -> tuple[bool, str]:
    """這張的判準能不能交給 swarm。"""
    if "needs-criteria" in issue.get("labels", []):
        return False, "標了 needs-criteria：判準還沒寫好"
    c = parse(issue.get("body", ""))
    if not c.present:
        return False, "沒有 `## 驗收判準`（或「✅ 修好了」表是空的）"
    if not c.all_commands:
        return False, "判準裡有不是指令的列（%s），這張應改標 by:human" % "；".join(c.non_commands)[:200]
    return True, ""


def _t(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def frozen(issue: dict, go_at: str | None) -> tuple[bool, str]:
    """判準是否在放行（EPIC 加上 swarm:go）之後就沒被動過。"""
    go = _t(go_at)
    if go is None:
        return False, "EPIC 沒有 swarm:go：還沒放行，判準沒有凍結"
    created = _t(issue.get("created_at"))
    if created and created > go:
        return False, "放行後才建立的 sub-issue：要重新放行（拿掉再加回 swarm:go）"
    edited = _t(issue.get("last_edited_at"))
    if edited and edited > go:
        return False, "放行後內文被改過：要重新放行（拿掉再加回 swarm:go）"
    return True, ""


def snapshot(issue: dict) -> dict:
    return {"number": issue.get("number"), "commands": parse(issue.get("body", "")).commands}


def same(issue: dict, snap: dict) -> bool:
    return parse(issue.get("body", "")).commands == snap.get("commands")


def go_commit(repo_dir: str, go_at: str, ref: str = "HEAD") -> str:
    """放行當下 ref 的 commit（committer date ≤ go_at 的最新一個）。"""
    out = subprocess.run(["git", "-C", repo_dir, "rev-list", "-1", "--before=" + go_at, ref],
                         check=True, capture_output=True, text=True).stdout.strip()
    if not out:
        raise ValueError(f"{ref} 在 {go_at} 之前沒有 commit")
    return out
