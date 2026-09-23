> **2026-09-23 註**：本文是原始的 NAS `file://` 方案。09-12 的實際部署改用 HTTP Depot（平台 8766）＋各台 detached worktree，
> 逐步指令在 repo 外的 `C:/Users/ricky/Desktop/em-forge/三台HFSS部署操作.html`；本輪修正後的切換順序與相容性見
> [handoff-2026-09-23-claude-to-codex.md](handoff-2026-09-23-claude-to-codex.md)。下文的驗收判準仍適用。

# 部署與切換（三台正式機：216／218／37）

> 原則：**任一步驗收不過就停在該步**，其餘機器繼續跑舊系統——不損失整日機時。
> 同一台機器同時只能有**一個** HFSS 使用者（`kill()` 殺全部 ansysedt.exe）：切換逐台做，先停舊 worker 再起新的。

## 0. 開發機先做

1. `EMFORGE_ANTENNA_REPO` 指向 Antenna clone；`python -m pytest` 全綠（含 adapter parity 500 筆逐位元；MCP 測試需 `pip install -e .[mcp]`，沒裝會 skip）。
2. 在本文釘住舊 repo 的 commit（`ANTENNA_PIN = <sha>`），三台切換前都 `git pull` 到同一個。
3. 假根端到端：`emforge init --root <tmp> --profile fake_f1`（registry.py 改成 `from emforge.testing import register_fakes; register_fakes()`）→ 一個終端 `emforge run --root <tmp> --profile fake_f1`、另一個 `emforge worker --root <tmp> --machine-tag dev`；跑 20 tick、中途 taskkill runtime 再起 → `emforge events`／`report` 看得懂、`work/` 空。

## 1. 匯入歷史資料（開發機，對 `/nas-backup` 鏡像）

```
emforge init --root "T:\碩二_鄒穎麒's\antenna\emforge" --profile dual_p01_db075
# registry.py 寫：from emforge.adapters.antenna.profiles import register_all; register_all()
emforge import-legacy --root C:\Users\Ricky\antenna_nas_backup --out "<EMFORGE_ROOT>" --stores "dedust_*,handoff_*,harvest_*" --verify --dry-run
#   看 unmapped 清單（slot_spec／pixel_count 變體應該都在裡面；意外的用 --map 處理）
emforge import-legacy … --verify            # 零 verify_fail 才算「新尺＝舊尺」
emforge rescore --root "<EMFORGE_ROOT>" --profile dual_p01_db075 --spec dual_v2 --by ricky
#   首任王應＝ smp073_d_040、score −2.39（與舊 records_dual.json.wm_mfg 一致）
emforge report --root "<EMFORGE_ROOT>" --profile dual_p01_db075
```

## 2. 一台正式機（先 216，最穩的那台）

**安裝**（在既有 conda env，已有 torch／pywin32／psutil）：
```
git clone <emforge remote> C:\Users\<u>\Documents\GitHub\emforge
pip install -e C:\Users\<u>\Documents\GitHub\emforge
setx EMFORGE_ANTENNA_REPO C:\Users\<u>\Documents\GitHub\Antenna
setx EMFORGE_MACHINE 216                                             # 每台各自（218／37）；不設 start_worker.cmd 拒起（檢查 #20）
setx EMFORGE_ROOT C:\emforge_root
setx EMFORGE_DEPOT "file://T:/碩二_鄒穎麒's/antenna/emforge"
setx MPLBACKEND Agg
emforge init --root %EMFORGE_ROOT% --depot %EMFORGE_DEPOT%       # 本機 root：registry.py／strategies/／limits.json；NAS：佈局前綴
```
**兩個根分開設**（檢查 #10，2026-09-07）：`EMFORGE_ROOT`＝**本機**程式碼／設定根（registry.py、strategies/、limits.json、本機層 ESTOP、
策略 workdir）——三台各自在自己的 C 槽；`EMFORGE_DEPOT`＝NAS 上的共享狀態樹（db／queue／batches／runtime_state／devices；今天的佈局一個 byte 不差）。
以前把 `EMFORGE_ROOT` 直接指到 NAS：本機層急停變成三台共用一個檔、`limits.json` 三台一份、NAS 斷線時三層急停全部讀成「沒有急停」。
`doctor` 會多印一行 `depot`（可寫／O_EXCL／時鐘偏移 >30 s 阻擋）；`start_worker.cmd` 沒設 `EMFORGE_DEPOT` 會警告（開發機假根才允許不設）。
**停舊 worker**：舊 repo `jobs_state/STOP` → `jobs-ls` 確認本機無 claim → `tasklist | findstr ansysedt` 為空。
**體檢**：`emforge doctor --root %EMFORGE_ROOT%` 必須 0（root 探針、磁碟 ≥ 20 GB、無 ansysedt）。
**smoke**（同一儀器的實證）：
```
emforge smoke <smp073_d_040 的 record id> --root %EMFORGE_ROOT% --profile dual_p01_db075 --machine 216 --by ricky --n 1
scripts\start_worker.cmd            # 桌面終端啟動（HFSS 視窗可見）；腳本＝pull → doctor → worker
emforge watch --root %EMFORGE_ROOT% --stores <smoke store>
```
驗收：`queue/log/216.jsonl` 有 `job_claimed` 無 `gate_rejected`；結果檔 `worker_ver` 兩段都在（`emforge=<sha> antenna=<sha>`）；**同機** `response` 與舊量測 `np.array_equal`
（舊 records 註記同機噪音地板 0.000）；跨機 `|Δm_k| ≤ 0.75`；`time_s` 100–250 s；C 槽剩餘不變。
**回滾**：`emforge stop --worker --machine-tag 216` → 刪舊 `jobs_state/STOP` → 桌面重啟 `python -m script.dedust worker`。舊佇列、舊資料全未動。

