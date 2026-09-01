# 實作文檔（依程式碼寫；code 與本文不一致時以 code 與 tests/ 為準）

> 讀者：要動手改／接手的人。設計的「為什麼」在 `architecture.md`；本文只講「現在是怎麼做的」。
> 版本：emforge 0.1.0，274 條測試全綠（2026-09-01）。事故編號 `I-N` 見 `incidents.md`。

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
  paths.py        磁碟命名唯一真相源（所有檔名／目錄名／store 名／保留字／is_valid_name）
  fs.py           協調原語：atomic_write_*、read_json、append_jsonl（單寫者）、try_claim/read_claim/release、is_stale、Lock、sweep_dirs
  events.py       EVENTS 白名單 ＋ emit(path, event, /, **fields)
  model.py        契約 dataclass：Profile／Spec／Proposal／Context／Record／Job／SimResult／Simulator；record_id；pack_bits
  db.py           Database（一筆一檔 .npz、_index.jsonl 增量、寫綁 profile）＋ View（唯讀）
  specs.py        measure／spec 註冊表（append-only）；measure()／score()
  profiles.py     profile 註冊表；load_simulator_class／check_geom_ver／check_labels／make_simulator／is_retired／load_user_registry
  strategy.py     strategies.yaml、resolve／load／check_compatible、validate_proposals、make_context、propose_in_process／in_subprocess
  queue.py        Queue：jobs.json 持鎖、pick（認領／接管）、mark_done／mark_fail／requeue／release、watch、STOP
  batches.py      Batch：manifest.json／patterns.npz／results/<id>.json
  ledger.py       Ledger（checksum；promote 唯一寫路徑）、Pending、rescore
  report.py       每策略報表、p_beats_blind、K_MIN 閘、跨 profile 拒、worker_ver 警告
  doctor.py       機器體檢
  netid.py        機器 tag（EMFORGE_MACHINE 或 IP 末段）
  testing.py      FakeSimulator、fake_measure、FAKE_PROFILE／FAKE_SPEC、register_fakes、make_fake_root、run_all_jobs
  worker/         loop.py gate.py guard.py fuse.py workdir.py batch.py（一檔一職責）
  runtime/        core.py reconcile.py schedule.py dispatch.py collect.py notarize.py
  strategies/     blind.py top_k_flip.py（內建；使用者同名檔蓋過）
  adapters/antenna/  _bind.py sim.py measure.py profiles.py（唯一碰 torch／antenna 的地方）
  legacy/antenna_import.py  舊 NAS 資料匯入（torch lazy）
  cli/            __init__（接線）base setup loops show verdict control
