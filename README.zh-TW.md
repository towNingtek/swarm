# Swarm

繁體中文 | [English](README.md)

一套可以自己架設的 AI 無人辦公室。平台採邀請制開站，每位客戶拿到一個隔離的 [DeepSeek Harness（DSH）](https://github.com/deepseek-ai/deepseek-harness) 站台，裡面已經有 AI 員工、排程和金鑰管理等辦公室範本。

> **狀態：施工中。** 程式碼正從私有部署搬過來，目前還不能安裝。進度見 [PLAN.md](PLAN.md)。

## 會包含什麼

- **平台**：邀請制開站、帳號、模型額度與計量、金鑰管理、通知、管理後台。
- **站台**：每位客戶一個 DSH 容器，含外觀、登入、網路隔離和模型 relay。平台不會把自己的模型金鑰放進站台。
- **辦公室範本**：公司（hive）、AI 員工角色、排程、任務庫、兩層金鑰，新站台一開就有。
- **外掛**：DSH 外掛，例如把對話分享給訪客的 [dsh-share-room](https://github.com/yillkid/dsh-share-room)。

## 名稱

「一群協作的 agent」這個概念啟發自 [openai/swarm](https://github.com/openai/swarm)。本專案獨立開發，與 OpenAI 無關。

## 安全

發現漏洞請私下回報，見 [SECURITY.md](SECURITY.md)。

## 授權

[MIT](LICENSE)
