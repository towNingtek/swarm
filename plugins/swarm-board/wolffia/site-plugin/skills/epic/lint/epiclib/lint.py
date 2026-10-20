"""Issue 品質檢查（template 第二道把關）。

一張 sub-issue 交給蜂群前，必須：
- 剛好有 `by:swarm` 或 `by:human` 其中一個 label；
- `by:swarm` 的判準（`## 驗收判準` → 「✅ 修好了」）全部是可執行指令（沿用 criteria.parse，
  不另寫第二套解析）；
- 有「可以修改的範圍」，每行一個路徑或 glob，不能是絕對路徑、不能跳出 repo（`..`）；
- 判準不是明顯的空殼（`true`、`exit 0`、`echo …` 這類一定成功的指令）。

EPIC 檢查：有目標、範圍、整體驗收三段，且帶 `epic` label。

（swarm 的 scheduler/epiclib/lint.py 的離線部分，站台 epic skill 內附；不碰 GitHub。）

結果只用來加／移除 `needs-criteria`；判準本身是否寫得夠嚴（六條規則）仍要人看，
這裡只擋得住「格式不對」與「明顯的空殼」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import criteria

SCOPE_HEADING = "可以修改的範圍"
EPIC_SECTIONS = ("目標", "範圍", "整體驗收")
_BY_LINE = re.compile(r"^\s*由誰做\s*[：:]\s*(\S+)", re.M)
_BY_VALUE = {"swarm": "by:swarm", "人": "by:human", "human": "by:human"}


def strip_comments(body: str) -> str:
    """去掉 HTML 註解（範本裡的說明）。"""
    return re.sub(r"<!--.*?-->", "", body or "", flags=re.S)


def declared_by(body: str) -> str | None:
    """內文「由誰做：swarm／人」對應的 label；沒寫或寫錯回 None。"""
    m = _BY_LINE.search(strip_comments(body))
    return _BY_VALUE.get(m.group(1).strip().lower()) if m else None
# 一定會成功、驗不了任何東西的指令（規則 3：不得同義反覆）。
_TRIVIAL = re.compile(r"^\s*(true|:|exit\s+0|echo\b.*|printf\b.*|sleep\b.*)\s*$")


@dataclass
class Report:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str):
        self.ok = False
        self.problems.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def _section_text(body: str, heading: str) -> str | None:
    """`## <heading>` 或 `### <heading>`（GitHub 表單用 ###，手寫多用 ##）的段落內容，
    到下一個同級或更高的標題為止。"""
    out, inside, level = [], False, 0
    for ln in (body or "").splitlines():
        m = re.match(r"^(#{2,3})\s+(.*)$", ln)
        if m:
            lv = len(m.group(1))
            if inside and lv <= level:
                break
            if not inside and m.group(2).strip().startswith(heading):
                inside, level = True, lv
                continue
            if inside:
                out.append(ln)
            continue
        if inside:
            out.append(ln)
    if not inside and not out:
        return None
    text = "\n".join(out).strip()
    return "" if text in ("", "_No response_") else text


def scope_paths(body: str) -> list[str]:
    text = _section_text(strip_comments(body), SCOPE_HEADING) or ""
    paths = []
    for ln in text.splitlines():
        ln = ln.strip().strip("`").strip()
        if not ln or ln.startswith("#") or ln.startswith("```"):
            continue
        ln = re.sub(r"^[-*]\s+", "", ln)
        paths.append(ln)
    return paths


def lint_sub(issue: dict) -> Report:
    r = Report()
    body = strip_comments(issue.get("body", "") or "")
    labels = set(issue.get("labels", []))
    want = declared_by(body)
    by = (labels & {"by:swarm", "by:human"}) or ({want} if want else set())
    if len(by) != 1:
        r.fail("要寫「由誰做：swarm」或「由誰做：人」（或剛好一個 by:swarm／by:human label）")
    paths = scope_paths(body)
    if not paths:
        # 舊格式（範本之前開的）沒有這欄：不擋，但 orchestrator 就無法抓範圍外修改。
        r.warn("沒有「可以修改的範圍」：orchestrator 無法檢查是否改到範圍外")
    for p in paths:
        if p.startswith("/") or ".." in p.split("/") or p.startswith("~"):
            r.fail(f"範圍不能是絕對路徑或跳出 repo：`{p}`")
    if "by:swarm" in by:
        c = criteria.parse(body)
        if not c.present:
            r.fail("沒有 `## 驗收判準`，或「✅ 修好了」表是空的")
        elif not c.all_commands:
            r.fail("「✅ 修好了」有不是指令的列（要用反引號包住指令）：" + "；".join(c.non_commands)[:200])
        if c.commands and all(_TRIVIAL.match(cmd) for cmd in c.commands):
            r.fail("判準全是一定會成功的指令（例如 `true`、`echo`），驗不了任何東西")
        placeholder = [cmd for cmd in c.commands if cmd.strip() in ("指令", "command")]
        if placeholder:
            r.fail("判準還是範本的佔位文字（`指令`），請換成實際指令")
    return r


def lint_epic(issue: dict) -> Report:
    r = Report()
    if "epic" not in set(issue.get("labels", [])):
        r.fail("EPIC 要有 `epic` label")
    body = strip_comments(issue.get("body", "") or "")
    for sec in EPIC_SECTIONS:
        text = _section_text(body, sec)
        if not text:
            r.fail(f"缺「{sec}」")
    return r


def lint(issue: dict) -> Report:
    return lint_epic(issue) if "epic" in set(issue.get("labels", [])) else lint_sub(issue)
