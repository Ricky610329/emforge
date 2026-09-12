# 2026-09-12 審查修復與恢復契約

本輪修復程式審查確認的六項問題，並補上完整回歸找到的「平台死亡後殘留 jobs.lock」問題。
正常工作樹驗證為 **640 passed（72.35 秒）**，pyflakes 通過；全部測試使用隔離資料與假模擬器。
以實際來源建立的隔離發布快照也通過 **640 passed（65.57 秒）**、pyflakes 與 `emforge.release.probe`。
這份文件記錄修正版程式的行為。三台遠端 worker 與目前平台仍是先前驗收的部署版本，需另行切換及現場演練。

## 修正與證據

| 項目 | 修正後行為 | 回歸位置 |
|---|---|---|
| 重複啟動清掉工作目錄 | Instrument 先取得 work-root 的本機行程所有權，再發布狀態；worker 取得儀器租約後才清理 | `tests/worker/test_startup.py`：兩個真 Python 行程，第二個被拒、第一個工作檔及 device PID 保留 |
| 發布包遺漏 launcher | 快照納入 scripts，於實際來源快照內載入 launcher 測試 | `tests/test_release.py::test_real_release_collects_launcher_tests_without_worktree` |
| requeue 與 pick 交錯 | 活躍檢查、claim/done/fail 清除加入相同 jobs.lock | `tests/test_queue.py::test_requeue_serializes_live_check_and_clear_with_pick` |
| 後處理失敗未計入熔斷 | 批次完成統計讀持久化 record；拒收或 error 計入錯誤率 | `tests/runtime/test_collect.py::test_postprocessing_errors_count_after_incremental_collect_and_restart` |
| 上傳斷線丟掉結果 | 結果先原子存入本機 outbox，再送到 Depot；重啟先補傳、已存成功結果不再求解 | `tests/worker/test_outbox.py`：Memory／真 HTTP、上傳前失敗／回覆遺失 |
| run_id 與內部鎖撞名 | run、node、config 使用獨立的控制鎖命名空間 | `tests/test_runner.py::test_run_name_cannot_collide_with_profile_config_lock` |
| 平台死亡後殘留佇列鎖 | child metadata 記錄 queue_owner，Supervisor 確認子行程死亡後只釋放該 owner 的鎖 | `tests/test_release.py`：強制終止、重新啟動與保留其他 worker 的鎖 |

## 本機工作目錄

若工作目錄為 `C:/emforge-worker/work`，本機持久狀態放在旁邊的 `C:/emforge-worker/work.emforge`。
全部名稱由 paths.py 提供，狀態讀寫仍經 FileDepot。

```text
work/                         可清理的求解暫存
  <store>/
work.emforge/                 不隨求解暫存清除
  worker.lock                 owner、PID、出生識別
  worker_guard.lock           所有權變更的序列化鎖
  results/<depot-hash>/
    <result-hash>.json         完整平台／批次／候選身分與原始結果
```

同 work-root 只允許一個已啟動的 Instrument／worker 行程。殘留 worker.lock 只有在 PID 不存在或出生識別不符時才回收；無法確認則拒絕。
MCP 與 worker 共用同一 Instrument；同一行程已有 MCP 模擬工作時跳過啟動清掃，再由正常儀器租約等待。
本規則不保證阻擋使用另一個 work-root 的其他 HFSS 程式，原有機器健康檢查仍需保留。

## 補傳與成本

1. 每筆求解結束後，先把完整結果原子寫入本機待傳區，再送到平台。
2. 上傳失敗時保留結果和自己的 batch claim，等待下一輪；不把此機器列入該批失敗名單。
3. 啟動及每次認領前補傳對應平台的結果。平台已存有成功結果時只清除本機副本，不再寫第二次，也不重新模擬。
4. 另一台仍持有該批 claim 時延後補傳；已有成功結果或更新的嘗試時保留遠端結果，避免舊 worker 覆寫接管者。
5. 已人工 abandon 且沒有對應結果的批次、批次已不存在或身分不相容時，不猜測回填；保留本機證據供處理。

短暫斷線不再必然重跑已完成結果。這不是跨多台 worker 的全域 exactly-once 保證：若 worker 長時間失聯、claim 過期後別台接管，仍可能發生額外求解。
本機磁碟損壞或空間耗盡也不在此補傳保證內。不要刪除或共用另一台的 work.emforge；更換平台位址會使用另一個待傳分區，舊待傳資料需在原平台身分下核對。

## 更新相容性

- 原有 batch、result、record 格式保留；收件結果計數直接讀 record，涵蓋前次已收件、重啟後才完成的批次。
- 控制鎖改為 `platform_locks/control/{run,node,config}/<name>.lock`，與 submission 鎖分開。
  平台 API 寫入者需一起更新，不可讓新舊版本同時以不同鎖命名空間操作同一 Depot。
- Supervisor 的死亡鎖恢復依賴新版 child metadata。舊 metadata 沒有 queue_owner 時不猜 owner；bootstrap 自身也需更新，單做平台 release-apply 不會熱換 bootstrap。
- worker 的本機鎖與 outbox 需重啟新版 worker 才生效。新版不會自動偵測未實作本機鎖的舊 worker，因此首次切換仍需先確認原 worker 已正常退出。
- 本輪沒有更改 Antenna、求解設定或模型登入。HFSS 收斂狀態依使用者決定不作本輪交付阻擋。

## 交付檔案

九月報告與 14 頁簡報另存於 `C:/Users/ricky/Desktop/em-forge/九月進度修訂-20260912`，Antenna 原檔保持唯讀。
包含 HTML 閱讀版、Markdown、可編輯 PPTX，以及由 records_dual.json 重建的曲線 PNG／SVG。
修正橋寬張數、中位數／樣本範圍、紀錄與上界的區別、方法論外推範圍、日期及平台驗收口徑。
網站和驗證中間產物繼續放在 Git 排除的 local/，不加入發布包。
