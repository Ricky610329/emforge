> 來源：Antenna repo `docs/extract/incidents.md`（2026-09-01）。回歸測試 docstring 的 `I-N` 指這裡。

# 事故史：每個機制背後的那一次失敗

> **為什麼這份最難重得**：程式碼結構其實很簡單，價值在「知道哪裡會爆」。
> 一個沒踩過這些坑的人重寫一遍，會把同樣的坑再踩一次——而每個坑當初都吃掉數小時到數天的機時。
> 出處：`script/CLAUDE.md` 第 5/7/8 條、`run()` 的 `#!`/`#?` 註解、`docs/memory/feedback_failure_modes.md`。

---

## 一、把產線打掛的（優先級最高）

### I-1　磁碟塞爆 → 求解器連環例外（2026-07-15）

**症狀**：某台機器的求解器開始丟 `0x80070223`，重開機看似好轉，隔天再犯。
**真因**：78 個工作的模擬暫存目錄吃光系統碟，磁碟見底導致 COM 例外爆發。**重開機是假好轉**。
**修法**：工作目錄跑完即刪；worker 啟動時掃 cwd 的殘留目錄全清（收 Ctrl-C／當機／讓位留下的）。
**留給新系統**：暫存目錄的生命週期必須有**兩道**——正常結束刪、啟動時兜底掃。
⚠ 本專案 2026-08-31 為了保留模擬檔加了 `keep_project` opt-in，**刻意讓那些目錄不被自動清**，
因此**必須人工清**。這種例外要嚴格限制在小批，整批線開啟等於重演本事故。

### I-2　殭屍 claim 與壞 claim

**症狀**：佇列顯示某工作「已被認領」，但沒有任何機器在做它。
**成因**：worker 寫了認領檔之後才進到執行函式，若那裡拋錯就留下無主 claim。
（2026-08-31 實例：埠數守門擋下錯誤設定 → 拋錯 → `.fail` 寫了，但 `.claim` 留著 →
清了 `.fail` 沒清 `.claim` → 其他機器一直不接。）
**修法**：無主 claim（>60s 無對應執行）自動清；stale 判定看**結果檔 mtime** 而非 claim 時間。
**留給新系統**：重派的動作必須是**原子的一組**（claim + done + fail + 佇列條目一起處理），
不能分次手動清。

### I-3　佇列檔並發損壞

**成因**：多台同時讀-改-寫 `jobs.json`。
**修法**：全程持鎖（`O_EXCL` 寫鎖 + 陳鎖 180s 自動破鎖），寫入走 tmp → 原子替換。

### I-4　背景自產把兩台機器炸死（2026-08-03）

**成因**：佇列空閒時的自產路徑拋出空 stack 例外，直接殺掉 worker 行程。
**修法**：自產路徑的任何例外**一律吞掉**，不准殺 worker。
**留給新系統**：**背景/填充等級的工作絕不能有能力終止執行器**。

### I-5　查重表把迴圈撐爆（2026-08-27）

**成因**：全歷史查重表隨資料夾數成長，記憶體尖峰弄崩無人值守迴圈的子行程。
**修法**：查重表落磁碟快取，只掃新增/變動的夾（6 分 → 1 分 14 秒）。
**留給新系統**：任何「掃全歷史」的操作都會隨時間變成瓶頸，**設計時就要有增量路徑**。

---

## 二、產出錯資料而不報錯的（最危險的一類）

### I-6　用錯模擬器，跑完且看起來正常（2026-08-31）

**症狀**：14 筆雙埠設計被單埠模擬器量完，結果欄位齊全、數字合理。
**真因**：派工時漏帶設定檔 → 執行器用自己的預設（單埠）。單埠底板只有一條饋線＝**量錯元件**，
連幾何都錯。
**為什麼危險**：**錯得不會報錯**。若不是因為「順便求解當公證」而比對了歷史值，
這批錯的模擬檔會直接送到合作對象手上做實體板。
**修法**：生成端在 manifest 宣告 `port`，執行端開模擬器**之前**比對，不符即拒。
**留給新系統**：凡是「生成端知道、執行端也需要知道」的屬性，都要**雙邊宣告 + 一致性檢查**，
且檢查要在昂貴操作之前。

