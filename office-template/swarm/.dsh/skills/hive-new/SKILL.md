---
name: hive-new
description: 在這個 swarm 辦公室新增一間公司（hive）：互動詢問基本資料，建立 hives/{id}/ 的規則、設定與各角色檔案，並登記到 .swarm/registry.yaml。
whenToUse: 使用者要建立第一間公司、新增 hive、開一個新專案或新客戶的工作區時。
---

# hive-new — 新增公司（hive）

一個 hive 代表一間公司或一個專案，有自己的規則、角色設定與工作紀錄。

## 流程

一次只問一到兩題，用使用者的話確認後再往下。全程使用繁體中文。

**Step 1　基本資料**
- Hive ID：英文小寫與連字號，當作目錄名（例：`my-shop`）。幫使用者從公司名稱建議一個。
- 顯示名稱：公司或專案的名字（例：「小明咖啡」）。
- 一句話說明：這間公司在做什麼、這個辦公室主要要幫忙什麼。

**Step 2　程式碼與任務追蹤（可跳過）**
- SCM：`github`、`gitlab` 或 `none`（預設 `none`：任務只記在本地 `hives/{id}/issues/`）。
- 選 github/gitlab 時再問組織與 repo 名稱。不要要求使用者在對話中貼 token；token 之後由開辦任務
  「接上 GitHub」帶他用 `node .swarm/set-key.mjs {id} github` 自己放。

**Step 3　要啟用的角色**
- 預設：`pm`（統一入口）、`rd`、`reviewer`、`tech-writer`。
- 可加：`devops`、`marketing`、`bd`。
- 各角色的共用說明在 `.swarm/agents/{角色}/SOUL.md`。

**Step 4　規則（HIVE.md）**
問使用者兩件事，寫進 HIVE.md：
- 有沒有「不能碰」的東西（例：正式資料、客戶個資、某個資料夾）。
- 有沒有固定的做事方式或語氣偏好。

## 執行動作

把收集到的內容給使用者確認一次，確認後才建立。

**用檔案寫入工具（write）逐一建立每個檔案**，不要用 shell 的 `echo`／`mkdir`／heredoc：
寫入工具會自動建立上層資料夾、換行正確，也不需要額外權限。空資料夾（`.keys/`、`issues/{角色}/`）
用放一個 `.gitkeep` 空檔的方式建立。

**每一個啟用的角色**都要建立自己的 `agents/{角色}/SOUL.md`、`agents/{角色}/WORKING.md`、
`issues/{角色}/.gitkeep`（例如啟用 4 個角色就是 4 組），不要只建 pm。

```
hives/{id}/
├── HIVE.md            # 開頭一行：「先讀 root `INITIAL.md`。」接著是公司簡介、禁止觸碰、操作範圍、偏好
├── project.yaml       # 見下方格式
├── .keys/.gitkeep     # 只放這間公司的 token，不進版控（.gitignore 已排除）
├── agents/{角色}/SOUL.md     # 以 .swarm/agents/{角色}/SOUL.md 為範本，補上這間公司的操作範圍與綽號
├── agents/{角色}/WORKING.md  # 第一行寫建立日期，其餘留白
└── issues/{角色}/.gitkeep    # 本地任務紀錄
```

`project.yaml`（經典 swarm 格式；沒有的欄位就省略，不要編造）：

```yaml
project:
  id: {id}
  name: {顯示名稱}
  description: {一句話說明}
scm:                       # 沒接就寫 type: none
  type: github             # github / gitlab / none
  org: {組織}
  repo: {repo}
notifications: none        # 接上 Discord 後改成 discord: true（見 connect-discord）
reports: {}                # 排程任務的內容定義（見 AGENTS.md「排程」）；還沒有排程就留空
```

並用編輯工具（edit）更新 `.swarm/registry.yaml`：把 `hives: []` 改成清單，或在既有清單後加一筆。
**格式要照檔案開頭的註解**（每個角色一個區塊，不是簡單清單）：

```yaml
hives:
  - id: {id}
    path: hives/{id}
    enabled: true
    agents:
      pm:
        model: ""
      rd:
        model: ""
```

## 完成後

- 用條列告訴使用者建了哪些檔案。
- 接著**用 bash 執行 `node .swarm/office-setup.mjs {id}` 開立 5 件開辦任務**（`issues/pm/001`～`005`），
  告訴使用者這些做完公司就能自己運作，問他要不要從第 1 件開始。

## 注意

- 新公司的 `enabled` 寫 `true`（排程總開關）。使用者想要定時任務時，照 `AGENTS.md`「排程」
  同時寫 registry 的角色區塊（`任務名: "cron"`）與 project.yaml 的 `reports:`，平台會照台北時間自動執行。
- 不要建立或複製任何其他公司的資料。
- 密鑰不寫進對話、不寫進 HIVE.md / project.yaml。
