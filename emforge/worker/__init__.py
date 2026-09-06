"""emforge.worker — 正式機上跑模擬的那個行程。一檔一職責：

  loop.py     主迴圈：STOP → 急停（job 之間不撿）→ pick → gate → 儀器租約 → run_batch → mark_done/fail
  gate.py     開模擬器前守門（profile 存在／未退役 → profile_hash → geom_ver → labels）；`check()` 給儀器層重用
  guard.py    看門狗（逾時／abort_if）、處決線、開模擬器重試
  fuse.py     連敗保險絲（純狀態機）
  workdir.py  本機工作目錄生命週期
  batch.py    run_batch：續跑、逐筆 simulate（經 Instrument）、逐筆結果檔、補測輪、讓位（接管／前景／急停）

依賴方向：gate／guard／fuse／workdir 是 leaf（device/ 可 import 它們）；batch／loop 可 import device/。
這個檔只有 docstring：`from emforge.worker.loop import worker_loop`、`from emforge.worker.batch import run_batch`。
worker 完全不認得天線：只呼叫 `sim.simulate(bits)` 寫原始響應；量測與評分在 runtime。
"""