### I-7　舊幾何的數值幻影（2026-08-13）

**症狀**：某類設計在模擬中表現優異，但幾何上是零厚度的角接觸。
**真因**：舊幾何底板讓對角接觸成為數值假象，**不可作實物預測**。
**修法**：換代新幾何；舊代的四個紀錄鍵**永久凍結**、標記不可跨代比較。
**留給新系統**：見架構草案 §2.3——**世代必須是資料的一等欄位**。

### I-8　安全閘的退出碼被吃掉（2026-08-06）

**症狀**：查重閘看似跑過，實際失效。
**真因**：把查重接在管線裡，退出碼被尾端指令吃掉 → **安全閘靜默失效**。
**修法**：查重必須**單獨跑**、看它自己的退出碼。
**留給新系統**：安全閘不可串接；它的失敗必須能終止整個流程。

### I-9　在本機用 CI 模式跑測試 → 靜默重錨基準（2026-08-31 發現）

**症狀**：基準測試檔被改動，但沒人記得改過它。
**真因**：CI 模式用較寬的容差，比對「通過」後測試框架把當前值寫回基準檔。
**修法**：本機永不加 CI 旗標。
**留給新系統**：**自動寫回基準的機制必須與「寬容差模式」互斥**，否則寬容差會慢慢侵蝕基準。

---

## 三、環境與部署

### I-10　只拉取不重啟＝跑舊程式（已犯兩次）

改動執行器程式後，各機器必須**拉取 + 重啟**。只拉取不重啟，worker 仍跑舊程式。
**留給新系統**：執行器要能在啟動時印出自己的版本，且判讀工具要能顯示「這批是哪個版本跑的」。

### I-11　基準測試綁執行緒數（2026-08-31 發現）

換機器後 3 條基準測試紅了。逐一排除（模型版本相同、數值庫版本無關、強制 CPU 故與顯卡無關），
真因是**執行緒數**：舊機預設 4 緒、新機 14 緒 → 歸約順序不同 → 浮點累加差異，
特徵值分解最敏感，迴圈逐步放大到 1.09%。
**留給新系統**：任何「位元級可重現」的宣稱都要把**執行緒數與數值庫**一起釘住，
否則換機器就會誤判成程式壞掉。

### I-12　harness 進程換代 → 背景任務全滅

某次工具進程換代，所有背景任務被清掉，一個訓練工作因此中斷。
**留給新系統**：長時間工作不可依賴 harness 的生命週期；要嘛 detach，要嘛可續跑。

### I-13　派工沒掛偵測＝鏈斷睡死（2026-08-17）

事件驅動架構下，派工若沒有伴隨監看，就**沒有喚醒源**，整條鏈停在那裡沒人知道。
**鐵則**：**派工與掛偵測必須在同一個動作裡完成**。

---

## 四、協作與紀錄

### I-14　在未落地的狀態上疊工作（2026-07-13）

某段工作的提交沒進版本庫、產物被回退，卻在未驗證的狀態上又疊了三層。
**修法**：狀態對帳流程（交叉驗證版本庫／產物／紀錄三方一致），在切換模型、長時間自動跑、
宣稱破紀錄前執行。

### I-15　多台各自寫同一份對照表，後跑的覆蓋先跑的（2026-08-31）

交付集散在多台機器，每台各跑一次收檔打包，整份重寫 → 39 筆的對照表只剩最後一台的 21 筆。
**修法**：改成以主鍵合併；且「缺」不得覆蓋別台已寫的「成功」。
**留給新系統**：任何「多方各跑一次」的匯總都必須是**合併語義**，不是覆寫語義。

### I-16　自己的實驗錯誤，三次都是稽核代理抓到的

記在 `feedback_failure_modes.md`：作者自陳「我自己的資料/實驗錯誤，3 次全是 agent 抓到的、不是我」。
**修法**：結論產出後派獨立稽核；**brief 必須帶「已知的失效模式」**——
效果最好的一次是因為 brief 裡列了自己踩過的三次當範例，稽核就去找同類、又找到三個。
「代理的價值來自我給的先驗，不只是它的獨立性。」
⚠ 但**代理的結論不當判決**：凡影響方向的宣稱要自己重跑一次再採信。

