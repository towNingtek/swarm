#!/usr/bin/env python3
"""每月趨勢報告（#455）。說明見 docs/epic-summary.md。

  python3 swarm/epic_report.py --repo acme/swarm --month 2026-10
  python3 swarm/epic_report.py --repo acme/swarm --month 2026-10 --discord-channel <channel-id>
  python3 swarm/epic_report.py --repo acme/swarm --month 2026-10 --issue <report-issue>

彙整該月（以結案日期算）所有 EPIC 的結案摘要，和上個月比：一次通過率、卡住率、criteria_defects、
人工介入、交給 swarm 的比例、每張平均 agent 數與用量、放行到結案天數。
摘要從 EPIC 上的 `<!-- swarm-epic-summary -->` 讀；還沒有摘要的 EPIC 現場算（不寫回）。
不給 --month 就是上個月（每月 1 日的排程用）。
給 --issue N 就把月報留言到 issue N（#514）：留言帶 `<!-- swarm-monthly-report month=YYYY-MM -->`，
同月份已有就更新那一則，不新增；沒給就只印出。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


MARK = "swarm-monthly-report"
_MARK_RE = re.compile(r"<!--\s*" + MARK + r"\s+month=(\d{4}-\d{2})\s*-->")


def with_mark(text: str, month: str) -> str:
    return f"{text.rstrip()}\n\n<!-- {MARK} month={month} -->\n"


def upsert_report(src, issue: int, month: str, body: str) -> str:
    """同一個月份只留一則月報：有就更新，沒有才新增。回傳 created／updated／unchanged。"""
    for c in src.list_comments(issue):
        m = _MARK_RE.search(c.get("body") or "")
        if m and m.group(1) == month:
            if c.get("body") == body:
                return "unchanged"
            src.edit_comment(c["id"], body)
            return "updated"
    src.comment(issue, body)
    return "created"


def load_summary(src, n: int) -> dict:
    from epiclib import summary
    for c in src.list_comments(n):
        s = summary.parse_summary(c.get("body") or "")
        if s:
            return s
    return summary.collect(src, n)


def main(argv=None, src=None):
    from epiclib import summary
    ap = argparse.ArgumentParser(description="swarm 每月趨勢報告（和上個月比）。")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--month", help="YYYY-MM（預設：上個月）")
    ap.add_argument("--discord-channel", help="發到這個 Discord 頻道（token：SWARM_DISCORD_TOKEN）")
    ap.add_argument("--issue", type=int, help="把月報留言到這個 issue（同月份更新同一則）")
    ap.add_argument("--json", action="store_true", help="另外印出 JSON")
    a = ap.parse_args(argv)
    month = a.month or summary.prev_month(datetime.now(timezone.utc).strftime("%Y-%m"))
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError:
        ap.error("--month 要是 YYYY-MM")

    src = src or summary.GhSource(a.repo)
    since = summary.prev_month(month) + "-01"
    sums = [load_summary(src, n) for n in src.closed_epics(since=since)]
    r = summary.report(month, sums)
    text = summary.render_report(r, a.repo)
    print(text)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    if a.issue:
        what = upsert_report(src, a.issue, month, with_mark(text, month))
        print(f"月報留言 #{a.issue}：{what}", file=sys.stderr)
    if a.discord_channel:
        token = os.environ.get("SWARM_DISCORD_TOKEN", "").strip()
        if not token:
            print("⚠️ 沒有 SWARM_DISCORD_TOKEN：不發 Discord", file=sys.stderr)
        else:
            summary.post_discord(a.discord_channel, text, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
