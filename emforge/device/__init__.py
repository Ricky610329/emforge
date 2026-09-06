"""emforge.device — 儀器層（M13，MHS 式）：一台正式機＝一個 `Instrument`。一檔一職責：

  estop.py       第 5 層：急停三層（全機 queue/ESTOP／單機 queue/ESTOP.<tag>／本機 <root>/ESTOP）；解除只能 CLI
  limits.py      第 2–4 層：Limits（硬限制）、check_preconditions（重用 worker/gate.check＋doctor.health）、兩段式 confirm token
  states.py      DeviceState（狀態字典欄位唯一真相）＋ write_state／read_state／read_fleet（offline 由讀者推導）
  instrument.py  Instrument：狀態機 idle/opening/ready/busy/estop/fault、租約、Simulator 協定直傳、abort、selfcheck、simulate_once
  reference.py   儀器說明檔（M14）
  mcp_server.py  這台的 MCP server（M15）：tools＝Instrument 程序一對一、resources、bearer、與 worker 同行程

依賴方向（test_smoke 釘死）：device/* 只可 import worker 的 leaf（guard／gate／workdir）；worker/batch.py、worker/loop.py 才可 import device。
`mcp`／`uvicorn`／`starlette` 只在 mcp_server.py 函式內 import（optional extra `emforge[mcp]`）。
"""
