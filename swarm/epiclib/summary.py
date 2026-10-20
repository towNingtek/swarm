"""EPIC 結案摘要與每月趨勢報告（#455，EPIC #489）。說明見 docs/epic-summary.md。

資料來源（src）是一個很薄的介面，真的用 `GhSource`（gh CLI），測試用假的：
  issue(n) / subs(n)            epiclib 的 issue dict；另有 closed_at
  list_comments(n)              [{"id", "body", "created_at", "author"}]，依時間排序
  comment(n, body) / edit_comment(id, body)
  prs(n)（可省略）              ["https://…/pull/…"]

數字只從 GitHub 上看得到的東西來：EPIC 留言裡的 `<!-- swarm-run … -->`（舊的執行沒有這段，
就從「蜂群這一輪結束」「這一輪結束（runner）」表格盡量補）、sub-issue 的判準結果留言與 label、
任何留言裡行首的 `criteria-defect：` 標記。找不到的值寫 null，不猜。
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime

import yaml

from .github import GitHub
from .lint import declared_by

MARK = "swarm-epic-summary"
RUN_MARK = "swarm-run"
VERSION = 1
HEAD = "## 結案摘要"
DEFECT = "criteria-defect"
STUCK = "swarm:stuck"

RUN_FIELDS = ("mode", "engine", "seconds", "agents", "peak", "input_tokens", "output_tokens", "cost_usd",
              "nudges", "retries", "conflicts", "passed", "stuck", "pr")
SUM_FIELDS = ("agents", "seconds", "input_tokens", "output_tokens", "cost_usd", "nudges", "retries", "conflicts")
SUB_FIELDS = ("total", "swarm", "human", "passed", "stuck", "human_closed", "first_pass")

_RUN_RE = re.compile(r"<!--\s*" + RUN_MARK + r"\s*\n(.*?)-->", re.S)
_SUMMARY_RE = re.compile(r"<!--\s*" + MARK + r"\s*\n(.*?)-->", re.S)
_JUDGE = "## 判準執行結果（runner）"
_JUDGE_OK = "**全部通過，關閉。**"
_STUCK_RE = re.compile(r"標\s*`?" + re.escape(STUCK))
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_DEFECT_RE = re.compile(r"^[ \t\-*>]*" + re.escape(DEFECT) + r"[ \t]*[:：]", re.M | re.I)


# ── 解析留言 ─────────────────────────────────────────────────────────────────

def _ours(body: str) -> bool:
    """機器留的摘要／執行紀錄：不拿來數 criteria-defect。"""
    return bool(_SUMMARY_RE.search(body or "")) or bool(_RUN_RE.search(body or ""))


def _num(s):
    if s is None:
        return None
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return s
    m = _NUM.search(str(s).replace(",", ""))
    if not m:
        return None
    v = float(m.group(0))
    return int(v) if v.is_integer() and "." not in m.group(0) else v


def _norm_run(d: dict, source: str) -> dict:
    r = {k: d.get(k) for k in RUN_FIELDS}
    for k in RUN_FIELDS:
        if k not in ("mode", "engine", "pr"):
            r[k] = _num(r[k])
    r["pr"] = r["pr"] or None
    r["model"] = d.get("model") or None
    r["at"] = str(d["at"]) if d.get("at") else None
    r["source"] = source
    return r


def _table(body: str) -> dict:
    rows = {}
    for ln in body.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) >= 2 and ln.strip().startswith("|"):
            rows.setdefault(cells[0], cells[1])
    return rows


def _legacy_swarm(body: str, at) -> dict:
    """「## 蜂群這一輪結束（orchestrator）」的表格（#455 之前沒有 swarm-run）。"""
    t = _table(body)
    d = {"mode": t.get("模式") or None, "seconds": t.get("時間（秒）"), "agents": t.get("agent 個數（總數）"),
         "peak": t.get("同時最多（峰值）"), "nudges": t.get("推一把次數"), "retries": t.get("重試次數"),
         "conflicts": t.get("合併衝突"), "at": at}
    em = t.get("引擎／模型")
    if em:
        eng, _, mod = em.partition("/")
        d["engine"], d["model"] = eng.strip() or None, mod.strip() or None
    ps = t.get("判準通過／卡住")
    if ps and "/" in ps:
        d["passed"], d["stuck"] = (x.strip() for x in ps.split("/", 1))
    us = t.get("用量（輸入／輸出 tokens）")
    if us and "/" in us:
        i, o = (_num(x) for x in us.split("/", 1))
        d["input_tokens"], d["output_tokens"] = (i or None), (o or None)   # 0 = CLI 沒回報
    if d.get("input_tokens") or d.get("output_tokens"):
        d["cost_usd"] = _num(t.get("費用估計（USD，CLI 回報）")) or None
    return _norm_run(d, "table")


