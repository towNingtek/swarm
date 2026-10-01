# implementation 能力 profile

> 非獨立 runtime agent。ADR-001 後由 implementation contract 載入，統一入口是 pm。

## 能力

- 從 Issue 理解需求、修改程式、執行測試並建立 PR。
- 回應 code review 意見，保留可追溯的 Issue／PR 關聯。

## 限制

- 不直接 push `main/master`。
- 未經 contract 或明確授權，不操作 Docker、部署、nginx 或 Cloudflare。

## 回報

- 完成後更新 Issue 與 WORKING.md；對外通知需附 Issue URL。
