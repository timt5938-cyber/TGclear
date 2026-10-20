#!/usr/bin/env bash
set -euo pipefail

release="${1:?release id required}"
archive="/home/timoha/tgclear-release-${release}.tar.gz"
stage="/home/timoha/tgclear-stage-${release}"
backup="/opt/tgclear/releases/src-before-${release}"
service_backup="/opt/tgclear/releases/tgclear-before-${release}.service"

test -f "$archive"
test -d /opt/tgclear/src
test -f /opt/tgclear/.env
test -f /opt/tgclear/crypto.env
test ! -e "$backup"
mkdir -p "$stage"
tar -xzf "$archive" -C "$stage"
test -f "$stage/src/tg_cleaner/api/app.py"
test -f "$stage/deploy/tgclear.service"
test -f "$stage/deploy/check_telegram_proxy.py"
/opt/tgclear/.venv/bin/python "$stage/deploy/check_telegram_proxy.py"

sudo -n mkdir -p /opt/tgclear/releases
sudo -n cp -a /etc/systemd/system/tgclear.service "$service_backup"
sudo -n systemctl stop tgclear
swapped=0
restore() {
  if [ "$swapped" = 1 ]; then
    sudo -n rm -rf /opt/tgclear/src
    sudo -n mv "$backup" /opt/tgclear/src
  fi
  sudo -n cp -a "$service_backup" /etc/systemd/system/tgclear.service
  sudo -n systemctl daemon-reload
  sudo -n systemctl start tgclear
}
trap restore ERR
sudo -n mv /opt/tgclear/src "$backup"
swapped=1
sudo -n cp -a "$stage/src" /opt/tgclear/src
sudo -n cp -a "$stage/deploy/tgclear.service" /etc/systemd/system/tgclear.service
sudo -n systemctl daemon-reload
sudo -n systemctl start tgclear
test "$(systemctl is-active tgclear)" = active
test "$(systemctl is-active sing-box)" = active
ready=0
for _ in {1..20}; do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8742/api/auth/status || true)" = 401 ] &&
     [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8310/tgclear/api/auth/status || true)" = 401 ]; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" = 1
trap - ERR
echo "TGclear ${release}: active; SOCKS/VLESS and local API checks passed"