def _legacy_runner(body: str, at) -> dict:
    """「## 這一輪結束（runner）」：epic_run.py 非蜂群模式；只數得出通過／卡住。"""
    m = re.search(r"模型：`([^`]*)`", body)
    eng, mod = (m.group(1).split("/", 1) + [None])[:2] if m else (None, None)
    rows = [ln for ln in body.splitlines() if ln.startswith("| #")]
    d = {"mode": "runner", "engine": eng, "model": mod, "at": at,
         "passed": sum("通過並關閉" in r for r in rows), "stuck": sum(STUCK in r for r in rows)}
    return _norm_run(d, "table")


def parse_runs(comments: list[dict]) -> list[dict]:
    """EPIC 留言 → 每次執行一筆。有 swarm-run 用它；沒有就從舊的總結表格補。"""
    runs = []
    for c in comments:
        body = c.get("body") or ""
        m = _RUN_RE.search(body)
        if m:
            try:
                d = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                d = {}
            if isinstance(d, dict):
                runs.append(_norm_run(d if d.get("at") else {**d, "at": c.get("created_at")}, RUN_MARK))
                continue
        if body.startswith("## 蜂群這一輪結束"):
            runs.append(_legacy_swarm(body, c.get("created_at")))
        elif body.startswith("## 這一輪結束（runner）"):
            runs.append(_legacy_runner(body, c.get("created_at")))
    return runs


def count_defects(comments: list[dict]) -> int:
    """有一行以 `criteria-defect：`／`criteria-defect:` 開頭的留言計一次（前面可有空白、`-`、`*`、`>`；
    一則留言最多算一次；句中提到不算；機器留的摘要不算）。"""
    return sum(1 for c in comments if _DEFECT_RE.search(c.get("body") or "") and not _ours(c.get("body")))


def by_of(it: dict) -> str | None:
    labels = it.get("labels") or []
    if "by:swarm" in labels:
        return "swarm"
    if "by:human" in labels:
        return "human"
    want = declared_by(it.get("body") or "")
    return {"by:swarm": "swarm", "by:human": "human"}.get(want)


def sub_result(it: dict, comments: list[dict]) -> dict:
    judged = [_JUDGE_OK in (c.get("body") or "") for c in comments if (c.get("body") or "").startswith(_JUDGE)]
    closed = it.get("state") == "closed"
    by = by_of(it)
    # swarm:stuck 只對 swarm 的張有意義（人做的張留言裡談到它不算）
    stuck = by == "swarm" and (STUCK in (it.get("labels") or []) or
                               any(_STUCK_RE.search(c.get("body") or "") for c in comments))
    return {"number": it["number"], "title": it.get("title", ""), "by": by, "state": it.get("state"),
            "passed": closed and any(judged), "first_pass": by == "swarm" and bool(judged) and judged[0],
            "stuck": stuck, "human_closed": closed and not any(judged)}


# ── 組摘要 ───────────────────────────────────────────────────────────────────

def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _days(a, b):
    ta, tb = _ts(a), _ts(b)
    return round((tb - ta).total_seconds() / 86400, 1) if ta and tb else None


def totals(runs: list[dict]) -> dict:
    """加總；有任何一次執行缺這個值就是 null（部分加總會低估，不如說不知道）。"""
    out = {}
    for k in SUM_FIELDS + ("peak",):
        vals = [r.get(k) for r in runs]
        if not vals or any(v is None for v in vals):
            out[k] = None
        elif k == "peak":
            out[k] = max(vals)
        else:
            out[k] = round(sum(vals), 6) if k == "cost_usd" else sum(vals)
    return {k: out[k] for k in ("agents", "peak") + SUM_FIELDS[1:]}


