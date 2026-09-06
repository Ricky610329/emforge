> 來源：Antenna repo `docs/platform/architecture.md`（commit f384fef，2026-09-01）。與實作的偏差見本文末「與實作的偏差」節（M11 回寫）。

# 平台架構：昂貴模擬下的設計搜尋

> **這份是什麼**：新系統的架構文件。獨立可讀，不需要先看舊專案的任何東西。
> **這份不是什麼**：不是實作規格（那份在架構定案後另寫）、不是舊專案的盤點（那在 `docs/extract/`）。
> **怎麼讀**：§2 是主圖，§3 用「一個候選的一生」把所有圖串起來——之後每一節的圖裡都找得到那個候選。
> 引用證據時只給代碼（`C-NN` 效果台帳／`I-N` 事故／`O-N` 未解問題／`D-N` 設計決定）加一句話，細節在 `docs/extract/`。
> 標 `❓` 的是推論或設計意圖，**不是實驗結果**。

---

## §1　這個系統要解什麼問題

**問題形狀**——四條同時成立才適用：

1. 評估一個候選要跑**昂貴的模擬**（本專案：一筆 HFSS 100–200 秒）
2. 參數空間**大且離散**（25×25 二值遮罩 ＝ 2^625 種）
3. **規格由領域專家定，而且會改**（本專案改過三次）
4. 目標不是「最佳解」，是「**在有限機時內找到夠好且可製造的解**」

**一句話**：把機時當稀缺資源調度，用累積的量測資料庫決定下一批要燒什麼。

**不適用**：模擬便宜（直接跑演化演算法即可）、參數連續可微（用梯度）、規格永遠不變。

**兩個硬性約束**（來自委託人，也是驗收條件）：
- **沒有 AI 也能用**。AI 是加值層，拔掉它系統要能跑一整晚。
- **平台不綁定任何特定策略**。我們研究出的方法只是「幫使用者做得更好」，他們不一定要用。

---

## §2　主圖：四個框

```
 ╔═══════════════════════════════════════════════════════════════════════╗
 ║  AI 加值層（可選；任意 harness；人也能扮演）                              ║
 ║   讀：狀態、事件、報表          寫：策略 .py、評估器、排程設定             ║
 ║   命令：promote（換王）/ retire / rescore / requeue                      ║
 ║   紅線：不直接改榜、不繞過去重、不設 kind                                 ║
 ╚═══════════════════════════════╤═══════════════════════════════════════╝
                                 │ 「操作」＝改檔案、跑命令。不是 API 呼叫。
                                 │ 所以任何 harness 都能做，人也能做。
 ┌───────────────────────────────┴───────────────────────────────────────┐
 │  runtime 實例（一次綁一個 sim_profile，一個評估器版本）                    │
 │                                                                       │
 │    排程 ──▶ 策略.propose ──▶ 驗證 ──▶ 去重 ──▶ 派工                     │
 │      ▲                                              │                 │
 │      │                                              ▼                 │
 │    公證觸發 ◀── 評分 ◀── 量測 ◀── 收結果 ◀── 佇列/worker               │
 │      │                                                                │
 │      └──▶ 待審清單（不加冕）                                             │
 │                                                                       │
 │  迴圈裡沒有 LLM。狀態全在磁碟，可中斷續跑。                                │
 └───┬───────────────────────────┬──────────────────────────┬────────────┘
     │ propose(ctx)              │ Profile → 開模擬器         │ Record 進出
 ┌───┴──────────────┐   ┌────────┴───────────────┐   ┌──────┴────────────────┐
 │  策略庫           │   │  模擬庫                 │   │  資料庫（所有實例共享）  │
 │                  │   │                        │   │                       │
 │  N 個 .py        │   │  Profile 註冊表         │   │  db/<profile>/*.pt     │
 │  各宣告相容的     │   │   名字 → 求解器類別、    │   │  ledger/<profile>/     │
 │   profile        │   │   幾何版、橋寬、網格、   │   │         <spec>.json    │
 │  我們出貨幾個     │   │   掃頻、饋墊、逾時       │   │  pending / events      │
 │   預設開          │   │  append-only            │   │  一筆＝(幾何, 條件,     │
 │  使用者可自寫     │   │  求解器 5 方法契約       │   │        原始響應, 世代)  │
 └──────────────────┘   └────────────────────────┘   └───────────────────────┘
```

