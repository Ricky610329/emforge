# CLAUDE.md — emforge 工作規範

> **本輪最新（2026-09-23）：[全面審查修復紀錄](docs/reliability-2026-09-23.md)。六個切面審查、確認的 bug 全部修復（I-18～I-35），696 項測試通過；設計取捨項只記錄不動（文末）。修正版尚未切換遠端。**

> **前一輪（2026-09-12）：[審查修復與恢復契約](docs/reliability-2026-09-12.md)。640 項測試通過；新增 worker 行程所有權與結果待傳區。使用者已決定不以 HFSS 收斂狀態作本輪交付阻擋。**

> **同日先前驗收：[AI harness 接口](docs/agent-harness.md)、[日月光交付評估](docs/delivery-readiness-2026-09-12.md)。三台 HFSS 已完成初步部署驗收，Pi extension 與共用 stdio MCP 已接上；後續修正與驗收範圍以上方最新文件為準。`local/` 架構網站不進 Git。下列 09-08 交接為歷史基準。**

> **最新交接（2026-09-08）：先讀 [Codex → Claude 工作交接](docs/handoff-2026-09-08-codex-to-claude.md)。程式基準 a5c808e，615 passed；簡化部署入口仍待實作，正式 HFSS 未切換。**

> 用**繁體中文**對話與寫文件；識別字用英文。
> 架構的「為什麼」在 `docs/architecture.md`（自 Antenna repo `docs/platform/architecture.md` 帶入）；本檔只放**怎麼做**。

## 這是什麼

昂貴模擬（HFSS，一筆 100–200 秒）下的設計搜尋平台。四個框：AI 加值層（可選）／runtime 實例（一實例綁一 sim_profile）／
三個庫（策略庫・模擬庫・共享資料庫）。**迴圈裡沒有 LLM**；拔掉 AI 要能跑一整晚。

## 四條硬規則

1. **TDD**：先寫紅測試 → 實作 → 綠。回歸測試 docstring 首行寫 `回歸 I-N（日期）：防止…`（I-N 見 `docs/incidents.md`）。
2. **命名照 `docs/naming.md`**；每個 Depot key（＝磁碟上的檔名／目錄名）只能來自 `emforge/paths.py`，不准在別處拼字串；
   **協調狀態只經 `Depot`**（`emforge/depot/`）——核心模組不 import `pathlib`／`emforge.fs`、不 `open(`（`tests/test_smoke.py` 三張清單釘死；
   DEPOT_ONLY／PARTIAL（附理由，只准程式碼根與使用者給的本機檔）／LOCAL_LAYER（後端本體、release、runner、本機工作目錄）；本機路徑的唯一來源仍是 `paths.py`）。換後端＝實作 `Depot`＋過 `tests/depot/test_contract.py`。
3. **一里程碑一 commit**：`python -m pytest` 全綠 + `python -m pyflakes emforge tests` 無 undefined name 才 commit。
   訊息 `type: 摘要`（繁中；type ∈ feat/fix/test/docs/chore/refactor）。**不 push**，除非 Ricky 要求。
4. **可維護性**（`tests/test_smoke.py` 釘死）：單檔 ≤ 400 行、單函式 ≤ 60 行——超過就拆，不調上限；
   檔名說出它做什麼，沒有 `utils/misc/helpers/dedust`；worker 與 runtime 是子套件、一檔一職責、一對一測試檔。

## 邊界

- **核心零領域依賴**：`emforge/` 除 `adapters/`、`legacy/` 外不得 import `torch/antenna/scipy/matplotlib/pandas/win32com`（靜態測試擋）。
  天線域只透過 `emforge/adapters/antenna/` 綁舊 repo（`EMFORGE_ANTENNA_REPO`），量測尺是 vendored numpy 版＋parity 測試。
- **worker 不認得天線**：只呼叫 `sim.simulate(bits)` 寫原始響應；measure/score 在 runtime `collect`。
- **`mcp`／`uvicorn`／`starlette`／`httpx2` 只在 `device/mcp_server.py`、`device/mcp_client.py`、`platform/mcp_server.py` 的函式內 import**（optional extra `emforge[mcp]`；
  `tests/test_smoke.py` 釘死）：沒裝的機器照常跑 worker／runtime／CLI。MCP tools 名單的唯一真相＝`device/reference.PROCEDURES`；急停解除永遠不給 MCP。
- **策略庫只 `propose`；hosted 算法使用 Client 收件**：去重、派工、評分、公證都是 runtime 服務；`kind=repeat` 只有 `runtime/notarize.py` 與 `cli smoke` 能設。
- **榜只能經 `promote`/`rescore` 寫**；ledger 檔帶 checksum，手改會被抓。

## 測試

- 從 repo 根跑 `python -m pytest`（`pyproject` 已設 `pythonpath=["."]`，裸 `pytest` 也行）。「全綠」要設 `EMFORGE_ANTENNA_REPO`，否則 adapter 綁定／parity 測試 skip（pytest 表頭會印實際綁到的路徑；設了但不是 Antenna repo 整套拒跑，I-30）。
- **測試永不碰 NAS**：用 `root` fixture（tmp_path 下、含中文與撇號）；conftest 清掉 `EMFORGE_ROOT`。
- **沒有 golden、沒有自動寫回基準**（舊 repo 的 `CI=1` 靜默重錨教訓）。決定性只在同 seed 同行程內斷言，且雙向（同 seed 相等／異 seed 不同）。
- 假件叫 `Fake*`，住 `emforge/testing.py`，使用者寫策略測試也能 import。
- 需要真 HFSS 的測試標 `hfss`（pyproject 已註冊；conftest 在 `EMFORGE_HFSS_TESTS` ≠ 1 時一律 skip；正式機手動）。目前還沒有這類測試。

## 註解慣例

- `#!`＝事故疤（刪掉或搬動這行，產線會再壞一次；附日期／事故編號）。
- `#?`＝設計理由（誰決定、為什麼；可以重新決定）。
- 續行用 `#  `（兩空格），一個區塊一個標記。

## 環境

conda env `ant`（Python 3.11、numpy、pyyaml、pytest；torch 只給 adapter/legacy 用）。安裝：`pip install -e .[test]`。
