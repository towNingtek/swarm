"""可接取判斷（契約第 5 節）：全部條件成立才接；任一條不符就跳過，並寫出原因。"""
from __future__ import annotations

from . import criteria


def check(epic: dict, issue: dict) -> tuple[bool, str]:
    n, labels = issue["number"], issue.get("labels", [])
    if issue.get("parent") != epic["number"]:
        return False, f"不是 EPIC #{epic['number']} 的 sub-issue"
    if issue.get("state") != "open":
        return False, "已經 closed"
    sw, hu = "by:swarm" in labels, "by:human" in labels
    if sw and hu:
        return False, "by:swarm 和 by:human 要剛好有一個，這張兩個都有"
    if hu:
        return False, "by:human：由人做"
    if not sw:
        return False, "沒有 by:swarm／by:human（要剛好有一個）"
    if "swarm:go" not in epic.get("labels", []) or not epic.get("go_at"):
        return False, f"EPIC #{epic['number']} 沒有 swarm:go：還沒放行"
    if "swarm:stuck" in labels:
        return False, "swarm:stuck：等人處理"
    ok, why = criteria.check(issue)
    if not ok:
        return False, why
    open_b = [b["number"] for b in issue.get("blocked_by", []) if b.get("state") != "closed"]
    if open_b:
        return False, "被 " + "、".join(f"#{b}" for b in open_b) + " 擋住"
    ok, why = criteria.frozen(issue, epic.get("go_at"))
    if not ok:
        return False, why
    return True, ""


def pick(epic: dict, subs: list[dict]) -> tuple[list[dict], dict[int, str]]:
    take, skipped = [], {}
    for it in sorted(subs, key=lambda x: x["number"]):
        ok, why = check(epic, it)
        if ok:
            take.append(it)
        else:
            skipped[it["number"]] = why
    return take, skipped
