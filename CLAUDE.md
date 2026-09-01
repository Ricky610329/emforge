# CLAUDE.md — emforge 工作規範

> 用**繁體中文**對話與寫文件；識別字用英文。
> 架構的「為什麼」在 `docs/architecture.md`（自 Antenna repo `docs/platform/architecture.md` 帶入）；本檔只放**怎麼做**。

## 這是什麼

昂貴模擬（HFSS，一筆 100–200 秒）下的設計搜尋平台。四個框：AI 加值層（可選）／runtime 實例（一實例綁一 sim_profile）／
三個庫（策略庫・模擬庫・共享資料庫）。**迴圈裡沒有 LLM**；拔掉 AI 要能跑一整晚。

## 四條硬規則

1. **TDD**：先寫紅測試 → 實作 → 綠。回歸測試 docstring 首行寫 `回歸 I-N（日期）：防止…`（I-N 見 `docs/incidents.md`）。
2. **命名照 `docs/naming.md`**；磁碟上的每個檔名／目錄名只能來自 `emforge/paths.py`，不准在別處拼字串。
3. **一里程碑一 commit**：`python -m pytest` 全綠 + `python -m pyflakes emforge tests` 無 undefined name 才 commit。
   訊息 `type: 摘要`（繁中；type ∈ feat/fix/test/docs/chore/refactor）。**不 push**，除非 Ricky 要求。
4. **可維護性**（`tests/test_smoke.py` 釘死）：單檔 ≤ 400 行、單函式 ≤ 60 行——超過就拆，不調上限；
   檔名說出它做什麼，沒有 `utils/misc/helpers/dedust`；worker 與 runtime 是子套件、一檔一職責、一對一測試檔。

## 邊界

- **核心零領域依賴**：`emforge/` 除 `adapters/`、`legacy/` 外不得 import `torch/antenna/scipy/matplotlib/pandas/win32com`（靜態測試擋）。
  天線域只透過 `emforge/adapters/antenna/` 綁舊 repo（`EMFORGE_ANTENNA_REPO`），量測尺是 vendored numpy 版＋parity 測試。
- **worker 不認得天線**：只呼叫 `sim.simulate(bits)` 寫原始響應；measure/score 在 runtime `collect`。
- **策略只 `propose`**：去重、派工、評分、公證都是 runtime 服務；`kind=repeat` 只有 `runtime/notarize.py` 與 `cli smoke` 能設。
- **榜只能經 `promote`/`rescore` 寫**；ledger 檔帶 checksum，手改會被抓。

## 測試

- 從 repo 根跑 `python -m pytest`（`pyproject` 已設 `pythonpath=["."]`，裸 `pytest` 也行）。
- **測試永不碰 NAS**：用 `root` fixture（tmp_path 下、含中文與撇號）；conftest 清掉 `EMFORGE_ROOT`。
- **沒有 golden、沒有自動寫回基準**（舊 repo 的 `CI=1` 靜默重錨教訓）。決定性只在同 seed 同行程內斷言，且雙向（同 seed 相等／異 seed 不同）。
- 假件叫 `Fake*`，住 `emforge/testing.py`，使用者寫策略測試也能 import。
- 需要真 HFSS 的測試標 `hfss`，只在 `EMFORGE_HFSS_TESTS=1` 跑（正式機手動）。

## 註解慣例

- `#!`＝事故疤（刪掉或搬動這行，產線會再壞一次；附日期／事故編號）。
- `#?`＝設計理由（誰決定、為什麼；可以重新決定）。
- 續行用 `#  `（兩空格），一個區塊一個標記。

## 環境

conda env `ant`（Python 3.11、numpy、pyyaml、pytest；torch 只給 adapter/legacy 用）。安裝：`pip install -e .[test]`。
