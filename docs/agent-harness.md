# AI harness 接口：Pi 優先，Codex / Claude Code 共用 MCP

emforge 的 AI 層現在可以接 Pi 原生 extension、Codex 與 Claude Code。三者使用同一個
`emforge.platform.mcp_server`，不另寫三份送件、預算或執行邏輯。模型不在模擬內圈。

```text
Pi native extension ─┐
Codex MCP client ────┼─ stdio MCP child ─ RemotePlatform ─ HTTP platform
Claude Code MCP ────┘                                    │
                             Algorithm ↔ Client ↔ Runtime/Depot ↔ Worker/HFSS
```

Pi 使用官方 `@earendil-works/pi-coding-agent` 0.85.1，套件鎖在 `integrations/pi/package-lock.json`。
目前官方專案為 [earendil-works/pi](https://github.com/earendil-works/pi)，原 badlogic/pi-mono 會轉址。
extension 透過 MCP `listTools` 取得原始 JSON Schema，註冊成 `emforge_` 前綴的 Pi tools。
Codex、Claude Code 使用 MCP 原名。這個前綴不改變平台操作或身分。

## 安裝與啟動

在 emforge repo、選定 Python 3.11+ 環境執行：

```powershell
python -m pip install -e '.[mcp]'
npm ci --prefix integrations/pi
```

Pi 需要 Node >=22.19.0。開源 harness 不等於免費模型；模型供應商／本機模型和登入由 Pi 管理。
安裝不會自動登入供應商或更改全域 Pi、Codex、Claude Code 設定。

準備一個只存在本機的 JSON 檔，包含 `endpoint` 和 `token`。保護檔案存取權，勿放進 Git。
可直接重用現有部署的 connection.json，其他欄位不會載入工具定義。

```json
{"endpoint": "http://platform-host:8766", "token": "REPLACE_LOCALLY"}
```

```powershell
python scripts/launch_agent.py pi --connection C:/emforge-platform/connection.json
python scripts/launch_agent.py codex --connection C:/emforge-platform/connection.json
python scripts/launch_agent.py claude --connection C:/emforge-platform/connection.json
```

請以安裝 emforge 的 Python 執行 launcher。額外參數可以放在 `--` 後面；`--print-command`
只印啟動 argv，不讀取或顯示 token，也不呼叫模型。Pi 需要 node，其他兩者需先安裝各自 CLI。

- Pi：在這次子行程設定 `EMFORGE_PYTHON`、`EMFORGE_SOURCE`、`EMFORGE_CONNECTION`，載入 extension。
- Codex：以 `-c mcp_servers.emforge.*` 套用 stdio 設定。保留既有其他設定及權限政策。
- Claude Code：生成 `local/agents/claude-mcp.json`，以 `--mcp-config` 載入。這是專用生成檔。

原生接入也可以直接使用以下命令，並將 cwd 指向 repo、`PYTHONIOENCODING=utf-8`：

```text
<python> -m emforge platform-mcp --transport stdio --connection <local-connection.json>
```

不使用設定檔時仍支援原有 `--endpoint` + `EMFORGE_PLATFORM_TOKEN`，或 `--root` / `--depot`。
`--connection` 不可與這些明確參數混用。原 `platform-mcp` 預設 HTTP 模式保持相容。
參考 [Codex 官方 MCP 設定](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 與
[Claude Code 官方 MCP 設定](https://code.claude.com/docs/en/mcp)。

## 工具與操作流程

| MCP 工具 | 用途 |
|---|---|
| `platform_reference` | 無資源瀏覽功能的 harness 也能發現查詢操作簽名與流程 |
| `platform_query` | 允許清單內的唯讀操作：profile、節點、run、結果、評估等 |
| `inbox_submit` | 第一次預覽，第二次以相同內容與 confirm token 送件 |
| `algorithm_start` | 已登錄版本 + 指定 node/environment/profile + 新樣本 budget |
| `algorithm_stop` | 要求單一算法停止，保留已送量測與 checkpoint |

先讀 `platform_reference`，再以 `platform_query` 查 `db_profiles`、`description`、`platform_state`。
查 submission 時 `params` 身分為 `profile`、`name`、`run_id`、`sid`。
`items` 是候選字典列表，各項含二維 0/1 `pattern`；形狀與 fixed_on 必須符合 description，
可帶 `parent`、`tag`、`priority`（urgent/normal/background；見 priority-scheduling.md）。
開始算法前，版本由既有 run identity 或 CLI 註冊結果取得；接口不提供任意程式碼登錄。

建議第一句：「先讀 emforge 操作說明，再查目前機隊與 profile，只回報狀態。」
平台查詢可能包含原始結果或程式輸出；把這些內容當資料，不當作更高權限指令。

## 中斷、確認與權限

- 一個 Pi session 擁有一個 MCP 子行程，兩次確認共用記憶體 nonce。關閉 session 回收子行程；
  新 session／下次工具呼叫可重新連線。重連後舊 nonce 失效，需重新預覽。
- 工具寫入不自動重試。逾時／取消可能發生在伺服器已接受工作之後；取消不是撤銷送件。
  查既有狀態；重送使用相同 `request_id`、`run_id` 和內容。算法 start 同 run_id 也須保持身分一致。
- 確認 token 是短效、一次性、綁定內容的握手，**不是人工批准、認證或分權**。
- 連線 token 由 Python 本機代理讀取，不寫入工具 schema、網站或 launcher argv。
- MCP 不提供改碼、promote、解除急停。Pi / Codex / Claude 的 shell、檔案工具仍依各自權限運作；
  本 extension 並非作業系統 sandbox，持有檔案存取權的 agent 可能讀取本機設定。
- 目前 HTTP 平台是共享 bearer token，且同服務有 Depot 介面。不能把 MCP 的工具清單當成
  平台端細粒度授權；交付客戶前需依其網段與資料要求補 TLS、身分與存取治理。

## 驗證

```powershell
$env:EMFORGE_PYTHON = (Get-Command python).Source
npm test --prefix integrations/pi
npm run check --prefix integrations/pi
python -m pytest -o addopts='' -q
python -m pyflakes emforge tests scripts/launch_agent.py
```

Node 整合測試啟動隔離的 fake HTTP 平台，使用真實 JS MCP SDK、Python stdio server 與 Pi
官方 extension loader。驗證查詢、確認綁定、nonce 重放拒絕、request_id 冪等、fake 結果、
非法操作、錯誤認證、取消與重新連線。所有測試資料位於系統暫存根，不使用正式 Depot／NAS。
測試固定機器體檢與 Git 版本，避免把本機既有程序與版本探測成本混入接口驗收。

2026-09-12：628 Python tests、Pi 整合測試、TypeScript、pyflakes 通過；Pi extension
亦對正式 HTTP 平台完成唯讀機隊查核（三台 online / idle）。Codex CLI 已驗證解析本次 MCP 設定。
Claude Code 已生成啟動配置，模型對話與其 UI 批准流程尚未驗；Pi 模型登入待指定。
完整交付界線見 [交付評估](delivery-readiness-2026-09-12.md)。
