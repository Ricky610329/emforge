# Claude → Codex 工作交接（2026-09-23）

這份是本輪（全面審查與修復）之後的交接入口。交接基準＝本檔所在的 commit（`git log -1`，2026-09-23 本機 main 的最後一個 commit，未 push）；正常工作樹 **697 passed**、pyflakes 無輸出。
本文件只整理現況、待辦與切換注意事項，不重述設計（設計在 `docs/architecture.md`，怎麼做在 `docs/implementation.md`）。

## 先讀什麼（順序）

1. 本檔。
2. `CLAUDE.md`（四條硬規則：TDD、命名與 Depot 守門、一里程碑一 commit、單檔 ≤400 行／單函式 ≤60 行）。
3. [reliability-2026-09-23.md](reliability-2026-09-23.md)：本輪修了什麼、恢復契約怎麼變、更新相容性、**本輪不動的設計取捨**。
4. [incidents.md](incidents.md) 第七節（I-18～I-35）：每個修正背後的失效情境；回歸測試 docstring 引用這些編號。
5. 需要操作時：[platform-quickstart.md](platform-quickstart.md)（fake 流程）、[agent-harness.md](agent-harness.md)（Pi／Codex／Claude Code 接口）；
   三台的實際部署步驟在 repo 外的 `C:/Users/ricky/Desktop/em-forge/三台HFSS部署操作.html`（HTTP Depot 8766＋detached worktree），
   [deploy.md](deploy.md) 是原始 NAS 方案、只留驗收判準。
6. 歷史基準（只在追溯時看）：[reliability-2026-09-12.md](reliability-2026-09-12.md)、[delivery-readiness-2026-09-12.md](delivery-readiness-2026-09-12.md)、
   [handoff-2026-09-08-codex-to-claude.md](handoff-2026-09-08-codex-to-claude.md)、[review-2026-09-07.md](review-2026-09-07.md)。

## 版本與部署現況

| 項目 | 現況 |
|---|---|
| 本機 main | 2026-09-23 的 commit：c3e630e worker、16f9d09 runtime、8216be0 資料層＋platform、8feaa85 adapter／CLI、2c4e242 docs、再加本檔所在的交接檢查 commit（補 `outbox_held` 白名單） |
| 遠端 origin | `git ls-remote` 於 2026-09-23 確認遠端 main＝998ad35（09-12 修復版已 push）；今天的五個 commit **尚未 push** |
| 三台 HFSS worker（216／218／37）與平台 | 仍是 09-12 驗收時的部署版 **480e874**。998ad35（09-12 修復）與今天的五個 commit 都**尚未切換上去** |
| Antenna repo | 本輪未改；本機 clone HEAD 曾核對 6ae39f8（09-08），三台實際版本未核對 |
| HFSS 收斂 | 使用者定案不作本輪交付阻擋。今天做了第一階段：`extra.hfss_convergence` best-effort 記錄，**COM 呼叫尚未在正式機驗證** |

## 本輪做了什麼（一句話版）

六個 Opus 審查代理各審一切面（worker／runtime／platform 與 release／資料層／Antenna adapter 對照／CLI 與文件），
確認的 bug 全部先寫紅測試再修，分四個 fix commit；純屬設計取捨的只記錄。最嚴重的三項：
`/depot` 可用 `C:/…` key 逃出 Depot 根讀寫平台機任意檔、看門狗遇 depot 例外會死掉讓卡住的 HFSS 沒人 kill、
n=1 的 job 錯一筆就暫停整個 profile。完整清單見 reliability-2026-09-23.md 的表。

## 尚未完成（三類，不要混在一起）

### A. 設計取捨——要使用者決定，不要自行實作