**分層原則**：上層對下層只有**宣告**，沒有直接控制。AI 不直接派工（它改排程設定，runtime 讀）；策略不直接跑模擬（它回傳候選，runtime 派）；runtime 不知道 margin 怎麼算（評估器掛載進來）。

**為什麼 runtime 一定要是顯性的框**：舊專案的無人值守迴圈（`grind_loop.py`）就是它的單策略版——「迴圈裡沒有 LLM」，而現任最佳解正是它產出的（`C-10`）。把它一般化成跑 N 個策略就是新系統。不畫出來，重寫時排程邏輯會散進策略庫或 AI 層，又回到「沒 AI 跑不動」。

---

## §3　一個候選的一生

以下用一個具體的候選 **X** 貫穿全篇。X 由策略 `top10_flip3` 在第 7 個 tick 產生，送去 `dual_p01_db075` 這個 profile 量測。

```
  策略庫          runtime                 佇列/worker          資料庫              人/AI
  ───────         ───────                 ───────────          ──────              ─────
    │                │                        │                  │                  │
    │◀── ctx ────────│  ① 排程：輪到 top10_flip3（prio 3，上批已回）                  │
    │                │                        │                  │                  │
    │── [X, …] ─────▶│  ② propose 回 60 筆，X 是其中一筆                              │
    │                │                        │                  │                  │
    │                │  ③ 驗證：shape 對、二值、饋墊全 1、不超 budget                  │
    │                │  ④ 去重：X 的 id = sha1(bits + profile) 不在 db 也不在 inflight  │
    │                │       （另外 4 筆重複，丟掉，計入 dup_dropped）                 │
    │                │                        │                  │                  │
    │                │── job ────────────────▶│  ⑤ 派工＋登記 inflight（同一動作）      │
    │                │                        │                  │                  │
    │                │                        │ ⑥ worker 認領、開模擬器前比對 geom_ver │
    │                │                        │    跑 HFSS ~160s → 原始響應 (3,17)      │
    │                │◀── 結果 ───────────────│                  │                  │
    │                │                        │                  │                  │
    │                │  ⑦ 收結果：profile_hash 比對 → 量測(凍結口徑) → 評分(掛載評估器)  │
    │                │──────────────────────────── Record(X) ──▶│  ⑧ 入庫（一筆一檔）│
    │                │                        │                  │                  │
    │                │  ⑨ X.score > 榜首？→ 是                     │                  │
    │                │── 重測 ×2 (kind=repeat) ▶│                  │                  │
    │                │◀── 兩筆結果 ────────────│                  │                  │
    │                │  ⑩ 三筆一致（≤ 噪音地板）→ 寫 pending，不改榜                    │
    │                │                        │                  │── pending ──────▶│
    │                │                        │                  │                  │ ⑪ 審
    │                │                        │                  │◀── promote X ────│
    │                │                        │                  │  ⑫ 榜更新，記 by │
    │                │                        │                  │                  │
    │◀── ctx ────────│  ⑬ 下一 tick：top10_flip3 從 ctx.db.top(10) 讀到 X（含公證後保守值）│
```

**誰在哪一步做什麼**：

| 步驟 | 誰 | 策略碰得到嗎 |
|---|---|---|
| ①②⑬ | 策略（透過 ctx） | 是——這是策略唯一的介面 |
| ③④⑤⑦⑧⑨⑩ | runtime | **否** |
| ⑥ | worker | 否 |
| ⑪⑫ | 人或 AI | 否 |

策略只在 ②（產候選）和 ⑬（讀資料庫）出現。去重、派工、評分、公證全部碰不到——這是設計，不是限制（`D7`、`I-8`：安全閘不可被繞）。

---

## §4　runtime：一個 tick 裡發生什麼

