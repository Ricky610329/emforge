# 2026-09-23 全面審查：修復紀錄與恢復契約更新

六個獨立審查代理（worker、runtime、platform／release、資料層、Antenna adapter 對照、CLI／文件）各自重現後回報，
本輪修正其中確認的問題；純屬設計取捨的項目只記錄不動（見文末）。事故編號 I-18～I-35 見 [incidents.md](incidents.md)。
每項修正都先有紅測試再實作；驗證結果見文末。三台遠端 worker 與平台仍是先前部署版本，需另行切換。

## 四個 commit 的內容

| 切面 | 修正 | 回歸位置 |
|---|---|---|
| worker | 看門狗輪詢急停時遇 depot 例外不再讓執行緒死掉（I-4） | `tests/worker/test_guard.py::test_watchdog_still_fires_when_abort_check_raises` |
| worker | 待傳結果：本機 done 一律勝過遠端非 done；不相容／批次不存在／已 abandon 搬到 `work.emforge/results_held/`、記 `outbox_held`、不再每圈拋例外 | `tests/worker/test_outbox.py` |
| worker | 認領前補傳的瞬斷包成 ResultPending，不列入 fail 名單 | `tests/worker/test_outbox.py::test_flush_failure_before_claim_is_result_pending_not_batch_failure` |
| worker | Windows 行程已結束但 handle 仍被持有 → 不再判成活著；空／半截 worker.lock 60 秒後回收；claim 寫入 fsync | `tests/worker/test_startup.py` |
| runtime | 錯誤率改看最近 20 筆樣本（跨批、至少 3 筆），`state.error_window`（I-18） | `tests/runtime/test_collect.py` |
| runtime | 每 tick `recover_missing` 補完派工半途失敗的意圖；inbox 派工例外不穿出 tick（I-20） | `tests/runtime/test_recovery.py`、`test_schedule.py` |
| runtime | 新收 done 樣本先落地 `state.notarize_deferred` 再標 collected；公證派不出去的候選留到下一 tick（I-21） | `tests/runtime/test_notarize.py` |
| runtime | collect 每 store 隔離（`collect_error`）、雜結果檔點名一次（`stray_result`）（I-19）；state.json 遺失對齊 tick（I-22） | `tests/runtime/test_collect.py`、`test_core.py` |
| 資料層 | jsonl 尾行半截跳過、append 前砍掉（I-23）；HTTP claim 回覆遺失先 release（I-24）；wire 編碼 NaN／inf（I-25） | `tests/test_fs.py`、`tests/depot/test_contract.py` |
| 資料層 | View.lineage／sample／runs／children 只載入需要的紀錄（I-5） | `tests/test_db.py::test_lineage_sample_runs_children_load_only_needed_records` |
| 資料層 | promote 到別的 spec 全被門檻擋時拒絕（I-28）；promote／rescore 加 `ledger/<p>/<spec>.lock` | `tests/test_ledger.py` |
| platform | Depot key 拒絕冒號／磁碟機代號／段尾 `.` 空白／根層 registry.py、strategies、limits.json（I-26） | `tests/depot/test_contract.py`、`tests/test_network.py` |
| platform | HTTP POST 必須 `application/json`、帶 Origin 一律 403、閒置 30 s 逾時、非 ASCII Authorization 回 401 | `tests/test_network.py` |
| platform | platform-service 主迴圈接住 tick 例外；Supervisor 切換中崩潰重啟後重新套用要求（I-27） | `tests/test_release.py` |
| adapter | NaN／inf 響應 → `nonfinite_response`；`energy_max > 1.05` → measure_failed（I-29） | `tests/test_batches.py`、`tests/runtime/test_collect.py`、`tests/adapters/test_antenna_pure.py` |
| adapter | `extra.hfss_convergence`（best-effort 掃 HFSS 訊息窗，只記錄不判定）；worker_ver 拼上 `antenna=<sha>`（I-10） | `tests/adapters/test_antenna_pure.py`、`tests/worker/test_batch.py` |
| adapter | adapter 宣告 `labels` 讓 gate 第 4 步真的比對（I-32）；pytest 表頭誠實回報綁定、設錯拒跑；`hfss` 標記註冊（I-30） | `tests/adapters/test_antenna_pure.py` |
| legacy | 同 pattern 多筆 y 不再全配 hits[0]；error 列補測後可升級為 done（`Database.upgrade_error`）；`_imported.json` 累計（I-31） | `tests/legacy/test_antenna_import.py`、`tests/test_db.py` |
| CLI | limits.json 接受 BOM（I-33）；init 範本 blind `enabled: false`；`stop --machine-tag` 沒配 `--worker` 拒絕；register 指名非 UTF-8 檔；節點認證失敗退出 2（I-34） | `tests/device/test_limits.py`、`tests/test_cli.py` |

