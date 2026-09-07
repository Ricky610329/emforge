# 實作文檔（依程式碼寫；code 與本文不一致時以 code 與 tests/ 為準）

> 讀者：要動手改／接手的人。設計的「為什麼」在 `architecture.md`；本文只講「現在是怎麼做的」。
> 版本：emforge 0.1.0，274 條測試全綠（2026-09-01）；M12（2026-09-06）基礎設施解耦後 ~400 條；M13–M14 儀器層後 ~450 條。事故編號 `I-N` 見 `incidents.md`。

---

## 1. 一句話與四個框

昂貴模擬（HFSS，一筆 100–200 s）下的設計搜尋平台。**迴圈裡沒有 LLM**。

```
 AI 加值層（可選；改檔案＋跑 CLI；任何 harness 或人）
 ─────────────────────────────────────────────────────
 runtime 實例（一實例綁一 sim_profile）：collect → notarize → schedule
 ─────────────────────────────────────────────────────
 策略庫（<root>/strategies/*.py ＋ 內建）   模擬庫（registry.py 註冊 Profile）   資料庫（db/<profile>/，所有實例共享）
                                             worker（正式機；只認 Simulator 協定）
```

十條設計決定 D1–D10 見 `architecture.md` §11；本文 §13 列與該文件的偏差。

## 2. 模組地圖

```
emforge/
  __init__.py     __version__；不 import 子模組
  _version.py     describe() → "emforge=<sha7>[+dirty]"（worker_ver、runtime_ver 的成分）
  paths.py        命名唯一真相源：共享狀態回 **Depot key**（POSIX 相對字串／尾 `/` 前綴）；本機程式碼路徑（registry_py／
                  user_strategies_dir／strategy_workdir）回 Path；store 名／保留字／is_valid_name
  depot/          **Depot 介面**（M12）：base.py（doc／log／lease／列舉四種語義＋衍生 put_json／get_json／require_*／newest／
                  is_stale／lock）、file.py（FileDepot：key→root/key，逐位元＝今天的佈局）、memory.py（MemoryDepot：測試／單行程）、
                  uri.py（open_depot("file://…"|"memory://…")）。契約測試 tests/depot/test_contract.py 對每個後端同套跑
  fs.py           FileDepot 的檔案系統原語（atomic_write_*、read_bytes/read_json、try_claim/read_claim/release、mtime、append_jsonl）
                  ＋ sweep_dirs（本機工作目錄）。協調狀態的呼叫端**不直接用**；鎖＝Depot.lock（fs.Lock 已退場）
  events.py       EVENTS 白名單 ＋ emit(depot, key, event, /, **fields)
  model.py        契約 dataclass：Profile／Spec／Proposal／Context／Record／Job／SimResult／Simulator；record_id；pack_bits
  db.py           Database（一筆一檔 .npz、_index.jsonl 增量、寫綁 profile）＋ View（唯讀）
  specs.py        measure／spec 註冊表（append-only）；measure()／score()
  profiles.py     profile 註冊表；load_simulator_class／check_geom_ver／check_labels／make_simulator／is_retired／load_user_registry
  strategy.py     strategies.yaml、resolve／load／check_compatible、validate_proposals、make_context、propose_in_process／in_subprocess
  queue.py        Queue(depot)：jobs.json 持 Depot.lock、pick（租約 claim／接管：break_if_stale 沒破到就讓、.fail 用 delete() 仲裁）、
                  mark_done／mark_fail／requeue／release(store, tag)（只放自己的）、watch、STOP（空 doc）
  batches.py      Batch：manifest.json／patterns.npz／results/<id>.json
  ledger.py       Ledger（checksum；promote 唯一寫路徑）、Pending、rescore
  report.py       每策略報表、p_beats_blind、K_MIN 閘、跨 profile 拒、worker_ver 警告
  doctor.py       機器體檢
  netid.py        機器 tag（EMFORGE_MACHINE 或 IP 末段）
  heartbeat.py    背景心跳執行緒（runtime 鎖與儀器狀態字典共用）：fn 回 False → `lost` 旗標（鎖丟了就停，M13）
  testing.py      FakeSimulator、fake_measure、FAKE_PROFILE／FAKE_SPEC、register_fakes、make_fake_root、run_all_jobs
  worker/         loop.py gate.py guard.py fuse.py workdir.py batch.py（一檔一職責；batch／loop 經 device，gate／guard／fuse／workdir 是 leaf）
  device/         **儀器層**（M13–M14，MHS 式）：estop.py（急停三層）limits.py（Limits／check_preconditions／confirm token）
                  states.py（DeviceState 唯一真相、write_state／read_fleet）instrument.py（Instrument 狀態機＋租約＋Simulator 直傳＋simulate_once）
                  reference.py（說明檔 md＋json；程序清單＝MCP tools）
                  mcp_server.py（M15：這台的 MCP server——tools＝Instrument 程序一對一、resources device://*、bearer 中介層、
                  與 worker 同行程 daemon thread；**唯一** import mcp／uvicorn／starlette 處、且只在函式內，optional extra `emforge[mcp]`）
                  mcp_client.py（M16：對一台儀器下單 call_device／call_device_async；`device-simulate` 的本體；httpx2／mcp 只在函式內）
  runtime/        core.py reconcile.py schedule.py dispatch.py collect.py notarize.py
  strategies/     blind.py top_k_flip.py（內建；使用者同名檔蓋過）
  adapters/antenna/  _bind.py sim.py measure.py profiles.py（唯一碰 torch／antenna 的地方；sim.open() 在非主執行緒先 pythoncom.CoInitialize——MCP tool 跑 worker thread）
  legacy/antenna_import.py  舊 NAS 資料匯入（torch lazy）
  cli/            __init__（接線）base setup loops（run／worker [--serve]）show verdict control
                  device（fleet [--mcp-config]／device-state／device-describe／device-estop／device-serve／device-simulate）
```

