# 蜂群 orchestrator

> #416（EPIC #412）。設計討論：`wolffia/discussion.md`（2026-10-17）。契約：`wolffia/contract.md`、判準：`docs/issue-acceptance.md`。

一般使用時，模型自己就會拆步驟、開子代理。orchestrator 補的是「人不在場」時的四件事：
**誰判定做完（判準，不信模型）**、**停下來問人怎麼辦（推一把）**、**同時派幾隻（廣度）**、**成果怎麼收（一個總 PR）**。

## 怎麼跑

```bash
# 人先在 EPIC 加 swarm:go（判準在這一刻凍結）
python3 swarm/epic_run.py --repo acme/swarm --epic 412 \
    --swarm --engine claude --max-bees 2
```

- `--engine`：`claude`（官方 Claude Code CLI）、`codex`（官方 Codex CLI）、`opencode`（免費模型、NVIDIA）。
- `--model`：可省略（用各 CLI 預設）；`opencode` 例：`--model opencode/big-pickle`。
- `--no-push`：不推送、不開 PR（試跑用）。
- 原本一次一張的模式不變（不加 `--swarm`）。

### 放行後自動開工（#474）

```bash
*/10 * * * *  python3 swarm/epic_autostart.py --repo acme/swarm --engine claude [--max-bees 2] [--dry-run]
```

- 對每個 open、帶 `swarm:go` 的 EPIC，用 `epiclib.takeable.pick` 看有沒有可接的 sub-issue。
- 有可接的、而且沒在跑，就在背景啟動 `epic_run.py --repo R --epic N --swarm --engine E`（`start_new_session`），
  輸出寫到 `~/.local/share/swarm-epic/autostart/epic<N>.log`，立刻返回不等它。
- 「在跑」用 `epiclib/round` 同一把檔案鎖判斷（`~/.local/share/swarm-epic/locks/epic-<N>.lock`，拿不到＝在跑）。
  autostart 拿到鎖後把 fd 交給子行程繼承：orchestrator 活著鎖就被拿著，結束時自動釋放，不會重複啟動。
- 每次在 stdout 印一行摘要：檢查了幾個 EPIC、啟動了哪些、略過哪些與原因。`--dry-run` 只印不啟動。

## 它做什麼

1. 讀 EPIC，挑「可接」的 sub-issue（`epiclib/takeable.py`：by:swarm、判準合格、依賴都關了、判準沒在放行後被改）。
2. **廣度**：同時最多 `min(max_bees, per_engine[engine])` 隻；一隻做完再補下一隻；被擋住的等依賴關閉。
3. 每隻蜂在自己的分支 `epic/<N>-bee-<issue>`（獨立 worktree）工作。
   開工時替那張 sub-issue 加 `swarm:running` 並留言 `🐝 開工：引擎 <engine>，<開始時間 ISO>`；這張結束（通過、卡住、出錯）一律移除 `swarm:running`（finally）。Wolffia 靠這個 label 顯示蜂群正在做哪一張（#457）。
   `--single` 模式對整批 sub-issue 同樣在開工時加上、結束時移除。
4. **深度**：蜂的回覆若是停下等人（結尾問句、「要不要繼續」「已完成第 N 階段」「時間晚了」…，見 `DEFAULT_STOP_PATTERNS`），
   在同一個 session 推一把，最多 `max_nudges` 次；每次嘗試最多 `max_minutes` 分鐘。
5. **驗收**：runner 照原文跑判準（`epiclib/judge.py`，只看 exit code），蜂怎麼說都不算。沒過就把輸出交給下一次嘗試；
   `max_tries` 次都沒過標 `swarm:stuck`。
6. **收成果**：判準過了才把蜂的分支合進 `epic/<N>`；合併衝突就標 `swarm:stuck`、不硬合（成果留在蜂的分支）。
7. 全部 sub-issue 關閉 → 開**一個總 PR**（含 benchmark 表）→ EPIC 加 `epic:review` → 人驗收、合併、結案。

