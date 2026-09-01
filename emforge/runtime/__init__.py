"""emforge.runtime — 一個實例綁一個 sim_profile 的排程迴圈。迴圈裡沒有 LLM。

  core.py       Runtime：單例鎖、設定重載、state/status、tick 骨架（collect → notarize → schedule）、run
  reconcile.py  啟動對帳：inflight／佇列／批 三方一致才准 tick（I-14）
  dispatch.py   去重 → 先寫 inflight → 寫批 → 加 job（同一動作，I-13）；去重無旁路（D7）
  collect.py    增量收結果 → profile_hash 比對 → 量測 → 評分 → 入庫 → 收尾 inflight
  schedule.py   靜態 prio、max_inflight、背景填空、策略例外→暫停（D5／D9）
  notarize.py   破榜候選 → 自動重測 ×n → 一致性 → pending；永不碰榜；唯一設 kind=repeat 處

各檔函式以 `rt: Runtime` 為第一參數；`Runtime.tick()` 只負責順序。
"""
from .core import Runtime, RuntimeLocked

__all__ = ["Runtime", "RuntimeLocked"]
