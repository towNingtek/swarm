"""一輪主程式與 EPIC 總結（契約第 5、6 節）。

一次只跑一個 EPIC、一輪：反覆挑可接的 sub-issue 交給 work()，直到沒有可接的就停。
每張一輪最多一次。結束時在 EPIC 留總結；所有 sub-issue 都關了才加 epic:review。永遠不關 EPIC。
"""
from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass, field

from . import takeable

REVIEW = "epic:review"


class Busy(RuntimeError):
    pass


@dataclass
class Report:
    done: list[int] = field(default_factory=list)
    stuck: list[int] = field(default_factory=list)
    skipped: dict[int, str] = field(default_factory=dict)
    review: bool = False


def _is_epic(it: dict) -> bool:
    return "epic" in it.get("labels", []) or (it.get("type") or "") == "Epic"


def _model_name(model) -> str:
    return "/".join(model)


def run_round(gh, epic_n: int, model, work, lock_dir: str) -> Report:
    if not model or not isinstance(model, (list, tuple)) or len(model) != 2:
        raise ValueError('要在啟動時指定模型，例如 ["claude","claude-opus-5-5"]')
    epic = gh.issue(epic_n)
    if not _is_epic(epic):
        raise ValueError(f"#{epic_n} 不是 EPIC（沒有 epic label 或 Epic 類型）")
    os.makedirs(lock_dir, exist_ok=True)
    fd = os.open(os.path.join(lock_dir, f"epic-{epic_n}.lock"), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(f"EPIC #{epic_n} 已經有一輪在跑")
        return _run(gh, epic_n, model, work)
    finally:
        os.close(fd)


def _run(gh, epic_n, model, work) -> Report:
    rep = Report()
    gh.comment(epic_n, f"## 開始一輪（runner）\n\n模型：`{_model_name(model)}`。一次一個 EPIC、一輪；沒有可接的 sub-issue 就停。")
    tried: set[int] = set()
    while True:
        epic = gh.issue(epic_n)
        take, skipped = takeable.pick(epic, gh.subs(epic_n))
        take = [t for t in take if t["number"] not in tried]
        if not take:
            rep.skipped = {k: v for k, v in skipped.items() if k not in rep.done and k not in rep.stuck}
            break
        it = take[0]
        tried.add(it["number"])
        r = work(epic, it)
        (rep.done if r == "done" else rep.stuck).append(it["number"])
    _summary(gh, epic_n, model, rep)
    return rep


def _summary(gh, epic_n, model, rep: Report):
    subs = gh.subs(epic_n)
    allclosed = bool(subs) and all(s["state"] == "closed" for s in subs)
    lines = [f"## 這一輪結束（runner）", "", f"模型：`{_model_name(model)}`", "",
             "| sub-issue | 狀態 | 這一輪 |", "|---|---|---|"]
    for s in subs:
        n = s["number"]
        now = "通過並關閉" if n in rep.done and s["state"] == "closed" else \
              "swarm:stuck" if n in rep.stuck else \
              ("做了但沒關閉" if n in rep.done else rep.skipped.get(n, "—"))
        lines.append(f"| #{n} {s.get('title', '')[:60]} | {s['state']} | {now} |")
    if not rep.done and not rep.stuck:
        lines += ["", "這一輪沒有可接的 sub-issue。"]
    if allclosed:
        lines += ["", f"**子 issue 已全部通過，可結案。** 加上 `{REVIEW}`；EPIC 由人關閉（必要條件，不是充分條件）。"]
    gh.comment(epic_n, "\n".join(lines))
    if allclosed:
        gh.add_label(epic_n, REVIEW)
        rep.review = True