### 低風險 EPIC 自動合併（#465，`swarm:auto-merge`）

人放行時可以在 **EPIC 上**另外加 `swarm:auto-merge`（sub-issue 上的不看）。開總 PR 時：

- **沒有** `swarm:auto-merge`：照舊開總 PR、加 `epic:review`，等人合併。
- **有** `swarm:auto-merge`，而且下面四條全部成立 → 總 PR 開好立刻合併（merge commit，`gh pr merge --merge`），
  在總 PR 與 EPIC 留言寫明「自動合併」與理由：
  1. 每張 sub-issue 都由 swarm 做完並通過判準（`by:swarm`、已關閉、沒有 `swarm:stuck`）；
  2. 總 PR 改到的每個檔案（`git diff --name-only <base>...epic/<N>`，改名的新舊路徑都算），
     都落在某張 sub-issue 的「可以修改的範圍」裡（`epiclib/lint.scope_paths`，glob 比對；`dir/`、`dir/**` 涵蓋整個目錄）；
  3. 總 PR 沒有改到 `.github/` 與 `swarm/model-policy.yaml`（就算寫在範圍裡也不行：不准自己放寬規則）；
  4. GitHub 回報 PR 可合併（`mergeable == MERGEABLE`；`CONFLICTING` 或一直算不出來都不合）。
- 任一條不成立（或合併指令失敗）：不合併，在 EPIC 留言「不自動合併」並列出原因，照舊等人。
- 不論有沒有自動合併，EPIC 都加 `epic:review`，由人驗收結案（入口與出口仍由人）。
- 結果記在 metrics（`auto_merged: true/false`、`auto_merge_reasons`）與 EPIC 總結留言的 benchmark 表。
- `--no-push` 不開總 PR，所以也不會自動合併。

## 設定

- `scheduler/swarm.yaml`：`max_bees`、`per_engine`、`max_minutes`、`max_nudges`、`max_tries`、`check_timeout`、`stop_patterns`。
- `swarm/model-policy.yaml`：無人值守可用的引擎與禁止的模型；不在清單內，**在任何 GitHub 寫入前**就拒絕。
  - Claude 訂閱只透過官方 `claude` CLI，由站台主人自己登入；禁止程式直接使用 Claude OAuth token。

## benchmark

每次執行把數據寫到 `~/.local/share/swarm-epic/swarm/metrics/epic<N>-<時間>-<engine>.json`，
並放進 EPIC 總結留言與總 PR：

| 指標 | 說明 |
|---|---|
| 時間 | 從開始到結束的秒數 |
| agent 個數 | 總共派出幾隻（含重試） |
| 峰值 | 同時最多幾隻 |
| 每張 | 每張 sub-issue 用了幾隻、嘗試幾次、推了幾次、花幾秒 |
| 推一把、重試、合併衝突 | 次數 |
| 判準通過／卡住 | 張數 |
| 自動合併 | `auto_merged: true/false` 與原因（`auto_merge_reasons`） |

總結留言最後附一段機器可讀的執行紀錄 `<!-- swarm-run … -->`（YAML：mode、engine、seconds、agents、peak、
input_tokens、output_tokens、cost_usd、nudges、retries、conflicts、passed、stuck、pr；CLI 沒回報的用量寫 null）。
EPIC 結案時 `scheduler/epic_summary.py` 彙整這些紀錄成「## 結案摘要」，每月再出趨勢報告——見 [epic-summary.md](epic-summary.md)（#455）。

A/B 比賽（#418）：A 組 `--swarm --single`（一個 session 一次做完整個 EPIC，之後逐張跑判準），B 組 `--swarm --max-bees 2`，同一份題目的兩個副本、同一個起點，比上表（含用量：tokens 與 CLI 回報的費用估計）。結果記在 `docs/benchmark/`。
