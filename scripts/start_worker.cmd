@echo off
REM emforge worker 啟動腳本（正式機桌面捷徑指這裡；從桌面終端啟動 HFSS 視窗才可見）
REM pull 與 restart 綁在一起：要拉就一定重啟、要重啟就一定拉（I-10：只 pull 不重啟＝跑舊程式）
setlocal
set REPO=%~dp0..
git -C "%REPO%" pull --ff-only || (echo git pull 失敗，不起 worker & exit /b 1)
if "%EMFORGE_ROOT%"=="" (echo 缺 EMFORGE_ROOT & exit /b 1)
emforge doctor --root "%EMFORGE_ROOT%" || (echo doctor 有阻擋條件，不起 worker & exit /b 4)
emforge worker --root "%EMFORGE_ROOT%" %*
