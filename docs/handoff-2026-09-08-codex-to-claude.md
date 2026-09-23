# Codex → Claude 工作交接（2026-09-08）

這份文件是本次工作的交接入口，整理目前實作、使用者定案與尚未完成的產品工作。交接基準為 **a5c808e033458c21beed3cd2823b64974f8f4805**；本次交接只更新文件，未改執行程式。

## 先讀這些結論

- 實際專案是 **C:/Users/ricky/Documents/GitHub/emforge**。對話工具的 cwd 常在 Antenna，但不能在 Antenna repo 實作 emforge。
- 六個平台里程碑與候選優先級已完成，最近完整驗證 **615 passed（67.57 秒）＋pyflakes 通過**，git diff --check 通過。
- 實作已本機 commit。Codex 沒有 push；沒有正式 HFSS 安裝、停機、模擬或 NAS 資料寫入。
- 使用者已設定 origin 為 https://github.com/Ricky610329/emforge.git。前次實際 ls-remote 確認遠端 main 為 d866cf1；交接時本機 origin/main 追蹤值仍為該 commit，本次未再次連線查遠端。
- **下一個產品重點是簡化工程師的操作。** 不要把已寫好的長部署文件，視為「易用的部署入口已完成」。
- 先讀本檔，再讀 CLAUDE.md、docs/priority-scheduling.md 與相關程式；舊任務書／架構文件有歷史語意，遇到衝突以目前使用者定案與實作為準。

## 使用者最新定案

最終使用者是日月光的工程師，未必熟悉軟體架構。平台需要承擔必要的複雜度，日常操作只需建立工作、選算法／模擬設定、看進度和結果。

HFSS 太慢是擴展多機的主要原因。目標是減少重複模擬、利用空閒設備、保留已完成結果與可靠恢復，而非把更多架構名詞交給工程師。

具體方向：

1. 部署為 **三台 HFSS（tag 216／218／37）＋一台平台／算法同機（目前按 rick 開發機編寫）**，共四台主機。
2. 保留內網 HTTP 和 NAS／共享資料夾方案。內網是連線環境，NAS 是儲存位置，兩者可以共存。
3. 使用方式盡量接近原本「各台 git clone／git pull，再長跑一個 worker」。
4. Token 是 HTTP 存取憑證，應一次設定後由軟體帶入；不要每次開終端手動輸入。這個簡化入口尚未實作。
5. pattern 有不同用途：主要演化、優先驗證、填補空閒的背景探索；同算法的個別候選也可有不同等級。
6. 給使用者的報告放 **C:/Users/ricky/Desktop/em-forge**，全部用 HTML，程式碼區塊提供複製按鈕。
7. 使用者最後要求：整理成果與工作紀錄，交給 Claude 接手。不是要求現在開始部署正式機。

## 已完成的提交

| Commit | 成果 | 當時完整測試 |
|---|---|---|
| 2d4c7c5 | run_id／tag、保留 Record.run、血統與取樣 | 523 |
| 2324e35 | 冪等收件、Client、部分派工、重複候選共用結果／恢復 | 528 |
| df14ed0 | 平台 HTTP 操作、HttpDepot、真 socket 契約 | 568 |
| 822b1f8 | 固定算法包、指定節點／既有 Python 環境、隔離行程、預算與 checkpoint | 577 |
| 9aa2a69 | 固定評估定義、多指標評估／校準、平台 MCP | 582 |
| d866cf1 | release 驗證、Supervisor 切換／恢復／相容回退、退火範例 | 595 |
| a5c808e | pattern 優先級、背景填空、算法／run 公平輪替、單筆讓位 | 615 |

六里程碑詳見 docs/plan-2026-09-08-platform.md、docs/platform-quickstart.md。615 是最新程式基準，595 只代表前一個版本。

## 架構與可用入口

| 元件 | 現況 |
|---|---|
| 平台操作 | platform/service.py；本機 Client、HTTP、MCP 共用固定語意操作 |
| 資料渠道 | FileDepot／MemoryDepot／HttpDepot；協調狀態只經 Depot，key 只在 paths.py 定義 |
| 平台服務 | platform-service：Supervisor 管理 HTTP＋單一 profile 的 Runtime，通常 port 8766 |
| 算法節點 | algorithm-worker：指定 node／既有 Python；固定算法包，每 run 隔離行程與工作目錄 |
| HFSS 節點 | worker；可加 --serve，於同一行程提供儀器 MCP，通常 port 8765 |
| 平台 MCP | 可選平台操作代理，平台同機時可用 loopback 8767 |
| release | 快照→pytest／pyflakes／隔離 fake 探針→送出要求→Supervisor 切換→恢復／健康確認 |