## 2b. 儀器層與 MCP（M13–M16；同一台做完 §2 再做）

**安裝** optional extra：`pip install -e C:\Users\<u>\Documents\GitHub\emforge[mcp]`（mcp 2.1、uvicorn、httpx2；**沒裝也能跑** worker／runtime／CLI）。
**token**（三台＋開發機同一個；共享密鑰＝bearer＝兩段式 confirm 的 secret）：
```
python -c "import secrets; print(secrets.token_urlsafe(24))"        # 開發機產一個
setx EMFORGE_DEVICE_TOKEN <token>                                    # 每台各設（不走旗標、別進 shell 歷史；換 token 三台一起換）
setx EMFORGE_MCP_HOST 0.0.0.0                                        # 不設＝127.0.0.1（只有本機連得到、不需 token）
setx EMFORGE_MCP_PORT 8765
netsh advfirewall firewall add rule name="emforge-mcp" dir=in action=allow protocol=TCP localport=8765 remoteip=LocalSubnet
```
非 loopback 綁定沒 token → `--serve`／`device-serve` **拒起**（exit 1）。token 外洩＝整個機隊都能被開 HFSS（見 implementation.md §12-24）。
**上限**：`emforge init` 已在 `%EMFORGE_ROOT%\limits.json` 留範本（本機檔——§2 的 root 在本機碟，所以真的是每台自己的
`allowed_profiles`／`max_sample_s`／`min_free_gb`）；改完重啟 worker 生效，`device-describe <tag>` 印來源。
**啟動**：`scripts\start_worker.cmd --serve`（＝pull → doctor → `emforge worker --serve`；MCP 在同一行程 daemon thread；server **綁上埠之後**
`devices/<tag>/state.json` 的 `url` 才會填上；埠被佔 → 印 `MCP 埠 … 綁不上`、exit 1、不跑 worker——檢查 #13）。
只服務、不撿佇列：`emforge device-serve <tag> --root %EMFORGE_ROOT%`。
**開發機接上**：`emforge fleet --root %EMFORGE_ROOT% --mcp-config > .mcp.json`（header 用 `${EMFORGE_DEVICE_TOKEN}` 佔位，Claude Code 讀環境變數）；
命令列試量：`emforge device-simulate --url http://<ip>:8765/mcp --profile P --bits @king.txt`（兩段式：先印 token，再帶 `--confirm`）。

## 3. runtime 一夜

停舊 `grind_loop`（`tmp/grind_loop.STOP`，否則它繼續往舊佇列丟 job）。開發機同樣兩個根分開：`EMFORGE_ROOT` 本機、`EMFORGE_DEPOT` 指 NAS 樹（runtime 的狀態在 NAS、策略程式碼在本機）：
```
emforge run --root %EMFORGE_ROOT% --profile dual_p01_db075     # detach（start /b 或工作排程器），不掛在 harness 下（I-12）
```
`strategies.yaml`：`top_k_flip(prio 3, batch 60, params {k:10, d:3})` ＋ `blind(prio 9, batch 20)`（init 範本預設 `enabled: false`，整夜跑要自己打開；背景策略實際每次派一筆）。
早上看：`status` tick ≥ 8、每策略 n_records > 0、`strategy_error` 未達 3、`profile_tamper` 零、有 `record_candidate` 就有 `notarize_*` 鏈、
`report` 印得出 P(勝 blind)（blind n ≥ k_min）、216 磁碟不變。

