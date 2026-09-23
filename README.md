# emforge

昂貴模擬（HFSS，一筆 100–200 秒）下的**設計搜尋平台**。

```
 AI 加值層（可選；改檔案＋跑命令，任何 harness 或人都能做）
 ────────────────────────────────────────────────────────
 runtime 實例（一實例綁一 sim_profile；排程→propose→驗證→去重→派工→收結果→評分→公證）
 ────────────────────────────────────────────────────────
 策略庫（N 個 .py）   模擬庫（Profile 註冊表）   資料庫（所有實例共享）
```

- **迴圈裡沒有 LLM**：拔掉 AI 層，系統要能跑一整晚。
- **平台不綁定策略**：一個策略＝一個 `.py`，`COMPATIBLE = {...}` + `propose(ctx) -> list[Proposal]`。
- **核心零領域依賴**：天線／HFSS 只在 `emforge/adapters/antenna/`。

## 開始

```
conda activate ant
pip install -e .[test]
python -m pytest                       # 設 EMFORGE_ANTENNA_REPO=<Antenna clone> 才會跑 adapter 綁定／parity（不設＝skip，表頭會印綁到哪；設錯路徑整套拒跑）
emforge init --root <root> --profile dual_p01_db075
#   <root>/registry.py 寫：from emforge.adapters.antenna.profiles import register_all; register_all()
emforge run --root <root> --profile dual_p01_db075      # 開發機：runtime 實例
emforge worker --root <root>                            # 正式機：見 docs/deploy.md
emforge report --root <root> --profile dual_p01_db075
```

假儀器試跑（不需 HFSS）：registry.py 改成 `from emforge.testing import register_fakes; register_fakes()`，profile 用 `fake_f1`。

## 文件

- `docs/architecture.md` — 為什麼這樣設計（四框主圖、一個候選的一生、D1–D10）
- `docs/implementation.md` — 現在怎麼做的：模組地圖、磁碟格式、tick／worker 演算法、策略作者指南、CLI／事件／exit code、事故→防線→測試、已知失效模式、與架構的偏差
- `docs/deploy.md` — 三台正式機安裝與逐台切換（含回滾）
- `docs/naming.md` — 命名規範（識別字、磁碟佈局、測試、commit）
- `docs/incidents.md` — 事故史（回歸測試的 `I-N`）
- `CLAUDE.md` — 工作規範（TDD、可維護性上限、邊界）
- `docs/handoff-2026-09-23-claude-to-codex.md` — 目前現況、待辦與切換注意事項（接手先讀）
