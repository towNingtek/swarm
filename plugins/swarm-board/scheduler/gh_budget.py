#!/usr/bin/env python3
"""GitHub API 額度守門（#485）。wolffia/fetch-project.py 與 scheduler/epic_autostart.py 共用。

  from gh_budget import check
  b = check(who="epic_autostart")      # 預設門檻 min_ratio=0.2
  if b["low"]:
      print(f"額度偏低，跳過到 {b['reset_at']}")

用 `{rateLimit{limit remaining resetAt}}` 取得 GraphQL 額度（這個查詢本身不計費）。
- 剩餘比例 < min_ratio：回傳 low=True，呼叫端自己決定怎麼跳過。
- 低於門檻時發一次 Discord 通知到 #sys-bot；token 檔路徑由環境變數 SWARM_NOTIFY_TOKEN_FILE 指定，
  沒設（或檔案讀不到）就只印不發。同一個 resetAt 只通知一次，記在 ~/.local/share/swarm-epic/gh-budget.json。
- rateLimit 查詢失敗（gh 不在、網路錯、回應怪）時回傳 low=False 並帶 error：照常執行，不中斷。

只用標準函式庫（wolffia 那邊沒有 requests）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

QUERY = "{rateLimit{limit remaining resetAt}}"
CHANNEL = os.environ.get("GH_BUDGET_DISCORD_CHANNEL", "")  # Discord channel for budget alerts; empty = no alert
STATE_FILE = "~/.local/share/swarm-epic/gh-budget.json"   # 用到時才展開 ~
TOKEN_ENV = "SWARM_NOTIFY_TOKEN_FILE"


def fetch_rate(gh: str = "gh", timeout: int = 60) -> dict:
    """回傳 {"limit", "remaining", "resetAt"}；任何失敗都 raise。"""
    r = subprocess.run([gh, "api", "graphql", "-f", "query=" + QUERY], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"gh exited {r.returncode}: {r.stderr.strip()[:300]}")
    rl = json.loads(r.stdout)["data"]["rateLimit"]
    limit, remaining = int(rl["limit"]), int(rl["remaining"])
    if limit <= 0:
        raise ValueError(f"limit={limit}")
    return {"limit": limit, "remaining": remaining, "resetAt": str(rl["resetAt"])}


def post_discord(token: str, channel: str, content: str, timeout: int = 30) -> None:
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel}/messages",
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (swarm-gh-budget, 1.0)"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def _load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, d: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read_token(token_file: str | None) -> str | None:
    if not token_file:
        return None
    try:
        with open(os.path.expanduser(token_file), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError as e:
        print(f"gh_budget: 讀不到 token 檔 {token_file}：{e}", file=sys.stderr)
        return None


def notify(text: str, reset_at: str, state_file: str = STATE_FILE, token_file: str | None = None,
           post=post_discord, channel: str | None = None) -> bool:
    """同一個 resetAt 只發一次。回傳這次是否真的發出。沒有 token 只印不發（也不記，之後設了 token 還會發）。"""
    channel = CHANNEL if channel is None else channel
    state_file = os.path.expanduser(state_file)
    st = _load_state(state_file)
    if st.get("notified_reset_at") == reset_at:
        return False
    if not channel:
        print(f"gh_budget（沒設 GH_BUDGET_DISCORD_CHANNEL，不發 Discord）：{text}", file=sys.stderr)
        return False
    token = _read_token(token_file)
    if token is None:
        print(f"gh_budget（沒設 {TOKEN_ENV}，不發 Discord）：{text}", file=sys.stderr)
        return False
    try:
        post(token, channel, text)
    except Exception as e:  # 通知失敗不影響呼叫端
        print(f"gh_budget: Discord 通知失敗：{e}", file=sys.stderr)
        return False
    st["notified_reset_at"] = reset_at
    try:
        _save_state(state_file, st)
    except OSError as e:
        print(f"gh_budget: 寫不了 {state_file}：{e}", file=sys.stderr)
    return True


def check(min_ratio: float = 0.2, who: str = "", fetch=fetch_rate, post=post_discord,
          state_file: str = STATE_FILE, token_file: str | None = None) -> dict:
    """回傳 {"low": bool, "ratio", "limit", "remaining", "reset_at", "error", "notified"}。

    token_file 沒給就讀環境變數 SWARM_NOTIFY_TOKEN_FILE。查不到額度時 low=False、error 有值。
    """
    try:
        rl = fetch()
        limit, remaining, reset_at = int(rl["limit"]), int(rl["remaining"]), str(rl["resetAt"])
        ratio = remaining / limit
    except Exception as e:
        print(f"gh_budget: 查不到 rateLimit，照常執行：{e}", file=sys.stderr)
        return {"low": False, "ratio": None, "limit": None, "remaining": None, "reset_at": None,
                "error": str(e), "notified": False}
    rep = {"low": ratio < min_ratio, "ratio": ratio, "limit": limit, "remaining": remaining,
           "reset_at": reset_at, "error": None, "notified": False}
    if rep["low"]:
        if token_file is None:
            token_file = os.environ.get(TOKEN_ENV)
        text = (f"⚠️ GitHub API 額度偏低：剩 {remaining}/{limit}（{ratio:.0%}，門檻 {min_ratio:.0%}）；"
                f"{who or '排程'}跳過到 {reset_at}")
        rep["notified"] = notify(text, reset_at, state_file, token_file, post)
    return rep


if __name__ == "__main__":
    b = check()
    print(json.dumps(b, ensure_ascii=False))
    sys.exit(0)
