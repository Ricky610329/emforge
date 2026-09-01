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
python -m pytest
```

## 文件

- `docs/naming.md` — 命名規範（識別字、磁碟佈局、測試、commit）
- `docs/architecture.md` — 為什麼這樣設計（四框主圖、一個候選的一生、D1–D10）
- `docs/implementation.md` — 模組地圖、tick 演算法、CLI／yaml／事件參考（實作完成後依程式碼撰寫）
- `CLAUDE.md` — 工作規範（TDD、可維護性上限、邊界）