- HFSS 收斂：是否拒收未收斂、要不要開新 profile（kwargs 在 profile_hash 裡，換設定＝換名字）。現役設定 max_passes 6／min_converged 5 幾乎必然撞上限，直接拒收會讓現役資料近乎全數被拒。
- `/depot` 授權模型：目前等於整棵共享儲存的控制權（可強制 release 任何鎖、刪 `queue/ESTOP`、寫 `runtime_state/*/strategies/`）；算法子行程與 agent 都拿到完整 token。縮小＝伺服器端限制 key 前綴＋程式根與 Depot 根分開。
- MCP 預算旁路（`inbox_submit` 用沒 start 過的 run_id）、`algorithm_start` 沒有確認步驟。
- `profile_hash` 不含 measure targets：改會讓已部署三台與既有紀錄全部 profile_tamper，需要遷移方案。
- Antenna 側：`script/kill.py` 殺整台所有 ansysedt、`keep_project`（送板 .aedt）emforge 沒有、COM 例外沒分類（磁碟滿應視為機器問題）。

### B. 產品待辦（09-08 交接的六項，現況）

| # | 項目 | 現況 |
|---|---|---|
| 1 | 簡化部署與日常啟動（角色設定、自動產生 registry／limits／策略設定） | 沒做 |
| 2 | 一次設定憑證 | 部分：`--connection` 檔只有 `platform-mcp` 吃；worker／algorithm-worker／platform-service 仍只讀 `EMFORGE_PLATFORM_TOKEN` |
| 3 | 簡單更新入口（git pull＋重啟，程式處理忙碌檢查、驗證、回退） | 部分：平台端有 release-apply／Supervisor；worker 端仍是 `start_worker.cmd` 先 pull 再重啟，沒有忙碌檢查 |
| 4 | 面向工程師的進度介面 | 沒做（只有 fleet／jobs／status／inbox CLI） |
| 5 | 正式機驗收 | 部分：480e874 做過 10 筆真 HFSS 三機一致性；之後兩輪修正未上機 |
| 6 | 依實測決定後續優化 | 沒做 |

### C. 需要正式機才能做的驗證

- `extra.hfss_convergence`：確認 `oDesktop.GetMessages`／`ClearMessages` 在現場 HFSS 版本可用；跑一筆已知不收斂案例看 `converged` 是否為 False。
- 生產故障恢復演練（真 HFSS 中斷網、worker 強制退出、平台重啟）：本輪的 outbox／held／recover_missing 都只有 fake 與真 socket 測試。
- 三台切換後的 smoke（同 pattern 三機一致）與 `results_held/` 是否出現非預期內容。

## 切換到正式機時要知道的事

- **先 push 才拿得到**：三台與平台是從 git 更新的；今天的 commit 還在本機，push 需要使用者授權。
- **不是 release-apply 就夠**：本輪改到 bootstrap 本身（`cli/release.py` 的 `run_service_loop`、`release/supervisor.py`）與算法節點
  （`runner/process.py`、`cli/algorithm.py`），所以 `platform-service` 行程和 `algorithm-worker` 節點都要**重啟**，不能只熱換平台 release。
- **順序**：平台 worktree 更新 → 重啟 platform-service、看 release-status 的 current／last_good → 逐台 worker：
  `emforge stop --root <本機根> --depot http://<平台>:8766 --worker --machine-tag <tag>`（`EMFORGE_PLATFORM_TOKEN` 要在環境裡）
  等它跑完當前 job 收工 → 該台 worktree checkout 新 commit → 重啟。同機兩套不能一起操作 HFSS。
- **每台驗收**：`fleet` 顯示該台 online；跑一筆 smoke，結果檔 `worker_ver` 兩段都在、`extra` 有 `hfss_convergence`（COM 呼叫在正式機是第一次跑，
  沒有這個欄位＝拿不到 oDesktop，要查）、同 pattern 與舊量測 `np.array_equal`；`work.emforge/results_held/` 應是空的。
