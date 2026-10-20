"""真實 GitHub（透過 gh CLI）。回傳的 issue dict 形狀和 tests/epic/fake_github.FakeGitHub 一樣。"""
from __future__ import annotations

import json
import subprocess

Q_ISSUE = """
query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){
  number title body state createdAt lastEditedAt
  labels(first:50){nodes{name}}
  issueType{name}
  parent{number}
  blockedBy(first:50){nodes{number state}}
  blocking(first:50){nodes{number}}
  subIssues(first:100){nodes{number}}
}}}"""

Q_PROJECT_ITEMS = """
query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){
  projectItems(first:20){nodes{id project{id title
    field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}}}
}}}"""

# 一次查完：open、帶 epic 與 swarm:go、沒有 epic:review 的 EPIC，連同 sub-issue 的狀態與 labels 摘要。
# swarm:go 的放行時間從 LABELED 事件取（取代逐張的 REST timeline 查詢）。
#
# 成本（#488，2026-10-19 用 rateLimit{cost} 實測）：GraphQL 的 cost 由各層 first/last 上限相乘估算，
# 不看實際筆數。原本 search 50 × labels 50／timeline 100／subIssues 100 × 子 labels 30 → cost 52；
# 自動開工每分鐘跑一次≈3,100 點／小時，超過每小時 5,000 點的預算（還要留給看板與蜂群）。
# 現在 search 20、labels 10、timeline 20、subIssues 50、子 labels 10 → cost 11（≈660 點／小時）。
# 同時放行的 EPIC、EPIC 的 labels、每張 sub-issue 的 labels 實際上都遠低於這些上限。
# 各層多要一個 totalCount（不增加 cost）：任一層被截斷，go_epics() 標 truncated，
# epic_autostart 對那個 EPIC 退回逐張檢查（gh.issue），不會因為上限漏判。
Q_GO_EPICS = """
query($s:String!){search(query:$s,type:ISSUE,first:20){nodes{... on Issue{
  number state
  labels(first:10){totalCount nodes{name}}
  timelineItems(itemTypes:[LABELED_EVENT],last:20){totalCount nodes{... on LabeledEvent{createdAt label{name}}}}
  subIssues(first:50){totalCount nodes{number state labels(first:10){totalCount nodes{name}}}}
}}}}"""

M_SET_FIELD = """
mutation($p:ID!,$i:ID!,$f:ID!,$v:String!){updateProjectV2ItemFieldValue(input:{
  projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$v}}){projectV2Item{id}}}"""


def _cut(conn: dict) -> bool:
    """GraphQL connection 的 nodes 比 totalCount 少＝被上限截斷。沒有 totalCount 視為沒截斷。"""
    return conn.get("totalCount", 0) > len(conn.get("nodes") or [])