## 本機工作目錄（更新）

```text
work.emforge/
  worker.lock                  owner、PID、出生識別（空／半截超過 60 秒視為斷電殘骸回收）
  worker_guard.lock
  results/<depot-hash>/        待傳（補傳成功即刪）
  results_held/<depot-hash>/   回填不了的證據：身分不相容、批次不存在、批次已 abandon／收尾且無成功結果
                               每檔多 reason／held_at；不再自動重試，人工核對後刪
```

## 補傳語義（更新第 4、5 條）

4. 另一台仍持有該批 claim 時延後補傳。遠端已有成功結果時只清本機副本；**本機 done 一律勝過遠端非 done**（不看 attempts）；
   兩邊都不是 done 才比 attempts。
5. 已 abandon 且沒有對應成功結果、批次不存在、身分不相容的結果搬到 `results_held/` 並記一次 `outbox_held` 事件，不猜測回填。
   佇列狀態 missing（派工半途）的仍留在待傳區等 runtime 對帳。

## 更新相容性

- `state.json` 新增 `notarize_deferred`、`error_window`；舊 state 缺欄位用預設。事件白名單新增 `stray_result`、`collect_error`、`dispatch_recovered`。
- 平台 HTTP 現在要求 POST 帶 `Content-Type: application/json`，且拒絕帶 `Origin` 的請求；`RemotePlatform`／`HttpDepot` 本來就符合，自寫客戶端要跟上。
- Depot key 規則收緊：含 `:` 的 key、段尾 `.` 或空白、根層 `registry.py`／`strategies`／`limits.json` 一律拒絕。`paths.py` 產生的 key 全部合規（`tests/test_paths.py`）。
- HTTP wire 對 NaN／±inf 用 `{"__emforge_float__": …}` 標記；舊客戶端讀到含非有限值的回應會看到這個字典而不是斷線。
- `Database.upgrade_error` 是唯一允許取代既有紀錄的路徑（error → done），只給 legacy 匯入用；runtime collect 不變（先到的 done 保留）。
- 結果檔 `worker_ver` 現在是 `emforge=<sha> antenna=<sha>`（模擬器有宣告時）；只讀前半段的判讀工具要改用前綴比對。
- `extra.hfss_convergence` 是 best-effort：`converged` 為 `None`＝沒看到相關訊息（不是收斂）；欄位缺席＝拿不到 oDesktop。COM 呼叫（`GetMessages`／`ClearMessages`）尚未在正式機驗證。
- worker 與平台都要重啟新版才生效；`pytest` 現在對 `EMFORGE_ANTENNA_REPO` 設錯直接拒跑。

## 本輪不動（設計取捨，待決）

- **HFSS 收斂判定**：現役設定（max_passes 6、min_converged 5）幾乎必然撞上限；直接拒收會讓現役資料近乎全數被拒。第二階段（是否拒收、開新 profile）另議。
- **/depot 的授權模型**：仍等於整棵共享儲存的控制權（可強制 release 任何鎖、刪 `queue/ESTOP`、寫 `runtime_state/*/strategies/`）；算法子行程與 agent 都拿得到完整 token。縮小只能在伺服器端限制 key 前綴＋把程式根與 Depot 根分開，交付客戶前決定。
- **MCP 預算旁路**：`inbox_submit` 用沒 start 過的 run_id 沒有預算檢查；`algorithm_start` 沒有確認步驟。
- **`profile_hash` 不含 measure targets**：使用者自寫的 measure 改 targets 不改名會靜默混用；改 hash 會讓已部署三台與既有紀錄全部 profile_tamper，需要遷移。
- **Antenna 側**：`script/kill.py` 殺整台所有 ansysedt；`keep_project`（送板 .aedt）emforge 沒有；COM 例外沒分類（磁碟滿應視為機器問題）。
- **pick 全程持 jobs.lock**、`mark_done`／`mark_fail` 不驗 claim 是否仍屬自己、`_deliver` 把 `claim_owner()` 回 None 當沒有 claim：審查列為可疑未確認，未改。

## 驗證

正常工作樹 `python -m pytest`：**696 passed**；`python -m pyflakes emforge tests scripts/launch_agent.py` 無輸出。全部使用隔離資料與假模擬器（`EMFORGE_ANTENNA_REPO` 綁本機 Antenna clone，parity 測試有跑）。
