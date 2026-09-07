# AGENTS.md — 給任何 coding agent（Codex／Claude／其他）的入口

1. 先讀 `CLAUDE.md`（工作規範與硬規則：TDD、單檔 ≤400 行、單函式 ≤60 行、磁碟名只經 `paths.py`、協調狀態只經 `Depot`、`mcp` 只在指定模組函式內 import）。
2. 再讀 `docs/naming.md`、`docs/implementation.md`（§2 模組地圖、§3 key 佈局、§12 已知失效模式）。
3. **目前的任務書：`docs/plan-2026-09-08-platform.md`**（第三輪 M17–M20：評估層、歷程與取樣、收件匣＋client、平台 MCP）。裡面有環境、測試指令、契約速查、每個里程碑的檔案與驗收。照順序做，一里程碑一 commit，全綠＋pyflakes 才 commit，**不 push**。
4. 歷史：`docs/review-2026-09-07.md`（全面檢查與修復進度）、`docs/architecture.md`（設計為什麼）、`docs/deploy.md`（正式機切換，延後）。

測試指令（repo 根、pipefail）：

```bash
cd /c/Users/ricky/Documents/GitHub/emforge && export PYTHONIOENCODING=utf-8 EMFORGE_ANTENNA_REPO=/c/Users/ricky/Documents/GitHub/Antenna && set -o pipefail && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pytest -o addopts="" -q 2>&1 | tail -3 && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pyflakes emforge tests && echo OK
```

不碰 `C:\Users\ricky\Documents\GitHub\Antenna` 與 `T:\`；測試只用 `tmp_path`／`MemoryDepot`。
