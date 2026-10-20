---
name: epic
description: 帶人把一件事開成可交給 swarm 蜂群的 EPIC（含 sub-issue 與可執行判準）；站台的分析對話用。template 與 lint 都在這個 skill 的目錄裡。
---

# 開 EPIC

LLM 帶人開 EPIC 的導航步驟。照順序走；任何一步卡住就停在那一步，不要跳過去開卡。

用到的檔案都在這個 skill 的目錄裡（下面寫 `<skill>`，也就是這份 SKILL.md 所在的目錄，例如 `$DSH_HOME/skills/epic`）：

- `<skill>/templates/epic.md` — EPIC 範本（目標、範圍、整體驗收、硬期限、背景與參考）
- `<skill>/templates/sub-issue.md` — sub-issue 範本（由誰做、要做什麼、可以修改的範圍、依賴、驗收判準）
- `<skill>/lint/epic_lint.py` — 離線檢查程式（只吃 `--file`，不需要 GitHub）；不通過的卡開出去會被標 `needs-criteria`，蜂不接
- 判準的六條規則：https://github.com/towNingtek/swarm/blob/main/docs/swarm/docs/issue-acceptance.md ；流程契約：https://github.com/towNingtek/swarm/blob/main/docs/swarm/docs/epic-contract.md 、https://github.com/towNingtek/swarm/blob/main/docs/swarm/docs/orchestrator.md

## 1. 意圖

先問人兩件事，再做別的：

1. **目標**：做完之後，人會看到什麼改變？寫結果，不寫做法（對應 `<skill>/templates/epic.md` 的「目標」）。
2. **怎樣算做完**：用什麼指令或觀察能確認？（對應「整體驗收」與每張 sub-issue 的判準）

寫不出可驗證的完成條件，就是還不知道要什麼 → **先探索、不開卡**。探索的產出是「能寫出完成條件」，
不是程式碼。不要為了推進流程而替人編一個完成條件。

## 2. 規模判斷

| 判斷 | 依據 |
|------|------|
| **EPIC** | 需要 2 張以上的 sub-issue；或跨模組（改到彼此獨立的目錄／服務）；或一天做不完、要跨天 |
| **單張 issue** | 一張就做得完、只動一個模組、當天可收；直接用 `<skill>/templates/sub-issue.md`，不必包 EPIC |
| **先探索** | 完成條件寫不出來；或不知道要改哪裡（「可以修改的範圍」填不出來）；或要先量測現況才知道問題在哪 |

拿不準時往小的方向判：能用單張解決就不要開 EPIC；連單張的判準都寫不出就是先探索。

## 3. 聚合

開新卡前先查既有 issue（`gh issue list --search ...`），避免重複：

- **縱向**：同一個目標底下已經散落的 issue（同一功能的前後步驟、同一個 bug 的不同症狀）→ 合成一個 EPIC，
  把它們掛成 sub-issue，而不是再開新的。
- **橫向**：跨 project／repo 出現的共同需求（例如兩個站台都要同一種金鑰路徑、同一個 CLI 參數）→
  抽成一個共用的 EPIC 或 sub-issue，在各 project 的 issue 裡連過去，不要各做一份。

把找到的關聯寫進 EPIC 的「背景與參考」。

## 4. 拆 sub-issue

每張 sub-issue 照 `<skill>/templates/sub-issue.md` 填。拆的規則：

- 每張的「可以修改的範圍」**不重疊**，才能平行交給不同的蜂。
- 範圍**有重疊**（同一個檔案、或一個 glob 涵蓋另一個）→ 必須設依賴：在「依賴」寫被誰擋住，並在 GitHub 用 blocked by 標。
- 「由誰做」只寫 `swarm` 或 `人`。要人判斷、要人授權（登入、金鑰、主機操作）的就是 `人`。
- 一張只做一件事；判準要能區分「全做完」與「做了一部分」。

反例（#537，2026-10-20）：四張蜂卡都改 `wolffia/v8.html` 與 `wolffia/tests/run-all.sh`，只設了「#540、#541 依賴 #539」，
於是 #538／#539 同時跑、#540／#541 同時跑。四張判準全過，但 #539、#541 合併進 EPIC 分支時衝突，被標 `swarm:stuck` 等人解。
較好：同一個檔案的卡排成一條鏈（#538 → #539 → #540 → #541），或把共用檔案的改動集中在一張卡；
寧可慢一點，也不要讓蜂卡在合併。拆完後逐一比對每兩張的「可以修改的範圍」，有交集就一定要有依賴。

## 5. 判準先實測

每條判準都要**先實際跑一次**：

- 「❌ 還沒好」那張表的「目前」欄，填今天實際執行的結果（日期寫實測日），不是預想的結果。
  現況跑出來不是「還沒好」，表示問題不在你以為的地方 → 回到第 1 步。
- 「✅ 修好了」的指令也要在現況跑一次，確認它現在**會失敗**（失敗時不可能出現預期結果）。
- 檢查**行為**，不綁**實作字串**。判準要在「用別種正確做法完成」時也成立。

反例（今天的實際案例）：

- **#476**（站台 `gh` 補 project 權限）：判準寫 `gh auth status 2>&1 | grep -q "'project'"`（含單引號），
  但 `gh auth status` 印出的權限沒有引號（`Token scopes: gist, project, ...`）。權限其實已經有了，判準照原文跑卻是 exit 1，
  只能記成 criteria-defect。原因：沒先在「做完」的狀態下實測輸出格式，又去比對輸出的字面。
  較好：檢查行為本身，例如實際查一次 project 欄位會成功。
- **#448**（bubble-wall-log 的 HackMD token 路徑改到平台金鑰目錄）：判準 1 寫
  `! grep -q '/opt/old-swarm/' /etc/systemd/system/bubble-wall-log.service`，綁的是 unit 檔裡的字串：
  改用 drop-in、環境變數檔或改檔名，舊字串都可能還在或根本不在這個檔案；檔案不存在時 `! grep` 也會「通過」。
  同一張的判準 3（重啟後 log 檔有在更新）才是在檢查行為。

## 6. lint

草稿寫成檔案，開卡前先離線檢查：

```
python3 <skill>/lint/epic_lint.py --file <草稿> --labels by:swarm
```

（`--labels` 依「由誰做」填 `by:swarm` 或 `by:human`。）exit 0 才往下走；不通過就照輸出修，再跑一次。
EPIC 本身也用 `--file` 檢查（`--labels epic`）。開好之後，repo 裡的 swarm 會再用同一套規則檢查 EPIC 與它所有 sub-issue。

## 7. 放行

把 EPIC 與 sub-issue 給人看，**由人放行**：人在 EPIC 加 `swarm:go`，判準在這一刻凍結。LLM 不自己加 `swarm:go`。

`swarm:auto-merge`（人放行時另外加在 EPIC 上）適合低風險的 EPIC：

- 每張 sub-issue 都是 `swarm` 做、判準全是指令且能完整代表「做完」；
- 改動都落在各張的「可以修改的範圍」內，沒有碰 `.github/` 或 `scheduler/model-policy.yaml`；
- 合錯了容易回復（文件、測試、獨立的小模組）。

不適合：有 `人` 的 sub-issue、動到主機或金鑰、改規則或權限、整體驗收要人親自看才知道對不對的。
就算自動合併，EPIC 仍加 `epic:review`，由人驗收結案。條件細節見 https://github.com/towNingtek/swarm/blob/main/docs/swarm/docs/orchestrator.md 的「低風險 EPIC 自動合併」。
