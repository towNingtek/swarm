# Swarm 開源計畫

> 狀態：**P2 搬遷中**（2026-10-01）。`site/`、`plugins/`、`office-template/`、`platform/`、`deploy/` 已搬入；下一批 `docs/`。

## 1. 目標

把一套正在營運的 AI 無人辦公室，整理成任何人都能自己架起來的開源專案：

- **平台**：邀請制開站、帳號、模型額度與計量、金鑰管理、通知、管理後台。
- **站台**：每位客戶一個 DeepSeek Harness（DSH）容器，含外觀、登入、網路隔離、模型 relay。
- **辦公室範本**：公司（hive）、AI 員工、排程、任務庫、兩層金鑰。
- **外掛**：DSH 外掛。獨立發布的外掛（例如 dsh-share-room）由 npm 引用。

**不做**：
- 不收任何客戶或公司的資料。
- 不帶原私有 repo 的 git 歷史。
- 不收舊的 opencode 站台系統，它已準備棄用。

## 2. 目錄結構

```
platform/         平台
site/             站台：建站、隔離、relay、DSH 映像
office-template/  每個新站台的辦公室範本
plugins/          repo 內的 DSH 外掛（swarm-brand）
deploy/           systemd、nginx 範例，全部參數化
docs/             架構、ADR、contract 規格、操作手冊
test/  e2e/       單元測試、拋棄式容器上的端對端測試
```

## 3. 去機敏

- 新 repo 從空白歷史開始，只搬清單上列出的檔案。
- 所有跟部署有關的值（網域、IP、路徑、Discord ID、人名）都從設定讀取，附 `*.example` 範本，範例一律用 `example.com`。
- CI 每次 push 都檢查兩件事：
  1. `scripts/check-denylist.py`：用加鹽雜湊比對本站專屬字詞，規則檔本身不含明文；另外攔下 `/home/...` 絕對路徑、私鑰、Discord webhook、17～20 位的 ID。
  2. gitleaks 掃描完整歷史。

## 4. 階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| P0 | 盤點：每個檔案要搬、改還是不搬 | ✅ |
| P1 | 骨架：README、LICENSE、SECURITY、CI、洩漏檢查 | ✅ |
| P2 | 分批搬遷：`site/` ✅、`office-template/` ✅、`platform/` ✅、`deploy/` ✅ → `docs/` | 進行中 |
| P3 | 自架驗證：在拋棄式容器裡照 README 從零架起來 | |
| P4 | 對抗式安全審查 | |
| P5 | 三語 README、架構圖、demo | |
| P6 | 公開，發布 v0.1.0 | |

## 5. 已決定

- 開源 v0.1 只收 DSH 平台，opencode 不出現在這個專案。
- `@summersec/dsh-web-auth` 的改動整理成 PR 送回原作者，合併前先用 fork 版。
- `dsh-share-room` 從 npm 安裝。
- 品牌外掛 `swarm-brand` 放在 `plugins/` 裡，暫不發 npm。
- Cloudflare DNS 改為選用，預設使用者自己設好萬用 DNS。
- 授權 MIT。