```
   ┌─────────────────────────────────────────────────────────────────┐
   │ 啟動：取 per-profile 單例鎖 → reconcile()（佇列/inflight/db 三方對帳）│
   │       不一致 → 停下印出差異，不繼續（I-14）                          │
   └────────────────────────────┬────────────────────────────────────┘
                                ▼
   ┌── 每個 tick ────────────────────────────────────────────────────┐
   │                                                                 │
   │  collect()        讀每個 inflight store 的增量結果                 │
   │     ├─ profile_hash 不符 → 事件 profile_tamper，不入庫（I-10）      │
   │     ├─ measure（凍結口徑）→ score（掛載評估器）→ db.add             │
   │     ├─ store 終態 → 事件 batch_done，移出 inflight                 │
   │     └─ 錯誤率 > 門檻 → 暫停該 profile，事件（不是暫停策略）          │
   │                                                                 │
   │  notarize_step()  新入庫且 score 破榜且不在公證中 → 派重測 ×2      │
   │                   重測齊 → 一致性判定 → 寫 pending（永不改榜）      │
   │                                                                 │
   │  機隊靜默 > N 分？ → 只等，不排程（排隊 ≠ 停滯）                    │
   │                                                                 │
   │  schedule()       依 strategies.yaml 靜態 prio 排序                │
   │     for 策略 in 排序:                                             │
   │        略過：disabled / paused_by_runtime                         │
   │        略過：背景 prio 且（有前景 inflight 或 佇列無餘量）（D5）      │
   │        略過：該策略 inflight ≥ max_inflight（預設 1 ＝上批回來才 tick）│
   │        ctx = (db視圖, profile, budget, seeded rng, workdir, tick)   │
   │        子行程呼叫 propose（逾時）                                  │
   │           例外 → 事件、本 tick 跳過、errors+1；連 3 次 → 暫停（D9）  │
   │           **絕不終止 runtime**（I-4）                              │
   │        validate → dedup → dispatch（寫 job ＋ 登記 inflight，同一動作，I-13）│
   │                                                                 │
   │  寫 status.json；sleep                                           │
   └──────────────────────────────────────────────────────── STOP 檔存在→退出 ┘
```

**負責**：排程、驗證、去重、派工、收結果、量測、評分、入庫、公證觸發、狀態記錄。
**不負責**：決定策略生死（D9 的暫停是操作保護，等人／AI 重啟）、改榜（只寫 pending）、解讀結果的意義。

**為什麼 max_inflight 預設 1**：這就是「策略照自己節奏」的實作——上批回來才輪到它，不用 callback。舊專案的 `grind_loop` 證明這足以承載需要重訓的策略（`C-25`）。

**為什麼派工與登記 inflight 是同一動作**：舊專案記錄過兩次「派了工但沒掛偵測 → 整條鏈停在那裡沒人知道」（`I-13`）。

---

## §5　策略契約

策略是**一個 `.py` 檔**，只需要一個模組級宣告和一個函式：

```
   ┌──────────── strategies/top10_flip3.py ────────────┐
   │                                                  │
   │  COMPATIBLE = {"dual_p01_db075"}                  │ ◀── runtime 載入時比對實例 profile
   │                                                  │     不符拒載（雙邊宣告的策略端，I-6）
   │  def propose(ctx) -> list[Proposal]:             │
   │      ...                                         │
   └──────────────────────────────────────────────────┘

         ctx（系統給的六樣東西）                         Proposal（策略回的）
   ┌──────────────────────────────────┐          ┌──────────────────────────────┐
   │ db       資料庫視圖（見 §7）        │          │ pattern   bool[H,W]  必填     │
   │ profile  本實例綁的 Profile（見 §6）│  ──▶     │ parent    Record.id  選填     │
   │ budget   本 tick 最多送幾筆         │ propose  │ arm       "blind"    選填     │
   │ rng      已 seed 的亂數（可重現）    │  ──▶     │ note      dict       選填     │
   │ workdir  策略專屬持久目錄           │          │                              │
   │ tick     第幾次被呼叫               │          │ （sim_profile 不用寫：實例綁定，│
   └──────────────────────────────────┘          │   runtime 蓋章）               │
                                                  └──────────────────────────────┘
```

| ctx 欄位 | 為什麼給 |
|---|---|
| `db` | 「用既有量測資料庫挑起點」是最大單一槓桿 +3.63 dB（`C-01`）；策略若各用各的資料等於放棄複利 |
| `profile` | 策略要知道 shape、饋墊位置、labels——這些是儀器屬性不是策略猜的 |
| `budget` | 機時是稀缺資源，由 runtime 調度，否則高 prio 策略會餓死背景臂 |
| `rng` | 「同 seed 同輸出」是舊專案鐵則；seed 記進 inflight，可重現 |
| `workdir` | 有狀態策略（模型權重、快取）落地在此；runtime **永不讀它**＝零耦合 |
| `tick` | 「每 3 tick 重訓」這種節奏不用策略自己數 |