關鍵語意：

- platform-service 已包含 runtime；同 profile 不要再啟第二個 run。platform-serve 只提供 HTTP，屬另一種組合入口。
- NAS 方案可讓模擬 worker 直接讀共享佇列；**目前 hosted 算法 Runner 仍使用 RemotePlatform HTTP endpoint**，不能宣稱整套 hosted 平台已能完全不啟 HTTP。
- 一個逐輪等待量測的退火 run 無法自然占滿三台。需要多 run，或算法提出足夠的獨立候選。
- budget 是新派工 sample 候選數；共用既有量測不扣。重試與公證另計，不能宣稱是 HFSS 總呼叫數上限。
- worker done 後仍需 Runtime 入庫；Client 等 completed 才能當作算法結果。
- examples/anneal 是 hosted 流程範例，並非完成舊 Antenna 全部算法移植。
- release-apply 成功代表驗證與要求送出。release-status 的 current／last_good／phase 才是實際切換證據。
- 更新只支援相容 schema；不回滾量測資料，也不熱換 Supervisor bootstrap／算法 Runner／模擬 worker。
- 目前是單協調平台，無備援平台自動接手；行程隔離也不是惡意程式沙箱。

## 最新優先級實作（a5c808e）

使用者可用 urgent=1、normal=3、background=9。numeric prio 仍接受；具名 priority 覆蓋預設 numeric prio，建議保留 background_prio=9。

### 契約與資料流

- Proposal 新增可選 priority、purpose。
- Client.submit 支援 priority（整份）、priorities（逐筆，優先於整份）、purposes（逐筆）。
- 清單長度／等級名稱／用途型別會驗證；相同 request_id 的內容比較包含優先級與用途。
- 提案穿過策略子行程仍保留新欄位；HTTP／MCP items 支援相同欄位。
- CLI submit 增加 --priority。
- 狀態保留原始候選 index；client.status／inbox_list／runtime inflight 可看到相關優先級資訊。
- strategy YAML 可使用 priority，prio 預設 3，batch 預設 1。

### 排程行為

- inbox 每策略每 tick 至多派一筆；大量候選留在收件匣，按優先級與 run 輪替挑選。
- 背景 propose 每次只要求一筆。相同 profile 尚有 queued job 時不再產生背景；已 claimed 的前景不擋填空。
- max_inflight 在 inbox 排程時計同策略中「同級或更高級」sample，低級工作不占高級候選配額。因此跨等級總數可能大於該值。
- 相同 profile／pattern 的未完成工作可以被其他提交者提升，採單向升級，共用結果，不重派。
- Queue.pick 在既有 jobs.lock 內按有效 prio、算法輪替、run 輪替認領；輪替狀態持久化。
- worker 在每筆結果保存後，遇到本機可接的更高級或另一個同級算法／run 工作時讓位；不打斷當前單筆。
- 最後一筆已完成則直接收尾，避免為了讓位之後再開一次 HFSS。
- 背景不保證取得資源；沒有固定百分比、加權時間份額或執行時限保證。

### 實作定位與相容邊界

| 檔案 | 職責 |
|---|---|
| emforge/priority.py | 等級映射、有效 prio、算法／run 分組與輪替 |
| emforge/queue.py | 認領、raise_priority、should_yield；list(original=True) 供恢復 |
| emforge/runtime/inbox.py | 提案驗證、共用引用、升級、候選與 run 排序 |
| emforge/runtime/schedule.py | 策略入口、配額、背景判斷、傳統逐筆 priority 分派 |
| emforge/runtime/dispatch.py、recovery.py | 派工意圖與原始 job 對帳 |
| emforge/worker/batch.py | 單筆保存後讓位與完成收尾 |
| emforge/model.py、strategy.py、submissions.py、client.py | 契約、序列化、持久送件與 Client |
| tests/runtime/test_priority.py、tests/test_priority.py、tests/worker/test_priority.py | 新行為測試 |

新增 queue/scheduling.json 保存 boosts／turns／sequence，key 由 paths.queue_scheduling 提供。Runtime state.inbox_turns 保存送件端的 run 輪替。job.extra 帶 strategy／run_id，舊 job 缺欄位時用相容分組。

