@echo off
REM Starts the Classroom Attendance server on a Windows PC (school server / admin PC).
REM First time: install Python 3.10+ from python.org (tick "Add Python to PATH").
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
set CLASSROOM_DATA_DIR=%~dp0data
.venv\Scripts\python run.py
pause
