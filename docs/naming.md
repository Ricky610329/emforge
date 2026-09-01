# 命名規範

> 程式碼裡的真相源是 `emforge/paths.py`（磁碟名、名字驗證、保留字）；本文是人讀版。
> 兩邊不一致時以 `paths.py` 與 `tests/test_paths.py` 為準，並修這份文件。

## 識別字

| 類別 | 規則 | 例 |
|---|---|---|
| 模組 | `snake_case` 單數名詞、扁平；檔名說出它做什麼 | `db.py`, `fs.py`, `worker/gate.py`, `worker/fuse.py` |
| **禁用檔名** | `utils` `util` `misc` `helpers` `common` `tools` `stuff` `dedust` | — |
| 類別 | `CapWords`；契約型別名照架構文件 | `Profile` `Spec` `Proposal` `Context` `Record` `Job` `Database` `View` `Ledger` `Pending` `Queue` `Runtime` `SimResult` |
| 假件 | `Fake*`（不叫 `Mock*`），住 `emforge/testing.py` | `FakeSimulator` |
| 函式 | 動詞開頭 `snake_case`；判斷式 `is_`/`has_`；私有 `_` 前綴 | `record_id()`, `try_claim()`, `is_stale()` |
| 常數 | `UPPER_SNAKE`；字串值本身小寫 snake | `STATUS_DONE = "done"`, `KIND_REPEAT = "repeat"`, `ARM_BLIND = "blind"` |
| 例外 | `CapWords` 名詞結尾，不加 `Exception`/`Error` 後綴 | `LockTimeout` `GeomVerMismatch` `ProposalError`（← 唯一例外：與 ValueError 對稱） `StrategyTimeout` `CrossProfileRefused` `AntennaUnavailable` |
| CLI 子命令 ↔ 函式 | kebab-case ↔ `cmd_<snake>` | `import-legacy` ↔ `cmd_import_legacy` |
| CLI 旗標 ↔ 屬性 | kebab ↔ `args.<snake>` | `--max-inflight` ↔ `args.max_inflight` |
| 環境變數 | `EMFORGE_` 前綴 | `EMFORGE_ROOT` `EMFORGE_ANTENNA_REPO` `EMFORGE_MACHINE` `EMFORGE_WORK` |
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
| **保留字**（不准當策略名） | `blind`（arm）、`repeat`（kind）、`notarize`、`runtime`、`cli` | — |
| arm | 自由字串，但 `blind` 是保留語義＝零演算法對照臂 | `"blind"`, `"L"`, `"d"` |
| kind | `sample` \| `repeat`；`repeat` 只有 runtime 公證與 `cli smoke` 能設 | |
| status | `queued` \| `running` \| `done` \| `error` | |
| 機器 tag | IP 末段字串；釘選＝完全相等 | `"216"` |
| store | `<profile>-<strategy>-t<tick:05d>`；公證 `<profile>-notarize-t<tick:05d>-<id[:8]>-r<n>` | `dual_p01_db075-top_k_flip-t00007` |
| Record id | `sha1(packbits(bits) + sim_profile)[:16]`，16 hex | `6c4e45d0ff24958e` |
| profile_hash | `sha1(canonical_json([simulator, geom_ver, kwargs, measure]))[:12]` | |
| 事件 | `<主詞>_<動詞或狀態>` 小寫 snake；白名單在 `events.py` | `batch_dispatched`, `strategy_paused`, `profile_tamper` |

## 磁碟佈局（`EMFORGE_ROOT` 下；每一項都有 `paths.py` 的函式）

```
<root>/
├── registry.py                        使用者 append-only 註冊表（profile / spec）
├── strategies/<name>.py               使用者策略（優先於內建）
├── db/<profile>/<id>-<store>.npz      一筆一檔；_index.jsonl 增量索引；RETIRED 標記
├── ledger/<profile>/<spec>.json       一榜；含 _checksum；history append-only
├── queue/jobs.json  jobs.lock         共用佇列（全程持鎖）
├── queue/state/<store>.claim|.done|.fail   queue/STOP  queue/STOP.<tag>
├── queue/log/<tag>.jsonl              各 worker 單寫者事件檔
├── batches/<store>/manifest.json  patterns.npz  results/<id>.json
└── runtime_state/<profile>/ lock  strategies.yaml  state.json  status.json  events.jsonl  pending.jsonl  STOP
                             inflight/<store>.json  strategies/<name>/（策略 workdir，runtime 永不讀）
```

- `_` 前綴的檔＝可重建快取（`_index.jsonl`、`_imported.json`）。
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
