@echo off
REM Runs the IFP Classroom server. Used by the Windows start-up task; you can
REM also double-click it to run the server in a window.
cd /d "%~dp0.."
set "CLASSROOM_DATA_DIR=%~dp0..\data"
if "%PORT%"=="" set "PORT=8000"
:loop
".venv\Scripts\python.exe" run.py >> "%CLASSROOM_DATA_DIR%\server.log" 2>&1
REM restart after a crash, after a short pause
timeout /t 10 /nobreak >nul
goto loop