def build(epic: dict, subs: list[dict], epic_comments: list[dict], sub_comments: dict, prs=()) -> dict:
    """純函式：EPIC、sub-issue、留言 → 摘要 dict（version 1）。"""
    results = [sub_result(s, sub_comments.get(s["number"], [])) for s in subs]
    sw = [r for r in results if r["by"] == "swarm"]
    runs = parse_runs(epic_comments)
    all_comments = list(epic_comments) + [c for n in sub_comments for c in sub_comments[n]]
    pr_list = []
    for p in [r.get("pr") for r in runs] + list(prs or []):
        if p and str(p).startswith("http") and p not in pr_list:
            pr_list.append(p)
    go = epic.get("go_at")
    return {
        "version": VERSION, "epic": epic["number"], "title": epic.get("title", ""),
        "opened": epic.get("created_at"), "go": go, "closed": epic.get("closed_at"),
        "days": _days(go, epic.get("closed_at")),
        "subs": {"total": len(results), "swarm": len(sw), "human": sum(r["by"] == "human" for r in results),
                 "passed": sum(r["passed"] for r in results), "stuck": sum(r["stuck"] for r in results),
                 "human_closed": sum(r["human_closed"] for r in results),
                 "first_pass": sum(r["first_pass"] for r in sw)},
        "runs": runs, "totals": totals(runs),
        "criteria_defects": count_defects(all_comments),
        "human_interventions": sum(r["stuck"] or r["human_closed"] for r in sw),
        "prs": pr_list,
        "_detail": results,
    }


def collect(src, epic_n: int) -> dict:
    epic = src.issue(epic_n)
    subs = src.subs(epic_n)
    ec = src.list_comments(epic_n)
    sc = {s["number"]: src.list_comments(s["number"]) for s in subs}
    prs = src.prs(epic_n) if hasattr(src, "prs") else []
    return build(epic, subs, ec, sc, prs)


# ── 留言 ────────────────────────────────────────────────────────────────────

def _v(x):
    return "—" if x is None else x


def dump(s: dict) -> str:
    d = {k: v for k, v in s.items() if not k.startswith("_")}
    return yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False).replace("-->", "-- >")


def render(s: dict) -> str:
    sb, t = s["subs"], s["totals"]
    rows = [("EPIC", f"#{s['epic']} {s.get('title', '')}"), ("開立／放行／結案", f"{_v(s['opened'])} ／ {_v(s['go'])} ／ {_v(s['closed'])}"),
            ("放行到結案（天）", _v(s["days"])),
            ("sub-issue（swarm／人）", f"{sb['total']}（{sb['swarm']}／{sb['human']}）"),
            ("判準通過／一次通過／卡住", f"{sb['passed']} ／ {sb['first_pass']} ／ {sb['stuck']}"),
            ("人工關閉", sb["human_closed"]), ("人工介入（swarm 張數）", s["human_interventions"]),
            ("判準寫錯（criteria-defect）", s["criteria_defects"]), ("執行次數", len(s["runs"])),
            ("agent 個數／峰值", f"{_v(t['agents'])} ／ {_v(t['peak'])}"), ("時間（秒）", _v(t["seconds"])),
            ("用量（輸入／輸出 tokens）", f"{_v(t['input_tokens'])} ／ {_v(t['output_tokens'])}"),
            ("費用估計（USD）", _v(t["cost_usd"])), ("推一把／重試／合併衝突", f"{_v(t['nudges'])} ／ {_v(t['retries'])} ／ {_v(t['conflicts'])}"),
            ("PR", "、".join(s["prs"]) or "—")]
    out = [HEAD, "", "| 項目 | 值 |", "|---|---|"] + [f"| {k} | {str(v).replace('|', '／')} |" for k, v in rows]
    det = s.get("_detail") or []
    if det:
        out += ["", "| sub-issue | 由誰做 | 狀態 | 結果 |", "|---|---|---|---|"]
        for r in det:
            res = "判準通過" if r["passed"] else "人工關閉" if r["human_closed"] else "—"
            if r["stuck"]:
                res += "（曾卡住）"
            out.append(f"| #{r['number']} {r['title'][:60].replace('|', '／')} | {_v(r['by'])} | {r['state']} | {res} |")
    out += ["", "缺的值寫 —（YAML 裡是 null），不猜。說明：docs/epic-summary.md", "",
            f"<!-- {MARK}\n{dump(s)}-->"]
    return "\n".join(out)


