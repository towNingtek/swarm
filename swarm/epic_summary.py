#!/usr/bin/env python3
"""EPIC 結案摘要（#455）。說明見 docs/epic-summary.md。

  python3 swarm/epic_summary.py --repo acme/swarm --epic 412
  python3 swarm/epic_summary.py --repo acme/swarm --epic 412 --discord-channel <channel-id>
  python3 swarm/epic_summary.py --repo acme/swarm --backfill

在 EPIC 留（或更新）一則「## 結案摘要」：人看的表格＋`<!-- swarm-epic-summary … -->`（YAML，version: 1）。
同一個 EPIC 重跑時更新同一則，不重複留。

--backfill：對已關閉的 EPIC（#390、#402、#430、#436，以及之後關的）補摘要；不發 Discord。
--discord-channel：留完摘要後發一則簡短通知；bot token 讀環境變數 SWARM_DISCORD_TOKEN（沒有就跳過並警告）。
--dry-run：只印出摘要，不寫 GitHub、不發 Discord。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BACKFILL = (390, 402, 430, 436)       # #455 寫明要補的；之後關的由 closed_epics() 找


def backfill_targets(src) -> list[int]:
    lo = min(BACKFILL)
    return sorted(set(BACKFILL) | {n for n in src.closed_epics() if n >= lo})


def notify(text: str, channel: str | None) -> None:
    if not channel:
        return
    from epiclib import summary
    token = os.environ.get("SWARM_DISCORD_TOKEN", "").strip()
    if not token:
        print("⚠️ 沒有 SWARM_DISCORD_TOKEN：不發 Discord", file=sys.stderr)
        return
    summary.post_discord(channel, text, token)


def main(argv=None):
    ap = argparse.ArgumentParser(description="EPIC 結案摘要（留在 EPIC 上，重跑更新同一則）。")
    ap.add_argument("--repo", required=True, help="owner/name，例如 acme/swarm")
    ap.add_argument("--epic", type=int, action="append", help="EPIC 的 issue 編號，可重複")
    ap.add_argument("--backfill", action="store_true", help="補記所有已關閉的 EPIC（不發 Discord）")
    ap.add_argument("--discord-channel", help="發通知到這個 Discord 頻道（token：SWARM_DISCORD_TOKEN）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.epic and not a.backfill:
        ap.error("要 --epic N 或 --backfill")

    from epiclib import summary
    src = summary.GhSource(a.repo)
    targets = sorted(set(a.epic or []) | (set(backfill_targets(src)) if a.backfill else set()))
    rc = 0
    for n in targets:
        try:
            s, what = summary.summarize(src, n, dry_run=a.dry_run)
        except Exception as exc:              # one EPIC's failure never stops the backfill
            print(f"#{n}: 失敗 {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
            rc = 1
            continue
        print(f"#{n}: {what}")
        if a.dry_run:
            print(summary.render(s))
            print(summary.notify_text(s, a.repo))
        elif not a.backfill:
            notify(summary.notify_text(s, a.repo), a.discord_channel)
    return rc


if __name__ == "__main__":
    sys.exit(main())
