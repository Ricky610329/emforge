# 命名規範

> 程式碼裡的真相源是 `emforge/paths.py`（磁碟名、名字驗證、保留字）；本文是人讀版。
> 兩邊不一致時以 `paths.py` 與 `tests/test_paths.py` 為準，並修這份文件。

## 識別字

| 類別 | 規則 | 例 |
|---|---|---|
| 模組 | `snake_case` 單數名詞、扁平；檔名說出它做什麼 | `db.py`, `fs.py`, `worker/gate.py`, `worker/fuse.py` |
| **禁用檔名** | `utils` `util` `misc` `helpers` `common` `tools` `stuff` `dedust` | — |
| 類別 | `CapWords`；契約型別名照架構文件 | `Profile` `Spec` `Proposal` `Context` `Record` `Job` `Database` `View` `Ledger` `Pending` `Queue` `Runtime` `SimResult`；後端 `Depot` `FileDepot` `MemoryDepot`（不叫 Store：`store` 已是「一批」的名字）；儀器層 `Instrument` `DeviceState` `Limits` `Heartbeat` |
| 假件 | `Fake*`（不叫 `Mock*`），住 `emforge/testing.py` | `FakeSimulator` |
| 函式 | 動詞開頭 `snake_case`；判斷式 `is_`/`has_`；私有 `_` 前綴 | `record_id()`, `try_claim()`, `is_stale()` |
| 常數 | `UPPER_SNAKE`；字串值本身小寫 snake | `STATUS_DONE = "done"`, `KIND_REPEAT = "repeat"`, `ARM_BLIND = "blind"` |
| 例外 | `CapWords` 名詞結尾，不加 `Exception`/`Error` 後綴 | `LockTimeout` `GeomVerMismatch` `StrategyFailure` `StrategyTimeout` `AdapterFailure` `StoreExists` `CrossProfileRefused` `AntennaUnavailable` `EstopEngaged` `DeviceBusy` `PreconditionFailed` `ConfirmRejected` `Aborted` `DeviceCallFailed`；`ProposalError`（← 唯一例外：與 ValueError 對稱） |
| CLI 子命令 ↔ 函式 | kebab-case ↔ `cmd_<snake>` | `import-legacy` ↔ `cmd_import_legacy` |
| CLI 旗標 ↔ 屬性 | kebab ↔ `args.<snake>` | `--max-inflight` ↔ `args.max_inflight` |
| 環境變數 | `EMFORGE_` 前綴 | `EMFORGE_ROOT`（本機程式碼／設定根）`EMFORGE_DEPOT`（共享狀態後端 spec：`file://…`／`memory://…`）`EMFORGE_ANTENNA_REPO` `EMFORGE_MACHINE` `EMFORGE_WORK` `EMFORGE_DEVICE_TOKEN`（儀器兩段式 confirm 的 secret＝MCP 共享 bearer token；不走旗標）`EMFORGE_MCP_HOST`／`EMFORGE_MCP_PORT`（`--serve`／`device-serve` 的預設綁定，127.0.0.1／8765） |
| MCP tools ↔ 儀器程序 | `device_<動詞或名詞>`＝`Instrument` 方法一對一；清單唯一真相＝`device/reference.PROCEDURES`（說明檔與 server 同源，測試對帳） | `device_state` `device_describe` `device_selfcheck` `device_log`（read-only）`device_simulate` `device_abort` `device_estop` `device_stop_worker` `device_resume_worker`；**沒有** `device_estop_clear` |
| MCP resources | `device://<名>` | `device://state` `device://reference` `device://reference.json` |
| MCP 錯誤碼 | `ToolError("<code>: …")`，code 小寫 snake、agent 照碼分支 | `device_busy` `estop_engaged` `precondition_failed` `confirm_rejected` `bad_bits` `unknown_profile` `open_failed` `internal` |
| 租約 owner | `<來源>:<對象>` | 儀器租約 `queue:<store>`（同一批續跑可重入）／`mcp:<by>:<rand>`（一次性、不重入）；runtime 鎖 `<tag>:<pid>:<rand>`；jobs.lock `<host>:<pid>` |
| 急停 scope | `fleet` \| `device` \| `local`（外層優先回報） | `queue/ESTOP`／`queue/ESTOP.<tag>`／`<root>/ESTOP` |
| Depot key | POSIX 相對字串、只能來自 `paths.py`；前綴以 `/` 結尾；末段 `.` 開頭或含 `.broken.`＝後端內部、不列 | `db/fake_f1/_index.jsonl`, `queue/state/`（前綴） |
| 單位 | 後綴 `_s`／`_min`；無單位的量不加 | `timeout_s`, `stale_s`, `noise_floor` |
| 時間戳 | 欄位名 `at`，ISO 8601 本地時間 `YYYY-MM-DDTHH:MM:SS` | `"at": "2026-09-01T14:03:22"` |
| JSON 鍵 | 小寫 snake，**＝dataclass 欄位名**（跨邊界不改名） | `sim_profile` `profile_hash` `worker_ver` `time_s` |
| 型別註解 | 簡單註解歡迎；`TypeVar`/`Generic`/`ParamSpec`/`@overload` 一律不用 | `def top(self, k: int) -> list[Record]` |