def parse_summary(body: str) -> dict | None:
    m = _SUMMARY_RE.search(body or "")
    if not m:
        return None
    try:
        d = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return d if isinstance(d, dict) and d.get("version") == VERSION else None


def upsert(src, epic_n: int, body: str) -> str:
    """同一個 EPIC 只留一則摘要：有就更新，沒有才新增。回傳 created／updated／unchanged。"""
    for c in src.list_comments(epic_n):
        if _SUMMARY_RE.search(c.get("body") or ""):
            if c.get("body") == body:
                return "unchanged"
            src.edit_comment(c["id"], body)
            return "updated"
    src.comment(epic_n, body)
    return "created"


def summarize(src, epic_n: int, dry_run: bool = False) -> tuple[dict, str]:
    s = collect(src, epic_n)
    body = render(s)
    return s, ("dry-run" if dry_run else upsert(src, epic_n, body))


def notify_text(s: dict, repo: str) -> str:
    sb, t = s["subs"], s["totals"]
    tok = None if t["input_tokens"] is None or t["output_tokens"] is None else t["input_tokens"] + t["output_tokens"]
    use = f"{_v(tok)} tokens" + (f"／${t['cost_usd']}" if t["cost_usd"] is not None else "")
    return (f"📦 EPIC #{s['epic']} 結案：{s.get('title', '')}\n"
            f"sub-issue {sb['total']} 張（swarm {sb['swarm']}／人 {sb['human']}）・通過 {sb['passed']}／卡住 {sb['stuck']}"
            f"・agent {_v(t['agents'])} 隻・用量 {use}\n"
            f"https://github.com/{repo}/issues/{s['epic']}")


# ── 每月報告 ─────────────────────────────────────────────────────────────────

def _ratio(a, b):
    return round(a / b, 3) if b else None


def month_of(s: dict) -> str | None:
    return str(s.get("closed") or "")[:7] or None


def stats(sums: list[dict]) -> dict:
    """一個月的結案摘要 → 指標。比例的分母是 swarm 張數；用量只算有回報的 EPIC。沒有 EPIC 的月份全是 null。"""
    sub = lambda k: sum((s.get("subs") or {}).get(k) or 0 for s in sums)
    swarm, total = sub("swarm"), sub("total")

    def per_sub(k):
        known = [s for s in sums if (s.get("totals") or {}).get(k) is not None]
        return _ratio(sum(s["totals"][k] for s in known), sum(s["subs"]["swarm"] for s in known)) if known else None

    def tokens(s):
        t = s.get("totals") or {}
        return None if t.get("input_tokens") is None or t.get("output_tokens") is None else t["input_tokens"] + t["output_tokens"]
    tk = [s for s in sums if tokens(s) is not None]
    days = [s["days"] for s in sums if s.get("days") is not None]
    return {"epics": len(sums), "subs": total, "swarm": swarm,
            "one_pass_rate": _ratio(sub("first_pass"), swarm), "stuck_rate": _ratio(sub("stuck"), swarm),
            "criteria_defects": sum(s.get("criteria_defects") or 0 for s in sums) if sums else None,
            "human_interventions": sum(s.get("human_interventions") or 0 for s in sums) if sums else None,
            "swarm_share": _ratio(swarm, total), "agents_per_sub": per_sub("agents"),
            "tokens_per_sub": _ratio(sum(tokens(s) for s in tk), sum(s["subs"]["swarm"] for s in tk)) if tk else None,
            "cost_per_sub": per_sub("cost_usd"),
            "days_to_close": round(sum(days) / len(days), 1) if days else None}


METRICS = [("one_pass_rate", "一次通過率", "%"), ("stuck_rate", "卡住率", "%"),
           ("criteria_defects", "判準寫錯（criteria-defect）", ""), ("human_interventions", "人工介入", ""),
           ("swarm_share", "交給 swarm 的比例", "%"), ("agents_per_sub", "每張平均 agent 數", ""),
           ("tokens_per_sub", "每張平均用量（tokens）", ""), ("cost_per_sub", "每張平均費用（USD）", ""),
           ("days_to_close", "放行到結案（天，平均）", "")]


