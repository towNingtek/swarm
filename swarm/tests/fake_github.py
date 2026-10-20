"""假的 GitHub：只存在記憶體裡，記錄每一次寫入，不碰真實 repo。

`epiclib` 的所有 GitHub 存取都經過同一組方法（見 `epiclib/github.py` 的 `GitHub`），
這裡提供同名方法的假實作，測試用它檢查 runner 做了哪些寫入。
"""
from __future__ import annotations

import copy

GO_AT = "2026-10-10T10:00:00Z"
BEFORE = "2026-10-10T09:00:00Z"
AFTER = "2026-10-10T11:00:00Z"


def criteria_body(goal: str, ok_cells: list[str], bad_cells: list[str] | None = None) -> str:
    """產生符合 docs/issue-acceptance.md 格式的內文。cell 原樣放進「怎麼驗」欄。"""
    s = f"## 目標\n\n{goal}\n\n## 驗收判準\n\n### ✅ 修好了 —— 全部成立\n| # | 怎麼驗 | 預期 |\n|---|--------|------|\n"
    s += "".join(f"| {i + 1} | {c} | 說明 |\n" for i, c in enumerate(ok_cells))
    s += "\n### ❌ 還沒好 —— 任一成立（附現況實測）\n| # | 怎麼驗 | 目前 |\n|---|--------|------|\n"
    s += "".join(f"| {i + 1} | {c} | 失敗輸出 |\n" for i, c in enumerate(bad_cells or ok_cells[:1]))
    return s


class FakeGitHub:
    """issues: number -> dict（number, title, body, labels, state, created_at, last_edited_at,
    parent, blocked_by:[number], type）。EPIC 另有 go_at（最後一次加上 swarm:go 的時間）。

    `issue(n)` 回傳的形狀就是 epiclib 要的 issue dict（真實的 `epiclib.github.GitHub` 也回傳同樣形狀）：
      blocked_by: [{"number", "state"}]、blocking: [number]（被這張擋住的）、subs: [number]。
    """

    WRITES = ("comment", "add_label", "remove_label", "close", "reopen")

    def __init__(self, issues: dict[int, dict]):
        self.issues = {int(k): dict(v) for k, v in issues.items()}
        for n, it in self.issues.items():
            it.setdefault("number", n)
            it.setdefault("title", f"issue {n}")
            it.setdefault("body", "")
            it.setdefault("labels", [])
            it.setdefault("state", "open")
            it.setdefault("created_at", BEFORE)
            it.setdefault("last_edited_at", None)
            it.setdefault("parent", None)
            it.setdefault("blocked_by", [])
            it.setdefault("type", None)
            it.setdefault("go_at", None)
            it["labels"] = list(it["labels"])
        self.calls: list[tuple] = []
        self.gql_calls = 0                        # 讀取查詢次數（issue() 與 go_epics() 各算一次）
        self.reads: list[tuple] = []              # 每次讀取查詢：("issue", n) 或 ("go_epics",)
        self.status: dict[int, str] = {}          # Project 的 Status（set_status 設的）
        self.comments: dict[int, list[str]] = {}

    # ── 讀取 ──
    def issue(self, n: int) -> dict:
        self.gql_calls += 1
        self.reads.append(("issue", int(n)))
        it = copy.deepcopy(self.issues[int(n)])
        it["blocked_by"] = [{"number": b, "state": self.issues[b]["state"]} for b in it["blocked_by"]]
        it["blocking"] = sorted(k for k, v in self.issues.items() if int(n) in v["blocked_by"])
        it["subs"] = sorted(k for k, v in self.issues.items() if v["parent"] == int(n))
        return it

    def subs(self, n: int) -> list[dict]:
        return [self.issue(k) for k in self.issue(n)["subs"]]

    def list_open_epics(self) -> list[int]:
        return sorted(k for k, v in self.issues.items() if v["state"] == "open" and "epic" in v["labels"])

    def go_epics(self) -> list[dict]:
        """一次查詢：open、帶 epic 與 swarm:go、沒有 epic:review 的 EPIC 摘要（同 GitHub.go_epics）。"""
        self.gql_calls += 1
        self.reads.append(("go_epics",))
        out = []
        for n in self.list_open_epics():
            v = self.issues[n]
            if "swarm:go" not in v["labels"] or "epic:review" in v["labels"]:
                continue
            subs = sorted(k for k, x in self.issues.items() if x["parent"] == n)
            out.append({"number": n, "state": v["state"], "labels": list(v["labels"]), "go_at": v["go_at"],
                        "subs": [{"number": k, "state": self.issues[k]["state"],
                                  "labels": list(self.issues[k]["labels"])} for k in subs]})
        return out

    # ── 寫入（全部記錄）──
    def comment(self, n: int, body: str) -> None:
        self.calls.append(("comment", int(n)))
        self.comments.setdefault(int(n), []).append(body)

    def add_label(self, n: int, label: str) -> None:
        self.calls.append(("add_label", int(n), label))
        if label not in self.issues[int(n)]["labels"]:
            self.issues[int(n)]["labels"].append(label)

    def remove_label(self, n: int, label: str) -> None:
        self.calls.append(("remove_label", int(n), label))
        if label in self.issues[int(n)]["labels"]:
            self.issues[int(n)]["labels"].remove(label)

    def close(self, n: int) -> None:
        self.calls.append(("close", int(n)))
        self.issues[int(n)]["state"] = "closed"

    def reopen(self, n: int) -> None:
        self.calls.append(("reopen", int(n)))
        self.issues[int(n)]["state"] = "open"

    def set_status(self, n: int, name: str) -> list[str]:
        self.calls.append(("set_status", int(n), name))
        self.status[int(n)] = name
        return ["fake project"]

    # ── 測試輔助 ──
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in self.WRITES]

    def status_calls(self, n: int | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] == "set_status" and (n is None or c[1] == int(n))]

    def closed_calls(self) -> list[int]:
        return [c[1] for c in self.calls if c[0] == "close"]


def world(**over) -> FakeGitHub:
    """一個已放行的 EPIC #1，底下 #2 → #3 → #4（鏈），#5 是 by:human。"""
    ok = criteria_body("做 A", ["`true`"])
    issues = {
        1: {"title": "[EPIC] 測試", "body": "## 目標\n\n整體目標：讓測試世界運作。\n", "labels": ["epic", "swarm:go"],
            "type": "Epic", "go_at": GO_AT},
        2: {"title": "第一步", "body": ok, "labels": ["by:swarm"], "parent": 1},
        3: {"title": "第二步", "body": ok, "labels": ["by:swarm"], "parent": 1, "blocked_by": [2]},
        4: {"title": "第三步", "body": ok, "labels": ["by:swarm"], "parent": 1, "blocked_by": [3]},
        5: {"title": "人做的", "body": criteria_body("人", ["人：看過"]), "labels": ["by:human"], "parent": 1},
    }
    for k, v in over.items():
        issues.setdefault(int(k.lstrip("i")), {}).update(v)
    return FakeGitHub(issues)