**四種策略怎麼塞進同一個函式**（沒有第二個介面）：

```
 純隨機                      鄰域爬山                    SM 排序（需重訓）              線上學習（需逐筆回饋）
 ────────                    ────────                    ─────────────                 ──────────────────
 rng 生 budget 筆            db.top(10)                  tick%3==0 → db.query 全量      db.mine(since=上次)
   ↓                           ↓                           ↓ 重訓，存 workdir             ↓ 更新 G，存 workdir
 arm="blind"                 每筆翻 d 格                  生大池 → 預測 → 配額            生下一批
   ↓                           ↓ parent=rec.id             ↓ note={pred,std}             ↓ max_inflight=1
 [Proposal]                  [Proposal]                  [Proposal]（c 臂 arm="blind"） [Proposal]

 狀態：無                    狀態：無（＝資料庫）          狀態：workdir                  狀態：workdir
 回饋：不需要                回饋：下 tick db.top 自然含   回饋：下 tick db.query          回饋：下 tick db.mine
```

線上學習的「需逐筆回饋」本質上是「下一次 propose 前要看到上批結果」——這正是資料庫 + tick 的語義。**沒有 callback**（`D4`）。

**最小範例**（31 行）：

```python
# strategies/top10_flip3.py —— 從資料庫抓 top-10、各翻 3 個像素
import numpy as np

COMPATIBLE = {"dual_p01_db075"}

def propose(ctx):
    free = ~ctx.profile.fixed_on.reshape(-1)         # 饋墊不可翻：事實來源是儀器
    top = ctx.db.top(10, profile=ctx.profile.name)   # 已量測、同儀器、依分數降冪
    if not top:                                      # 新域資料庫空 → 交給 blind 策略，不硬湊
        return []
    per = max(1, ctx.budget // len(top))
    out = []
    for rec in top:
        for _ in range(per):
            q = rec.bits.copy().reshape(-1)
            pos = ctx.rng.choice(np.flatnonzero(free), 3, replace=False)
            q[pos] ^= True
            out.append(dict(pattern=q.reshape(ctx.profile.shape), parent=rec.id, note={"d": 3}))
    return out[:ctx.budget]
```

去重、饋墊檢查、seed 記錄、派工、掛偵測、評分、公證——**全部不在這 31 行裡**。註冊只要在 `strategies.yaml` 加一行：`- {name: top10_flip3, prio: 3, batch: 60}`。

**策略負責**：怎麼從資料庫挑起點、怎麼變異、要不要用模型、內部配額、重訓節奏、用什麼代理目標。
**策略不負責**：去重、派工、評分、公證、自己的生死。

**候選 X 在這裡**：`top10_flip3` 第 7 tick 從 `db.top(10)` 拿到起點，翻 3 格得到 X，`parent` 指向那個起點。

---

## §6　模擬庫

```
   ┌──────────────── sims/registry.py（append-only）────────────────┐
   │                                                                │
   │  PROFILES["dual_p01_db075"] = Profile(                          │
   │      simulator = "antenna.patch.DualPortSimulator",  ─┐         │
   │      geom_ver  = "p01",                               │ 這三項的 hash│
   │      kwargs    = {pixel_count:25, sweep:"Fast",       │ = profile_hash│
   │                   diag_bridge_w:0.075, max_delta_s:… }┘ 蓋進每筆 Record│
   │      shape     = (25,25), labels=("S11","S21","S22"), n_points=17│
   │      fixed_on  = <饋墊遮罩>,        ← 生成端約束的事實來源        │
   │      spec      = "dual_v2",         ← 預設評估器（可換，見 §8）   │
   │      timeout_s = 900,               ← 只進看門狗，不進模擬器      │
   │      retired   = False,             ← True＝拒收新工作，資料凍結保留│
   │  )                                                             │
   └────────────────────────────────────────────────────────────────┘
                                │
                                │ worker 開模擬器時
                                ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  1. 從 job.sim_profile 查 PROFILES                               │
   │  2. 比對 simulator 類別的 GEOM_VER == profile.geom_ver            │ ◀── 雙邊宣告
   │     不符 → 拒開，寫 .fail（I-7：類別名沒變但程式碼換代）            │
   │  3. SIM_CLS(**kwargs) → 五方法契約：open / start / __call__ / end / quit│
   │  4. 結果附 profile_hash + worker_ver + machine                    │
   └────────────────────────────────────────────────────────────────┘
```

