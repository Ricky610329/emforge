# 命名規範

> 2026-09-12 新增：控制鎖 `platform_locks/control/{run,node,config}/<name>.lock`；worker 本機 `work.emforge/` 保存 PID 鎖及按 Depot 分區的結果待傳資料。詳細佈局見 [恢復契約](reliability-2026-09-12.md)。

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

## 2026-09-08 HTTP 平台
platform/service.py 是本機 client、HTTP /rpc、後續 MCP 的共同操作層。
platform-serve --root R --depot D --host 127.0.0.1 --port 8766 啟動。
非 loopback 需 EMFORGE_PLATFORM_TOKEN；client/HttpDepot 從環境讀 token，不放 URL。
submit --endpoint URL --profile P --name S --run-id R --patterns X.npz 回 sid；
inbox --endpoint URL --profile P 顯示全部送件。Python Client(URL, P, S, run_id=R) 介面相同。
worker 使用 --depot http://host:8766；仍需本機 registry.py 與 HFSS/Python 相依環境。
HTTP /depot 只接受固定 Depot 原語，/rpc 只接受固定平台操作。token 為受信任機隊的共享鑰匙，
具有儲存庫讀写能力，不是多租戶權限隔離；跨不可信網路需外接 TLS。
HTTP 使用標準函式庫，無新增必要套件；網路故障拋錯，不自動重送副作用命令。
所有 Depot 契約對真 socket HttpDepot 執行；check_prefix 現在與 check_key 同樣拒絕 .. 路徑。

## 2026-09-08 算法執行端
algorithm-register --endpoint URL --name anneal --source DIR 上傳文字程式碼包，
回 pkg_<sha256>；入口預設 main.py，requires 為需 import 的模組名，不代装環境。
algorithm-worker --endpoint URL --node gpu_a --work-root LOCAL --environment ant=PYTHON
登記節點；--max-runs 預設 1，--stop-timeout-s 預設 30。
algorithm-start --endpoint URL --name anneal --version pkg_... --run-id run_a --node gpu_a
--environment ant --profile P --budget 100；同 run_id 同內容冪等，不同內容拒絕。
algorithm-status / algorithm-logs / algorithm-stop 皆接受 --endpoint、--run-id。
algo_runs/<run_id>.json 分 desired 與 state；節點 session 與每次更新均驗所有權。
run_id、profile、版本、spec、seed、params、environment 固定；新版本新 run_id。
budget 是新增 sample 候選配額，共用已存在量測不扣；HFSS 重試/公證另計，不宣稱是總呼叫硬上限。
成本分提出數/共用數/新增量測數/已保存耗時，遺失的失敗嘗試耗時不在帳內。

節點的 runs/<run_id>/{source,work,stdout.log,STOP} 在本機，checkpoint 不刪。
Windows 使用 Job Object 在節點死亡時回收自己的行程樹；POSIX 入口監看父端 stdin。
節點重啟把未確認完成的舊執行標 interrupted，不自動換機重開。
明確提供相同 checkpoint_schema 的版本才可 --resume-from 已結束、同節點的執行。
算法由環境讀 EMFORGE_ENDPOINT / ALGORITHM / RUN_ID / PROFILE / SPEC / PARAMS / SEED，
EMFORGE_RUN_STOP 為合作退出旗標、EMFORGE_RESUME_FROM 為來源工作目錄。
EMFORGE_PLATFORM_TOKEN 只經環境傳遞，不寫程式包、參數或執行紀錄。

冪等 request_id 限定在 run_id 內；sid 是平台對兩者的雜湊，不同算法可各用 round_0。
client 等到 runtime 入庫才算完成，worker.done 只是前一個階段。
已驗證兩個真算法子行程、兩個假模擬端、真 socket 三輪迴圈。


## Release 與維護
- 共享 runtime_state/<profile>/MAINTENANCE.json：停止新派工的 owner 控制。
- inflight.intent：原子落地的派工意圖，含固定 manifest、job、patterns。
- releases-root 的 releases/<rel_sha256>/manifest.json 與 source/：驗證紀錄與程式快照。
- releases-root 的 service_state.json、service_request.json、service_child.json：行程狀態、更新要求、ready 證據。
- service.lock、release.lock：本機 supervisor 所有權及快照序列化鎖。
- service_logs/<launch_id>.log：每個平台子行程 stdout；不清除歷史。
- state.notarize_deferred：維護期间尚未派出的公證候選 id/store。
- identity.spec_snapshot、submission.spec_snapshot：固定評估定義，舊格式可缺。
所有磁碟名透過 paths.py；不使用通用任意檔案寫入 MCP。


## 2026-09-08 候選優先級與公平排程

完整規則及用法見 [priority-scheduling.md](priority-scheduling.md)。Proposal 新增可選 priority／purpose；priority 為 urgent／normal／background。
queue/scheduling.json（paths.queue_scheduling）保存 boosts 與 turns，所有更新經 jobs.lock。
inflight.intent 保持原始 job；Queue.list(original=True) 用於恢復核對，預設 list／pick 採有效優先級。
runtime state.inbox_turns 記錄 run 輪替；inbox 每批一筆，背景 propose 每次要求一筆。背景判斷改為同 profile 尚未認領的 job，不再被已執行中的前景擋住。