提升不改原始 job／manifest／inflight.intent；Queue.list 預設顯示有效優先級，恢復比對使用 original=True。新增不同優先級的傳統提案可拆成 -pN 子批次，名字由 paths.priority_store 產生。

**已知限制，接手時不要誤讀成硬保證：**

- 舊多筆 job 被提升會整批提升；新 inbox 與有逐筆 priority 的提案才使用單筆 job。
- 傳統 propose 若一次回傳多筆含 priority 的提案，_dispatch_props 會在同一輪拆成多個 job；max_inflight 是策略入口檢查，未在每個子批次重新扣除。若產品要硬性限制全部預取數，需補測並收斂這條路徑。
- 公平輪替按認領機會，不是按模擬秒數；未做大量候選 backlog／NAS 延遲的吞吐基準。
- 優先級提升是單向的，沒有「需求取消後自動降回背景」。
- 平台與三台 worker 都需更新；只更新平台不能讓舊 worker 具備完整新排程。
- 配置中加入 blind 會在符合條件時自動探索。首次正式驗收仍建議先用 inbox-only 配置，之後再開背景工作。

## 驗證紀錄

最近一次完整程式驗證：

- 615 passed in 67.57s。
- python -m pyflakes emforge tests：無輸出、exit 0。
- git diff --check：無錯。
- 包含真 socket HTTP、MemoryDepot／本機 FileDepot、真算法子行程與 release 切換；模擬器仍是 fake。
- 新增覆蓋候選排序／原始 index、共用升級、重啟恢復、run 公平、三台背景填滿與上限、HTTP 公平狀態、單筆讓位、最後一筆直接收尾、壞送件不得升級等。
- 舊測試中依賴「背景一次三筆／inbox 一次多筆」的預期已按新行為調整；三筆批次中斷恢復測試改用前景批，保留原本覆蓋。
- 測試沒量真 HFSS、沒驗證正式 NAS／授權／三台網路／實際吞吐提升。

需要重跑時，在 emforge repo 的 PowerShell：

```powershell
Set-Location C:/Users/ricky/Documents/GitHub/emforge
$py = 'C:/Users/ricky/miniforge3/envs/ant/python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:EMFORGE_ANTENNA_REPO = 'C:/Users/ricky/Documents/GitHub/Antenna'
$env:EMFORGE_HFSS_TESTS = '0'
& $py -m pytest -o 'addopts=' -q
if ($LASTEXITCODE -ne 0) { throw 'pytest failed' }
& $py -m pyflakes emforge tests
if ($LASTEXITCODE -ne 0) { throw 'pyflakes failed' }
```

## 正式部署的現況

目前只準備文件，沒有對三台下指令。

| 項目 | 文件中的配置／未確認事項 |
|---|---|
| 平台主機 | 先按 rick 編寫；曾回報 140.123.106.227／100.96.223.58，三台能否連到尚未核對 |
| 三台 HFSS | tag 216／218／37；舊文件是 140.213.106.*，與本機 140.123.* 不同，不能自行改字當成驗證 |
| Profile | 範例 dual_p01_db075；若切 single_db100，整套 registry／limits／runtime／算法參數要一致 |
| Python | 平台使用既有 ant；HFSS 各台既有環境版本／依賴待核對，emforge 要 Python >=3.11 |
| Antenna | 本機曾核對 HEAD 6ae39f82ce5cbda6fa5c2199bdf0be0b12fbc7a7；三台實際路徑／commit／本機差異未核對 |
| 平台資料 | C:/emforge-platform；release C:/emforge-releases；算法 work C:/emforge-algorithms |
| 各台本機資料 | C:/emforge-worker；HFSS 暫存 C:/emforge-work |
| 連線方式 | 文件以 HTTP Depot 8766、儀器 MCP 8765 示範；NAS 操作簡化入口待做 |

首次切換前，讓舊 worker 完成工作並退出，再啟新 worker。同機兩套不能一起操作 HFSS；adapter 的 kill 會影響 ansysedt 程序。舊 Antenna jobs_state/STOP 是共享旗標，不是單台停止；新 emforge 可用 --machine-tag 停單台。

尚需挑一筆符合 profile 的已知可用 pattern，逐台驗收，然後才跑少量算法閉環。device-simulate 是 ad-hoc 儀器測試，不會入共享資料庫。

HTTP 重啟期間的連線錯誤仍可能令 worker 工作失敗或需要重試。保存派工意圖不等於跨斷線完整重播未送出的響應；首次真機驗收不要同時測熱更新。