## 名字（會落到磁碟或設定檔的識別字）

全部符合 `^[a-z][a-z0-9_]*$`（`paths.is_valid_name`）：小寫、底線、不含 `-`。`-` 保留給 store 名的欄位分隔。

| 名字 | 規則 | 例 |
|---|---|---|
| profile | `<domain>_<geom>_<variant>` | `dual_p01_db075`, `single_db100`, `fake_f1` |
| spec | `<domain>_v<N>` | `dual_v2`, `single_v1` |
| 策略 | ＝檔案 stem；使用者 `<root>/strategies/<name>.py` 蓋過內建 | `top_k_flip` |
| **保留字**（不准當使用者策略名） | `repeat`（kind）、`notarize`、`runtime`、`cli`（runtime／CLI 產出的紀錄會填的策略欄位值） | — |
| arm | 自由字串，但 `blind` 是保留語義＝零演算法對照臂；內建策略 `blind` 就叫這個名字、也標這個 arm | `"blind"`, `"L"`, `"d"` |
| `COMPATIBLE` | 策略模組級宣告：profile 名集合，或 `{"*"}`＝領域無關（內建策略用） | `COMPATIBLE = {"dual_p01_db075"}` |
| kind | `sample` \| `repeat`；`repeat` 只有 runtime 公證與 `cli smoke` 能設 | |
| status | `queued` \| `running` \| `done` \| `error` | |
| 機器 tag | IP 末段字串；釘選＝完全相等 | `"216"` |
| store | `<profile>-<strategy>-t<tick:05d>`；公證 `<profile>-notarize-t<tick:05d>-<id[:8]>-r<n>` | `dual_p01_db075-top_k_flip-t00007` |
| Record id | `sha1(packbits(bits) + sim_profile)[:16]`，16 hex | `6c4e45d0ff24958e` |
| profile_hash | `sha1(canonical_json([simulator, geom_ver, kwargs, measure]))[:12]` | |
| 事件 | `<主詞>_<動詞或狀態>` 小寫 snake；白名單在 `events.py` | `batch_dispatched`, `strategy_paused`, `profile_tamper` |

## Depot key 佈局（`paths.py` 的 key；`FileDepot(root)` 把它貼在 `EMFORGE_ROOT` 下＝下面這棵樹）

`paths.py` 分三節：**key 函式**（回字串，如 `record_file(profile, id, store)`）、**前綴函式**（回尾 `/`，只給 `list`／`newest`／`ensure_prefixes`）、
**本機路徑**（吃 `root` 回 `Path`：`registry_py`、`user_strategies_dir`、`strategy_workdir`）。協調狀態只經 `Depot`（doc／log／lease／列舉四種語義，見 `implementation.md` §3）。

