@echo off
REM Stops the server and removes the start-up task, backup task and firewall rule.
REM Your data (the "data" folder) is NOT deleted.
net session >nul 2>&1
if errorlevel 1 (
  echo Please right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)
schtasks /end /tn "IFP Classroom Server" >nul 2>&1
schtasks /delete /tn "IFP Classroom Server" /f >nul 2>&1
schtasks /delete /tn "IFP Classroom Backup" /f >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_server.bat*' -or ($_.CommandLine -like '*run.py*' -and $_.CommandLine -like '*.venv*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
netsh advfirewall firewall delete rule name="IFP Classroom Server" >nul 2>&1
echo Removed. The data folder was kept.
pause
