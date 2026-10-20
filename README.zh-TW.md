# Swarm

繁體中文 | [简体中文](README.zh-CN.md) | [English](README.md)

[![CI](https://github.com/towNingtek/swarm/actions/workflows/ci.yml/badge.svg)](https://github.com/towNingtek/swarm/actions/workflows/ci.yml) [![Release](https://img.shields.io/github/v/release/towNingtek/swarm)](https://github.com/towNingtek/swarm/releases) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

一套可以自己架設、由**蜂群**運作的 AI 辦公室：AI agent 平行處理一個 EPIC 底下的 issue，裁判只在驗收判準全過時才接受成果，人負責決定與驗收。你架一個平台，邀請客戶進來；每位客戶拿到一個隔離的 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/deepseek-harness) 站台，裡面已經放好辦公室範本：公司、AI 員工角色、排程，以及放自己金鑰的地方。

![營運者建立客戶、送出邀請；客戶啟用帳號、站台自動建好，在自己的站台裡和 AI 一起工作](docs/media/demo.gif)

含字幕的完整影片：[docs/media/demo.mp4](docs/media/demo.mp4)。示範跑在一次性的測試安裝上，模型是腳本，AI 的回覆是預先寫好的。

> 介面目前只有繁體中文。


## 群體智能怎麼運作

Swarm 把一個目標拆成 **一個 EPIC 和幾張小 issue**，每張交給一個 AI agent，**平行**實作。成果要等 issue 上的**可執行驗收判準**全部通過才算數，判準由獨立的裁判執行。人負責定目標、放行、驗收，不需要盯進度。

```mermaid
flowchart LR
  subgraph P1["① 定義"]
    direction TB
    A(["人：目標"]) --> B["epic skill<br/>完成條件・拆卡"]
    B --> C["判準先實測<br/>＋ lint"]
  end
  subgraph P2["② 放行"]
    direction TB
    G(["人：swarm:go"])
  end
  subgraph P3["③ 平行實作與裁定"]
    direction TB
    I["🐝🐝🐝 Agents<br/>各自 worktree"] --> J{"⚖️ 判準<br/>全過？"}
    J -- 否・重試 --> I
    J -- 3 次未過 --> M["swarm:stuck<br/>交給人"]
  end
  subgraph P4["④ 驗收"]
    direction TB
    Q["EPIC 總 PR"] --> U(["人：驗收結案"])
  end
  C --> G --> I
  J -- 是 --> Q
```

| 階段 | 參與者 | 關卡 |
|------|--------|------|
| ① 定義 | 人＋`epic` skill | 寫不出可驗證的「做完」就不開卡；每條判準先在現況實測、必須失敗；草稿要通過 `epic_lint` |
| ② 放行 | 人 | 加上 `swarm:go` 後判準凍結 |
| ③ 實作與裁定 | Agents＋runner | 每個 agent 一個 worktree，只能改 issue 列出的檔案；改到同一個檔案的卡排成一條鏈；runner 照原文執行判準、只看 exit code |
| ④ 驗收 | 人 | 審核 EPIC 總 PR（低風險的可自動合併）；結案摘要計入月報 |

![看板（示範資料）：行星是未關閉的 issue，依狀態排在不同軌道；星團是 EPIC 與它的完成比例；紅暈代表卡住](docs/media/30-board.png)

每個站台都有 **Wolffia 看板**（`/board/`）：即時呈現 GitHub Project，可以拖曳卡片改狀態、放行 EPIC、從任何看得到的組織加入 project，也可以按 **📊 分析** 讓站台自己的 AI 讀完看板回答。細節見 [`swarm/`](swarm/) 與 [`plugins/swarm-board/`](plugins/swarm-board/)。

## 功能

- **邀請制開站。** 營運者在管理後台建立客戶、選辦公室樣板、設定每月模型額度，再送出一次性的邀請連結。帳號密碼由客戶自己設定。
- **每位客戶一個容器。** 站台由平台自己建：一個 DSH 容器，有自己的登入、資料和工作區，加上 nginx 路由和 DNS 紀錄（可選）。刪除客戶時這些都會一起移除。
- **有設定助手的平台首頁。** 客戶看得到設定進度和建站狀態。設定助手只根據平台實際記錄的狀態說明下一步，不會自己編造進度。
- **進站不用第二組密碼。** 按「進入我的站台」，平台會用一次性的入場票把登入中的客戶交給站台。
- **平台的模型金鑰不會進站台。** 每個站台有自己的起步金鑰，而且只能對平台的 relay 使用。relay 核對金鑰和這個站台的每月額度，再用平台金鑰轉送。
- **第一次進站，辦公室就在。** 工作區一開始就有辦公室範本（`AGENTS.md`、新手指南、放公司的資料夾）。客戶之後可以接上自己的模型金鑰。
- **對話分享。** 每個站台都裝了 [dsh-share-room](https://github.com/yillkid/dsh-share-room)，客戶可以邀請訪客一起看一段對話。
- **營運者總覽。** 一頁列出每位客戶的站台、額度政策和樣板。客戶要找真人時，營運者也在同一個後台回覆。

## 截圖

| 營運者 | 客戶 |
|---|---|
| ![營運總覽：每個站台的用量、最近排程與待處理事項；卡片上有 📊 看板與進入站台按鈕](docs/media/20-admin-overview.png) | ![客戶總覽：自己的站台、用量、最近排程、一鍵進看板](docs/media/21-customer-overview.png) |
| ![用邀請連結啟用帳號](docs/media/04-activate.png) | ![客戶設定：設定進度與站台密碼](docs/media/22-customer-settings.png) |
| ![一次性邀請連結（截圖已遮住）](docs/media/03-admin-invite.png) | ![平台首頁：站台已經建好](docs/media/06-site-ready.png) |

在客戶自己的站台裡：回覆經過平台的 relay，用量計入每月額度。

![在自己的站台裡和 AI 工作](docs/media/09-site-chat.png)

## 架構

```mermaid
flowchart LR
  browser([瀏覽器]) -->|"HTTPS"| nginx["nginx"]
  nginx -->|"platform.example.com"| platform["平台 :8210<br/>邀請、登入、平台首頁、建站"]
  nginx -->|"platform.example.com/admin"| admin["管理後台 :8211"]
  nginx -->|"acme.example.com"| site["站台容器<br/>DSH + 登入 + 辦公室"]
  platform --- db[(SQLite)]
  admin --- db
  platform -->|"docker"| site
  site -->|"站台金鑰"| relay["站台模型 relay :8212<br/>核對金鑰與額度"]
  relay --- db
  relay -->|"平台金鑰"| gateway["你的 OpenAI 相容<br/>模型 gateway"]
```

每個站台都在隔離的 Docker 網路裡，主機上唯一連得到的是 relay，其他主機埠、內網和雲端 metadata 都連不到。平台本身從不執行 `sudo`，需要 root 的事交給兩個小單元：一個管防火牆，一個套用 nginx 設定。細節見 [docs/architecture.md](docs/architecture.md) 和 [docs/security.md](docs/security.md)（英文）。

## 架設前請先了解

- **請用專門給 Swarm 的主機。** 平台使用者在 `docker` 群組裡，等同 root。
- **容器不是 VM。** 所有客戶共用同一個核心和同一個 Docker daemon。DSH 自己的沙箱在容器裡無法運作，所以隔離只靠容器、網路和防火牆。
- **站台可以連外網。** 客戶的 AI 可以把該客戶的資料送到任何地方。
- **起步模型的費用由你負擔。** 每個站台的起步呼叫都經過你的 gateway、用你的金鑰。每月上限會強制執行，但 token 數是估算的。
- **TLS 要自己處理。** 這個 repo 不處理 TLS，請在前面放 certbot、load balancer 或 tunnel。

完整清單見[已知限制](docs/security.md#known-limits)。

## 安裝

需要：一台 Linux 主機（Ubuntu 22.04 或類似版本），裝有 Docker 24 以上、nginx、Python 3.10 以上；站台 image 約需 2 GB 磁碟；一個 OpenAI 相容的模型 gateway（例如 LiteLLM）。把 `platform.<domain>` 和 `*.<domain>` 指到這台主機。

```sh
sudo git clone https://github.com/towNingtek/swarm /opt/swarm
cd /opt/swarm
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo docker build -f site/Dockerfile -t swarm-site:latest .
sudo deploy/install.sh
sudo editor /etc/swarm/platform.env          # 網域、origin、模型 gateway
sudo editor /etc/swarm/secrets/model-key
sudo cp deploy/nginx/swarm-platform.conf.example /etc/nginx/conf.d/swarm-platform.conf
sudo editor /etc/nginx/conf.d/swarm-platform.conf && sudo nginx -t && sudo systemctl reload nginx
sudo deploy/install.sh --start
```

接著打開 `https://platform.<domain>/admin`，用 `/etc/swarm/secrets/admin-password` 裡的密碼登入。其他設定、日誌和第一位客戶的建立方式見 [deploy/README.md](deploy/README.md)（英文）。

整套安裝流程會在一次性容器裡從零測試：`bash deploy/tests/clean-install/run.sh`。

## 目錄

| 路徑 | 內容 |
|---|---|
| [`platform/`](platform/) | 平台、管理後台、站台模型 relay |
| [`site/`](site/) | 建站引擎與 DSH 站台 image |
| [`office-template/`](office-template/) | 新站台一開就有的辦公室 |
| [`plugins/`](plugins/) | 放在本 repo 的 DSH 外掛：品牌外觀，以及看板（Wolffia）與 `epic` skill |
| [`swarm/`](swarm/) | 蜂群 orchestrator：agents、裁判、自動開工、結案摘要與月報 |
| [`deploy/`](deploy/) | 用 systemd、Docker、nginx 在一台主機上安裝 |
| [`demo/`](demo/) | 上面示範用的腳本模型和錄影腳本 |
| [`docs/`](docs/) | 架構、安全模型、HTTP API、設計紀錄、蜂群契約 |

## 開發

```sh
bash scripts/test.sh          # 全部測試；Python 3.10 以上、Node 22
```

這裡怎麼寫 issue（EPIC＋可執行驗收判準）、PR 需要什麼，見 [CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

平台用 Python（FastAPI），頁面由伺服器產生。站台 image 是 DSH 加上 profile patch，不修改 DSH 本身。

## 名稱

「一群協作的 agent」這個概念啟發自 [openai/swarm](https://github.com/openai/swarm)。本專案獨立開發，與 OpenAI 無關。

## 安全

發現漏洞請私下回報，見 [SECURITY.md](SECURITY.md)。

## 授權

[MIT](LICENSE) © towNingtek inc.
