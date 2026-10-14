#!/usr/bin/env bash
# One-time setup on a GoDaddy VPS (Ubuntu 22.04 / 24.04), run as root:
#
#   bash deploy/vps-setup.sh attendance.yourdomain.com you@example.com
#
# Installs nginx + HTTPS (Let's Encrypt), runs the app as a systemd service
# and keeps all data in /var/lib/attendance (outside the code folder).
set -euo pipefail
DOMAIN="${1:?usage: vps-setup.sh <domain> <email>}"
EMAIL="${2:?usage: vps-setup.sh <domain> <email>}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR=/var/lib/attendance
RUN_USER=attendance

apt-get update
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git

id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$RUN_USER"
mkdir -p "$DATA_DIR"
chown -R "$RUN_USER:$RUN_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

cat > /etc/attendance.env <<ENV
ATTENDANCE_DATA_DIR=$DATA_DIR
HOST=127.0.0.1
PORT=5000
BEHIND_PROXY=1
HTTPS_ONLY=1
ENV

cat > /etc/systemd/system/attendance.service <<UNIT
[Unit]
Description=Hikvision attendance web app
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/attendance.env
ExecStart=$APP_DIR/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/nginx/sites-available/attendance <<NGINX
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 8m;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/attendance /etc/nginx/sites-enabled/attendance
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

systemctl daemon-reload
systemctl enable --now attendance
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --redirect -n

# nightly backup at 02:15
echo "15 2 * * * $RUN_USER ATTENDANCE_DATA_DIR=$DATA_DIR APP_PYTHON=$APP_DIR/.venv/bin/python bash $APP_DIR/deploy/backup-cron.sh >/dev/null" \
  > /etc/cron.d/attendance-backup

echo
echo "Done: https://$DOMAIN  (first login admin / admin - change it immediately)"
