@echo off
REM ---------------------------------------------------------------------------
REM  IFP Classroom Attendance - install on the Windows server PC
REM
REM  Right-click this file and choose "Run as administrator". It:
REM   1. installs the app's Python packages (Python 3.10+ must be installed,
REM      with "Add Python to PATH" ticked - get it from python.org)
REM   2. starts the server whenever Windows starts (even before anyone signs in)
REM   3. opens TCP port 8000 in Windows Firewall so the classroom panels connect
REM   4. backs up the database every day at 17:30
REM  Running it again is safe (it updates everything in place, data is kept).
REM ---------------------------------------------------------------------------
setlocal
net session >nul 2>&1
if errorlevel 1 (
  echo Please right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)
set "APPDIR=%~dp0.."
cd /d "%APPDIR%"
set "PORT=8000"

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" during installation. Then run this again.
  pause
  exit /b 1
)

echo [1/4] Installing packages...
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo Package installation failed. Check the internet connection and run this again.
  pause
  exit /b 1
)
if not exist "data" mkdir "data"

echo [2/4] Creating the start-up task...
schtasks /end /tn "IFP Classroom Server" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_server.bat*' -or ($_.CommandLine -like '*run.py*' -and $_.CommandLine -like '*.venv*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
schtasks /create /tn "IFP Classroom Server" /tr "\"%APPDIR%\windows\run_server.bat\"" /sc onstart /ru SYSTEM /rl HIGHEST /f >nul
if errorlevel 1 (
  echo Could not create the start-up task.
  pause
  exit /b 1
)

echo [3/4] Opening port %PORT% in Windows Firewall...
netsh advfirewall firewall delete rule name="IFP Classroom Server" >nul 2>&1
netsh advfirewall firewall add rule name="IFP Classroom Server" dir=in action=allow protocol=TCP localport=%PORT% profile=private,domain >nul

echo [4/4] Scheduling the daily backup...
schtasks /create /tn "IFP Classroom Backup" /tr "\"%APPDIR%\windows\backup.bat\"" /sc daily /st 17:30 /ru SYSTEM /f >nul

schtasks /run /tn "IFP Classroom Server" >nul
echo.
echo Done. The server is starting and will start by itself every time this PC is switched on.
echo.
echo Open one of these addresses in a browser (this PC, or any PC on the school network):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do for /f "tokens=*" %%b in ("%%a") do echo     http://%%b:%PORT%
echo     (on this PC you can also use http://localhost:%PORT%)
echo.
echo First sign-in: admin / admin  - change the password straight away under Settings.
echo Data and backups are kept in: %APPDIR%\data
echo.
echo Tip: give this PC a fixed IP address (in your router or network settings),
echo so the classroom panels can always find it.
pause
