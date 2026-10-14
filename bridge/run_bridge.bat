@echo off
rem Starts the Hikvision bridge agent. Used by the Windows scheduled task.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3 -m venv .venv || python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" bridge\hik_bridge.py