**負責**：定義「一種模擬」的全部——幾何底板、幾何版、橋寬、網格、掃頻、饋墊、預設評估器、逾時。
**不負責**：決定誰來跑、跑什麼順序（runtime 的事）。

**為什麼 append-only、改＝新名字**：profile 就是 era（`D6`）。舊專案的教訓是：換了幾何底板（p00→p01）之後，舊資料**不是變錯，是變不可比**——零厚度對角接觸在舊底板下是數值幻影（`C-24`、`I-7`）。改了 profile 內容但沿用名字，等於把兩個世代混進同一個資料夾。

**為什麼 `keep_project` 不在這裡**：那是交付動作不是儀器屬性。放進 profile 等於讓策略能開它，會重演磁碟塞爆（`I-1`、`D10`）。

**候選 X 在這裡**：worker 用 `dual_p01_db075` 開模擬器，比對 geom_ver 通過，跑完把 profile_hash 蓋在 X 的結果上。

---

## §7　資料庫佈局

```
   db/
   ├── dual_p01_db075/                    ← era ≡ profile 名（D6）
   │   ├── 6c4e45d0ff24958e-r77b1a.pt     ← <id>-<store>.pt
   │   ├── 6c4e45d0ff24958e-r77n1.pt      ← 同設計的公證重測：不同 store，不同檔
   │   ├── a1b2c3…-smp073b.pt
   │   └── _index.npz                     ← 去重索引（packbits），入庫時增量寫（I-5）
   ├── single_db100/
   │   └── …
   │
   ledger/
   ├── dual_p01_db075/
   │   ├── dual_v2.json                   ← (profile, spec) 一榜；history append-only
   │   └── dual_v3.json                   ← 換評估器＝新榜，舊榜凍結不刪（D3）
   │
   runtime_state/dual_p01_db075/          ← 每個實例自己的
   ├── inflight/<store>.json              ← 已派未回（含 seed、tick、策略名）
   ├── pending.jsonl                      ← 公證通過待審（不是榜）
   ├── events.jsonl                       ← strategy_error / batch_done / record_candidate / …
   ├── status.json                        ← 每 tick 更新，人可讀
   └── strategies/<name>/                 ← 各策略的 workdir
```

**一筆 Record**：

```
   id            sha1(packbits(bits) + sim_profile)[:16]   ← 去重鍵；同 bits 不同 profile ＝ 不同設計
   sim_profile   "dual_p01_db075"                          ← 就是 era
   bits          bool[25,25]                                ← 幾何宣告：bits 之外的幾何全在 profile 裡
   response      float[3,17] | None                         ← 原始曲線；永遠保存；error 時 None
   measure       {"m1":…, "m2":…, …}                       ← 凍結口徑算出的軸值
   score         float | None                               ← 當前評估器算出的主分數（導出值）
   status        queued | running | done | error
   strategy / arm / parent / tick / seed / note            ← 誰產的、對照臂？、血統、可重現
   kind          sample | repeat                            ← repeat 只有 runtime 能設（D7）
   run           {store, machine, worker_ver, profile_hash, time_s}
```

**四個欄位為什麼缺一不可**：
- **幾何宣告**（bits ＋ profile）：可製造性處理（橋、槽、像素尺寸）是幾何的一部分，**不是事後濾鏡**。同 bits × 不同橋寬 ＝ 不同量測對象，各自要量。舊專案「事後修補」四個獨立案例全失敗，唯一成功的是把條件前移到生成階段（`C-13`）。
- **量測條件**（profile_hash）：「結果自帶這批用什麼設定量的，不靠人記」。
- **原始響應**：導出指標會隨評估器改版，原始曲線不會（§8）。
- **世代**（≡ profile）：換代後歷史資料變不可比，沒有這個欄位三年後接手的人會拿跨代數字做決策。

**跨 profile 規則**：
- **讀**：允許（`ctx.db.query(profile="single_db100")` 可以抓別的 profile 當種子）
- **寫**：只寫本實例綁定的 profile
- **比較**：runtime 的 report 預設拒絕跨 profile 並排；`--cross-profile` 顯式旗標才印，且印警告

