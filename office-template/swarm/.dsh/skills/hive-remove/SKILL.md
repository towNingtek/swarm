---
name: hive-remove
description: 安全移除這個 swarm 辦公室裡的一間公司（hive）：要求使用者輸入 ID 確認後，刪除目錄並從 .swarm/registry.yaml 移除。
whenToUse: 使用者明確要刪除某個 hive／公司時。
disable-model-invocation: true
---

# hive-remove — 移除公司

1. 問要移除的 hive ID，並顯示它的目錄內容摘要（檔案數、最後工作時間）。
2. 警告：`hives/{id}/` 會整個刪除，包含 `.keys/` 與所有紀錄，無法復原。建議先把資料夾下載或備份。
3. 要求使用者**完整輸入**該 hive ID 確認；不一致就停止。
4. 確認後：從 `.swarm/registry.yaml` 移除該條目，再刪除 `hives/{id}/`。
5. 回報完成；只刪這一個 hive，不動其他任何檔案。