守門測試（`tests/test_smoke.py`）：核心（`adapters/`、`legacy/` 以外）零 `torch/antenna/scipy/matplotlib/pandas/win32com` import；單檔 ≤ 400 行；單函式 ≤ 60 行；禁 `utils/misc/helpers/common/dedust` 檔名；
**Depot 守門**：核心每個模組必須在 `DEPOT_ONLY`（不 import pathlib／shutil／glob／emforge.fs、不 os.path／os.replace…、不 open(）／`DEPOT_ONLY_PARTIAL`（可用本機路徑，附理由）／`LOCAL_LAYER`（後端本體與本機層，附理由）三張清單之一。

**兩個根**（M12）：`--root`／`EMFORGE_ROOT`＝本機程式碼／設定根（registry.py、strategies/、策略 workdir、worker 工作目錄）；
`--depot`／`EMFORGE_DEPOT`＝共享協調狀態後端 spec（`file://<path>`／`memory://<name>`；未來 `s3://`、`sql://`），不給＝`FileDepot(root)`＝下面這棵樹、一個 byte 都不差。
`Runtime(root, profile, *, depot=None)`、`worker_loop(root, tag, *, depot=None)`、`Queue(depot)`、`Database(depot)`、`Batch(depot, store)`、`Ledger(depot, p, spec)` 都吃 `Depot | str | Path`。

## 3. 磁碟佈局與檔案格式（＝`FileDepot` 的 key 佈局）

每個 key 的**語義**（doc＝整份原子替換／log＝單寫者 append／lease＝互斥認領＋心跳＋過期破除／marker＝存在即真／local＝本機路徑、不經 Depot）與**寫者**標在右欄。

```
<root>/                                    EMFORGE_ROOT（NAS）；測試永不碰，用 tmp_path
├── registry.py                            local  使用者註冊表；runtime 與 worker 啟動都 runpy 它（雙邊同源）
├── strategies/<name>.py                   local  使用者策略（優先於內建）
├── db/<profile>/<id>-<store>.npz          doc    runtime／匯入器  Record：bits(packbits u8) shape response(f32 或空) has_response meta(json 字串)；永不覆寫
├── db/<profile>/_index.jsonl              log    同上（一 profile 一寫者）  一行一檔：{stem, store, worker_ver, id, sim_profile, status, score, strategy, arm, parent, tick, kind}；refresh 壓實用 rewrite_log
├── db/<profile>/RETIRED                   marker CLI retire  {by, at}
├── db/<profile>/_imported.json            doc    匯入器  legacy 匯入簽名 {store: {results_mtime, n_pt, n_records, …}}
├── ledger/<profile>/<spec>.json           doc    CLI promote／rescore  {profile, spec, best:{id,score,at,by,note,force}|null, history:[…], _checksum}
├── queue/jobs.json                        doc    任何加 job 者，全程持 jobs.lock  [Job…]
├── queue/jobs.lock                        lease  Depot.lock（owner=host:pid；stale 180 s 破）
├── queue/state/<store>.claim              lease  worker  {owner, at, prior_fail}；心跳＝每筆 touch；壞（無主 >60 s）／陳（老且批無進度）可接管
├── queue/state/<store>.done|.fail         doc    worker  done {machine, at, n_done, n_error, error_ids}；fail {machines, last, at}（接管用 delete() 仲裁）
├── queue/STOP  queue/STOP.<tag>           marker CLI  worker 收工（job 之間生效）；空 doc
├── queue/log/<tag>.jsonl                  log    該台 worker  單寫者事件
├── batches/<store>/manifest.json          doc    runtime  {store, sim_profile, profile_hash, strategy, tick, seed, prio, kind, items:[{id,parent,arm,note}]}；patterns 先、manifest 最後
├── batches/<store>/patterns.npz           doc    runtime  ids(<U16) packed(n, ceil(HW/8)) shape
├── batches/<store>/results/<id>.json      doc    持 claim 的 worker  {id, status, response, time_s, extra, machine, worker_ver, profile_hash, at, attempts} | {…, status:"error", error}；newest()＝進度心跳
├── queue/ESTOP  queue/ESTOP.<tag>      marker CLI device-estop  急停 {by, reason, at}（全機／單機；第三層 <root>/ESTOP 是本機路徑）；**解除只能 CLI**
├── devices/<tag>/state.json            doc    該台 Instrument  DeviceState（轉換即寫＋30 s 心跳；offline 由讀者用 modified_at 推導；寫失敗吞掉）
├── devices/<tag>/reference.md|.json    doc    該台 Instrument  說明檔（open 成功與每小時刷；程序清單＝MCP tools）
├── devices/<tag>/log.jsonl             log    該台 Instrument  裝置事件（device_*／lease_refused／estop_*）
├── devices/<tag>/adhoc/<stamp>-<id>.json  doc 該台 Instrument  simulate_once 結果（與批結果同格式、**不入 db**）
└── runtime_state/<profile>/
    ├── lock                               lease  該 profile 的 runtime  {owner=tag:pid:rand, pid, machine, at}；背景 30 s touch（鎖不是自己的 → lost → 下一圈停）；stale = max(600 s, 5×tick_s)；release 只刪自己的
    ├── strategies.yaml                    doc    人／init  見 §5；**內容 sha1** 變才重讀（不看 mtime）；無效沿用上次
    ├── state.json                         doc    runtime  {tick, strategies:{name:{errors_consecutive, paused, n_dispatched, last_dispatch_tick}}, paused_profile, notarize:{id:{stores,tick,score}}, seed_base}
    ├── status.json                        doc    runtime  每 tick 導出（人讀）
    ├── events.jsonl  pending.jsonl        log    runtime（CLI 的 promoted／retired／rescored／batch_requeued 也 append，低頻例外 §12-7）
    ├── control.json                       doc    CLI → runtime（resume）；tick 開頭消費並 delete
    ├── STOP                               marker CLI  runtime 收工（tick 之間）；空 doc
    ├── inflight/<store>.json              doc    runtime（abandon 可刪）  {store, strategy, tick, seed, kind, prio, ids, items:{id:{parent,arm,note}}, collected, at}；list 到但 get 回 None 就跳過
    └── strategies/<name>/                 local  策略 workdir（runtime 永不讀）；_proposals.npz 是同機子行程交接檔
```

`FileDepot` 之外的檔名：`.<name>.<pid>.<rand>.tmp`（原子寫入中）、`.selfcheck.*`（doctor 探針）、`<name>.broken.<pid>.<rand>`（破鎖證據）——都是後端內部，`list` 不列。

Record 檔名 `<id>-<store>.npz`，id＝`sha1(packbits(bits)+sim_profile)[:16]`；同 id 不同 store（公證重測）是不同檔；同名永不覆寫（`Database.add` 回 False）。

## 4. 契約

```python
Profile(name, simulator="module:Class", geom_ver, kwargs, shape, labels, n_points, fixed_on, measure, spec, timeout_s, retired=False)
    .profile_hash = sha1(canonical_json([simulator, geom_ver, kwargs, measure]))[:12]
Spec(name, labels, measure, axes, offsets).score(measure_dict) -> float|None      # min(m[a]+o)；缺軸/NaN → None
Proposal(pattern, parent=None, arm=None, note={})  ; Proposal.from_dict 嚴格鍵集（多 kind/sim_profile/score → ProposalError）
Context(db: View, profile, budget, rng, workdir, tick, params)
Record(id, sim_profile, bits, response|None, measure, score|None, status, strategy, arm, parent, tick, seed, note, kind, run, extra)
    run = {store, machine, worker_ver, profile_hash, time_s}；status ∈ queued|running|done|error；kind ∈ sample|repeat
Job(store, sim_profile, profile_hash, prio, n, machine=None, origin="runtime", by="", at="", extra={})   # 未知鍵保留
SimResult(response f32[n_labels,n_points], time_s, extra)
Simulator: geom_ver|None, labels; __init__(*, workdir, profile); open(); simulate(bits)->SimResult; kill(); close()
measure fn: fn(response, labels, targets) -> dict[str, number]；targets 由註冊表綁定（凍結）
```

## 5. runtime

### 5.1 一個 tick（`runtime/core.py::Runtime.tick`）

```
state.tick += 1 → save_state     #! tick 號一加就落地：中途死掉重啟不重播同號（review-3）
→ heartbeat（touch lock；鎖被破後 touch 是 no-op、不重建）→ apply_control（control.json：resume）→ reload_config（yaml **內容 sha1**）
→ new = collect()                # 增量收結果、量測、評分、入庫、收尾 inflight、錯誤率
→ notarize_step(new) → save_state   # 完成中的公證 → pending；新破榜候選 → 重測 ×repeat_n；登記後立刻落地
→ fleet_quiet()? 事件 fleet_quiet：schedule()   # 有 inflight 且 quiet_s 內整個機隊沒產出 → 只等（M14：正拿著我們的批在跑的儀器，其狀態字典心跳也算產出）
→ save_state → write_status
```
`run(once)`：acquire_lock（`Depot.claim`；stale → `break_if_stale`，沒破到＝別人先破，重試 claim 決勝負）→ 背景心跳執行緒（每 `heartbeat_s`=30 s touch 鎖；一個 tick 超過 stale 門檻也不會被第二個實例破鎖，review-7）
→ runtime_start → `db.refresh`（索引 append 前死掉的檔補回去，事件 index_repaired）→ `reconcile()` 不一致 → 事件 reconcile_mismatch、**return 2**（I-14）
→ 迴圈：heartbeat（鎖不見了或換主 → 事件 lock_lost、runtime_stop、**return 3**，M13 收 §12-16）→ STOP 檔 → runtime_stop；tick（例外 → `tick_error` 續跑，連續 `TICK_ERROR_LIMIT`＝10 次或 `--once` → **return 1**，檢查 #7）；sleep(tick_s)。起動時 `db.refresh` 跳過讀不到的紀錄並發 `db_unreadable`（檢查 #4）。
`status.json["fleet"]`＝`device.states.read_fleet` 摘要（tag／state／owner／store／n_done／offline…）。
`Runtime(readonly=True)`（CLI smoke／abandon 用）：不拿鎖、`save_state` 拋——別用舊快照覆寫跑著的 runtime（review-4）。

### 5.2 reconcile（`reconcile.py`）
inflight 無 job → `inflight_without_job`；inflight 的批缺 manifest 或 patterns → `inflight_without_batch`；本 profile、origin=runtime、非終態的 job 無 inflight → `job_without_inflight`。別的 profile／`cli:*` 不管。

### 5.3 schedule（`schedule.py`）
```
paused_profile → return
for sc in strategies 依 prio 升冪:
    disabled 或 paused → skip
    本策略 kind=sample 的 inflight ≥ max_inflight → skip          # max_inflight＝節奏（預設 1）
    prio ≥ background_prio 且（有前景 inflight 或 佇列有 queued job）→ skip   # D5，佇列全機共用
    seed = yaml seed 或 strategy_seed(seed_base, profile, name, tick)
    props = propose（子行程；逾時 → strategy_timeout＋strategy_error；任何例外 → strategy_error；連 strategy_error_limit 次 → paused）
    成功 → errors_consecutive=0；空 → strategy_empty；否則 dispatch
    dispatch 例外（StoreExists／鎖逾時／NAS）→ 事件 dispatch_failed，不計策略連敗、runtime 繼續（review-3）
```
### 5.4 dispatch（`dispatch.py`）
`kind=sample`：去重＝`db.ids(profile)`（只算 done）∪ 所有 inflight 的 ids ∪ 批內重複；全重複 → None。
`kind=repeat`：不去重、store 必須指定（只有 notarize／smoke 這樣叫）。
寫任何東西之前先查 inflight 檔或批是否已存在 → `StoreExists`（tick 重播的保險，review-3）。
順序：**先寫 inflight** → Batch.write（patterns 先、manifest 最後）→ Queue.add → 事件。簽名沒有 skip／bypass／keep_* 參數（測試釘死）。

### 5.5 collect（`collect.py`）
每個 inflight：`Batch.results(known_ids=collected)` 只讀新檔 → `profile_hash` ≠ 註冊表 → `profile_tamper`、不入庫（記為已收）→
done：`specs.measure(profile.measure, response, labels)`、`specs.score(profile.spec, m)` → `db.add` → 事件 record_added → **每筆後立刻**落地 inflight.collected（review-8）；
add 回 False 但這筆沒記到 collected（上次死在 add 與落地之間）→ 仍算新收、交給 notarize。
**error 結果在批未 done 前不入庫、不記 collected**——worker 補測輪會覆寫同一個結果檔，記了就永遠讀不到翻案（review-1）；批 done 才把殘留 error 收進 db。
終態只有 `done`：能收的都收了 → 移除 inflight、事件 batch_done；kind=sample 且 error 率 > max_error_rate → `paused_profile`、事件 profile_paused。
**`fail` 不是終態**（名單外的機器會接管重跑，review-2）：只發一次 batch_failed（inflight 記 `fail_reported`）、inflight 留著繼續收；
沒人接由人 `emforge abandon`：殘留結果（含 error）入庫、inflight 移除、佇列標 done（別台不再接）、事件 batch_abandoned；有新鮮 claim 拒。

### 5.6 notarize（`notarize.py`）
完成中：重測批都不再 inflight（queue 狀態 fail 的不算、不擋）→ scores=[原始]+重測；spread ≤ noise_floor → append pending
（`{id, tick, scores, conservative=min, spread, stores, at, status}`）、事件 notarize_pass；否則 notarize_reject。一個重測 store 死了就以其餘的判。
新候選：門檻＝max(榜首 score（經 `Ledger.best()` 走 checksum；被手改 → 事件 ledger_tamper、當沒有榜）, pending 最好的 conservative, 公證中的 score)；
new_records 中 done、kind=sample、score > 門檻的（依分數降冪）→ 派 repeat_n 個 store（`<profile>-notarize-t<tick>-<id8>-r<n>`，prio=notarize_prio），
派不出去 → dispatch_failed、不登記候選；門檻更新為該分數。**永不寫榜。**
`smoke_dispatch(rt, id, n, machine, by)`：CLI smoke 用，strategy="cli:smoke"、origin="cli:smoke"、可釘機。

### 5.7 strategies.yaml
```yaml
profile: dual_p01_db075            # 必須＝--profile
runtime: {tick_s: 60, background_prio: 9, notarize_prio: 1, repeat_n: 2, noise_floor: 0.3, k_min: 20,
          quiet_s: 3600, max_error_rate: 0.5, propose_timeout_s: 600, strategy_error_limit: 3}
strategies:
  - {name: top_k_flip, prio: 3, batch: 60, max_inflight: 1, enabled: true, seed: null, propose_timeout_s: null, params: {k: 10, d: 3}}
  - {name: blind, prio: 9, batch: 20}
```
未知鍵、重名、保留字（repeat／notarize／runtime／cli）、名字不合規、profile 不符 → ConfigError。**數值全是佔位預設**（跨域要重調）。

## 6. worker

`worker_loop(root, tag, *, depot=None)`：載入本機 registry.py → 印 worker_ver ＋ depot spec → 清 `<EMFORGE_WORK>/`（I-1）→ worker_start → 迴圈：STOP（job 之間）→ `Queue(depot).pick(tag)` → `gate(job, depot)` → `run_batch` → mark_done／mark_fail。守門不過＝那批 `.fail`、worker 繼續；`once=True` 跑完第一個真正執行的 job 就回。
**儀器層**（M13）：worker 經 `device.Instrument` 跑批——`Instrument` 實作 Simulator 協定（`bind/open/simulate/kill/close`），`run_batch(..., instrument=inst)` 把它當 sim 用，逐筆邏輯不變。
多的是：主迴圈**急停中 job 之間不撿**（等 poll_s 再看）；`inst.acquire("queue:<store>")` 拿不到（MCP 持有）→ 放掉 claim、`job_yield reason=device_busy`、等一輪；
每筆前急停 → `job_yield reason=estop_engaged` 並放掉**自己的** claim（急停可能只停這台，別台可續跑）；正在跑的那筆由 `guard.guarded_call(abort_if=急停, poll_s=5)` 殺掉 → 結果 `error: aborted: estop_engaged`（吃一次 attempts、不重開、不算保險絲）。
`Instrument.open()` 前：急停硬檢查 → `limits.check_preconditions`（allowed_profiles → `gate.check` → timeout_s ≤ max_sample_s → `doctor.health` 阻擋；開過一次後「ansysedt 在跑」不算阻擋——那是自己的）。
`inst` 沒給就自建、收工時 `stop()`；M15 的 MCP server 與迴圈共用同一台。
**故障邊界**（外圈，檢查 #7）：一圈裡（STOP／急停／pick／log／release）的任何例外 → `worker_error` 事件、睡一輪續跑，連續 `WORKER_ERROR_LIMIT`＝10 圈才收工回 1；`--once` 遇急停給一個 poll 寬限再 `worker_stop reason=estop`（檢查 #15）。**故障邊界**（內圈，review-6）：gate 之後的任何例外（FsBusy／PermissionError／FileNotFoundError…）→ 那批 `.fail` 記 `worker_exception:…`、claim 釋放、工作目錄清掉、**worker 繼續**下一個 job；`fs.release` 對 sharing violation 退避重試；接管 `.fail` 時檔已被別台搶走＝輸了競賽、跳過。

`gate`（順序固定、不建構不 open）：profile 註冊且未退役 → job.profile_hash == 註冊表 → 載入類別、geom_ver（模擬器宣告 None＝單邊跳過）→ labels。

`run_batch`：建工作目錄 → 建模擬器 → `open_with_retries`（3 試，各 300 s 看門狗，失敗間 kill＋等 15 s）→ 第 0 輪跑「未 done **且 attempts<3**」（續跑；毒樣本三振後不再跑，review-10）→ 補測輪跑「error 且 attempts<3」（每輪前殺透重開）→ 每筆：`guarded_call(simulate, timeout_s, kill)` → 逐筆結果檔（含 machine／worker_ver／profile_hash／attempts）→ `touch_claim`（claim 心跳）→ error：看門狗逾時 → 重開；第 0 輪走保險絲（連 max_fail 敗 → 冷卻 cooldown_s＋重開；第 max_blowout 次 → 回 "fail"）；補測輪連 3 敗放棄本輪 → 每筆後：claim 被別台接走 → "yield"（不動 claim）；背景 job 遇前景 queued → 釋放 claim、"yield" → 結束一律 close＋刪工作目錄。
`requeue` 的「有人正在跑」＝claim 新鮮**或**批有進度（`Queue.is_live`）；`watch` 在 `.fail` 被接管刪掉的瞬間不誤判 FAIL。
天線 adapter：看門狗 `kill()` 過的模擬器在 except 路徑**不**呼叫舊 `end()`（舊 end 內部會自己 reopen、無守門會卡，review-9）；交給 `_restart`。
worker **不**量測、不評分、不寫 db。

`Queue.pick`（M12c 順序）：prio 升冪；done／釘機不符跳過；`.fail` 名單含我 → 跳過；claim 判定：自己的 → 續跑；別人的且（批有進度 <stale_s 或 claim 新鮮）→ 讓；無主（空／半截）未過 60 s → 讓；
→ 有 `.fail` 就 `delete()`（回 False＝別台先接走 → 讓）→ 陳 claim `break_if_stale`（回 False＝別台先破 → 讓）→ `claim`（O_EXCL 仲裁；prior_fail 帶進新 claim）。
**新鮮 claim 在任何路徑都不會被動到**；破鎖不是仲裁（Windows 兩個 rename 可都成功，見 `depot/file.py`），claim 才是。

## 7. 寫一個策略

```python
# <root>/strategies/my_strategy.py
COMPATIBLE = {"dual_p01_db075"}          # 或 {"*"}
def propose(ctx):
    top = ctx.db.top(ctx.params.get("k", 10))        # 保守值排序；ctx.db 只有 query/top/mine/measurements
    ...
    return [dict(pattern=bool_array, parent=rec.id, arm=None, note={})]   # ≤ ctx.budget 筆
```
規則（`strategy.validate_proposals`）：list；≤ budget；pattern shape＝profile.shape、bool 或 0/1；fixed_on 像素必為 True；只能有 pattern/parent/arm/note 四鍵。狀態放 `ctx.workdir`；上批回饋用 `ctx.db.mine()`；`ctx.rng` 已 seed（inflight 記 seed，可重現）。試跑：`emforge check-strategy --root R --profile P --strategy my_strategy --budget 5`。正式由 runtime 在**子行程**呼叫（逾時可殺、例外不傳染）。

## 8. CLI

每個吃 `--root` 的命令也吃 `--depot <spec>`（或 `EMFORGE_DEPOT`）；不給＝`FileDepot(root)`。`run --depot memory://…` 自動改 in-process（子行程看不到）。

| 命令 | 做什麼 | exit |
|---|---|---|
| `init --root R [--depot D] [--profile P]` | 佈局前綴與 strategies.yaml 範本進 depot；registry.py／strategies/／limits.json（儀器上限範本，M16）在本機 root（不覆寫） | 0 |
| `version`／`doctor --root R [--depot D] [--hfss]` | 版本戳／體檢（root 探針、`depot.selfcheck()`：可寫／O_EXCL／時鐘偏移、磁碟、ansysedt） | 0；doctor 阻擋 4 |
| `check-strategy --root R --profile P --strategy S [--budget --seed --tick --params]` | 本行程試跑 propose | 0／1 |
| `run --root R --profile P [--once] [--in-process]` | runtime 實例 | 0；對帳不符 2；鎖被占 3 |
| `worker --root R [--machine-tag T --poll-s --once --work-root --bg-prio --max-fail --cooldown-s --max-blowout --retry-passes] [--serve --host H --port N]` | 正式機 worker；`--serve`＝同行程再起這台的 MCP server（daemon thread；host／port 預設 `EMFORGE_MCP_HOST`／`EMFORGE_MCP_PORT`，token 讀 `EMFORGE_DEVICE_TOKEN`；非 loopback 無 token 拒起） | 0；拒起 1 |
| `status／events [--last N --event E]／pending／jobs [--all]` | 讀狀態 | 0 |
| `watch --root R --stores a,b [--poll-s --fail-grace-min --timeout-min]` | blocking 等終態 | 0 全 done／1 fail／2 逾時 |
| `report --root R --profile P [--profile Q --cross-profile] [--k-min]` | 每策略報表 | 0；跨 profile 無旗標 2 |
| `promote <id> --root R --profile P --by WHO [--spec --note --force]` | 換王（唯一寫榜路徑）；分數用該榜的 spec 對 db 量測重算取 min | 0；不在 pending 3；榜被手改 5 |
| `retire --root R --profile P --by WHO` | 凍結 profile | 0 |
| `rescore --root R --profile P --spec S --by WHO [--force]` | 換評估器建新榜 | 0；榜已存在 6 |
| `requeue <store> --root R --by WHO` | 原子清 claim+done+fail | 0；有人正在跑（claim 新鮮或批有進度）7 |
| `abandon <store> --root R --profile P --by WHO` | 放棄 fail 沒人接的批：殘留入庫、inflight 移除、佇列標 done | 0；不在 inflight／有人正在跑 1 |
| `resume --root R --profile P [--strategy S] --by WHO` | 寫 control.json | 0 |
| `stop --root R (--profile P | --worker [--machine-tag T]) [--clear]` | STOP 檔 | 0 |
| `smoke <id> --root R --profile P --by WHO [--machine T --n N]` | 對已量 id 派重測（切機驗同一儀器） | 0 |
| `import-legacy --root OLD --out R [--depot D] --stores g1,g2 [--profile auto --map s=p --include-errors --verify --dry-run --force --no-rad]` | 舊資料匯入（舊樹＝本機路徑只讀；輸出經 depot） | 0；verify 不符 2 |
| `fleet --root R [--mcp-config]` | 機隊儀表板（每台狀態字典＋offline 推導＋全機 ESTOP 提示）；`--mcp-config` 吐 `.mcp.json` 片段（`emforge-<tag>`：url＝各台狀態字典的 `url`、header `Authorization: Bearer ${EMFORGE_DEVICE_TOKEN}`） | 0 |
| `device-serve <tag> --root R [--host H --port N --work-root W]` | 只起這台的 MCP server、**不撿佇列**（單筆試量、或這台暫時不當 worker）；阻塞到 Ctrl-C | 0；非 loopback 無 token 1 |
| `device-simulate --url U --profile P --bits B\|@file [--confirm T --by WHO --timeout-s S]` | 經 MCP 對一台儀器跑一筆（兩段式：先印 token，再帶 `--confirm`）；token 讀 `EMFORGE_DEVICE_TOKEN`；結果印 JSON、不入 db | 0；tool 錯誤（`device_busy:…` 等）1 |
| `device-state <tag>`／`device-describe <tag> [--json]` | 一台的狀態字典／說明檔（open 成功後才有） | 0；尚無 1 |
| `device-estop engage [--tag T \| --local] --by WHO --reason R` | 按急停（全機／單機／本機層）；儀器 open／simulate 硬擋、正在跑的那筆 abort、worker 不撿 | 0；缺 --reason 1 |
| `device-estop clear [--tag T \| --local] --confirm` | **唯一**解除急停的路徑（MCP 沒有）；--tag／--local 互斥 | 0；沒 --confirm 2；沒東西可清 1 |

任何未預期例外 → stderr `emforge: <Type>: <msg>`、exit 1。

## 9. 事件（`events.py`，封閉集合）

runtime：`runtime_start runtime_stop lock_lost tick_error reconcile_mismatch config_reloaded config_invalid fleet_quiet profile_paused profile_resumed index_repaired db_unreadable`
策略：`strategy_loaded strategy_rejected strategy_error strategy_timeout strategy_paused strategy_resumed strategy_empty proposals_validated(n_in,n_dup,n_out)`
派收：`batch_dispatched dispatch_failed record_added batch_done batch_failed batch_abandoned batch_requeued profile_tamper`
公證：`record_candidate notarize_dispatched notarize_pass notarize_reject`
榜：`promoted retired rescored ledger_tamper`
worker（queue/log/<tag>.jsonl）：`worker_start job_claimed sample_done sample_error job_done job_failed job_yield(reason: claim_taken_over|foreground_job_appeared|estop_engaged|device_busy) gate_rejected sim_restart worker_error worker_stop`
儀器（devices/<tag>/log.jsonl，單寫者＝該台 Instrument）：`device_start device_stop device_fault device_simulate device_abort lease_refused estop_engaged estop_cleared device_serve(url,host,port,auth) device_stop_worker device_resume_worker`（後三個＝M15 MCP）

## 10. AI 加值層怎麼接

讀：`status`／`events`／`pending`／`jobs`／`report`（或直接讀對應檔）。
寫：`strategies.yaml`（開關、prio、batch、params）、`<root>/strategies/<name>.py`（新策略）、`registry.py`（新 spec／profile，append）。
命令：`promote`／`retire`／`rescore`／`requeue`／`resume`／`stop`／`smoke`。
紅線（沒有這些路徑）：改榜檔（checksum 抓）、設 kind=repeat（Proposal 沒這欄）、繞去重（dispatch 沒旁路）、改已註冊 profile 內容（RegistryConflict）。
喚醒源：`watch` 的 exit code、`events.jsonl` 追檔、`status.json`——任何 harness 的 cron／輪詢／人都行。

## 11. 事故 → 防線 → 釘住的測試

| 事故 | 防線 | 測試 |
|---|---|---|
| I-1 磁碟塞爆 | done/fail/yield 皆刪工作目錄；啟動整清；契約無 keep_* | worker/test_workdir::sweep_all…、worker/test_batch::workdir_removed…、test_model::…no_keep_project_field |
| I-2 殭屍/壞 claim | 無主 >60 s 清；stale 看批進度；requeue 原子三清 | test_queue::pick_clears_ownerless…、pick_takes_over_stale…、requeue_clears…；test_fs::read_claim_empty… |
| I-3 佇列並發 | Lock（O_EXCL、rename 破鎖）＋原子寫 | test_fs::lock_serializes_counter、lock_breaks_stale_by_rename；test_queue::concurrent_add_loses_no_job |
| I-4 背景炸機 | 策略子行程；連 N 次暫停；runtime 永活 | test_strategy::child_exception…parent_alive；runtime/test_schedule::three_consecutive_errors… |
| I-5 全史掃描 | _index.jsonl append；collect 增量 | test_db::index_appended_not_rebuilt；test_batches::read_results_incremental |
| I-6 用錯儀器 | 實例綁 profile；manifest 帶 profile_hash；COMPATIBLE；gate 在 open 前 | test_strategy::check_compatible…；worker/test_gate::gate_order…；adapters::labels_shape…before_construct |
| I-7 幾何幻影 | era≡profile 進 id；GEOM_VER 雙邊（dual）；report 拒跨 profile | test_model::record_id_differs_by_profile；test_profiles::geom_ver_mismatch…；adapters::dual_geom_ver…；test_report::cross_profile |
| I-8 退出碼被吞 | dispatch 無旁路；每命令回 int、main 不吞 | runtime/test_dispatch::no_dedup_bypass；test_cli::promote_unknown_id_nonzero、main_converts_exceptions |
| I-9 CI 重錨 | 無 golden、無自動寫回 | **紀律**（CLAUDE.md） |
| I-10 只拉不重啟 | worker_ver 蓋每筆；profile_tamper；report 雙版本警告；start_worker.cmd 綁 pull | worker/test_batch::result_stamps…；runtime/test_collect::profile_tamper；test_report::two_worker_vers；worker/test_loop::prints_worker_ver |
| I-11 執行緒綁基準 | 無位元級基準；bootstrap 帶 seed | test_report::same_seed_equal（**紀律**） |
| I-12 harness 殺背景 | 狀態全在磁碟、可續跑 | worker/test_batch::resumes_skipping_done；test_e2e::restart_mid_batch |
| I-13 派工沒掛偵測 | inflight 先寫 | runtime/test_dispatch::writes_inflight_before_job |
| I-14 未落地疊工 | 啟動 reconcile 不符→exit 2 | runtime/test_reconcile::…run_refuses |
| I-15 多機覆寫 | 一筆一檔永不覆寫；逐筆結果檔 | test_db::same_id_same_store_keeps_first；test_batches::per_id_merge_semantics |
| I-16 自己的錯只有稽核抓到 | §12 清單供稽核 brief；blind 不足只印數字 | test_report::numbers_only_when_blind_below_k_min（**紀律**） |

## 11b. 2026-09-05 獨立審查 → 修正 → 釘住的測試

| review | 修了什麼 | 測試 |
|---|---|---|
| 1 error 記 collected 擋掉補測翻案 | error 等批 done 才收 | runtime/test_collect::error_waits_for_retry_until_batch_done |
| 2 fail 當終態、接管機結果沒人收 | fail 非終態、abandon 命令 | runtime/test_collect::fail_is_not_terminal…、abandon_*；test_cli::abandon_cli |
| 3 tick 尾才存、重播撞 BatchExists、dispatch 例外殺 runtime | tick 號先存、StoreExists 先擋、dispatch_failed | runtime/test_core::tick_number_is_saved_before_dispatch…、test_dispatch::refuses_existing_store…、test_schedule／test_notarize::survives_dispatch_failure |
| 4 smoke 覆寫 state.json | readonly Runtime | test_cli::smoke_cli_does_not_touch_state_json；runtime/test_core::readonly_runtime_refuses… |
| 5 Lock 破鎖分支忙迴圈 | 每圈走逾時檢查＋sleep | test_fs::lock_times_out_when_claim_keeps_failing…（紅測試真的卡住 300 s 重現） |
| 6 worker 無故障邊界 | _handle try/except、fs.release 重試、接管競賽 | worker/test_loop::survives_filesystem_error…；test_fs::release_retries…；test_queue::take_over_fail_lost_race… |
| 7 心跳只在 tick 頭 | 背景心跳執行緒 | runtime/test_core::heartbeat_thread_keeps_lock_fresh… |
| 8 collected 整批後才落地、索引缺檔 | 每筆落地、add False 仍算新收、啟動 refresh | runtime/test_collect::persists_collected_per_record…、run_refreshes_index_at_start |
| 9 被殺後叫舊 end 觸發 reopen | `_killed` 旗標 | adapters::simulate_after_kill_does_not_call_legacy_end |
| 10 第 0 輪不看 attempts | attempts<3 | worker/test_batch::pass0_skips_poison_samples… |
| 砍掉的 | promote 依本榜 spec 重算；_threshold 走 checksum；requeue 看進度＋claim 心跳；watch 接管瞬間；匯入器預設鍵；例外改名 | test_ledger::promote_with_other_spec…；runtime/test_notarize::threshold_on_tampered_ledger…；test_queue::requeue_refuses_when_batch_has_recent_progress…、touch_claim…、watch_treats_vanishing_fail…；legacy::map_profile_ignores_solver_keys… |

## 11c. 2026-09-07 全面檢查 → P0／P1／P2 修正 → 釘住的測試

完整清單 `docs/review-2026-09-07.md`（1 S1／24 S2／20 S3）；P0 十條、P1 十條、P2 七條已修（同日兩輪），剩 P3 與 #12。

| 檢查 # | 修了什麼 | commit | 測試 |
|---|---|---|---|
| 1（S1）MCP 租約同名重入、同機兩個 HFSS | `acquire` 只讓 `queue:<store>` 重入；`simulate_once` owner 一次性 `mcp:<by>:<rand>` | `08ac3fe` | device/test_instrument::mcp_lease_never_reenters…、concurrent_simulate_once_with_default_by_runs_exactly_one |
| 2 abandon 被 .fail 遮蔽、正在量的批標 done | `abandon` 用 `queue.is_live`（與 requeue 同尺） | `d4f1eb2` | runtime/test_collect::abandon_refuses_when_fail_marker_coexists_with_fresh_claim… |
| 3 公證重測當獨立 blind 樣本 | `report._blind_reference` 排除 `kind=repeat` | `9ce2b5f` | test_report::blind_reference_excludes_notarize_repeats |
| 5 一拍讀不到鎖＝永久 lock_lost | 讀到別人的 owner 才丟；None 連續 3 拍才丟；`read_claim` 退避 | `d4f1eb2` | runtime/test_core::heartbeat_treats_unreadable_lock_as_unknown…、run_survives_single_lock_read_blip；test_fs::read_claim_retries… |
| 6 急停在 open 前被當卡住、寫進 .fail | `open_with_retries(fatal=(EstopEngaged, PreconditionFailed))`；急停 → 讓位 | `d4f1eb2` | worker/test_batch::…yields_when_estop_engaged_before_open、…precondition_failed_is_fail_without_retries；worker/test_loop::estop_between_pick_and_open… |
| 8 doctor 探針只綁 pid | `.doctor_probe_<pid>_<rand>`、finally 釋放 | `08ac3fe` | test_doctor::probe_root_is_concurrency_safe_and_ignores_leftover_probes |
| 9 confirm token 同窗同值 | nonce 式：每次不同、`_issued`、消費即失效、10 分鐘到期 | `08ac3fe` | device/test_instrument::confirm_tokens_unique_per_issue_bound_to_op_key_and_expire |
| 10 deploy 把 EMFORGE_ROOT 指 NAS | deploy.md §2：本機 root ＋ `EMFORGE_DEPOT=file://T:/…`；start_worker 警告 | 本 commit | —（文件；fail-closed 留 P1，§12-28） |
| 13 MCP 埠撞靜默死、url 已宣告 | `server.started` 才 announce；綁不上 → RuntimeError → CLI exit 1 | `8502f8a` | device/test_mcp_server::serve_in_thread_raises_and_does_not_announce…、…real_socket_announces_after_bind…；test_cli::worker_serve_reports_mcp_bind_failure…、device_serve_reports_bind_failure… |
| 14 EMFORGE_MCP_PORT 壞值全命令 traceback | `--port` 走 argparse 型別（只在用到時轉） | `8502f8a` | test_cli::mcp_port_env_bad_value_only_bites_serve_commands_via_argparse |
| 4 一個壞 .npz 讓 run 起不來；索引真清空不壓實 | `Reader.try_load` 跳過並記 `unreadable`、refresh／View 走它、索引剔除靠 `exists()`；起動發 `db_unreadable` | `1799a8f` | test_db::refresh_skips_unreadable_record…、refresh_skips_record_listed_but_gone…、view_skips_record_whose_file_became_unreadable、refresh_drops_index_line_only_when_file_confirmed_missing；runtime/test_core::run_starts_despite_corrupt_record_file… |
| 11 策略的 View 反手可寫、未綁 profile | 讀的一半拆 `Reader`，View 只持 Reader；`make_context` 綁 `write_profile`；refresh 也過守門 | `1799a8f` | test_db::view_holds_no_database_reference、refresh_refuses_other_profile_when_write_bound；test_strategy::make_context_database_is_write_bound… |
| 7 worker 主迴圈／tick 沒有故障邊界 | `_loop` 外圈 try：`worker_error` 續跑、連續 10 才回 1；`Runtime.run` 的 tick 同（`tick_error`） | `7e83a76` | worker/test_loop::loop_survives_depot_error_in_pick…、loop_exits_1_after_ten_consecutive_errors；runtime/test_core::run_survives_transient_tick_error…、run_exits_1_after_ten_consecutive_tick_errors、run_once_returns_1_on_tick_error |
| 15 急停中 --once 不返回 | 一個 poll 寬限後 `worker_stop reason=estop` | `7e83a76` | worker/test_loop::loop_once_returns_when_estop_stays_engaged |
| 20 機器 tag 靠 IP 探測 | 沒 --machine-tag／EMFORGE_MACHINE → stderr 警告；start_worker.cmd 拒起；deploy §2 setx | `7e83a76` | test_cli::worker_warns_when_machine_tag_comes_from_ip_probe |
| 21 device-estop --tag 與 --local 同給、clear 沒東西回 0 | 互斥組（argparse exit 2）；沒東西可清 exit 1 | `7e83a76` | test_cli::device_estop_tag_and_local_are_mutually_exclusive… |
| 16 limits.json 不驗型別 | `Limits.__post_init__` 驗型別／範圍，`load_limits` 指名檔案與欄位 | `203b32f` | device/test_limits::limits_json_rejects_wrong_types_naming_the_file_and_field |
| 17 _restart 的 kill 打空 | kill→close→open | `203b32f` | worker/test_batch::restart_via_instrument_kills_before_close… |
| 18 close 無處決線 | `guard.close_quiet`（60 s 逾時 kill）；Instrument.close 與批收尾都走它 | `203b32f` | device/test_instrument::close_has_a_watchdog…；worker/test_batch::close_quiet_has_a_watchdog… |
| 19 磁碟守門量錯碟 | `doctor.health(work_root=)`；儀器傳 `self.work.root` | `203b32f` | test_doctor::health_measures_the_given_work_root…；device/test_instrument::open_checks_free_space_of_the_instrument_work_root |
| 22 子行程 depot 路徑零覆蓋 | 兩條真子行程測試 | `8777f07` | test_strategy::propose_in_subprocess_reopens_file_depot_under_cjk_root；runtime/test_core::run_once_with_default_subprocess_propose |
| 23／25 測試耦合真機狀態與環境變數 | conftest autouse 釘體檢、pop 整組 EMFORGE_* | `8777f07` | test_smoke::suite_is_pinned_against_live_machine_state_and_env |
| 24 一條測試無終止上限 | sleep 計數超過 20 就 request_stop | `8777f07` | worker/test_loop::loop_stop_file_finishes_current_job_then_exits |
| 31（＋#27）MemoryDepot 註冊表 | 匿名不註冊、同名拋、`clear_registry`、未註冊的 `memory://` 拋 | `8777f07` | depot/test_memory::open_depot_memory_same_name_same_instance |
| 44 六條靜默 skip | pytest 表頭印啟用／跳過；README／CLAUDE 註明 | `8777f07` | —（表頭） |
| 45 Queue 鎖 owner 非每實例獨一 | 加 urandom 後綴；契約補 lock 層測試 | `8777f07` | test_queue::queue_lock_owner_is_unique_per_instance；depot/test_contract::lock_holder_broken_and_reclaimed_does_not_delete_the_new_lock_on_exit |

## 12. 已知失效模式（給獨立稽核的 brief 用）

1. stale-claim 接管、runtime 鎖、fleet_quiet 都靠 **`modified_at`（伺服器側）vs 本機 `now()`**：各機時鐘不同步會誤判——`doctor` 的 `depot.selfcheck()` 量偏移 >30 s 就阻擋，不修正。
2. `noise_floor／k_min／quiet_s／max_error_rate／strategy_error_limit` 全是佔位數字，沒有任何一個經過本域以外的校準。
3. 冷啟動：資料庫空、榜空 → 每個「新最好」都觸發公證重測，前幾 tick 重測比例偏高（`notarize_min_score` 尚未實作）。
4. single 的 geom_ver 是**單邊**宣告（舊 single_port.py 無 GEOM_VER，Ricky 決定不動舊 repo）：single 幾何若換代不會被抓。
5. `arm="blind"` 是策略自我宣告；說謊會留紀錄但不會被擋。
6. 背景策略的「佇列空」看**全機共用佇列**：另一個 profile 的實例有 job 排隊時，這個實例的背景策略也不派（設計如此，但可能讓人以為「卡住」）。
7. CLI 的 promoted／retired／rescored／batch_requeued／batch_abandoned append 到 runtime 的 events.jsonl，違反單寫者契約的低頻例外（SMB 上理論上可能交錯）。
8. `_index.jsonl` 若兩個寫者同時 add 同一 profile（設計禁止：一實例一 profile；匯入器與 runtime 不同時跑）會有重複行——`refresh` 以 dict 合併可容忍，但檔會變胖；runtime 只在啟動時 refresh，跑著的時候匯入器加的檔要到下次啟動才進去重集合。
9. legacy 匯入的親代解析只做「legacy id 全域唯一才解」，不重建血統鏈；同 pattern 多 y 靠舊 wm 欄 2 位小數對回，理論上可能對錯。
10. `propose_in_subprocess` 每 tick 每策略多 1–2 s 子行程啟動；策略多時 tick 變慢（可接受，換 I-4 隔離）。
11. `worker_ver` 目前只含 emforge sha；adapter（antenna repo）的 sha 由 `_bind.antenna_sha()` 提供但**尚未**拼進 worker_ver。
12. `Simulator.kill()` 對 HFSS 是殺**全部** ansysedt.exe：同機第二個 HFSS 使用者會被誤殺（doctor 拒起）。
13. 策略層（N 個策略並行）沒有實測資料：所有「多樣性從策略池湧現」都是推論（architecture.md §12）。
14. `fail` 非終態的代價：一批被所有機器判死後會**永遠 inflight**（策略被 max_inflight 卡住、notarize 除外），直到人 `abandon`——status.json 的 `queue_state=fail` 是唯一提示，沒有自動逾時。 `abandon` 用 `is_live`（與 requeue 同一把尺）：`.fail` 與別台的新鮮 claim 並存（接管後原主判死）時會拒——先 `stop` 那台（檢查 #2：以前看 `state()=="claimed"` 會放行、把正在量的批標 done）。
15. `abandon` 與跑著的 runtime 的 collect 有一個很小的競賽窗（runtime 記憶體裡的 inflight 在 abandon 刪檔後不會再寫回，但同一 tick 內兩邊可能各 add 同一筆——db.add 冪等，只是事件可能各發一次）。
16. 心跳執行緒與主迴圈共用 `Depot.touch`。讀到**別人的** owner → 立刻 `lost`、主迴圈下一圈 `lock_lost`＋停（回 3；正在跑的 tick 跑完）。讀不到（缺／SMB 瞬斷）＝不知道：連續 `LOCK_UNREADABLE_BEATS`＝3 拍（30 s × 3）仍讀不到才算丟（檢查 #5：以前一拍讀不到就永久停）。代價：鎖真的被清掉後最多再跑約 2 個 tick；那段時間第二個實例若拿到鎖，兩邊各跑一個 tick（dispatch 去重靠 db／inflight）。
17. `promote --spec` 的分數重算用 `Record.measure`（量測凍結）；spec 若換了 measure 名（＝換儀器）`rescore` 會拒，但 promote 不會——它只認 spec 名有沒有註冊。
18. `FileDepot.break_if_stale` 不是仲裁：Windows 上兩個並發 rename 可以都成功（契約測試實抓）；破鎖後一律接 `claim`，claim 才決勝負。mtime 身分檢查把誤搬的新鎖放回去，但「放回」在第三方剛好又 claim 到的微秒窗會失敗（留 `.broken.*` 證據、回 False）。
19. `list` 可最終一致的後端（S3）：`db.refresh` 索引剔除只在 `exists()` 確認缺檔時做（不靠列舉為空）、列到但讀不到／解不開的跳過並記 `unreadable`（View 的 query／top／measurements 同；起動發 `db_unreadable`，檢查 #4）；`inflight()`、`Batch.results` 對「列到但讀不到」跳過。其餘列舉呼叫端（`report`、`profiles()`）還沒逐一審過。
20. `memory://` depot 只存在本行程：`run` 自動 in-process；未註冊的名字 `open_depot` 直接拋（另一個行程永遠拿不到、也不會靜默開出空庫，檢查 #31／#27）——只給測試與單行程 demo，用前要在本行程 `MemoryDepot(name=…)`。
21. 急停的三層都是「檔存在即真」，**沒有簽章**：任何能寫 depot 的人都能 engage／clear（clear 走 CLI 只是流程約束，不是權限）。正在跑的那筆被殺是 `abort_if` 每 `estop_poll_s`（5 s）查一次 depot——急停到真的殺掉最多晚 5 s；殺完那筆算 error（attempts+1），三次急停剛好落在同一筆會把它三振成毒樣本。
22. 儀器租約（`Instrument.acquire`）是**行程內**的鎖（同機只能一個 HFSS 使用者、MCP 與 worker 同一行程），不在 depot——換機器看不到；跨機協調仍靠 queue 的 claim。`status.json["fleet"]`／`fleet` 的 `offline` 只是「心跳年齡 > 90 s」，儀器行程死掉與 NAS 斷線分不出來。MCP 的 owner 是一次性 `mcp:<by>:<rand>`、永不重入；只有 worker 的 `queue:<store>` 可重入（同一批續跑）（檢查 #1）。
23. `check_preconditions` 每次 `open()` 都跑 `doctor.health`（含 `tasklist`，~0.1 s）與 `depot.selfcheck()`（建一個探針檔）；重開頻繁（保險絲冷卻）時會多幾次 NAS 往返。「開過一次後 ansysedt 在跑不算阻擋」假設殘留的 ansysedt 是自己的——同機有人手開 HFSS 就抓不到。
24. **MCP token 是一把鑰匙開整個機隊**：`EMFORGE_DEVICE_TOKEN` 三台共享、無身分（`by` 是自報）、無到期；外洩＝任何人能對每台開 HFSS 跑任何 profile（受 `allowed_profiles`／兩段式 confirm 限制，但 confirm token 也是同一把 secret 算的）。傳輸是明文 HTTP（區網＋防火牆 `remoteip=LocalSubnet` 是唯一屏障）。confirm token 帶 nonce、每次不同、10 分鐘到期、消費即失效（檢查 #9），但仍是同一把 secret 算的。
25. MCP 走 **stateless＋JSON response**：每個請求獨立，沒有 session、沒有 SSE 推送；重放同一個 `device_simulate(confirm=…)` 由 token 單次使用擋，但 `device_abort`／`device_estop` 沒有 nonce——重放就再按一次（冪等，無害）。
26. 同步 tool 跑 SDK 的 anyio thread pool（預設 40 個 worker thread）：`device_simulate` 佔一條 100–250 s，`device_state` 照回；但同時來 40 個阻塞呼叫就全塞（儀器租約只讓一個 simulate 進，其餘立刻 `device_busy`，所以實際只會塞一條）。uvicorn 在 daemon thread：worker 主迴圈死掉行程結束，MCP 也跟著沒了（設計如此——沒有 worker 的 MCP 用 `device-serve`）。
27. `pythoncom.CoInitialize()` 只在 `open()` 叫、從不 `CoUninitialize`：SDK 的 thread pool 執行緒重用，同一條 thread 多次 open 重複 CoInitialize（無害，回 S_FALSE）；HFSS COM 物件跨執行緒（開在 thread A、下一筆在 thread B）是否成立**未在正式機驗證**——`simulate_once` 一筆一開一關（同一條 thread 內）刻意避開這個問題。
28. **depot 讀不到時急停 fail-open**：三層急停都靠 `exists`，`FileDepot.exists` 對 OSError 回 False → NAS 斷線時 fleet／device 層讀成「沒有急停」；本機層只有在 `EMFORGE_ROOT` 真的在本機碟時才擋得住（deploy.md §2 已改兩個根分開）。「讀不到就當 engaged」（fail-closed）尚未做——P1，檢查 #10。
29. `worker --serve`／`device-serve` 綁不上埠 → exit 1、不跑 worker（檢查 #13）；預綁檢查與 uvicorn 真正綁定之間有微秒級 TOCTOU，撞到會以 RuntimeError 收場、不會靜默。
30. 故障邊界是「續跑」不是「修好」：worker 一圈／runtime 一個 tick 的例外會吞掉續跑，連續 10 次才停（`worker_error`／`tick_error` 事件是唯一線索）；depot 長時間斷線＝10 個 poll／tick 後行程結束，仍然沒有 supervisor（start_worker.cmd 是一次性）。`close()` 的處決線 60 s：quit() 卡住會被 kill，HFSS 專案可能沒存乾淨（我們本來就不留專案）。

## 13. 與 architecture.md 的偏差

| 架構文件 | 實作 | 為什麼 |
|---|---|---|
| Record 檔 `.pt` | `.npz`（`np.savez`，`allow_pickle=False`） | 核心零 torch 依賴 |
| `Context` 六欄 | 多 `params: dict`（yaml `params:`） | 策略參數要有地方進（k／d） |
| `Record` 無 `extra` | 多 `extra: dict` | 儀器側通道（single 的 radiation）；永不進 measure/score/report |
| 批 `results.json` 整份 | `batches/<store>/results/<id>.json` 逐筆 | I-15 合併語義、I-5 增量、SMB 無多寫者 append |
| `profile_hash`＝三項 | 多納入 `measure` 名 | 換尺＝換儀器，profile_tamper 順便抓 |
| Simulator 五方法 open/start/call/end/quit | `open/simulate/kill/close`；start/call/end 藏在 adapter | worker 只需要「一筆進一筆出、可殺」 |
| `state.json`／`status.json` 未分 | 分開：state 持久重讀、status 導出人讀 | 人改 status 不汙染狀態 |
| resume 直接改 state | `control.json` 給 runtime 消費 | runtime 每 tick 覆寫 state.json |
| 保留字含 `blind` | `repeat/notarize/runtime/cli`；blind 是內建策略名兼保留 arm | 內建策略就叫 blind |
| single GEOM_VER 雙邊 | 單邊 `s00` | Ricky 2026-09-01：不動舊 repo |
| `deliver`／keep_project | 未實作；Job 保留 `origin`／未知欄位 | Ricky：之後實作方要模擬檔時再整理 |
| §4「store 終態 → 移出 inflight」 | 只有 `done` 是終態；`fail` 留 inflight 等接管，人 `abandon` 才收尾 | 2026-09-05 審查 review-2：名單外的機器會接管 |
| §4 心跳＝tick 時 touch | 背景執行緒每 30 s | review-7：一個 tick 可能超過 stale 門檻 |
| §2／§7 假設共享檔案系統（NAS）；O_EXCL＋rename 是協調原語 | 抽成 `Depot` 介面（doc／log／lease／列舉）；`FileDepot` 逐位元＝原佈局；`--root`（本機程式碼）與 `--depot`（共享狀態）分工 | Ricky 2026-09-05：不倚賴特定渠道、支援多種部署（日月光）；「算法、基礎設施都解耦」 |
| 破鎖＝rename 恰一人成功 | 破鎖可能多人 True、claim 仲裁 | M12a 契約測試抓到 Windows 並發 rename 可都成功 |
| worker 直接開 Simulator | worker 經 `device.Instrument`（狀態機／租約／急停三層／前置檢查／說明檔／`simulate_once`） | Ricky 2026-09-05：參考 MHS「讓 HFSS 那裡更專注於模擬」、三台各自是 MCP server（M15）；儀器層寫在 Depot 上，不用二次搬家 |
| 狀態字典 `devices/<tag>.json` | `devices/<tag>/{state.json, reference.md, reference.json, log.jsonl, adhoc/}` | 一台一目錄：機隊列舉＝`dir_names`，說明檔／日誌／ad-hoc 結果不混進狀態列表 |
| 鎖被破後原 runtime 繼續 tick（§12-16） | 心跳比對 owner → `lock_lost` → 下一圈停（回 3） | M13 順手收掉 |
| MCP auth 用 SDK `token_verifier`＋`AuthSettings` | ASGI bearer 中介層 `BearerGate`（`hmac.compare_digest`）；`build_server(inst)` 不吃 secret，bearer 在 HTTP 層 | SDK 那條是 OAuth 資源伺服器模型（要 issuer_url／resource_server_url、掛 /.well-known）；共享密鑰不是 OAuth；中介層 15 行、可用 ASGITransport 免 socket 測 |
| `Limits` 值來源未定（M13） | `<root>/limits.json`（本機檔、`init` 留範本、沒有＝預設、壞 JSON 拒起）；來源進說明檔 | 每台自己的上限（磁碟／允許 profile 不同）；本機檔＝NAS 斷線也讀得到 |
| 兩段式 token 被拒就燒掉（M13） | 驗證與消費分開：真的跑了才 `consume_confirm` | token 同窗同值，燒掉＝十分鐘內不能重試（M15 測試抓到） |
| 兩段式 token＝sha1(secret\|op\|key\|10 分鐘窗)（M13–M15） | nonce 式：每次不同、記 `_issued`、消費即失效、`confirm_window_s` 到期 | 同窗同值＝同一件事十分鐘內做不了第二次、訊息叫人重拿卻拿回同一個（檢查 #9） |
| 儀器租約同 owner 可重入（M13） | 只有 `queue:<store>` 可重入；MCP owner 一次性 `mcp:<by>:<rand>` | 兩個 MCP 呼叫者不帶 by 就同名重入、同機兩個 HFSS（檢查 #1，S1） |
| MCP 起 server 前就宣告 url（M15） | `server.started` 才 announce＋device_serve；綁不上 → RuntimeError → CLI exit 1 | 埠被佔時 daemon thread 靜默死、fleet 吐死端點（檢查 #13） |
| deploy：三台 `EMFORGE_ROOT` 指 NAS（M16） | 每台本機 root ＋ `EMFORGE_DEPOT=file://T:/…` | 本機層急停與 limits.json 才真的是本機的；fail-open 範圍縮到共享兩層（檢查 #10；fail-closed 留 P1） |
| doctor 探針 `.doctor_probe_<pid>` | `<pid>_<rand>`、finally 釋放 | 同行程並發互撞成假的「root 不可寫」、殘留永久擋 open（檢查 #8） |
| report 的 blind 參考分佈含公證重測 | 排除 `kind=repeat` | 同一片重量三次不是三個獨立樣本，P 零新增樣本就翻盤（檢查 #3） |

## 14. 未驗證與待決

- 三台正式機的切換（`deploy.md`）尚未執行：`smoke` 同機 bit 級對 `smp073_d_040` 是「同一儀器」的實證，還沒跑。
- 儀器層／MCP 的正式機驗收（`deploy.md` §6：fleet 看到 216 → `device-simulate` 現任王同機 bit 級 → 忙碌拒絕 → 急停／解除 → 從 MCP 停續 worker）尚未執行；
  MCP 全部測試都是 in-memory（SDK Client＋ASGITransport），**真 socket＋真 HFSS＋非主執行緒 COM** 三件事都要在 216 上第一次見真章（§12-27）。
- `import-legacy --verify` 對真實 NAS 樹（六萬筆）尚未跑：本機只跑過合成迷你樹。
- `worker_ver` 拼進 antenna sha；`doctor --hfss` 的 COM 連線探測；`notarize_min_score`；SM 排序策略（舊 smpool）移植成 `strategies/sm_rank.py`；`deliver`。
- 架構 §12 的三條 `❓`（策略層無實作證據、弱模型化未對照、重訓＝跟上分布）——這份實作沒有改變它們的狀態。

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