---

## 五、給新系統的六條歸納

1. **昂貴操作之前要有檢查**（I-6、I-8）——檢查放在開模擬器前，不是跑完才發現。
2. **背景工作不得有能力終止前景**（I-4、I-5）。
3. **自動寫回機制要與寬容差模式互斥**（I-9）。
4. **多方匯總一律用合併語義**（I-15）。
5. **派工與監看是同一個動作**（I-13）。
6. **「錯得不報錯」是最貴的一類**（I-6、I-7、I-8、I-9 都屬此類）——設計時要問的不是
   「這會不會出錯」，而是「**出錯的時候我會不會知道**」。

## 六、emforge 實機部署驗收

### I-17　同秒逐台 smoke 撞批名（2026-09-12）

準備三台驗收時，以固定時間的回歸測試重現：同一個 pattern 先派到 216，再派到 218，
第二次拋出 `StoreExists`。原批名只使用秒級時間、pattern 前綴與重測序號，未區分每次請求；
同台在同秒再次要求重測也會相撞。

**修法**：每次 smoke 呼叫產生獨立 UUID，和時間一起交給 `paths.smoke_store_name`。
每次呼叫內的重測仍共用同一請求識別、以序號區分；保留機器釘選與原量測。
回歸測試固定相同時間，依序對 216／218／216 各派兩筆，確認六個批名唯一且各自釘選正確。

## 七、2026-09-23 全面審查（六個切面、Antenna 對照）

> 六個獨立審查代理（worker／runtime／platform／資料層／Antenna adapter／CLI 與文件）各自重現後回報；
> 每項都有回歸測試，docstring 引用下列編號。修正紀錄與恢復契約見 [reliability-2026-09-23.md](reliability-2026-09-23.md)。

### I-18　單筆 job 錯一筆就暫停整個 profile

inbox 與背景派的是 n=1 的批；逐批算錯誤率＝1.0 > 0.5 → `paused_profile`，其餘送件永遠停在 received。
**修法**：錯誤率看最近 20 筆樣本（跨批、至少 3 筆），視窗存 `state.error_window`。

### I-19　一個雜結果檔讓整個 runtime 退出

結果目錄裡一個不在 manifest 的檔 → `patterns[rid]` KeyError → 每個 tick 都炸，連錯十次 runtime 退出，期間其他批次全收不到。
**修法**：collect 每個 store 各自隔離（事件 `collect_error`）；雜檔點名一次（`stray_result`）、記為已讀、不入庫。

### I-20　派工半途失敗的孤兒 inflight 只有重啟才修

inflight 已寫、`queue.add` 瞬斷 → 佇列狀態 missing：collect 收不到、占住 `max_inflight`、只剩它時 `fleet_quiet` 停掉整個排程；
`recover` 只在啟動時跑一次。`inbox.take` 的例外還會穿出 tick，同 tick 其他策略全被跳過。
**修法**：每 tick `recovery.recover_missing` 補完意圖（事件 `dispatch_recovered`）；inbox 派工例外只記 `dispatch_failed`。
**留給新系統**：I-13 的「派工與偵測同一動作」要在**執行中**也成立，不是只有啟動對帳。

### I-21　collect 之後、notarize 之前例外，破榜候選永遠不進公證

collected 已逐筆落地，notarize 還沒跑就 tick 例外（或行程死掉）→ 下個 tick 這些 record 不再是「新收」。
公證重測派不出去時也直接丟掉候選。
**修法**：新收的 done 樣本在標 collected 之前先寫進 `state.notarize_deferred` 並落地；派不出去的候選留到下一 tick。

### I-22　state.json 遺失，tick 歸零撞既有批

重啟後 tick 從 0 開始 → 新 store 名撞既有批（StoreExists）連錯十次退出。
**修法**：state.json 缺席時對齊到既有 inflight／批的最大 tick。

### I-23　jsonl 尾行半截後永遠 FsCorrupt

