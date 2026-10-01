---
name: hive-list
description: 列出這個 swarm 辦公室目前有哪些公司（hive）、各自啟用的角色與最後工作時間。
whenToUse: 使用者問有哪些公司、hive 狀態、要盤點工作區時。
---

# hive-list — 列出公司

1. 讀 `.swarm/registry.yaml` 的 `hives:`。
2. 對每個 hive 顯示：
   - ID 與顯示名稱（`hives/{id}/project.yaml` 的 `name`；沒有就用 ID）
   - 啟用的角色（registry 裡 `agents:` 下的每個鍵）與各自的 model（空白＝站台預設）
   - 排程：角色區塊裡除了 `model` 以外的欄位就是排程任務與 cron；`enabled: false` 時標示「未啟用排程」
   - SCM（github／gitlab／none）
   - 最後工作時間：各角色 `hives/{id}/agents/{角色}/WORKING.md` 的第一行；沒有檔案標示「尚無紀錄」
3. registry 裡有、目錄不存在（或反過來）時，列在最後的「不一致」區塊，不要自行修正。

輸出用繁體中文表格；一個 hive 都沒有時，建議使用 `hive-new` 建立第一間公司。