class GitHub:
    def __init__(self, repo: str, gh: str = "gh"):
        self.repo, self.gh = repo, gh
        self.owner, self.name = repo.split("/", 1)
        self._cache: dict[int, dict] = {}
        self.gql_calls = 0           # 本物件發出的 GraphQL 呼叫次數

    def _run(self, *args, input=None) -> str:
        r = subprocess.run([self.gh, *args], capture_output=True, text=True, input=input)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])}…: {r.stderr.strip()[:500]}")
        return r.stdout

    def _gql(self, q, **v):
        self.gql_calls += 1
        args = ["api", "graphql", "-f", "query=" + q]
        for k, x in v.items():
            args += ["-F" if isinstance(x, int) else "-f", f"{k}={x}"]
        return json.loads(self._run(*args))["data"]

    def _go_at(self, n: int) -> str | None:
        out = self._run("api", f"repos/{self.repo}/issues/{n}/timeline", "--paginate", "--jq",
                        '.[]|select(.event=="labeled" and .label.name=="swarm:go")|.created_at')
        ts = [t for t in out.split() if t]
        return ts[-1] if ts else None

    def issue(self, n: int) -> dict:
        d = self._gql(Q_ISSUE, o=self.owner, r=self.name, n=int(n))["repository"]["issue"]
        labels = [x["name"] for x in d["labels"]["nodes"]]
        it = {
            "number": d["number"], "title": d["title"], "body": d["body"] or "",
            "state": d["state"].lower(), "created_at": d["createdAt"], "last_edited_at": d["lastEditedAt"],
            "labels": labels, "type": (d.get("issueType") or {}).get("name"),
            "parent": (d.get("parent") or {}).get("number"),
            "blocked_by": [{"number": b["number"], "state": b["state"].lower()} for b in d["blockedBy"]["nodes"]],
            "blocking": [b["number"] for b in d["blocking"]["nodes"]],
            "subs": [s["number"] for s in d["subIssues"]["nodes"]],
            "go_at": None,
        }
        if "swarm:go" in labels:
            it["go_at"] = self._go_at(n)
        return it

    def subs(self, n: int) -> list[dict]:
        return [self.issue(k) for k in self.issue(n)["subs"]]

    def list_open_epics(self) -> list[int]:
        out = self._run("issue", "list", "-R", self.repo, "--state", "open", "--label", "epic",
                        "--limit", "200", "--json", "number", "--jq", ".[].number")
        return sorted(int(x) for x in out.split())

    def go_epics(self) -> list[dict]:
        """一次 GraphQL：open、帶 epic 與 swarm:go、沒有 epic:review 的 EPIC 摘要。

        每個：{number, state, labels, go_at, subs: [{number, state, labels}], truncated}。
        truncated：labels／sub-issue／子 labels 超過查詢上限，或最近 20 個 LABELED 事件裡沒有 swarm:go
        而更早還有事件——摘要可能漏東西，呼叫端要退回逐張檢查。
        """
        q = f'repo:{self.repo} is:issue is:open label:epic label:"swarm:go" -label:"epic:review"'
        out = []
        for d in self._gql(Q_GO_EPICS, s=q)["search"]["nodes"]:
            if not d:
                continue
            tl = d["timelineItems"]
            go = [e["createdAt"] for e in tl["nodes"]
                  if e and (e.get("label") or {}).get("name") == "swarm:go"]
            subs = d["subIssues"]["nodes"]
            out.append({
                "number": d["number"], "state": d["state"].lower(),
                "labels": [x["name"] for x in d["labels"]["nodes"]],
                "go_at": max(go) if go else None,
                "subs": [{"number": x["number"], "state": x["state"].lower(),
                          "labels": [y["name"] for y in x["labels"]["nodes"]]} for x in subs],
                # 任一層超過查詢上限（見 Q_GO_EPICS 註解）：摘要不完整，呼叫端要逐張檢查
                "truncated": (_cut(d["labels"]) or _cut(d["subIssues"]) or any(_cut(x["labels"]) for x in subs)
                              or (not go and _cut(tl))),
            })
        return sorted(out, key=lambda x: x["number"])

    def comment(self, n: int, body: str) -> None:
        self._run("issue", "comment", str(n), "-R", self.repo, "--body-file", "-", input=body)

    def add_label(self, n: int, label: str) -> None:
        self._run("issue", "edit", str(n), "-R", self.repo, "--add-label", label)

    def remove_label(self, n: int, label: str) -> None:
        self._run("issue", "edit", str(n), "-R", self.repo, "--remove-label", label)

    def close(self, n: int) -> None:
        self._run("issue", "close", str(n), "-R", self.repo)

    def reopen(self, n: int) -> None:
        self._run("issue", "reopen", str(n), "-R", self.repo)

    def set_status(self, n: int, name: str) -> list[str]:
        """把 issue 在每個 project 的 Status 單選欄位設成 `name`。

        沒有 Status 欄位、或欄位沒有這個選項的 project 略過。回傳有更新的 project 名稱。
        """
        d = self._gql(Q_PROJECT_ITEMS, o=self.owner, r=self.name, n=int(n))["repository"]["issue"]
        done = []
        for item in (d.get("projectItems") or {}).get("nodes") or []:
            proj = item.get("project") or {}
            field = proj.get("field") or {}
            opt = next((o for o in field.get("options") or [] if o["name"] == name), None)
            if not field.get("id") or not opt:
                continue
            self._gql(M_SET_FIELD, p=proj["id"], i=item["id"], f=field["id"], v=opt["id"])
            done.append(proj.get("title", ""))
        return done
