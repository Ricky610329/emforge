"""emforge — 昂貴模擬下的設計搜尋平台。

四個框：AI 加值層（可選）／runtime 實例（一實例綁一 sim_profile）／策略庫・模擬庫・共享資料庫。
迴圈裡沒有 LLM。設計理由見 docs/architecture.md；怎麼做見 CLAUDE.md 與 docs/naming.md。

#? 這個檔刻意不 import 任何子模組：`import emforge` 必須極輕（策略子行程、worker 啟動都會踩）。
"""

__version__ = "0.1.0"
