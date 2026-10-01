---
name: connect-discord
description: 幫一間公司接上 Discord 通知（webhook）：使用者自己申請並用終端機放好 webhook，AI 測試、發第一則通知，並寫進 project.yaml。
whenToUse: 使用者要接 Discord、要 AI 做完事通知他、開辦任務「接上 Discord 通知」時。
---

# connect-discord — 接上 Discord 通知

接好之後，AI 做完排程或重要任務時可以發通知到 Discord 頻道。
用的是頻道的 **Webhook**：只能「發訊息到這一個頻道」，不能讀訊息、不能管伺服器，權限最小。

## 先問：有沒有現成的？

- **原本已經有**（例如之前的系統、舊站台已經在用某個頻道的 webhook）→ 可以沿用同一個網址，請他照「放進站台」那步做。
- **沒有** → 照「申請」帶他做，一次只講一步。

## 申請（使用者在 Discord 做）

1. 在 Discord 打開要收通知的頻道 → 頻道名稱旁的齒輪「編輯頻道」。
2. 「整合」→「Webhook」→「新 Webhook」。
3. 取個名字（例如「AI 辦公室」），確認頻道正確。
4. 按「複製 Webhook 網址」。

需要伺服器的「管理 Webhook」權限；沒有的話請伺服器管理員幫忙建立。

## 放進站台（使用者自己做，AI 不經手）

**不要請使用者把網址貼進對話**（對話會留紀錄，拿到網址的人就能用這個名義發訊息）。請他：

1. 開左側「終端機」（新開一個終端）。
2. 輸入：`node .swarm/set-key.mjs {公司代號} discord`，按 Enter。
   （所有公司都要用同一把 → 把 `{公司代號}` 換成 `--shared`，存到 `.swarm/keys/shared/`。）
3. 貼上剛才複製的網址（畫面不會顯示），按 Enter。

程式會自己存好（只有他能讀）並測試，顯示「✓ 測試成功」就完成了。

## AI 接著做

1. 執行 `node .swarm/key-test.mjs {公司代號} discord` 確認（只讀資訊，不發文）。
2. **先問使用者可不可以發一則測試通知**，同意後執行：
   `node .swarm/notify.mjs {公司代號} "✅ AI 辦公室已接上 Discord，之後排程結果會通知到這裡。"`
   請他確認頻道有收到。
3. 用編輯工具把 `hives/{公司代號}/project.yaml` 的 `notifications: none` 改成：
   ```yaml
   notifications:
     discord: true      # webhook 在 .keys/ 或 .swarm/keys/shared/
   ```
4. 把開辦任務「接上 Discord 通知」標成 `狀態：完成`，附上測試結果（不要貼網址）。

## 之後怎麼用

- 發通知：`node .swarm/notify.mjs {公司代號} "訊息"`；長訊息可用 `echo "…" | node .swarm/notify.mjs {公司代號}`。
- 什麼時候發：排程做完、任務完成、卡住需要人處理時。內容簡短：做了什麼、結果、要不要人處理。
- 沒接 Discord 時 notify 會說明並跳過，不會出錯；那就只寫本地任務紀錄。
- 不在通知裡放金鑰、密碼、客戶個資。程式不允許 @everyone／@here。

## 換掉或移除

- 換新網址：再跑一次 `set-key.mjs`。
- 移除：`node .swarm/set-key.mjs {公司代號} discord --remove`，並到 Discord 刪掉那個 webhook。
- 懷疑外流：直接在 Discord 刪掉 webhook 再建新的。
