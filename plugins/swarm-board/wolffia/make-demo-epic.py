#!/usr/bin/env python3
"""示範資料：EPIC 星雲（假資料，不對應任何真實 issue）。輸出 demo-epic.js，部署時放到 data/。
用法：python3 make-demo-epic.py [OUT]   v8.html?demo=1 會載入它。"""
import json, sys, datetime as D
NOW = D.datetime.now(D.timezone.utc).replace(microsecond=0)
iso = lambda d: (NOW - D.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
U = "https://github.com/acme/wolffia-demo/issues/%d"
W = ["alice", "bob", "carol"]
items = []
def add(n, t, col, days, labels=(), parent=None, blocked=(), typ=None, closed=None, who=None, target=None):
    created = days + 6 + n % 5
    h = [[iso(days), col]]
    if closed is not None:
        h.append([iso(closed), "closed"])
    items.append({"n": n, "t": t, "u": U % n, "repo": "wolffia-demo", "owner": "acme",
                  "state": "CLOSED" if closed is not None else "OPEN", "created": iso(created),
                  "closed": iso(closed) if closed is not None else None, "status": "完成" if closed is not None else col,
                  "since": iso(days), "hist": h, "who": [who] if who else [W[n % 3]], "labels": list(labels),
                  "type": typ, "parent": U % parent if parent else None, "blockedBy": [U % b for b in blocked],
                  "target": target})
TD, IP, RV = "待辦", "進行中 (7 天一輪)", "審核後請關閉"
S, H = "by:swarm", "by:human"
# EPIC 101：進行中，有硬期限，混合人／swarm，一張卡住
tgt = (NOW + D.timedelta(days=41)).strftime("%Y-%m-%d")
add(101, "[EPIC] 2027 黑客松報名系統", IP, 9, ["epic"], typ="Epic", target=tgt, who="alice")
add(111, "報名表欄位與資料表設計", IP, 15, [S, "model:cheap"], 101, closed=8)
add(112, "報名 API（建立／查詢）", IP, 3, [S, "model:cheap"], 101, [111])
add(113, "報名頁前端", TD, 5, [S, "model:cheap"], 101, [112, 114])
add(114, "確認個資同意書文字", TD, 12, [H], 101, who="alice")
add(115, "報名成功通知信", TD, 6, [S, "model:free"], 101, [112, 114])
add(116, "後台匯出 CSV", IP, 11, [S, "model:cheap", "swarm:stuck"], 101, [111])
add(117, "主辦方報表頁", TD, 4, [S, "model:frontier"], 101, [116])
add(118, "上線前壓力測試", TD, 2, [S, "model:cheap"], 101, [113, 115, 117])
add(119, "與合作大學確認場地", RV, 9, [H], 101, closed=3, who="bob")
# EPIC 102：子 issue 全過，等人驗收
add(102, "[EPIC] 官網視覺移植回正式站", RV, 2, ["epic", "epic:review"], typ="Epic", who="alice")
for i, t in enumerate(["FAQ 頁移植", "聯絡頁移植", "消息列表移植", "首頁移植"]):
    add(121 + i, t, IP, 12 - i * 2, [S, "model:cheap"], 102, [120 + i] if i else [], closed=8 - i * 2)
# EPIC 103：卡在人
add(103, "[EPIC] 雲林知識庫 FAQ 匯入", TD, 20, ["epic"], typ="Epic", who="carol")
add(131, "提供 FAQ 原始檔（Excel）", TD, 20, [H], 103, who="carol")
add(132, "FAQ 轉檔與匯入", TD, 20, [S, "model:cheap"], 103, [131])
add(133, "匯入後抽查 20 題", TD, 20, [H], 103, [132], who="bob")
# 零散 issue
for n, t, col, d in [(41, "縣府月報資料整理", TD, 35), (42, "官網 SSL 憑證到期檢查", IP, 9), (43, "合約用印", RV, 2),
                     (44, "社群貼文排程", TD, 3), (45, "會議記錄上傳", IP, 2), (46, "舊站備份清理", TD, 10),
                     (47, "請款單送出", RV, 10), (48, "名片印製", TD, 1)]:
    add(n, t, col, d)
d = {"num": 0, "demo": True, "cols": [TD, IP, RV, "完成"], "title": "示範：EPIC 星雲（假資料）",
     "url": "https://github.com/orgs/acme/projects", "fetched": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items}
out = sys.argv[1] if len(sys.argv) > 1 else "demo-epic.js"
open(out, "w", encoding="utf-8").write("window.PROJECTS=window.PROJECTS||{};window.PROJECTS.demo=%s;\n" % json.dumps(d, ensure_ascii=False))
print("wrote", out, len(items), "items")
