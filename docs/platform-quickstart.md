# 單機、多機與版本更新

這是開發驗收用的 fake 流程。所有機器先備妥可 import emforge、numpy、yaml 的 Python；
模擬端另外備妥該 profile 的 adapter。平台不安装環境，不替使用者選 GPU 或遷移算法。

## 單機 fake

以下 PowerShell 命令在 emforge repo 執行；各長駐命令分別開終端。
Python 請使用已安裝此專案的環境。

```powershell
$py = 'C:/Users/ricky/miniforge3/envs/ant/python.exe'
& $py -c "from emforge.testing import make_fake_root; make_fake_root(r'C:/emforge-demo/platform'); make_fake_root(r'C:/emforge-demo/sim')"
& $py -m emforge init --root C:/emforge-demo/platform --profile fake_f1
@'
profile: fake_f1
runtime:
  tick_s: 1
strategies: []
'@ | Set-Content -Encoding utf8 C:/emforge-demo/platform/runtime_state/fake_f1/strategies.yaml
```

先驗證並要求啟用第一個版本。需保留測試所用 EMFORGE_ANTENNA_REPO 指向既有唯讀原專案，
正式 HFSS 測試在 release 驗證子行程內一律停用。

```powershell
$env:EMFORGE_ANTENNA_REPO = 'C:/Users/ricky/Documents/GitHub/Antenna'
& $py -m emforge release-apply --releases-root C:/emforge-demo/releases --source . --python $py
& $py -m emforge platform-service --releases-root C:/emforge-demo/releases --root C:/emforge-demo/platform --profile fake_f1
```

另開終端跑模擬端與算法端：

```powershell
python -m emforge worker --root C:/emforge-demo/sim --depot http://127.0.0.1:8766 --machine-tag sim_a --poll-s 1
python -m emforge algorithm-worker --endpoint http://127.0.0.1:8766 --node gpu_a --work-root C:/emforge-demo/algorithms --environment ant=C:/Users/ricky/miniforge3/envs/ant/python.exe
```

登錄範例並啟動；VERSION 換成登錄回傳的 pkg_ 雜湊。

```powershell
python -m emforge algorithm-register --endpoint http://127.0.0.1:8766 --name anneal --source examples/anneal --requires numpy --checkpoint-schema anneal_v1
python -m emforge algorithm-start --endpoint http://127.0.0.1:8766 --name anneal --version VERSION --run-id trial_a --node gpu_a --environment ant --profile fake_f1 --budget 100 --seed 7
python -m emforge algorithm-status --endpoint http://127.0.0.1:8766 --run-id trial_a
python -m emforge algorithm-logs --endpoint http://127.0.0.1:8766 --run-id trial_a
python -m emforge report --endpoint http://127.0.0.1:8766 --profile fake_f1 --run-id trial_a --curve
```

examples/anneal/main.py 自己持有溫度、接受率、RNG 與 checkpoint.json。
它以 run 內 request_id 送件；停止時保存最後完成的一輪。中斷輪在續跑時重新提出，
平台會共用已存在的量測。需調整參數可用 algorithm-start --params 的 JSON，
例如 rounds、temperature、cooling；新設定使用新 run_id。

algorithm-stop --run-id trial_a 要求合作停止，超過寬限只終止該算法行程樹。
已送出的量測繼續收。相同節點、已終止執行、相同 checkpoint_schema 可用
algorithm-start --run-id trial_b ... --resume-from trial_a；
來源工作目錄由 EMFORGE_RESUME_FROM 提供，不覆蓋舊 checkpoint。

不需版本管理時，可分開跑 platform-serve 與 run，兩者指同一 Depot。
platform-service 已含 HTTP 與 runtime，不能再在同 profile 另起 run 或占用相同 HTTP 埠。

## 多機

協調機用 platform-service --host 0.0.0.0；各機設相同 EMFORGE_PLATFORM_TOKEN。
算法機的 --endpoint、模擬機的 --depot 改成協調機的 HTTP 位址。
算法機各用不同 --node 與本機 --work-root；模擬機各用不同 --machine-tag、registry.py 與本機工作目錄。
registry.py 的 profile／measure 定義必須同源，儀器變動使用新 profile 名。