- **相容性**：新舊混跑期間 HTTP 都能通（480e874 的 `RemotePlatform`／`HttpDepot` 本來就送 `application/json`、urllib 不帶 Origin）；新平台的 key 規則收緊不影響 `paths.py` 產生的 key；
  `state.json` 新欄位舊 runtime 忽略。但 worker 側的 held 分區、done 優先、看門狗修正、`worker_ver` 新格式都要**該台重啟新版**才生效。
- **結果檔 `worker_ver` 格式變了**：`emforge=<sha> antenna=<sha>`；只比對前半段的判讀工具要改成前綴比對。
- **`/depot` 的行為變更**：自寫客戶端（若有）必須送 `Content-Type: application/json` 且不能帶 `Origin`；含 `:` 的 key 會被拒。
- **pytest 前置**：`EMFORGE_ANTENNA_REPO` 指到不是 Antenna repo 的路徑，pytest 整套拒跑（以前是靜默 skip）。
- 三台 IP／路徑／Python 版本以 09-08 交接的「正式部署的現況」表與 Desktop 的部署 HTML 為準（deploy.md 沒有 IP），未重新核對；
  舊文件的 140.213.106.* 與本機的 140.123.* 不一致，不要自行改字當成驗證。

## 給 Codex 整理文件時的建議

- `AGENTS.md`／`CLAUDE.md` 最上面已堆了四層「本輪最新／前一輪／同日先前／最新交接」頭註。建議收成一段「現況」（指向本檔）＋一行「歷史」列表，其餘搬到本檔的歷史節。
- `docs/` 裡屬於歷史紀錄的：`review-2026-09-07.md`、`plan-2026-09-07-round3.md`、`plan-2026-09-08-platform.md`、`handoff-2026-09-08-codex-to-claude.md`、`delivery-readiness-2026-09-12.md`、`reliability-2026-09-12.md`。
  它們的數字（595／615／628／640 passed、480e874、a5c808e）都是當時的時間點，整理時標「歷史」即可，不要改數字。
- `docs/incidents.md` 是回歸測試 docstring 的真相源（`I-N`），只能追加、不能重編號。
- `docs/naming.md` 的主佈局樹沒有畫上 09-08 之後新增的 key（`queue/scheduling.json`、`inbox/`、`inbox_status/`、`algo_logs/`、`platform_locks/`、`ledger/<p>/<spec>.lock`、`work.emforge/results*`），目前靠頭註與 reliability 文件補；真相源是 `paths.py` 與 `tests/test_paths.py`。
- 使用者要的 HTML 報告（`C:/Users/ricky/Desktop/em-forge`，含複製按鈕）在 repo 外，本輪沒有更新。

## 驗證指令（repo 根、Git Bash）

```bash
cd /c/Users/ricky/Documents/GitHub/emforge && export PYTHONIOENCODING=utf-8 EMFORGE_ANTENNA_REPO=/c/Users/ricky/Documents/GitHub/Antenna EMFORGE_HFSS_TESTS=0 && set -o pipefail && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pytest -o addopts="" -q 2>&1 | tail -3 && /c/Users/ricky/miniforge3/envs/ant/python.exe -m pyflakes emforge tests scripts/launch_agent.py && echo OK
```

預期 `697 passed`（約 100 秒）與 `OK`。表頭會印 adapter 綁定到的路徑；沒設 `EMFORGE_ANTENNA_REPO` 時 adapter 綁定／parity 測試 skip、數字會少。

## 協作規範與授權邊界（沿用）

- 繁體中文；工程師可理解的操作優先。TDD、單檔 ≤400 行、單函式 ≤60 行、完整回歸與 pyflakes 通過後才 commit；新增核心模組要列入 `tests/test_smoke.py` 的 Depot 守門三張清單之一。
- 不修改 Antenna repo 與 NAS 正式資料；測試只用 tmp_path／MemoryDepot。沒有新的正式部署授權；不 push，除非使用者要求。
- 使用者不喜歡反覆確認已授權的事：bug 直接修、明確較好的設計直接改、純屬取捨的只記錄（本輪三條指示）。
