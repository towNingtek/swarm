# Swarm 辦公室守則 — 起始點

> 這是你的 AI 辦公室。每間公司（hive）有自己的規則與紀錄；各個角色（pm、rd…）在你開的 session 裡替你工作，排程則由平台照時間自動執行。
> 每次會話自動載入的重點在 `AGENTS.md`；本檔是完整守則。

## 導航（先讀這裡）

開始任何工作前，依序：

1. 確認這次處理哪一間公司（**hive**）。使用者沒說、而 `hives/` 裡不只一間時，先問；不要猜。
2. 讀 `hives/{hive-id}/HIVE.md`（公司規則：禁止觸碰、操作範圍）。
3. 讀 `.swarm/agents/{角色}/SOUL.md`（共用身份）＋ `hives/{hive-id}/agents/{角色}/SOUL.md`（這間公司的操作範圍）。
4. 讀 `hives/{hive-id}/agents/{角色}/WORKING.md`（上次做到哪）。
5. 執行工作。

**不要跨 hive 載入**：一次只讀、只改一間公司的資料。

`hives/` 還是空的 → 這是新辦公室，請看 `getting-started/README.md`，用 `hive-new` 建立第一間公司。

## 目錄

| 位置 | 用途 |
|---|---|
| `.swarm/agents/{角色}/SOUL.md` | 各角色的共用身份與能力 |
| `.swarm/registry.yaml` | 辦公室裡有哪些公司、啟用哪些角色、各角色的模型與排程時間（格式見 `AGENTS.md`「排程」） |
| `.swarm/platform-status.md` | 平台自動更新的狀態：模型、起步額度、排程與最近執行結果（唯讀） |
| `.dsh/skills/` | 辦公室共用的 skill（hive-new、hive-list、hive-remove、office-setup、connect-github、connect-discord） |
| `.swarm/*.mjs` | 小工具：`check-schedules`（排程檢查）、`check-office`（任務與整合總覽）、`office-setup`（開立開辦任務）、`set-key`（使用者放金鑰）、`key-test`、`notify`（Discord 通知）、`git-credential` |
| `hives/{id}/HIVE.md` | 公司規則 |
| `hives/{id}/project.yaml` | 公司設定（SCM、通知、排程任務內容 `reports:`） |
| `.swarm/keys/shared/` | 整個辦公室共用的 token（`github.token`、`discord-webhook.url`、`hackmd.token`），不進版控、不貼進對話 |
| `hives/{id}/.keys/` | 這間公司專屬的 token（同樣檔名，有的話優先用），不進版控、不貼進對話 |
| `hives/{id}/agents/{角色}/` | 這間公司裡該角色的 SOUL.md 與 WORKING.md |
| `hives/{id}/issues/{角色}/` | 本地任務紀錄 |

## 角色

pm 是統一入口：接到需求先由 pm 整理成任務，再依內容換上對應能力。

| 角色 | 做什麼 |
|---|---|
| pm | 整理需求、拆任務、追進度、彙整報告 |
| rd | 寫程式、跑測試、開 PR |
| reviewer | 檢查成果的正確性與風險 |
| tech-writer | 寫文件、SOP、說明 |
| devops | 部署與服務健康（需明確授權） |
| marketing | 對外內容草稿（預設不發布） |
| bd | 商務需求與合作脈絡整理 |

## 工作流程

1. 讀導航。
2. 執行工作，只操作該公司的資源。
3. 寫任務紀錄：`hives/{id}/issues/{角色}/{編號}-{描述}.md`（例：`006-website-copy.md`），第二行 `狀態：待辦／進行中／完成／擱置`。
   新公司先有 5 件開辦任務（`office-setup`），完成後這間公司就能無人值守地運作。
4. 更新 `hives/{id}/agents/{角色}/WORKING.md`：只寫「目前狀態＋最近工作」，約 100 行內；舊的移到同目錄 `WORKING-archive.md`。

## 鐵則：有成果必有任務，有任務必有判準

- 每個成果都要有一份任務紀錄；沒有紀錄＝沒有發生過。
- 每份任務都要寫出「怎樣算完成」與「怎樣算還沒好」，而且要能實際檢查。
- 完成時把檢查結果貼進紀錄。

## 邊界

- 沒有使用者明確同意，不刪除資料、不對外發布、不寄信、不花錢、不部署。
- 密鑰只放在 `.swarm/keys/shared/`（共用）或 `hives/{id}/.keys/`（公司專屬），由使用者用 `node .swarm/set-key.mjs` 自己放；不寫進對話、HIVE.md、project.yaml 或任務紀錄，也不印出內容。
- 有接 Git 時：不直接 push `main/master`，走分支與 PR；commit 格式 `[{角色}] {type}: {描述}`。
- 排程由平台照台北時間自動執行（registry 的 `enabled: true` 才會跑）；設定方式見 `AGENTS.md`「排程」。

## 語言

預設使用繁體中文與使用者溝通；程式碼、指令與檔名保持原文。
