# EPIC 結案摘要與每月趨勢報告（#455）

讓人持續檢討自己運用 swarm 的能力（Lani 2026-10-18）：每個 EPIC 結案時留一份**結案摘要**，每月發一份**趨勢報告**。

| 東西 | 在哪 |
|---|---|
| 執行紀錄 `<!-- swarm-run … -->` | orchestrator 每次執行結束的 EPIC 總結留言（`swarm/epiclib/swarm.py`） |
| 結案摘要 `<!-- swarm-epic-summary … -->` | `scheduler/epic_summary.py`（邏輯在 `swarm/epiclib/summary.py`） |
| 每月報告 | `scheduler/epic_report.py` |
| 自動觸發 | `.github/workflows/epic-summary.yml` |
| 測試 | `swarm/tests/test_summary.py` |

原則：**數字只從 GitHub 上看得到的東西來，缺的寫 `null`（表格裡是 —），不猜。**

## 1. 執行紀錄（swarm-run）

orchestrator（`epic_run.py --swarm`）每次執行結束，在 EPIC 的「## 蜂群這一輪結束」留言最後附：

```
<!-- swarm-run
mode: swarm            # swarm／single
engine: claude
model: claude-opus-5-5
seconds: 284.1
agents: 5              # 總共派出幾隻（含重試）
peak: 2                # 同時最多幾隻
input_tokens: 1457151  # CLI 沒回報 → null（不寫 0）
output_tokens: 38244
cost_usd: 2.235209
nudges: 0
retries: 0
conflicts: 0
passed: 5
stuck: 0
pr: https://github.com/acme/swarm/pull/42   # 沒開 PR → null
at: '2026-10-18T09:35:56+00:00'
-->
```

## 2. 結案摘要

```
python3 swarm/epic_summary.py --repo acme/swarm --epic 436 [--dry-run]
```

讀 EPIC、sub-issue、所有留言，在 EPIC 留一則「## 結案摘要」：人看的表格＋`<!-- swarm-epic-summary … -->`（YAML）。
**同一個 EPIC 重跑時更新同一則**（找帶這個標記的留言編輯），不重複留；內容沒變就不動。

YAML（`version: 1`）：

| 欄位 | 意思 / 怎麼算 |
|---|---|
| `epic`、`title` | EPIC 編號、標題 |
| `opened`、`go`、`closed` | 開立、放行（最後一次加 `swarm:go`）、結案時間 |
| `days` | 放行到結案的天數（沒有放行或還沒結案 → null） |
| `subs.total` | sub-issue 張數 |
| `subs.swarm`／`subs.human` | `by:swarm`／`by:human`（沒 label 就看內文「由誰做：」） |
| `subs.passed` | 已關閉、且有 runner 判準「全部通過」留言的張數 |
| `subs.first_pass` | swarm 的張裡，第一次判準就通過的張數（月報的一次通過率） |
| `subs.stuck` | swarm 的張裡，曾經卡住的（現在有 `swarm:stuck`，或 runner 留過「標 `swarm:stuck`」） |
| `subs.human_closed` | 已關閉、但沒有 runner 判準通過紀錄的張數（人關的，含人做的張） |
| `runs[]` | 每次執行一筆（欄位同 swarm-run，另有 `source`：`swarm-run` 或從舊表格補的 `table`） |
| `totals` | `runs` 加總（`peak` 取最大）；**任何一次執行缺這個值就是 null**——部分加總會低估 |
| `criteria_defects` | EPIC 與 sub-issue 留言裡有行首 `criteria-defect：` 標記的則數（格式見下） |
| `human_interventions` | swarm 的張裡需要人出手的張數（曾卡住，或由人關閉） |
| `prs[]` | 執行紀錄裡的 PR＋分支 `epic/<N>` 的 PR |

### 判準寫錯標記

發現判準本身寫錯時，在那張 issue 留言**另起一行**，以 `criteria-defect：`（全形冒號）或 `criteria-defect:`（半形冒號）開頭，例：

```
驗收時發現：
criteria-defect：搜尋條件會誤中其他 issue
```

規則：

- 只看**行首**：標記前面只能有空白、`-`、`*`、`>`（清單項目、引用都可以），例如 `- criteria-defect: 路徑寫錯`、`> criteria-defect：…`。
- 字後面要緊接冒號（中間可有空白）；大小寫不拘。
- **句中提到不算**：`判準寫錯（criteria-defect）：#448、#451`、`` `criteria-defect` ``、`這不是 criteria-defect：…` 都是 0。
- 一則留言最多算一次（同一則寫兩行標記仍算 1）。
- 機器留的摘要（`<!-- swarm-epic-summary`）與執行紀錄（`<!-- swarm-run`）不算。

### 補記（--backfill）

```
python3 swarm/epic_summary.py --repo acme/swarm --backfill
```

對已關閉的 EPIC（#390、#402、#430、#436，以及之後關的）補摘要；不發 Discord。#455 之前的執行沒有 swarm-run，
就從「## 蜂群這一輪結束（orchestrator）」表格（時間、agent 數、峰值、用量…）與「## 這一輪結束（runner）」
（只數得出通過／卡住）盡量補；舊表格的用量 0 視為沒回報，寫 null。

## 3. 自動觸發與 Discord

`.github/workflows/epic-summary.yml`：

- **EPIC（`epic` label）被關閉** → `epic_summary.py --epic N --discord-channel <channel-id>`：
  留摘要後發一則簡短通知到 Discord `#swarm`（EPIC 名稱、張數、通過／卡住、agent 數、用量、連結）。
- **每月 1 日 01:00 UTC** → `epic_report.py --month <上個月> --issue <report-issue>`：月報留言到固定 issue #512
  （同月份重跑更新同一則）；有設 `SWARM_DISCORD_TOKEN` 才另外帶 `--discord-channel` 發到 `#swarm`。
- **手動（workflow_dispatch）**：填 `epic` 重跑一個；勾 `backfill` 補記；填 `month` 只跑月報。

Discord bot token：repo secret **`SWARM_DISCORD_TOKEN`**（由人設定）。沒設時摘要照留，只是不發通知（log 有警告）。

## 4. 每月報告

```
python3 swarm/epic_report.py --repo acme/swarm --month 2026-10 [--json] [--discord-channel ID] [--issue N]
```

`--issue N`：把月報留言到 issue N，留言末尾帶 `<!-- swarm-monthly-report month=YYYY-MM -->`。該 issue 已有**同月份**
標記的留言就更新那一則（PATCH），不同月份新增一則；沒給 `--issue` 只印出、不寫 GitHub。

彙整該月（以 EPIC 結案日期算）所有結案摘要（讀 EPIC 上的 swarm-epic-summary；還沒有的現場算、不寫回），和上個月比：

| 指標 | 算法 |
|---|---|
| 一次通過率 | Σ`subs.first_pass` ／ Σ`subs.swarm` |
| 卡住率 | Σ`subs.stuck` ／ Σ`subs.swarm` |
| 判準寫錯 | Σ`criteria_defects` |
| 人工介入 | Σ`human_interventions` |
| 交給 swarm 的比例 | Σ`subs.swarm` ／ Σ`subs.total` |
| 每張平均 agent 數 | Σ`totals.agents` ／ Σ`subs.swarm`（只算 agents 不是 null 的 EPIC） |
| 每張平均用量 | tokens（輸入＋輸出）、費用，同上只算有回報的 EPIC |
| 放行到結案天數 | `days` 的平均 |

上個月沒有結案的 EPIC 時，上月的值都是 null，差值也是 null（不和 0 比）。
