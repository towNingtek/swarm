# Wolffia ↔ swarm 的 issue 契約

> 版本：2026-10-10 定稿（#391，使用者確認「內容都正確」）。
> 來源：`discussion.md`「Wolffia ↔ swarm 的 issue 契約」一節。這份文件只寫**現行**規則，已作廢的寫法列在最後的附錄。
> swarm 的 prompt、runner（`scheduler/epic_run.py`）、Wolffia 都以這份為準。條文衝突時，以這份為準；這份沒寫到的，就是還沒決定，要停下來問人。

## 0. 分工

- **Wolffia：** 規劃與圖。看 GitHub Projects，拆 EPIC 和 sub-issue，畫出依賴，提供「啟動」按鈕。
- **swarm：** 執行的 loop。DSH 的 agent 做事，runner（一般程式）判定結果。
- **人：** 放行、啟動、做 `by:human` 的工作、處理卡住的 issue、結案 EPIC。

圖記在 GitHub 上，不另存一份。

## 1. 結構

| 東西 | 怎麼記 |
|---|---|
| EPIC | issue type `Epic`，**加上** label `epic`（兩個都要） |
| EPIC → 子工作 | GitHub 原生 sub-issue（`addSubIssue`） |
| 依賴 | GitHub 原生 `blockedBy`（`addBlockedBy`） |
| 硬期限 | 只在 EPIC 上，而且只有外部硬期限才填，用 project 的 `Target date`。sub-issue 不填日期 |

進度不靠估日期，看「最長的未完成依賴鏈上，還有幾張 `by:human`」。

## 2. Label

| label | 意思 | 誰加 |
|---|---|---|
| `epic` | 這是 EPIC | 人 |
| `by:swarm` | 由 swarm 做 | 人 |
| `by:human` | 由人做 | 人 |
| `swarm:go` | 人已放行這個 EPIC；判準在這一刻凍結 | 人（只加在 EPIC 上） |
| `swarm:stuck` | swarm 試 3 次沒通過，等人處理 | runner |
| `epic:review` | 子 issue 全部通過判準，等人驗收結案 | runner |
| `needs-criteria` | 缺判準，不可實作 | 任何人 |

每張 sub-issue 必須剛好有 `by:swarm` 或 `by:human` 其中一個。沒標的不會被當成任何一種，swarm 不接。

**模型不標在 issue 上。** 模型在啟動時選（見第 5 節）。

## 3. 判準（KPI）——整個設計的根本

> 2025 年，swarm 的 agent 能力不到兩個月就被改回手動，原因是開 issue、關 issue 都變成「LLM 主觀判斷」。使用者判斷 2026 年這個問題依舊存在。下面每一條都是為了防止這件事。

1. **格式：** 沿用 `docs/issue-acceptance.md` 的 `## 驗收判準`，分「✅ 修好了」和「❌ 還沒好」兩表，六條規則照舊。
2. **沒有判準就不做：** 沒有 `## 驗收判準`，或標了 `needs-criteria`，swarm 不接。
3. **`by:swarm` 的判準全是指令：**
   - 「✅ 修好了」表的每一列，「怎麼驗」欄都必須是一條指令（反引號包住）。
   - 只看 exit code：0 ＝ 通過。
   - 比對邏輯要寫進指令本身（例如 `jq -e`、`grep -q`、`test`）。「預期」欄只是給人看的說明，runner 不讀。
   - 只要有一列不是指令，這張就不能是 `by:swarm`，要改標 `by:human`。
4. **指令照原文執行，不限格式**（使用者選 A）。這等於接受一個前提：能編輯這些 issue 的人和他們的帳號都可信。有新協作者或外部貢獻者加入時，要重新評估。
5. **agent 不能關 issue，也不能宣告完成：**
   - 由 runner 執行判準，把每一列的輸出貼到 issue。
   - 全部通過才由 runner 關閉。
   - agent 說「做完了」不算數。
6. **判準在放行時凍結：**
   - 凍結時間 ＝ EPIC 被加上 `swarm:go` 的時間（以 GitHub timeline 的 LabeledEvent 為準）。
   - 如果某張 sub-issue 的內文在這之後被改過，runner 就不接這張，留言說明，等人重新放行（拿掉再加回 `swarm:go`）。
   - 放行之後才新增的 sub-issue，也要等重新放行才算凍結。
7. **runner 提供的環境變數**：判準指令可以使用下列變數。

   | 變數 | 值 |
   |---|---|
   | `$GO` | 凍結時間點（加上 `swarm:go` 當下）EPIC branch `epic/<EPIC>` 的 commit；那時還沒有這條 branch 的話，就用預設分支的 commit |
   | `$REPO` | `owner/name` |
   | `$ISSUE` | 這張 sub-issue 的編號 |
   | `$EPIC` | EPIC 的編號 |

## 4. 失敗與卡住

- **失敗：** 「✅ 修好了」表中有任何一列 exit code 不是 0。
- **重試：** 同一張最多試 3 次。
  - 每次都把失敗輸出貼到留言。
  - 第 2、3 次的 prompt 附上前一次的失敗輸出。
- **卡住：** 3 次都失敗，就標 `swarm:stuck`，停在這張。
  - 不改成 `by:human`。
  - 不自動換模型；要不要換、換哪個，由人在下一輪啟動時決定。