每台算法機只宣告自己的環境別名。algorithm-start 的 --node/--environment 明確指定目的地；
平台不自動搬機。單機可以把所有角色放在同一台，接口相同。
共用 token 代表可信任的機隊，不提供多租戶隔離；HTTP /depot 有儲存操作權。
跨不可信網路需外接 TLS。算法程式使用節點的作業系統權限，行程隔離不是惡意程式 sandbox。

## 平台 MCP

```powershell
python -m emforge platform-mcp --endpoint http://127.0.0.1:8766 --port 8767
```

連線 /mcp；platform://reference 列出 platform_query 的唯讀操作與參數。
inbox_submit 以同 payload 兩次呼叫完成預覽和送件；agent 可以自動握手。
algorithm_start/algorithm_stop 控制已登錄的算法；不提供改碼、promote 或解除急停。
MCP 代理行程與儀器 MCP 各自獨立，optional extra 未安裝時其他介面照常運作。

## 更新與回退

agent 在候選 checkout 改碼，呼叫：

```powershell
python -m emforge release-apply --releases-root C:/emforge-demo/releases --source C:/path/to/candidate --python C:/path/to/existing/python.exe
python -m emforge release-status --releases-root C:/emforge-demo/releases
```

release-apply --source 依序：凍結程式碼及測試 → pytest → pyflakes → 隔離 fake HTTP/送件/量測探針 →
寫入更新要求。回傳要求表示已排入更新，完成與否以 release-status 的 state 為準。
可拆成 release-stage、release-validate、release-apply --version REL_VERSION。
驗證失敗不改更新要求；每次啟動再次比對內容 hash，不能原地改已驗證快照。

supervisor 收到要求後建立 MAINTENANCE，runtime 繼續收結果、暫停所有新派工，
公證候選另存待續清單。等到該 tick 保存 state/status，才要求舊平台退出。
新版本讀原 Depot、修復索引、補完可證明的派工意圖，再核對 inflight／job／batch。
對帳與 HTTP 健康檢查成功才恢復派工、記為 last_good。

新版本啟動失敗會保留 stdout 與原因，回到 last_good；沒有可用上一版則維持 failed。
未知矛盾、舊格式缺乏恢復證據、hash 不符均拒絕猜測。
runtime 意外退出後可由 agent 查 release-status，再以 release-apply --version 重啟已驗證版本；
沒有無限重啟循環。supervisor 重啟會重開 last_good，算法節點各自保存工作目錄。

更新不等待所有 HFSS 結束、不殺模擬端、不回滾資料；HTTP 會短暫中斷。
算法需處理連線錯誤或從 checkpoint 新建 run。Client.wait 的 timeout 可持原 sid 繼續查，
有副作用操作不透明重送；重送 submit 必須使用相同 run_id/request_id/內容。
相容資料格式是此版更新前提，沒有破壞性 schema 遷移。
bootstrap 自身、算法節點、模擬端及 MCP 代理不在這次平台行程更新中；
它們的更新需要各自重啟及版本相容驗證。

## 評估口徑

run 與送件保存完整 spec_snapshot，避免新程式重新登錄同名 spec 改動舊分數。
舊資料缺快照仍按原相容讀法解析；平台不回填猜測歷史定義。
原始 response、measure 與原提出者保留；共用結果不把原資料改成新提出者。

--curve、--calibration、--metrics 皆從 measure 用固定 spec 重算。
效率曲線只含新 sample 的唯一 id；失敗仍算探索樣本、repeat／共用不灌水。
成本分 proposed/shared/new_measurements；budget 是新增候選數，
不是包括 HFSS 重試與公證的總呼叫或牆鐘時間上限。
預測寫 preds 或 note.pred；校準沒有足夠變異就回 null。
P(勝 blind) 是觀察分佈比較，沒有因果效果保證。
