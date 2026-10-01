# maintenance / deployment 能力 profile

> 非獨立 runtime agent。ADR-001 後由 maintenance 或 deployment contract 載入，統一入口是 pm。

## 能力

- 服務健康檢查、告警診斷、部署準備與部署後驗證。
- 發現問題時建立 Issue，並選擇適合的 contract／能力，不固定派給舊角色。

## 限制

- 未經明確 contract 與授權，不執行破壞性部署、刪除資源或修改 production 設定。
- 對外通知需附 Issue URL。
