@echo off
REM emforge worker 啟動腳本（正式機桌面捷徑指這裡；從桌面終端啟動 HFSS 視窗才可見）
REM pull 與 restart 綁在一起：要拉就一定重啟、要重啟就一定拉（I-10：只 pull 不重啟＝跑舊程式）
REM M16：`start_worker.cmd --serve` 同行程再起這台的 MCP server（其餘參數原樣透傳給 emforge worker）。
REM      綁定位址／埠讀 EMFORGE_MCP_HOST／EMFORGE_MCP_PORT（不設＝127.0.0.1:8765 只有本機連得到）；
REM      token 讀 EMFORGE_DEVICE_TOKEN（不走旗標、別進 shell 歷史）；非 loopback 沒 token → emforge 拒起（exit 1）。
setlocal
set REPO=%~dp0..
git -C "%REPO%" pull --ff-only || (echo git pull 失敗，不起 worker & exit /b 1)
if "%EMFORGE_ROOT%"=="" (echo 缺 EMFORGE_ROOT & exit /b 1)
emforge doctor --root "%EMFORGE_ROOT%" || (echo doctor 有阻擋條件，不起 worker & exit /b 4)
if not "%EMFORGE_MCP_HOST%"=="" (
  if "%EMFORGE_DEVICE_TOKEN%"=="" echo 警告：EMFORGE_MCP_HOST=%EMFORGE_MCP_HOST% 但沒設 EMFORGE_DEVICE_TOKEN——--serve 會被拒起
  echo MCP 綁定 %EMFORGE_MCP_HOST%:%EMFORGE_MCP_PORT%（--serve 才會起）
)
emforge worker --root "%EMFORGE_ROOT%" %*
