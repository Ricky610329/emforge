# 部署與切換（三台正式機：216／218／37）

> 原則：**任一步驗收不過就停在該步**，其餘機器繼續跑舊系統——不損失整日機時。
> 同一台機器同時只能有**一個** HFSS 使用者（`kill()` 殺全部 ansysedt.exe）：切換逐台做，先停舊 worker 再起新的。

## 0. 開發機先做

1. `EMFORGE_ANTENNA_REPO` 指向 Antenna clone；`python -m pytest`（274 綠，含 adapter parity 500 筆逐位元）。
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
setx EMFORGE_ROOT "T:\碩二_鄒穎麒's\antenna\emforge"
setx MPLBACKEND Agg
```
**停舊 worker**：舊 repo `jobs_state/STOP` → `jobs-ls` 確認本機無 claim → `tasklist | findstr ansysedt` 為空。
**體檢**：`emforge doctor --root %EMFORGE_ROOT%` 必須 0（root 探針、磁碟 ≥ 20 GB、無 ansysedt）。
**smoke**（同一儀器的實證）：
```
emforge smoke <smp073_d_040 的 record id> --root %EMFORGE_ROOT% --profile dual_p01_db075 --machine 216 --by ricky --n 1
scripts\start_worker.cmd            # 桌面終端啟動（HFSS 視窗可見）；腳本＝pull → doctor → worker
emforge watch --root %EMFORGE_ROOT% --stores <smoke store>
```
驗收：`queue/log/216.jsonl` 有 `job_claimed` 無 `gate_rejected`；結果檔 `worker_ver` 帶 sha；**同機** `response` 與舊量測 `np.array_equal`
（舊 records 註記同機噪音地板 0.000）；跨機 `|Δm_k| ≤ 0.75`；`time_s` 100–250 s；C 槽剩餘不變。
**回滾**：`emforge stop --worker --machine-tag 216` → 刪舊 `jobs_state/STOP` → 桌面重啟 `python -m script.dedust worker`。舊佇列、舊資料全未動。

## 3. runtime 一夜

停舊 `grind_loop`（`tmp/grind_loop.STOP`，否則它繼續往舊佇列丟 job）。開發機：
```
emforge run --root %EMFORGE_ROOT% --profile dual_p01_db075     # detach（start /b 或工作排程器），不掛在 harness 下（I-12）
```
`strategies.yaml`：`top_k_flip(prio 3, batch 60, params {k:10, d:3})` ＋ `blind(prio 9, batch 20)`。
早上看：`status` tick ≥ 8、每策略 n_records > 0、`strategy_error` 未達 3、`profile_tamper` 零、有 `record_candidate` 就有 `notarize_*` 鏈、
`report` 印得出 P(勝 blind)（blind n ≥ k_min）、216 磁碟不變。

## 4. 其餘兩台

218、37 各重複 §2（各釘一筆 smoke）。三台 `queue/log/<tag>.jsonl` 都有 `worker_start`；一個 store 只被一台 claim。

## 5. 收尾

本文記三台 sha、doctor 輸出、smoke 數值；舊 `jobs.json` 封存；`/nas-backup` 增列 `emforge/`。

## start_worker.cmd

`scripts/start_worker.cmd`：`git pull --ff-only` → `emforge doctor` → `emforge worker`。**要拉就一定重啟、要重啟就一定拉**（I-10 的制度性解法）。