兩個連線 token 環境變數分別是 EMFORGE_PLATFORM_TOKEN 與 EMFORGE_DEVICE_TOKEN；文件使用同一秘密值示範，但讀取變數不同。MCP 操作確認 nonce 又是另一件事，正常佇列 worker 不需逐筆確認。

Windows PowerShell 5 的 Set-Content -Encoding utf8 會寫 BOM；limits.json 讀取不接受該 BOM，所以文件用無 BOM WriteAllText。init 仍會產生 blind 範本，首次操作不要不看設定就啟動 runtime。
（2026-09-23 註：這兩點已過期——limits.json 現在接受 BOM（I-33）、init 範本的 blind 預設 `enabled: false`（I-34）。）

## 使用者 HTML 文件

資料夾：C:/Users/ricky/Desktop/em-forge。這些文件在 repo 外，不會跟 git clone 下載。

| 檔案 | 用途與版本 |
|---|---|
| index.html | 全部文件入口 |
| 架構與部署報告.html | 架構概覽，原六里程碑基準 |
| 部署操作指令.html | 通用／fake 操作參考 |
| 三台HFSS部署操作.html | 原 d866cf1 Git 固定版本部署方案，尚未變成簡化安裝器 |
| Pattern優先級與排程.html | a5c808e 新優先級、三台配置、相容限制 |
| Claude交接紀錄.html | 本交接文件的 HTML 副本 |

已有複製按鈕：三台部署 21 段、一般指令 6 段、優先級 4 段。使用 Clipboard API，失敗時退回本機檔案適用的複製方式，再不行就選取並提示 Ctrl+C；複製內容不含按鈕，保留原始程式碼。沒有透過真瀏覽器做剪貼簿端到端驗收，不能把靜態驗證說成瀏覽器實測。

三台部署文件的 21 段 PowerShell 曾通過語法解析，CLI 參數曾對本機 parser 核對；僅解析，沒有執行部署。

## 尚未完成：建議 Claude 接著處理

以下是依使用者方向整理的待辦，不表示已實作：

1. **簡化部署與日常啟動。** 做一次性的角色設定，處理平台位址／NAS 路徑、機器 tag、既有 Python／Antenna 路徑；自動產生合適 registry／limits／策略設定，日常只需啟動平台或 worker。
2. **一次設定憑證。** HTTP token 由平台初始化產生、於節點加入時保存到本機未追蹤設定，啟動時自動讀取；不要每次 Read-Host，也不把秘密寫進 repo 或 HTML。這只是待實作方案，尚無 pairing／設定保存程式。
3. **簡單更新入口。** 讓使用者保留 git pull＋重啟的熟悉操作，由程式處理忙碌檢查、驗證、版本記錄和回退。現有 start_worker.cmd 只是先 pull 的舊入口，不是完整的新更新器。
4. **清楚的進度介面。** 顯示主要／背景工作、三台狀態、排隊／執行／完成／失敗、結果；目前沒有完成面向日月光工程師的操作 GUI。
5. **正式機驗收。** 核對地址／路徑／環境／舊工作狀態，先單台已知 pattern，再三台少量閉環，量測實際吞吐與恢復行為。
6. **依實測決定後續優化。** 長佇列掃描／NAS 延遲、公平份額、更多舊算法移植、資料匯入與備份還原驗收，避免先擴大架構。

不要為了接手就重新做六個已完成里程碑。不要把優先級範例 YAML 直接覆寫到正式資料根，也不要把舊 IP 記錄或 HTML 佔位字直接執行。

## 協作規範與授權邊界

- 使用繁體中文。工程師可理解的操作優先，內部技術細節由軟體承擔。
- emforge 的 CLAUDE.md 要求 TDD、每模組 <=400 行、每函式 <=60 行、完整回歸與 pyflakes 通過後提交；新增核心模組要列入 Depot 靜態守門。
- 不修改 Antenna repo 與 NAS 正式資料；測試用 tmp_path／MemoryDepot。沒有本次新的正式部署授權。
- 不 push，除非使用者要求。設定 remote 不代表自動授權發布後續 commit。
- 目前沙箱可寫根是 Antenna，但工作在 emforge／Desktop；這兩处寫入需使用環境的核准機制，不要為繞過限制把產物寫進 Antenna。
- 使用者不喜歡反覆確認已授權事項。對可逆的本機實作與文件整理直接完成；真正缺少的機器資訊才詢問。
- 本文件與 AGENTS.md／CLAUDE.md 的入口連結是正式交接記錄，不依賴 Codex 的對話記憶。
