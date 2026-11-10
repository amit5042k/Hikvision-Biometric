@echo off
REM Copies the database into data\backups (keeps the latest 30). Run daily by the installer's task.
cd /d "%~dp0.."
set "CLASSROOM_DATA_DIR=%~dp0..\data"
".venv\Scripts\python.exe" -m flask --app classroom_app backup >> "%CLASSROOM_DATA_DIR%\backup.log" 2>&1