## 4. 其餘兩台

218、37 各重複 §2（各釘一筆 smoke）。三台 `queue/log/<tag>.jsonl` 都有 `worker_start`；一個 store 只被一台 claim。

## 5. 收尾

本文記三台 sha、doctor 輸出、smoke 數值；舊 `jobs.json` 封存；`/nas-backup` 增列 `emforge/`。

## 6. 儀器層／MCP 驗收（216 先；每步照抄，任一步不過就停在該步）

前提：§2 的 smoke 已過、§2b 已設 token／host／port、`start_worker.cmd --serve` 已起、開發機也設了同一個 `EMFORGE_DEVICE_TOKEN`。

1. **機隊看得到**：`emforge fleet --root %EMFORGE_ROOT%` → `216 idle`、`url` 非空（`--mcp-config` 印得出 `http://<216 ip>:8765/mcp`）、estop 空。
2. **說明檔**：`emforge device-describe 216 --root %EMFORGE_ROOT%` → profile 表含 `dual_p01_db075`、限制來源 `…\limits.json`、體檢「阻擋：無」。
3. **同機 bit 級（同一儀器的實證）**：現任王的 bits 存檔 → 經 MCP 跑一筆 → 與 db 那筆逐位元相等：
   ```
   python -c "from emforge.db import Database; r=Database(r'%EMFORGE_DEPOT%').view('dual_p01_db075').top(1)[0]; print(r.id); open('king.txt','w').write(''.join('1' if b else '0' for b in r.bits.reshape(-1)))"
   （db 在 `EMFORGE_DEPOT`，不是本機根；`top(1)` 是分數最高的一筆，不一定是榜上的現任王——要拿王用 `Ledger(D, profile, spec).best()`；CLI 沒有列榜的子命令）
   emforge device-simulate --url http://<216 ip>:8765/mcp --profile dual_p01_db075 --bits @king.txt --by ricky      # 印 token 與預覽（record_id 應＝上面印的 id）
   emforge device-simulate --url http://<216 ip>:8765/mcp --profile dual_p01_db075 --bits @king.txt --by ricky --confirm <token>   # 100–250 s
   ```
   驗收：結果 `status=done`；`devices/216/adhoc/<stamp>-<id>.json` 的 `response` 與 db 那筆 `np.array_equal`（同機噪音地板 0.000）；`time_s` 100–250；C 槽剩餘不變；**db 沒多一筆**（`emforge report` 筆數不變）。
4. **忙碌拒絕**：worker 正在跑批時再下 `device-simulate … --confirm` → stderr `device_busy: …`、exit 1；批照跑、同一 token 稍後還能用。
5. **急停**：`emforge device-estop engage --root %EMFORGE_ROOT% --tag 216 --by ricky --reason 驗收` → `queue/log/216.jsonl` 出現 `job_yield reason=estop_engaged`（正在跑的那筆 ≤ 5 s 內被殺、記 error）、`fleet` 顯示 estop `device`、`device-simulate` 回 `estop_engaged`。
6. **解除只能 CLI**：Claude Code 對 216 叫 `device_estop`（engage）可以、找不到任何 clear tool；`emforge device-estop clear --root %EMFORGE_ROOT% --tag 216 --confirm` → `fleet` 回 idle、worker 下一輪重新撿、`devices/216/log.jsonl` 有 `estop_cleared`。
7. **從 MCP 停／續 worker**：Claude Code 接上 `.mcp.json` 後叫 `device_stop_worker`（兩段式）→ `queue/STOP.216` 出現、worker 跑完當前 job 收工（`worker_stop reason=stop_file`）；`device_resume_worker` 刪檔；`start_worker.cmd --serve` 重啟。
8. **記錄**：三台 sha、token 輪替日期、每台 `limits.json`、第 3 步的 `np.array_equal` 結果與 `time_s` 進 §5；218、37 各重複 1–3。

## start_worker.cmd

`scripts/start_worker.cmd [--serve …]`：`git pull --ff-only` → `emforge doctor` → `emforge worker`（參數原樣透傳；`--serve` 同行程起 MCP）。**要拉就一定重啟、要重啟就一定拉**（I-10 的制度性解法）。
