"""emforge.worker — 正式機上跑模擬的那個行程。一檔一職責：

  loop.py     主迴圈：STOP → pick → gate → run_batch → mark_done/fail
  gate.py     開模擬器前守門（profile 存在／未退役 → profile_hash → geom_ver → labels）
  guard.py    看門狗、處決線、開模擬器重試
  fuse.py     連敗保險絲（純狀態機）
  workdir.py  本機工作目錄生命週期
  batch.py    run_batch：續跑、逐筆 simulate、逐筆結果檔、補測輪、讓位

worker 完全不認得天線：只呼叫 `sim.simulate(bits)` 寫原始響應；量測與評分在 runtime。
"""
from .batch import run_batch
from .loop import worker_loop, worker_version

__all__ = ["run_batch", "worker_loop", "worker_version"]