**候選 X 在這裡**：入庫成 `db/dual_p01_db075/<X.id>-r77b1a.pt`；兩筆公證重測是 `<X.id>-r77n1.pt`、`<X.id>-r77n2.pt`；promote 後 `ledger/dual_p01_db075/dual_v2.json` 的 `best` 指向 X.id。

---

## §8　判準與評估器掛載

```
   原始響應 (3,17)
        │
        ▼
   ┌─ measure（凍結口徑）──────────────────────────┐
   │  frozen: 頻點怎麼取、哪幾軸、方向檢查            │  ← 換這層要走公證流程
   │  worst_margin_dual(response, labels, targets)   │     （等於換儀器）
   │  → {m1, m2, m3, m4, m5, m6, energy_max}          │
   └───────────────────────┬──────────────────────┘
                           ▼
   ┌─ score（可換評估器）────────────────────────────┐
   │  Spec "dual_v2": axes=(m1,m2,m3,m4)             │  ← 專家改規格只動這層
   │                  offsets=(+2, +2, 0, +5)        │     不重量任何資料
   │  score = min(measure[a] + o)                    │
   └───────────────────────┬──────────────────────┘
                           ▼
              ledger/<profile>/dual_v2.json

   換評估器：註冊 "dual_v3" → runtime rescore --spec dual_v3
             → 掃過 db/<profile>/ 每筆重算 score（零重量）
             → 新榜 ledger/<profile>/dual_v3.json，首任王由重算得出
             → 舊榜 dual_v2.json 凍結保留
```

**為什麼分兩層**：舊專案的規格由專家改過一次（帶內門檻放寬 2 dB、阻帶放寬 5 dB），**不必重量任何資料**，缺口從 6.1 → 3.05 dB（`C-23`）。做到這件事的正是「量測凍結、評分可換」。委託人明說「有 AI 加值的好處是我可以很善變我的評估系統」（`D1`）——這層讓善變的成本是零機時。

**為什麼分數在 runtime 算、不讓策略自評**：策略自評＝「離線考」，舊專案三度證明離線考 ≠ 實戰考（`C-17`）；跨策略比較需要同一把尺；紀錄榜只認 profile 的 spec。策略可以附自己的診斷（`note`），記錄、供人看，**不當成效**。

**候選 X 在這裡**：原始響應存進 Record；measure 算出 m1..m6；`dual_v2` 算出 score −2.31；破了榜首 −2.39。若之後掛 `dual_v3`，X 的 score 會被重算，可能不再是王——但 `dual_v2` 榜上它永遠是。

---

## §9　多實例共用

```
   ┌──────────────────────────┐        ┌──────────────────────────┐
   │ runtime A                 │        │ runtime B                 │
   │ profile = dual_p01_db075  │        │ profile = single_db100    │
   │ 鎖：runtime_state/dual…/  │        │ 鎖：runtime_state/single…/│
   │ 策略：smpool(prio3),      │        │ 策略：hill(prio3),        │
   │       blind(prio9)        │        │       blind(prio9)        │
   └─────────────┬────────────┘        └─────────────┬────────────┘
                 │ 派 job（帶 sim_profile）              │
                 ▼                                     ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  共用佇列  jobs.json ＋ jobs_state/                               │
   │  prio 排序；worker 不管 job 是誰派的                               │
   └───────┬──────────────────┬──────────────────┬───────────────────┘
           ▼                  ▼                  ▼
      worker 216         worker 218         worker 37       … 可加機器
      (任何 profile)     (任何 profile)     (任何 profile)
           │                  │                  │
           └──────────────────┴──────────────────┘
                              ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  共用資料庫  db/dual_p01_db075/   db/single_db100/                │
   │  A 只寫左邊、B 只寫右邊；兩邊都可讀對方                             │
   └─────────────────────────────────────────────────────────────────┘
```

**為什麼一實例一 profile**（`D2`）：委託人要求「不要允許 single 跟 dual 同時跑，不然有點混亂」。實例綁定後：沒有 per-batch 的 config 可以漏（`I-6` 整個消失）；資料庫視圖自動是該 profile 的世代（`I-7` 解）；策略的 `COMPATIBLE` 一次檢查。要跑第二種模擬＝開第二個實例，兩個行程、不同 `--profile`。