NAS 斷線／斷電讓 `_index.jsonl` 只寫半行 → refresh／View 永遠 FsCorrupt、runtime 起不來；下一次 append 黏在半截後面＝永久的中段壞行。
**修法**：`read_jsonl` 跳過**沒有換行的最後一行**；`append_jsonl` 先砍掉半截再寫。中段壞行仍拋。

### I-24　HTTP claim 回覆遺失，鎖殘留 180 秒

伺服器已建好 claim、回覆遺失 → `lock()` 直接拋、不 release → jobs.lock 擋住整個機隊到過期。
**修法**：claim 的呼叫炸了先補一次 `release(owner=自己)`（冪等）再拋。

### I-25　NaN／inf 讓 HTTP 平台斷線

File／Memory 後端存得下 NaN，HTTP 的 `wire.dumps(allow_nan=False)` 在 `_reply` 的 try 外炸 → RemoteDisconnected；
response 含 −inf（dB 取 log 0）時 query／sample／top 的 RPC 整批失敗。
**修法**：wire 以 `{"__emforge_float__": "nan"|"inf"|"-inf"}` 編碼；`_reply` 序列化失敗回 `ok:false`。

### I-26　/depot 用磁碟機代號逃出 Depot 根；瀏覽器可跨站寫

`check_key` 只擋 `/` 開頭、反斜線與 `..`，`C:/…` 放行 → `Path(root) / "C:/x"` 直接換掉 root：遠端可讀寫刪列平台機任意檔；
根層 `registry.py`／`strategies/` 是平台機會執行的程式碼。loopback 無 token 的部署，網頁用 text/plain simple request 就能寫 /depot。
**修法**：key 拒絕冒號、段尾 `.`／空白、根層本機專屬名；FileDepot.path 再擋絕對路徑；POST 必須 application/json、帶 Origin 一律 403。
**仍待處理**（設計層，交付客戶前）：/depot 等於整棵共享儲存的控制權（可強制 release 任何鎖、刪 `queue/ESTOP`）；
算法子行程與 agent 都拿得到完整平台 token。要縮小只能在伺服器端限制 key 前綴＋把程式根與 Depot 根分開。

### I-27　平台服務一次瞬斷就停；切換中崩潰丟更新要求

`platform-service` 主迴圈只接 KeyboardInterrupt，`Supervisor.tick` 的一次共享 Depot 例外穿出 with 區塊 → `close()` 停掉平台子行程。
draining／stopping 中崩潰：要求已記 `seen_request`、重啟後 phase 回 idle、target 不再套用。
**修法**：`run_service_loop` 接住 tick 例外續跑；重啟時 draining／stopping 的 `seen_request` 清掉重新套用。

### I-28　promote 到別的 spec 時抄了別人的分數

spec 已註冊但全部量測被門檻擋（score 全 None）→ 退回抄 pending 裡 profile 規格的分數寫進另一個 spec 的榜。
**修法**：算不出分數就拒絕 promote。promote／rescore 加榜鎖；rescore 的 history 記正確的 force。

### I-29　NaN／inf 響應與違反能量守恆的響應照樣給分

S11 帶外一點 NaN、帶內 −inf、S21 全 +3 dB（Σ|S|²≈2，被動網路不可能）都標 done 並拿到有限分數。
來源：舊 `align_curve` 用 `np.interp` 補點、CSV 空值一路傳下去。
**修法**：`make_result` 對非有限值回 `nonfinite_response`；`specs.check_passivity` 對 `energy_max > 1.05` 拒收（collect 記 measure_failed）。

### I-30　「全綠」可能是假的

`EMFORGE_ANTENNA_REPO` 指到不存在的路徑，pytest 表頭照樣寫「啟用」，adapter 綁定／parity 測試其實全 skip。
**修法**：表頭回報實際綁到的路徑；設了但不是 Antenna repo 就整套拒跑（UsageError）。`hfss` 標記正式註冊、非 `EMFORGE_HFSS_TESTS=1` 一律 skip。

### I-31　legacy 匯入：重複量測選同一條 y、error 列擋住補測

