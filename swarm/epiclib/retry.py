"""重試與卡住（契約第 4 節）：同一張最多試 3 次；都失敗就標 swarm:stuck，不換模型、不改 by:human。"""
from __future__ import annotations

STUCK = "swarm:stuck"


def work(gh, epic: dict, issue: dict, attempt, check, max_tries: int = 3) -> str:
    n, prev = issue["number"], None
    for i in range(1, max_tries + 1):
        a = attempt(prev)
        if getattr(a, "criteria_wrong", None):
            gh.comment(n, f"## 嘗試 {i}/{max_tries}：agent 認為判準有誤，停下來\n\n"
                          f"> {a.criteria_wrong}\n\n判準不能由 agent 修改。標 `{STUCK}`，等人判斷。")
            gh.add_label(n, STUCK)
            return "stuck"
        note = "（agent 逾時，已取消；仍照樣跑判準）" if getattr(a, "timed_out", False) else ""
        res = check()
        if res.ok:
            gh.comment(n, f"## 嘗試 {i}/{max_tries}：判準全部通過{note}")
            return "done"
        gh.comment(n, f"## 嘗試 {i}/{max_tries}：判準沒通過{note}\n\n````text\n{str(res.summary)[-6000:]}\n````")
        prev = f"第 {i} 次嘗試的判準輸出：\n{res.summary}"
    gh.comment(n, f"試了 {max_tries} 次都沒通過，標 `{STUCK}`。不自動換模型，由人決定下一步。")
    gh.add_label(n, STUCK)
    return "stuck"