```

守門測試（`tests/test_smoke.py`）：核心（`adapters/`、`legacy/` 以外）零 `torch/antenna/scipy/matplotlib/pandas/win32com` import；單檔 ≤ 400 行；單函式 ≤ 60 行；禁 `utils/misc/helpers/common/dedust` 檔名。

## 3. 磁碟佈局與檔案格式

```
<root>/                                    EMFORGE_ROOT（NAS）；測試永不碰，用 tmp_path
├── registry.py                            使用者註冊表；runtime 與 worker 啟動都 runpy 它（雙邊同源）
├── strategies/<name>.py                   使用者策略（優先於內建）
├── db/<profile>/<id>-<store>.npz          Record：bits(packbits u8) shape response(f32 或空) has_response meta(json 字串)
├── db/<profile>/_index.jsonl              一行一檔：{stem, store, worker_ver, id, sim_profile, status, score, strategy, arm, parent, tick, kind}
├── db/<profile>/RETIRED                   retire 標記 {by, at}
├── db/<profile>/_imported.json            legacy 匯入簽名 {store: {results_mtime, n_pt, n_records, …}}
├── ledger/<profile>/<spec>.json           {profile, spec, best:{id,score,at,by,note,force}|null, history:[…], _checksum}
├── queue/jobs.json  jobs.lock             [Job…]；讀改寫全程 Lock
├── queue/state/<store>.claim|.done|.fail  claim {machine, at, prior_fail}；done {machine, at, n_done, n_error, error_ids}；fail {machines, last, at}
├── queue/STOP  queue/STOP.<tag>           worker 收工（job 之間生效）
├── queue/log/<tag>.jsonl                  各 worker 單寫者事件
├── batches/<store>/manifest.json          {store, sim_profile, profile_hash, strategy, tick, seed, prio, kind, items:[{id,parent,arm,note}]}
├── batches/<store>/patterns.npz           ids(<U16) packed(n, ceil(HW/8)) shape
├── batches/<store>/results/<id>.json      {id, status, response, time_s, extra, machine, worker_ver, profile_hash, at, attempts} | {…, status:"error", error}
└── runtime_state/<profile>/
    ├── lock                               單例鎖（心跳＝每 tick touch；stale = max(600 s, 5×tick_s)）
    ├── strategies.yaml                    見 §5；mtime 變才重讀；無效沿用上次
    ├── state.json                         {tick, strategies:{name:{errors_consecutive, paused, n_dispatched, last_dispatch_tick}}, paused_profile, notarize:{id:{stores,tick,score}}, seed_base}
    ├── status.json                        每 tick 導出（人讀）
    ├── events.jsonl  pending.jsonl        單寫者（CLI 的 promoted／retired／rescored／batch_requeued 也 append，低頻）
    ├── control.json                       CLI → runtime（resume）；tick 開頭消費並刪
    ├── STOP                               runtime 收工（tick 之間）
    ├── inflight/<store>.json              {store, strategy, tick, seed, kind, prio, ids, items:{id:{parent,arm,note}}, collected, at}
    └── strategies/<name>/                 策略 workdir（runtime 永不讀）；_proposals.npz 是子行程交接檔