```
<root>/
├── registry.py                        使用者 append-only 註冊表（profile / spec）
├── strategies/<name>.py               使用者策略（優先於內建）
├── limits.json                        這台儀器的上限（本機檔；init 留範本、沒有＝預設）
├── db/<profile>/<id>-<store>.npz      一筆一檔；_index.jsonl 增量索引；RETIRED 標記
├── ledger/<profile>/<spec>.json       一榜；含 _checksum；history append-only
├── queue/jobs.json  jobs.lock         共用佇列（全程持鎖）
├── queue/state/<store>.claim|.done|.fail   queue/STOP  queue/STOP.<tag>
├── queue/log/<tag>.jsonl              各 worker 單寫者事件檔
├── batches/<store>/manifest.json  patterns.npz  results/<id>.json
├── queue/ESTOP  queue/ESTOP.<tag>     急停（全機／單機）；本機層是 <root>/ESTOP
├── devices/<tag>/ state.json  reference.md  reference.json  log.jsonl  adhoc/<stamp>-<id>.json   儀器層（一台一目錄）
└── runtime_state/<profile>/ lock  strategies.yaml  state.json  status.json  events.jsonl  pending.jsonl  STOP
                             inflight/<store>.json  strategies/<name>/（策略 workdir，runtime 永不讀）
```

- `_` 前綴的檔＝可重建快取（`_index.jsonl`、`_imported.json`）。
- `registry.py`、`strategies/`、`runtime_state/<p>/strategies/`（策略 workdir）、`ESTOP`（本機急停）、`limits.json`（儀器上限）是**本機路徑**，不經 Depot；其餘全部是 Depot key。
- worker 本機工作目錄：`<EMFORGE_WORK>/<store>/`，啟動時整個清。

## 測試

- 檔：`tests/test_<module>.py`；子套件對應 `tests/worker/test_<module>.py`。
- 函式：`test_<單元>_<斷言的性質>`——名字說結論不說步驟：`test_pick_clears_ownerless_claim_older_than_60s`。
- 回歸測試 docstring 首行：`回歸 I-N（YYYY-MM-DD）：防止…`。
- 負對照寫在同一個測試裡（證明「有查」不是「查不到」）。
- 決定性雙向斷言：同 seed 相等、異 seed 不同。

## 註解

- `#!`＝事故疤：刪了產線會再壞一次；附日期／事故編號。
- `#?`＝設計理由：誰決定、為什麼；可重新決定。
- 續行 `#  `（兩空格），一區塊一標記。

## commit

`type: 摘要`（繁中），type ∈ `feat` `fix` `test` `docs` `chore` `refactor`。一里程碑一 commit。

## 2026-09-08 執行身分與歷程
Proposal / Record 新增可選 tag、run_id；Record.run 仍是模擬來源字典。
舊紀錄缺欄位讀為 None。策略子行程、dispatch、collect、公證沿用身分。
View 支援 query(tag/run_id/parent)、mine(run_id)、children、lineage、sample、runs。
lineage 包含自身，忽略公證自親代並防環；sample 依內容去重且 seed 決定性。
tag/run_id 使用既有 is_valid_name；算法名稱、執行編號與內容 hash 是不同身分。

## 2026-09-08 收件與 client
策略項可設 kind: inbox；batch/max_inflight/prio 沿用。client.submit 回 sid，
wait 預設等 completed，until="dispatched" 只等派工；results 可讀部分結果。
request_id 冪等、不同內容重用同 id 拒絕。不可變送件在 inbox/<profile>/<sid>.json，
進度在 inbox_status/<profile>/<sid>.json；每項保存 index/id/store/shared/state。
重複候選引用相同量測而非消失；收到但尚未派工、部分派工、已派工、完成、拒絕各有狀態。
已派工但尚未記進度時，由 inflight/Record.note._submission 重建，不另派。
算法 log 為 algo_logs/<profile>/<run_id>.jsonl（單寫者）；平台不解讀內容。
