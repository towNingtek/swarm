"""EPIC runner：照 docs/epic-contract.md 跑一個 EPIC 一輪。

模組：
  criteria  判準解析、檢查、凍結（#394）
  takeable  可接取判斷（#395）
  judge     執行判準、貼結果、關 sub-issue（#396）
  agent     DSH ACP agent 執行器（#397）
  retry     重試與卡住（#398）
  round     一輪主程式與 EPIC 總結（#399）
  watch     cron 觀察、通知（#400）
  github    真實 GitHub（gh CLI）；測試用 tests/epic/fake_github.py
"""
