#!/usr/bin/env python3
"""cron：觀察 EPIC，有需要人處理的事就發 Discord（docs/epic-contract.md 第 7 節）。只讀取、只通知。

  */30 * * * *  python3 swarm/epic_watch.py --repo acme/swarm --channel <id>

--dry-run：只印出要發的訊息，不發送、不寫狀態。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE = os.path.expanduser("~/.local/share/swarm-epic/watch")


def main(argv=None):
    ap = argparse.ArgumentParser(description="觀察 EPIC（epic:review、擋住後續的 swarm:stuck），發 Discord 通知。")
    ap.add_argument("--repo", required=True, action="append", help="owner/name，可重複")
    ap.add_argument("--channel", type=int, help="Discord channel id（--dry-run 時可省略）")
    ap.add_argument("--state", default=None, help="狀態檔（預設：%s/<repo>.json）" % STATE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.dry_run and not a.channel:
        ap.error("--channel 必填（或用 --dry-run）")

    from epiclib import watch
    from epiclib.github import GitHub

    if a.dry_run:
        send = lambda t: print("[dry-run]", t)
    else:
        from reply_discord import send_discord_message
        send = lambda t: send_discord_message(a.channel, t)

    for repo in a.repo:
        path = a.state or os.path.join(STATE, repo.replace("/", "-") + ".json")
        try:
            state = json.load(open(path))
        except (OSError, ValueError):
            state = {}
        new = watch.scan(GitHub(repo), state, datetime.now(timezone.utc), send)
        if not a.dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            json.dump(new, open(tmp, "w"))
            os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
