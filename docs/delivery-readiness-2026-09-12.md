# 日月光交付評估 / 2026-09-12

目前可進行受控工程試用與展示，尚不足以宣告正式生產交付完成。驗收證明三台遠端 worker
可配置、接取工作並回傳一致結果；尚未證明所有回傳結果都已達到 HFSS 求解收斂標準。

## 已有證據

- 模擬部署版本 480e874；本機協調平台、算法 runner、三台 Windows HFSS worker 已部署。
- 12 筆 fake、10 筆真實 HFSS 工作完成，均單次成功，查核時無未完成工作。
- 相同已知 dual_p01_db075 圖形在三機重複，完整 response 最大差
  1.9073486328125e-6 dB；全部 measure 與 score 完全相同。同機重複 response 完全相同。
- hosted fake 與 HFSS 算法各完成 3 輪回饋，符合新增樣本預算。重送冪等、共享結果、指定機器
  repeat 已驗證。37 正常停止後重新啟動與心跳通過；計數是單次程序生命週期，不是歷史總量。
- HTTP 正確／錯誤／未提供 token 的認證結果已測。
- 新 AI 入口支援 Pi native extension、Codex / Claude Code stdio MCP。
  Pi 官方 loader 與 fake 送件閉環通過，且正式平台唯讀查核三台 online / idle。
- 本輪 628 Python tests、pyflakes、Pi 整合測試與 TypeScript 檢查通過。

詳細模擬驗收報告保留於本機 Desktop/em-forge，架構網站副本在 Git 排除的
`local/architecture/evidence/`。網站快照有明確查核時間，並非持續監控服務。

## 正式交付阻礙與接受條件

| 優先 | 缺口 | 必須驗收的行為 |
|---|---|---|
| P0 | HFSS 未收斂仍可能標成 done | 擷取求解收斂狀態與依據，區分執行完成／科學接受；以已知收斂及不收斂案例回歸 |
| P1 | 生產故障恢復未演練 | 真實 HFSS 中斷網、worker 強制退出、平台重啟後，工作身分與結果對帳正確，不重複執行或遺失 |
| P1 | 客戶環境持續運作未驗 | 依客戶預期負載連續運行並量測吞吐、錯誤率、資源占用，定義與驗收告警 |
| P1 | 備份、版本與操作交付 | 可重現安裝、版本鎖定、備份還原、回退、責任與操作手冊通過現場演練 |
| P1 | 存取治理依客戶需求補齊 | 目前為 HTTP / 共享 bearer；驗收 TLS／網段隔離、身分分權與稽核需求 |
| P2 | 範圍與 AI 效果尚有限 | single_db100、NAS、本次選用的模型及策略品質另行驗證，不能由 dual/fake 外推 |

HFSS 216 的驗收畫面曾顯示「Adaptive Passes did not converge based on specified criteria」。
固定 profile 有最大 6 passes 等條件，本輪沒有改動 Antenna 或既有求解設定。
跨機數值一致證明重現性，不能替代物理／數值收斂判定。

## 本輪修改的範圍

新增 agent stdio MCP 入口、本機連線檔讀取、Pi extension 與三個 harness 的 session launcher。
AI 代理從新工作樹載入，運行中的 platform release 和遠端 worker 保持部署版 480e874。
AI 代理與後端接口版本可分開更新；本輪工具操作皆沿用已部署後端支援的呼叫。

尚未使用 Pi / Codex / Claude Code 的模型對話驅動真實 HFSS。模型供應商與登入待指定，
接口的 deterministic 測試不代表 AI 策略已優於人工或既有算法。
操作方式見 [agent-harness.md](agent-harness.md)。
