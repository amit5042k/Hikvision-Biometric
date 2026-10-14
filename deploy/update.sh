#!/usr/bin/env bash
# Safely update the app on the server to the latest code from git.
#
#   bash deploy/update.sh            # cPanel shared hosting (Passenger)
#   bash deploy/update.sh --systemd  # VPS installed with deploy/vps-setup.sh
#
# Order of operations: back up the database -> pull code -> install packages ->
# restart. The database lives outside this folder (ATTENDANCE_DATA_DIR) and is
# never part of git, so logins and attendance data are kept. Any database
# changes in the new version are applied automatically on restart, after the
# app takes its own extra backup.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"
if [ -f /etc/attendance.env ]; then set -a; . /etc/attendance.env; set +a; fi   # VPS install
DATA_DIR="${ATTENDANCE_DATA_DIR:-$HOME/attendance-data}"
BRANCH="${DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

# Python of the app's virtualenv: cPanel puts it in ~/virtualenv/<app>/<ver>/, the VPS setup in .venv
PY="${APP_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$APP_DIR/.venv/bin/python" ]; then PY="$APP_DIR/.venv/bin/python";
  else PY="$(ls -d "$HOME"/virtualenv/"$(basename "$APP_DIR")"/*/bin/python 2>/dev/null | tail -1 || true)"; fi
fi
[ -x "$PY" ] || { echo "Cannot find the app's Python. Set APP_PYTHON=/path/to/venv/bin/python"; exit 1; }

echo "== 1/4 Backing up database in $DATA_DIR"
if [ -f "$DATA_DIR/attendance.db" ]; then
  ATTENDANCE_DATA_DIR="$DATA_DIR" "$PY" -c "
from app import db
print('   saved', db.backup('$DATA_DIR/attendance.db', '$DATA_DIR/backups'))"
  # VPS: the app runs as the "attendance" user and must own everything in its data folder
  if [ -f /etc/attendance.env ] && id -u attendance >/dev/null 2>&1; then chown -R attendance: "$DATA_DIR"; fi
else
  echo "   no database yet - first install"
fi

echo "== 2/4 Fetching code ($BRANCH)"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "   Local code changes found on the server; stashing them (see 'git stash list')."
  git stash push -m "server changes before update $(date +%F_%T)"
fi
git fetch origin "$BRANCH"
git merge --ff-only "origin/$BRANCH"

echo "== 3/4 Installing packages"
"$PY" -m pip install -q -r requirements.txt

echo "== 4/4 Restarting"
if [ "${1:-}" = "--systemd" ]; then
  sudo systemctl restart attendance
else
  mkdir -p tmp && touch tmp/restart.txt   # tells Passenger to reload the app
fi
echo "Done. Now at $(git log -1 --format='%h %s')"