**HFSS 競爭**：由佇列 prio 處理，與現行機制相同。A 的正式策略 prio 3、B 的正式策略 prio 3，公平；兩邊的背景填空 prio 9，只在整個佇列空時才跑。

**候選 X 在這裡**：由 runtime A 派出，worker 218 認領，結果回到 `db/dual_p01_db075/`。runtime B 讀得到 X（若它想拿 dual 的設計當 single 的種子），但不會寫進 dual 的資料夾。

---

## §10　AI 加值層怎麼互動

```
   AI（或人）
     │
     ├─ 讀 ──▶ runtime_state/*/status.json      現在在跑什麼、上一 tick 做了什麼
     │         runtime_state/*/events.jsonl     strategy_error / batch_done / record_candidate …
     │         runtime_state/*/pending.jsonl    公證通過、等審的候選
     │         runtime report --profile P       每策略 n / best / P(勝 blind) / 命中率 / dup_dropped
     │
     ├─ 寫 ──▶ strategies.yaml                  開關策略、調 prio / batch / max_inflight
     │         strategies/<new>.py              寫新策略（NL → 策略 .py 是 AI 最自然的產出）
     │         spec/registry.py                 註冊新評估器
     │         sims/registry.py                 註冊新 profile（append）
     │
     └─ 命令 ▶ runtime promote <id> --by <誰>   換王：唯一能改榜的路徑，記 by 與 history
               runtime retire --profile P       凍結一個 profile
               runtime rescore --spec S         換評估器後重算
               runtime requeue <store>          重派（claim/done/fail/佇列條目一起處理，I-2）

   ┌─ 紅線（沒有這些路徑）─────────────────────────────────────┐
   │  ✗ 直接改 ledger/*.json（只能 promote）                      │
   │  ✗ 設 kind=repeat（只有 runtime 的公證能設）                  │
   │  ✗ 繞過去重派工（dispatch 內部呼叫 dedup，沒有旁路）          │
   │  ✗ 改已註冊的 profile 內容（append-only，改＝新名字）          │
   └───────────────────────────────────────────────────────────┘
```

**AI 負責**：提問（下一個值得追的異常）、選策略與配資源、判讀（這批結果代表什麼）、裁決（promote / retire）、寫新工具（策略、評估器、profile）、停滯診斷、綜合層文件。
**AI 不負責**：判定（門檻、通過與否——在程式裡）、宣告紀錄成立（只能觸發公證，`D7`）、在內迴圈裡（`C-10`）。

**為什麼「操作＝改檔＋跑命令」**：這讓 AI 層與 harness 無關——任何能改檔案、跑命令列的東西都能扮演這一層，包括人。舊專案的 agent 層約 90% 已是這樣（runbook 是純文字、判定在程式裡），唯一的硬綁定是「誰來喚醒」（`O-4`）；本設計把終態寫成 `events.jsonl`，消費端可以是任何 harness 的背景監看、cron、輪詢、或人。

**候選 X 在這裡**：AI 讀 `pending.jsonl` 看到 X 三筆一致、保守值 −2.31；判斷這不是假象；跑 `runtime promote <X.id> --by claude-session-xxx`；榜更新。

---

## §11　十條設計決定