同 pattern 多次量測、wm 四捨五入到 2 位相同（跨機複驗幾乎都是）→ 每列都拿 `hits[0]`，第二台的實際響應消失。
先以 `--include-errors` 匯入 error 列、舊 store 補測成功再重匯 → `(rid, store)` 相同、`add` 回 False，done 被靜默丟掉。
**修法**：每個 pattern 的 y 配過就不再配；`Database.upgrade_error` 讓 done 取代同 (id, store) 的 error（唯一允許取代的路徑）；
`_imported.json` 的 n_records 改累計。

### I-32　gate 第 4 步對 antenna 是空操作

`profiles.check_labels` 讀 `labels`，adapter 宣告的是 `LABELS` → 永遠比對空 tuple。靠建構子的 `_validate` 沒漏網，但 gate 的說法不實。
**修法**：adapter 同時宣告 `labels`。

### I-33　limits.json 遇 BOM 讀不起來

PowerShell 5 的 `Set-Content -Encoding utf8` 一定寫 BOM；`connection.json` 用 `utf-8-sig`、`limits.json` 用 `utf-8` → 「Unexpected UTF-8 BOM」。
**修法**：本機設定檔一律 `utf-8-sig`。

### I-34　CLI 陷阱四則

`init` 範本預設開 blind prio 9 batch 20（照錯誤訊息 init 再 run 就往真 HFSS 派 20 筆盲探索）→ 範本 `enabled: false` 加說明；
`stop --profile P --machine-tag T` 靜默忽略 `--machine-tag` 停掉整個 runtime → 拒絕；
`algorithm-register` 目錄裡一個二進位檔就整包 UnicodeDecodeError → 指名檔案；
`algorithm-worker` token 錯時 PermissionError（OSError 子類）被當暫時錯誤無限重試 → 退出 2。

### I-35　worker 可靠性四則（看門狗、待傳區、啟動鎖）

看門狗輪詢急停時遇 depot 例外（SMB／HTTP 瞬斷）→ 執行緒死掉，之後 HFSS 卡住再也沒人 kill（I-4 的變體）。
一筆不相容／已 abandon 的待傳結果每圈拋 ValueError → worker 連錯十圈自行退出、重啟照樣再死，還擋住其他 store 的補傳；
本機 done 被遠端同 attempts 的 error 蓋掉後刪除（違反 I-15）；認領前補傳的瞬斷被當成這批的失敗列入 fail 名單。
Windows 行程結束後只要還有人持有 handle 就被判活著 → 永遠拒絕啟動；藍屏留下 0 byte worker.lock → 永遠拒絕啟動（I-2 本機版）。
**修法**：abort_if 例外一律當 False；回填不了的搬到 `work.emforge/results_held/`、記一次 `outbox_held`；done 一律勝過遠端非 done；
補傳瞬斷包成 ResultPending；`process_identity` 先看 exit code；空鎖 60 秒寬限後回收、claim 寫入 fsync。

### 本輪未做（屬設計選擇，記錄待決）

- **HFSS 收斂判定**：新舊系統都沒擷取收斂狀態；現役設定（max_passes 6、min_converged 5）幾乎必然撞上限。
  本輪只做第一階段：`sim.simulate` best-effort 掃訊息窗，把 `{converged, source, messages}` 放進 `extra.hfss_convergence`（需正式機驗證 COM 呼叫）。
  是否拒收未收斂、要不要開新 profile（kwargs 在 profile_hash 裡）是第二階段的決定。
- **worker 側 kill 範圍**（舊 `script/kill.py` 殺整台所有 ansysedt）、`keep_project`（送板用 .aedt）、COM 例外分類（磁碟滿 0x80070223 應視為機器問題）
  都在 Antenna repo 或需要產品決定。
- MCP `inbox_submit` 用沒 start 過的 run_id 可繞過預算；`algorithm_start` 沒有確認步驟——授權模型的選擇。
- `profile_hash` 不含 measure 的 targets：使用者自寫的 measure 改了 targets、名字不變會靜默混用（antenna 的尺已凍結不受影響）。改 hash 會讓已部署的三台與既有紀錄全部 profile_tamper，需要遷移方案。
