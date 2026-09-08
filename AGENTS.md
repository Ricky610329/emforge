# AGENTS.md — 給任何 coding agent（Codex／Claude／其他）的入口

1. 先讀 `CLAUDE.md`（工作規範與硬規則：TDD、單檔 ≤400 行、單函式 ≤60 行、磁碟名只經 `paths.py`、協調狀態只經 `Depot`、`mcp` 只在指定模組函式內 import）。
2. 再讀 `docs/naming.md`、`docs/implementation.md`（§2 模組地圖、§3 key 佈局、§12 已知失效模式）。
3. **本輪已完成的計畫：[docs/plan-2026-09-08-platform.md](docs/plan-2026-09-08-platform.md)**：六個里程碑涵蓋執行身分、收件、HTTP、指定機器算法端、評估/MCP、版本更新/恢復。驗收 595 passed、pyflakes 全綠；**不 push**。操作見 [docs/platform-quickstart.md](docs/platform-quickstart.md)。後續修改仍遵守 TDD 與完整回歸。
4. 歷史：`docs/review-2026-09-07.md`（全面檢查與修復進度）、`docs/architecture.md`（設計為什麼）、`docs/deploy.md`（正式機切換，延後）。

測試指令（repo 根、pipefail）：

```bash
cd /c/Users/ricky/Documents/GitHub/emforge && export PYTHONIOENCODING=utf-8 EMFORGE_ANTENNA_REPO=/c/Users/ricky/Documents/GitHub/Antenna && set -o pipefail && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pytest -o addopts="" -q 2>&1 | tail -3 && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pyflakes emforge tests && echo OK
```

不碰 `C:\Users\ricky\Documents\GitHub\Antenna` 與 `T:\`；測試只用 `tmp_path`／`MemoryDepot`。

## 最新變更（2026-09-08）

候選優先級／同級算法與 run 輪替已實作，完整驗證 **615 passed**、pyflakes 通過。
三種 priority、背景填空、單筆讓位、共享提升與 NAS／HTTP 相容規則見 [docs/priority-scheduling.md](docs/priority-scheduling.md)。
本次尚未 push，正式 HFSS 未切換。舊六里程碑驗收 595 是歷史基準。
