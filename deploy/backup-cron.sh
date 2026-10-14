#!/usr/bin/env bash
# Nightly database backup (keeps the last 30). Add to cPanel → Cron Jobs:
#   15 2 * * *  bash $HOME/Hikvision-Biometric/deploy/backup-cron.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DATA_DIR="${ATTENDANCE_DATA_DIR:-$HOME/attendance-data}"
PY="${APP_PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || ls -d "$HOME"/virtualenv/"$(basename "$(pwd)")"/*/bin/python | tail -1)}"
"$PY" -c "from app import db; print(db.backup('$DATA_DIR/attendance.db', '$DATA_DIR/backups'))"