def compare(cur: dict, prev: dict) -> dict:
    """每個指標：(這個月, 上個月, 差)；任一邊是 null 差就是 null。"""
    out = {}
    for k, _, _ in METRICS:
        a, b = cur.get(k), prev.get(k)
        out[k] = {"cur": a, "prev": b, "delta": None if a is None or b is None else round(a - b, 3)}
    return out


def prev_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _fmt(v, unit):
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if unit == "%" else f"{v:g}" if isinstance(v, float) else str(v)


def report(month: str, all_sums: list[dict]) -> dict:
    pm = prev_month(month)
    cur = stats([s for s in all_sums if month_of(s) == month])
    prev = stats([s for s in all_sums if month_of(s) == pm])
    return {"month": month, "prev_month": pm, "cur": cur, "prev": prev, "compare": compare(cur, prev),
            "epics": [s["epic"] for s in all_sums if month_of(s) == month]}


def render_report(r: dict, repo: str = "") -> str:
    out = [f"📈 swarm 每月趨勢 {r['month']}（和 {r['prev_month']} 比）",
           f"結案 EPIC {r['cur']['epics']} 個（上月 {r['prev']['epics']}）、sub-issue {r['cur']['subs']} 張"]
    for k, name, unit in METRICS:
        c = r["compare"][k]
        d = c["delta"]
        arrow = "" if d is None else (" ↑" if d > 0 else " ↓" if d < 0 else " →")
        dd = "" if d is None else f"（{'+' if d > 0 else ''}{_fmt(d, unit) if unit != '%' else f'{d * 100:.1f} 個百分點'}）"
        out.append(f"・{name}：{_fmt(c['cur'], unit)}，上月 {_fmt(c['prev'], unit)}{arrow}{dd}")
    if r["epics"] and repo:
        out.append("EPIC：" + " ".join(f"#{n}" for n in r["epics"]))
    return "\n".join(out)


# ── Discord ────────────────────────────────────────────────────────────────

def post_discord(channel: str, text: str, token: str) -> str:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        data=json.dumps({"content": text[:1990]}).encode(),
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (swarm-epic-summary, 1.0)"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}").get("id", "")


# ── 真的 GitHub ─────────────────────────────────────────────────────────────

class GhSource(GitHub):
    """epiclib.github.GitHub 加上：closed_at、已關 EPIC 的放行時間、留言列表與編輯、PR、已關的 EPIC。"""

    def issue(self, n: int) -> dict:
        it = super().issue(n)
        meta = json.loads(self._run("api", f"repos/{self.repo}/issues/{int(n)}", "--jq",
                                    "{closed_at: .closed_at, created_at: .created_at}"))
        it["closed_at"] = meta.get("closed_at")
        it["created_at"] = meta.get("created_at") or it.get("created_at")
        if not it.get("go_at") and "epic" in it["labels"]:
            it["go_at"] = self._go_at(n)        # 結案後 swarm:go 可能被拿掉；看 timeline
        return it

    def list_comments(self, n: int) -> list[dict]:
        out = self._run("api", f"repos/{self.repo}/issues/{int(n)}/comments", "--paginate", "--jq",
                        ".[] | {id: .id, body: .body, created_at: .created_at, author: .user.login}")
        return [json.loads(ln) for ln in out.splitlines() if ln.strip()]

    def edit_comment(self, cid, body: str) -> None:
        self._run("api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{cid}", "--input", "-",
                  input=json.dumps({"body": body}))

    def prs(self, n: int) -> list[str]:
        out = self._run("pr", "list", "-R", self.repo, "--head", f"epic/{int(n)}", "--state", "all",
                        "--json", "url", "--jq", ".[].url")
        return [x for x in out.split() if x]

    def closed_epics(self, since: str | None = None) -> list[int]:
        args = ["issue", "list", "-R", self.repo, "--state", "closed", "--label", "epic", "--limit", "500",
                "--json", "number", "--jq", ".[].number"]
        if since:
            args[-4:-4] = ["--search", f"closed:>={since}"]
        return sorted(int(x) for x in self._run(*args).split())