| # | 決定 | 為什麼（證據） |
|---|---|---|
| D1 | 評估可掛載；runtime 統一算分，策略診斷當附註 | 委託人「我可以很善變我的評估系統」；策略自評＝離線考三度失敗（C-17）；換評分不重量（C-23） |
| D2 | 一實例綁一 profile；資料庫與佇列共用 | 委託人「不要 single 跟 dual 同時跑」；資料庫本來就按 profile 分帳，零額外成本 |
| D3 | 每評估器版本一榜，舊榜凍結不刪 | 舊專案現行做法（三代榜並存），已驗證可行 |
| D4 | 策略＝一個 `.py` 的 `propose(ctx)`；狀態靠 workdir，回饋靠下 tick 讀 db；沒有 callback | 委託人定調；`grind_loop` 證明「每 tick 呼叫＋狀態在檔案」足以承載重訓策略（C-10/C-25） |
| D5 | 機時＝靜態 prio；背景填空只在佇列空時跑 | 委託人「會有背景填空閒的 agent」 |
| D6 | era ≡ sim_profile 名，不另設欄；資料庫實體分目錄 | 讓舊代不可比的是儀器本身（C-24）；兩個必須永遠一致的欄位是 I-6 型事故溫床 |
| D7 | 去重、派工、評分、公證是 runtime 服務；策略只 propose；`kind=repeat` 只有 runtime 能設 | 安全閘不可被繞（I-8）；「迴圈不自己加冕」 |
| D8 | 對照臂不強制；`arm="blind"` 保留字；blind n 不足時 report 拒印比較 | 委託人要求不綁定；選拔/開採的拆帳只在有零演算法臂時成立（C-01/C-02） |
| D9 | 策略連續 3 次例外 → runtime 暫停它（操作保護，非淘汰） | 背景自產炸死兩台機器（I-4）；策略不得自決生死 |
| D10 | `keep_project` 不在 Profile／Proposal 任何欄位 | 放進去等於讓策略能開它，重演磁碟塞爆（I-1） |

---

## §12　本文件的限制

- **策略層（§5、§9 多策略並行）沒有實作驗證。** 舊專案只跑過「一條批次線 ＋ 一條背景填充 ＋ 一條無人值守迴圈」，從沒跑過 N 個獨立策略並行。「多樣性從策略池湧現」`❓` 是推論。這是路線圖裡要最早驗證的一層。
- **「換一個模型／harness 接手會得到同一份帳」未經對照實驗**（`C-11`）。§10 的整個分工建立在「判定已下沉到程式」之上，而舊專案自陳這是設計意圖不是實驗結果。半天可驗、零機時，列在路線圖階段 A。
- **冷啟動沒有解，只有架構上的緩解。** 代理模型在完全沒見過的域選批比亂猜還差（`C-07`，P(勝隨機) 21–26%）。本設計靠「策略庫常駐 blind 策略」讓新域自然有打底，但「多少樣本後模型才能上崗」沒有數字——那是路線圖階段 D 要量的東西。
- **`arm="blind"` 是自我宣告，契約無法驗證。** 策略可以說謊。設計只保證說謊的地方會留紀錄（`C-12` 修正版：「繞過會留下紀錄，而非不可能」）。
- **所有具體數字都來自單一問題域**（毫米波像素化天線／濾波器）。配額比例、門檻、樣本數、噪音地板——跨域全部要重調。本文件刻意不寫任何數值當預設。
- **「重訓的作用是讓模型跟上分布、不是讓它更準」`❓`** 是從 C-07（域外崩潰）與 C-08（相關性改善不轉化）推出的解釋，不是實驗結果。它影響 SM 策略內部的重訓設計（近期資料加權 vs 全量），但不影響平台契約。


---

## 與實作的偏差（2026-09-01 實作完成後回寫）

實作完整清單見 `implementation.md` §13：Record 檔用 `.npz`（核心零 torch）、`Context` 多 `params`、`Record` 多 `extra`、批結果改逐筆檔 `batches/<store>/results/<id>.json`、`profile_hash` 納入 measure 名、Simulator 協定收成 `open/simulate/kill/close`、state/status 分檔、resume 走 `control.json`、保留字不含 `blind`、single geom_ver 單邊、`deliver` 未實作。
**2026-09-06（M12）**：§2／§7 假設的「共享檔案系統（NAS）＋O_EXCL／rename 協調原語」抽成 `Depot` 介面（doc／log／lease／列舉四種語義；`FileDepot` 逐位元＝原佈局、`MemoryDepot` 給測試；契約測試對每個後端同套跑）——基礎設施可換（S3／SQL），算法層一行不改；`--root`＝本機程式碼根、`--depot`＝共享狀態後端。
**2026-09-06（M13–M14）**：worker 不再直接開 Simulator，改經**儀器層** `device.Instrument`（MHS 式：狀態字典 `devices/<tag>/`、程序＝open/simulate/kill/close/abort/selfcheck/simulate_once、租約、`Limits`＋前置檢查、兩段式 confirm、急停三層——解除只能 CLI）；`fleet` 儀表板與說明檔給人與 AI 層讀；每台各自的 MCP server 在 M15 貼上這些方法。§4 的「worker 只認 Simulator 協定」仍成立——Instrument 本身就實作那個協定。
