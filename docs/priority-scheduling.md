# Pattern 優先級與排程

工程師選「優先、主要、背景」即可。平台把 HFSS 時間優先分配給推進目標的候選，背景探索用來填補空檔。正常排程不打斷正在執行的單筆模擬。

## 三種等級

| 介面名稱 | API 值 | 數值 prio（小者先） | 用途 |
|---|---|---|---|
| 優先 | urgent | 1 | 指定驗證、阻塞重要決策的重測 |
| 主要 | normal | 3 | 推進算法演化的候選 |
| 背景 | background | 9 | 補充資料、廣泛探索、填補空閒 |

算法有預設等級，每個 pattern 可以覆寫，也可以附 purpose 說明用途。priority 不影響 pattern ID、幾何定義或評分；同 profile 下同 pattern 仍共用量測。

舊版 numeric prio 設定仍可使用。priority 有提供時優先採用具名等級；具名模式搭配預設 background_prio: 9。自訂 numeric 門檻與 prio 要一起核對，勿把 background_prio 改到具名主要／背景等級之間以外。

## 三台 HFSS 的簡單配置

下面以既有 anneal 算法和內建 blind 探索為例。在平台的策略設定中選好一次：

```yaml
profile: dual_p01_db075
runtime:
  tick_s: 5
strategies:
  - name: anneal
    kind: inbox
    priority: normal
    max_inflight: 3
  - name: blind
    priority: background
    max_inflight: 3
```

batch 預設 1。inbox 一次收到很多候選時，平台會保留待選項，每次派一個 pattern 的 job。背景 propose 策略每次只要求一筆，避免預先占住整批。每個策略每 tick 至多處理一份 inbox 候選；空閒機器會在後續 tick 逐步填滿。

同 profile 還有未認領的 job 時，暫不繼續產生背景工作；已被其他機器認領、正在跑的前景，不會阻止空閒機器取得背景候選。其他 profile 的排隊工作不直接阻擋此 profile 產生候選，worker 仍按共用佇列及釘機條件選擇。

## 算法送件

送出一個主要候選，不用理解佇列格式：

```python
sid = client.submit(
    [pattern],
    priority="normal",
    purposes=["推進下一輪演化"],
    request_id="round_12",
)
```

同次送件可混合不同用途。狀態與回傳結果保留原始 index，因此排程順序改變不會把結果配錯：

```python
sid = client.submit(
    [exploration, evolution, verification],
    priorities=["background", "normal", "urgent"],
    purposes=["補充資料", "下一輪演化", "工程師指定驗證"],
)
status = client.status(sid)
```

省略 priority／priorities 時沿用策略預設。priorities 是逐筆清單，提供時覆蓋 priority；清單長度須等於 pattern 數。priority 拼錯或 purpose 非文字會拒絕送件。同 request_id 重送必須連 priority／purpose 都相同；要提高同一候選需求，可建立新的送件，平台會共用並提升原工作。

CLI 對整份 npz 設定同一等級：

```powershell
python -m emforge submit --endpoint http://PLATFORM_IP:8766 --profile dual_p01_db075 --name anneal --run-id trial_a --patterns C:/patterns.npz --priority urgent
```

HTTP 和 MCP 的 items 也可帶 priority／purpose。傳統 propose 策略回傳的 Proposal 或 dict 支援相同欄位，會保留穿越算法子行程的資料；有逐筆 priority 時拆成各自的 job。

## 排程的實際規則

1. **先比較優先級。** 下一次認領時讀最新有效等級，已在執行的單筆模擬不中斷。
2. **同級輪替算法，再輪替 run。** 大量舊候選不會排在另一個同級算法的全部工作前。輪替按取得工作機會，不是精確 GPU 時間或固定百分比。
3. **低級工作不占高級候選的派工配額。** 計算 max_inflight 時，只計同策略中同級或更高級的 sample。這允許主要工作在背景已滿時進入佇列；全部級別加起來可能超過 max_inflight，但機器數量不變。
4. **重複候選共用並提升。** 尚未完成的同 profile／同 pattern 工作，採所有需求中最高的等級。提升不另派模擬、不取消原提交者；結果兩邊都能收到。
5. **多筆舊批次在每筆保存後重看排程。** 本機可接的更高級工作，或另一個同級算法／run 排隊時，釋放剩餘工作後重新領取。完成的 pattern 不重跑；已完成最後一筆則直接收尾。
6. **背景允許等待。** 主要工作一直有需求時，背景可能一直沒有機會。需要保證進度的算法應列主要；目前未提供固定資源百分比或時間配額。

## 看進度

client.status／inbox_list 的每筆項目可看到 priority、purpose，以及已派工後的 effective_prio。runtime 狀態中的 inflight 也列有效優先級；queue 的 prio 顯示提升後的值。

「已送件」與「完成」不同：候選可能尚未派工、正在等共享量測、部分完成。worker 完成後仍要經 runtime 入庫，才能作為完成結果交給算法。

## 持久化與部署

兩種部署共用完全相同的排程邏輯：

- **NAS／共享資料夾**：FileDepot，worker 直接讀取共享佇列。
- **平台 HTTP**：HttpDepot，worker 經平台讀取佇列，優先級不由各台自行設定。

新增 queue/scheduling.json 保存單向優先級提升與輪替順序；更新使用既有 jobs.lock。runtime 的 inbox_turns 保存各 run 的送件輪替。原始 job、manifest 與 inflight.intent 保持不變，重啟對帳仍比對原始意圖。

部署時平台與三台 worker 都要更新至包含本功能的版本；舊 worker 不會識別新優先級覆寫與同級輪替。切換前讓該台完成目前工作並退出，再更新重啟。Git 更新步驟沿用三台部署文件。

既有多筆批次的優先級提升作用於整個 job；新 inbox 與有逐筆 priority 的提案使用單筆 job，避免把無關 pattern 一併提升。真 HFSS 的耗時、吞吐提升與三台切換尚待正式機驗收；目前使用 fake 模擬器驗證控制行為。
