@echo off
rem Registers the bridge to start automatically when Windows starts (run as Administrator).
set TASK=HikvisionAttendanceBridge
schtasks /Create /F /TN %TASK% /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "\"%~dp0run_bridge.bat\""
schtasks /Run /TN %TASK%
echo.
echo Bridge installed as scheduled task "%TASK%" and started.
echo Log file: %~dp0bridge.log
pause