```

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
state.tick += 1 → heartbeat（touch lock）→ apply_control（control.json：resume）→ reload_config（yaml mtime）
→ new = collect()                # 增量收結果、量測、評分、入庫、收尾 inflight、錯誤率
→ notarize_step(new)             # 完成中的公證 → pending；新破榜候選 → 重測 ×repeat_n
→ fleet_quiet()? 事件 fleet_quiet：schedule()   # 有 inflight 且 quiet_s 內整個機隊沒產出 → 只等
→ save_state → write_status
```
`run(once)`：acquire_lock → runtime_start → `reconcile()` 不一致 → 事件 reconcile_mismatch、**return 2**（I-14）→ 迴圈：STOP 檔 → runtime_stop；tick；sleep(tick_s)。

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
```
### 5.4 dispatch（`dispatch.py`）
`kind=sample`：去重＝`db.ids(profile)`（只算 done）∪ 所有 inflight 的 ids ∪ 批內重複；全重複 → None。
`kind=repeat`：不去重、store 必須指定（只有 notarize／smoke 這樣叫）。
順序：**先寫 inflight** → Batch.write（patterns 先、manifest 最後）→ Queue.add → 事件。簽名沒有 skip／bypass／keep_* 參數（測試釘死）。

### 5.5 collect（`collect.py`）
每個 inflight：`Batch.results(known_ids=collected)` 只讀新檔 → `profile_hash` ≠ 註冊表 → `profile_tamper`、不入庫（記為已收）→ done：`specs.measure(profile.measure, response, labels)`、`specs.score(profile.spec, m)`；error：response None、note.error → `db.add` → 事件 record_added。批到終態且能收的都收了 → 移除 inflight、事件 batch_done／batch_failed；kind=sample 且 error 率 > max_error_rate → `paused_profile`、事件 profile_paused。

### 5.6 notarize（`notarize.py`）
完成中：重測批都收尾了 → scores=[原始]+重測；spread ≤ noise_floor → append pending（`{id, tick, scores, conservative=min, spread, stores, at, status}`）、事件 notarize_pass；否則 notarize_reject。一個重測 store 死了就以其餘的判。
新候選：門檻＝max(榜首 score, pending 最好的 conservative, 公證中的 score)；new_records 中 done、kind=sample、score > 門檻的（依分數降冪）→ 派 repeat_n 個 store（`<profile>-notarize-t<tick>-<id8>-r<n>`，prio=notarize_prio），門檻更新為該分數。**永不寫榜。**
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

`worker_loop`：載入 registry.py → 印 worker_ver → 清 `<EMFORGE_WORK>/`（I-1）→ worker_start → 迴圈：STOP（job 之間）→ `Queue.pick(tag)` → `gate` → `run_batch` → mark_done／mark_fail。守門不過＝那批 `.fail`、worker 繼續；`once=True` 跑完第一個真正執行的 job 就回。

`gate`（順序固定、不建構不 open）：profile 註冊且未退役 → job.profile_hash == 註冊表 → 載入類別、geom_ver（模擬器宣告 None＝單邊跳過）→ labels。

`run_batch`：建工作目錄 → 建模擬器 → `open_with_retries`（3 試，各 300 s 看門狗，失敗間 kill＋等 15 s）→ 第 0 輪跑「未 done」（續跑）→ 補測輪跑「error 且 attempts<3」（每輪前殺透重開）→ 每筆：`guarded_call(simulate, timeout_s, kill)` → 逐筆結果檔（含 machine／worker_ver／profile_hash／attempts）→ error：看門狗逾時 → 重開；第 0 輪走保險絲（連 max_fail 敗 → 冷卻 cooldown_s＋重開；第 max_blowout 次 → 回 "fail"）；補測輪連 3 敗放棄本輪 → 每筆後：claim 被別台接走 → "yield"（不動 claim）；背景 job 遇前景 queued → 釋放 claim、"yield" → 結束一律 close＋刪工作目錄。
worker **不**量測、不評分、不寫 db。

`Queue.pick`：prio 升冪；done 跳過；`.fail` 名單含我 → 跳過，否則接管（刪 fail、清殘留 claim、prior_fail 帶進新 claim）；釘機 tag 完全相等；claim 存在：無主（空／半截）且 >60 s → 清；自己的 → 續跑；別人的且（批有進度 <stale_s 或 claim 新鮮）→ 跳過，否則接管；`try_claim`。

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

| 命令 | 做什麼 | exit |
|---|---|---|
| `init --root R [--profile P]` | 建佈局、registry.py／strategies.yaml 範本（不覆寫） | 0 |
| `version`／`doctor --root R [--hfss]` | 版本戳／體檢（root 探針、磁碟、ansysedt） | 0；doctor 阻擋 4 |
| `check-strategy --root R --profile P --strategy S [--budget --seed --tick --params]` | 本行程試跑 propose | 0／1 |
| `run --root R --profile P [--once] [--in-process]` | runtime 實例 | 0；對帳不符 2；鎖被占 3 |
| `worker --root R [--machine-tag T --poll-s --once --work-root --bg-prio --max-fail --cooldown-s --max-blowout --retry-passes]` | 正式機 worker | 0 |
| `status／events [--last N --event E]／pending／jobs [--all]` | 讀狀態 | 0 |
| `watch --root R --stores a,b [--poll-s --fail-grace-min --timeout-min]` | blocking 等終態 | 0 全 done／1 fail／2 逾時 |
| `report --root R --profile P [--profile Q --cross-profile] [--k-min]` | 每策略報表 | 0；跨 profile 無旗標 2 |
| `promote <id> --root R --profile P --by WHO [--spec --note --force]` | 換王（唯一寫榜路徑） | 0；不在 pending 3；榜被手改 5 |
| `retire --root R --profile P --by WHO` | 凍結 profile | 0 |
| `rescore --root R --profile P --spec S --by WHO [--force]` | 換評估器建新榜 | 0；榜已存在 6 |
| `requeue <store> --root R --by WHO` | 原子清 claim+done+fail | 0；新鮮 claim 7 |
| `resume --root R --profile P [--strategy S] --by WHO` | 寫 control.json | 0 |
| `stop --root R (--profile P | --worker [--machine-tag T]) [--clear]` | STOP 檔 | 0 |
| `smoke <id> --root R --profile P --by WHO [--machine T --n N]` | 對已量 id 派重測（切機驗同一儀器） | 0 |
| `import-legacy --root OLD --out R --stores g1,g2 [--profile auto --map s=p --include-errors --verify --dry-run --force --no-rad]` | 舊資料匯入 | 0；verify 不符 2 |

任何未預期例外 → stderr `emforge: <Type>: <msg>`、exit 1。

## 9. 事件（`events.py`，封閉集合）

runtime：`runtime_start runtime_stop reconcile_mismatch config_reloaded config_invalid fleet_quiet profile_paused profile_resumed`
策略：`strategy_loaded strategy_rejected strategy_error strategy_timeout strategy_paused strategy_resumed strategy_empty proposals_validated(n_in,n_dup,n_out)`
派收：`batch_dispatched record_added batch_done batch_failed batch_requeued profile_tamper`
公證：`record_candidate notarize_dispatched notarize_pass notarize_reject`
榜：`promoted retired rescored`
worker（queue/log/<tag>.jsonl）：`worker_start job_claimed sample_done sample_error job_done job_failed job_yield gate_rejected sim_restart worker_stop`

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

## 12. 已知失效模式（給獨立稽核的 brief 用）

1. stale-claim 接管與 fleet_quiet 都靠 **mtime**：各機時鐘不同步會誤判（doctor 只報不修）。
2. `noise_floor／k_min／quiet_s／max_error_rate／strategy_error_limit` 全是佔位數字，沒有任何一個經過本域以外的校準。
3. 冷啟動：資料庫空、榜空 → 每個「新最好」都觸發公證重測，前幾 tick 重測比例偏高（`notarize_min_score` 尚未實作）。
4. single 的 geom_ver 是**單邊**宣告（舊 single_port.py 無 GEOM_VER，Ricky 決定不動舊 repo）：single 幾何若換代不會被抓。
5. `arm="blind"` 是策略自我宣告；說謊會留紀錄但不會被擋。
6. 背景策略的「佇列空」看**全機共用佇列**：另一個 profile 的實例有 job 排隊時，這個實例的背景策略也不派（設計如此，但可能讓人以為「卡住」）。
7. CLI 的 promoted／retired／rescored／batch_requeued append 到 runtime 的 events.jsonl，違反單寫者契約的低頻例外（SMB 上理論上可能交錯）。
8. `_index.jsonl` 若兩個寫者同時 add 同一 profile（設計禁止：一實例一 profile；匯入器與 runtime 不同時跑）會有重複行——`refresh` 以 dict 合併可容忍，但檔會變胖。
9. legacy 匯入的親代解析只做「legacy id 全域唯一才解」，不重建血統鏈；同 pattern 多 y 靠舊 wm 欄 2 位小數對回，理論上可能對錯。
10. `propose_in_subprocess` 每 tick 每策略多 1–2 s 子行程啟動；策略多時 tick 變慢（可接受，換 I-4 隔離）。
11. `worker_ver` 目前只含 emforge sha；adapter（antenna repo）的 sha 由 `_bind.antenna_sha()` 提供但**尚未**拼進 worker_ver。
12. `Simulator.kill()` 對 HFSS 是殺**全部** ansysedt.exe：同機第二個 HFSS 使用者會被誤殺（doctor 拒起）。
13. 策略層（N 個策略並行）沒有實測資料：所有「多樣性從策略池湧現」都是推論（architecture.md §12）。

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

## 14. 未驗證與待決

- 三台正式機的切換（`deploy.md`）尚未執行：`smoke` 同機 bit 級對 `smp073_d_040` 是「同一儀器」的實證，還沒跑。
- `import-legacy --verify` 對真實 NAS 樹（六萬筆）尚未跑：本機只跑過合成迷你樹。
- `worker_ver` 拼進 antenna sha；`doctor --hfss` 的 COM 連線探測；`notarize_min_score`；SM 排序策略（舊 smpool）移植成 `strategies/sm_rank.py`；`deliver`。
- 架構 §12 的三條 `❓`（策略層無實作證據、弱模型化未對照、重訓＝跟上分布）——這份實作沒有改變它們的狀態。