- **判準有錯：** agent 認為判準本身有錯時，不能修改判準，要停下來留言說明，並標 `swarm:stuck`。
- **測試由人寫：** 判準用到的測試檔，在放行前由人寫好、審過、進 master。agent 不得修改，判準裡要有一列檢查測試沒被改過，例如 `git diff --quiet $GO -- <測試目錄>`。

## 5. 觸發：一律手動

- **一次只處理一個 EPIC、只跑一輪：** 人在 Wolffia 按「啟動」或下指令，指定一個 EPIC，**同時選模型**。
  - 可選：Claude 或 codex gpt-5.6-luna，都走 DSH 訂閱外掛。
  - 沒選模型就拒絕啟動，不猜預設值。
  - 這一輪用了哪個模型，寫進 EPIC 的留言。
- **可以接的 sub-issue：** 同時符合下列條件才接；任一條不符就跳過，並記下原因。
  - 狀態是 open。
  - 標了 `by:swarm`。
  - 所屬 EPIC 有 `swarm:go`。
  - 沒有 `swarm:stuck`。
  - `blockedBy` 全部已關閉。
  - 判準存在、全是指令，而且放行後沒被改過。
- **何時停：** 做到沒有可接的 sub-issue，就停下來回報。
- **同時只能跑一輪：** 同一個 EPIC 不能同時跑兩輪。
- **swarm 不會自己掃描 `swarm:go`，也不會自己啟動任何東西。**

## 6. EPIC 由人結案

- 子 issue 全部關閉、判準都通過，是**必要條件，不是充分條件**：每一塊都過了，整體目標不一定達成。
- runner 驗完後在 EPIC 留言，附上每張子 issue 的判準結果，並加 `epic:review`。
- 之後由人決定：關閉、補新的 sub-issue，或判定目標本身要改。
- **runner 永遠不關 EPIC。**

## 7. cron：只看、只通知

cron 定時讀 GitHub，只發 Discord 通知，不啟動任何東西。在下列情況通知：

1. EPIC 標上 `epic:review`：可以驗收了。
2. EPIC 裡有 `swarm:stuck`，而且它擋住了後續的 sub-issue：附上卡在哪裡、擋住哪些。

狀態第一次出現時通知一次；沒人處理的話，每天提醒一次。

## 8. Wolffia 怎麼畫（摘要）

- **主星系：** 只畫 EPIC 和零散 issue。sub-issue 只在 EPIC 的彈出面板裡出現。
- **EPIC 的樣子：**
  - 大小是一般 issue 的 2 到 3.5 倍。
  - 顏色和一般 issue 一樣，代表停留天數。
  - 外圈綠弧代表完成比例。
- **★ 的意思：** 「我現在該處理什麼」，依序挑：
  1. 有 `epic:review` 的 EPIC。
  2. 擋住後續的 `swarm:stuck`。
  3. 擋住後續的 `by:human`。
  4. 超過停留上限的零散 issue。

細節見 `discussion.md` 的 v8 一節，實作在 `v8.html`。

## 9. 改動放哪裡、判準在哪裡跑（2026-10-10 定案，使用者授權執行者決定）

- **改動進 branch，由人合併。** 每個 EPIC 一條 branch `epic/<EPIC 編號>`，從預設分支開出來（人寫的測試先 commit 在這條 branch 上，再放行），放在獨立的 git worktree，agent 只在那裡工作。
  - 一輪結束時，runner 推送 branch，並開（或更新）一個 PR 指向預設分支。
  - **合併由人做**，跟 EPIC 由人結案是同一個關卡。
- **判準在那個 worktree 裡跑**，cwd 是 repo 根目錄。`$GO` 是放行當下 EPIC branch 的 commit（見第 3 節第 7 條），所以 `git diff --quiet $GO -- <測試目錄>` 檢查的是「從放行到現在，這條 branch 有沒有動過測試」。
- **固定的執行環境：** venv `~/.venvs/swarm-sched`，內容和 `scheduler/Dockerfile` 一致：`scheduler/requirements.txt` 加上 `PyJWT cryptography requests`。runner 執行判準時，把這個 venv 的 `bin` 放在 `PATH` 最前面，所以判準裡的 `python3` 就是它。
  - 2026-10-10 實測：在這個環境裡，`test_runner_line test_runner_runlog test_run_issue` 全部 OK；在主機的系統 python 上，則缺 `httpx`、`requests`。

## 10. 還沒決定（遇到時停下來問人）

1. `by:human` 在等人時，要不要通知 Discord。

## 附錄：已作廢的寫法（只留紀錄）

| 作廢 | 改成 | 時間 |
|---|---|---|
| `## 驗收` 段落，加上 `check`／`human` 勾選框 | 第 3 節，沿用 `## 驗收判準` | 2026-10-10 |
| EPIC 有 `swarm:go` 就自動接手 | 第 5 節，一律手動啟動 | 2026-10-10 |
| `model:frontier`／`cheap`／`free` label | 第 5 節，啟動時選模型 | 2026-10-10 |
| 失敗 3 次後自動升級模型，附檢討 | 第 4 節，標 `swarm:stuck`，由人決定 | 2026-10-10 |
| label `epic:ready` | `epic:review` | 2026-10-10 |
| check 指令是否要限制格式（白名單） | 不限格式（第 3 節第 4 條） | 2026-10-10 |
