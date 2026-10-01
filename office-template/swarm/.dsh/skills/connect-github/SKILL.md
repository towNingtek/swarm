---
name: connect-github
description: 幫一間公司接上 GitHub：使用者自己申請 fine-grained token 並用終端機放好，AI 測試、設定 git 憑證與 project.yaml，之後可以同步 Issue、開分支與 PR。
whenToUse: 使用者要接 GitHub、要 AI 開 PR 或看線上 Issue、開辦任務「接上 GitHub」時。
---

# connect-github — 接上 GitHub

不接也可以：任務就記在本地 `hives/{公司代號}/issues/`。接了之後，AI 可以讀寫這間公司的 repo、開分支與 PR、看和更新 Issue。

## 先問三件事（一次一題）

1. 要接哪個 repo？（組織或帳號／repo 名稱，例如 `my-org/website`）
2. **原本已經有 token 嗎？**（例如之前的系統或舊站台在用）
   - 有，而且是 **fine-grained**、只給這幾個 repo → 可以沿用，直接跳到「放進站台」。
   - 是 classic token（`ghp_` 開頭，能動整個帳號）→ 建議重新申請一個 fine-grained 的，舊的之後撤銷。
   - 沒有 → 照「申請」帶他做。
3. 要 AI 做到哪裡：只看（讀 Issue、讀程式）還是也要改（開分支、PR、更新 Issue）？

## 申請 fine-grained token（使用者在 GitHub 做）

1. GitHub 右上角頭像 → Settings → 最下面 Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
2. Token name：例如「AI 辦公室 - {公司名稱}」。Expiration：建議 90 天（到期前 AI 會提醒）。
3. Resource owner：選 repo 所在的帳號或組織（組織可能需要管理員核准）。
4. Repository access：**Only select repositories** → 只選要接的 repo。
5. Permissions → Repository permissions：
   - 只看：Contents、Issues、Pull requests 都選 **Read-only**（Metadata 會自動是 Read-only）。
   - 也要改：Contents、Issues、Pull requests 選 **Read and write**。
   - 其他全部保持 No access。
6. Generate token → 複製（`github_pat_` 開頭，只會顯示一次）。

## 放進站台（使用者自己做，AI 不經手）

**不要請使用者把 token 貼進對話。** 請他：

1. 開左側「終端機」（新開一個終端）。
2. 輸入：`node .swarm/set-key.mjs {公司代號} github`，按 Enter。
   （所有公司都要用同一把 → 把 `{公司代號}` 換成 `--shared`，存到 `.swarm/keys/shared/`。）
3. 貼上 token（畫面不會顯示），按 Enter。

顯示「✓ 測試成功」並列出看得到的 repo，就完成了；看不到要的 repo，回到 GitHub 檢查第 4 步。

## AI 接著做

1. `node .swarm/key-test.mjs {公司代號} github` 確認看得到指定的 repo。
2. 用編輯工具更新 `hives/{公司代號}/project.yaml`：
   ```yaml
   scm:
     type: github
     org: {組織或帳號}
     repo: {repo}
     token_file: github.token   # 在 .keys/ 或 .swarm/keys/shared/，不要寫內容
   ```
3. 需要程式碼時，clone 到 `hives/{公司代號}/repos/{repo}`，並只在那個 repo 設定憑證小幫手：
   ```bash
   git clone https://github.com/{組織}/{repo}.git hives/{公司代號}/repos/{repo} \
     -c credential.https://github.com.helper='!node /home/dsh/workspace/.swarm/git-credential.mjs {公司代號}'
   cd hives/{公司代號}/repos/{repo}
   git config credential.https://github.com.helper '!node /home/dsh/workspace/.swarm/git-credential.mjs {公司代號}'
   git config user.name "AI 辦公室 ({角色})"
   git config user.email "noreply@users.noreply.github.com"
   ```
   **絕對不要**把 token 放進網址（`https://token@github.com/…`）或 `git config` 的值，它會留在 `.git/config`。
4. 把開辦任務「接上 GitHub」標成 `狀態：完成`，附上 key-test 的結果（不要貼 token）。

## 之後怎麼用

- Issue／PR 用 GitHub API（node 的 `fetch`，帶 `Authorization: Bearer` 標頭，token 用 `.swarm/keys.mjs` 的 `readKey` 讀，不要印出來）。
  站台沒有 `gh` 與 `curl`，用 node 寫一段小程式即可。
- 改程式：開分支 `{角色}/{簡短描述}`，commit 格式 `[{角色}] {type}: {描述}`，推分支後開 PR；**不直接 push main/master**，不 merge，除非使用者明確要求。
- 本地任務紀錄裡附上 Issue／PR 連結，兩邊對得起來。
- 任何輸出（日誌、錯誤訊息）若可能含 token，先用 `redact` 遮蔽。

## 換掉或移除

- token 到期或換新：再跑一次 `set-key.mjs`。
- 移除：`node .swarm/set-key.mjs {公司代號} github --remove`，並到 GitHub 撤銷那個 token。
- 懷疑外流：立刻到 GitHub 撤銷，再申請新的。
