"""cron 觀察、通知 Discord（契約第 7 節）：只讀取、只通知，不啟動任何東西。

通知：EPIC 有 epic:review；EPIC 底下有擋住「未關閉後續」的 swarm:stuck。
第一次出現立刻通知；沒處理就每 24 小時提醒一次；消失後再出現，視為新的一次。
發送失敗不記錄，下次 cron 再試。
"""
from __future__ import annotations

from datetime import datetime, timedelta

DAY = timedelta(hours=24)


def _events(gh) -> dict[str, str]:
    ev = {}
    for e in gh.list_open_epics():
        epic = gh.issue(e)
        if epic.get("state") != "open":
            continue
        title = epic.get("title", "")
        if "epic:review" in epic.get("labels", []):
            ev[f"review:{e}"] = f"✅ EPIC #{e}「{title}」：sub-issue 已全部通過判準（epic:review），請驗收、結案。"
        for s in gh.subs(e):
            if s.get("state") != "open" or "swarm:stuck" not in s.get("labels", []):
                continue
            blocked = [b for b in s.get("blocking", []) if gh.issue(b).get("state") == "open"]
            if blocked:
                ev[f"stuck:{s['number']}"] = (f"⛔ EPIC #{e}「{title}」：#{s['number']}「{s.get('title', '')}」卡住了（swarm:stuck），"
                                              f"擋住 " + "、".join(f"#{b}" for b in blocked) + "。")
    return ev


def scan(gh, state: dict, now: datetime, send) -> dict:
    sent = dict((state or {}).get("sent", {}))
    ev = _events(gh)
    out = {}
    for key, text in ev.items():
        last = sent.get(key)
        due = last is None or now - datetime.fromisoformat(last) >= DAY
        if due:
            try:
                send(text)
                out[key] = now.isoformat()
            except Exception:
                if last:
                    out[key] = last
        else:
            out[key] = last
    return {"sent": out}
