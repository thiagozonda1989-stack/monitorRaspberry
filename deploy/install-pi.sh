#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(id -u)" -ne 0 ]; then
    echo "Execute com sudo." >&2
    exit 1
fi
apt-get update
apt-get install -y python3 python3-minimalmodbus python3-smbus ca-certificates
raspi-config nonint do_i2c 0
raspi-config nonint do_onewire 0
getent group i2c >/dev/null || groupadd --system i2c
id monitorraspberry >/dev/null 2>&1 || useradd --system --user-group --no-create-home monitorraspberry
install -D -m 0644 monitor.py /opt/monitorraspberry/monitor.py
install -d -m 0750 -o root -g monitorraspberry /etc/monitorraspberry
config=/etc/monitorraspberry/config.json
if [ ! -f "$config" ]; then
    install -m 0640 -o root -g monitorraspberry deploy/config.example.json "$config"
fi
install -m 0644 deploy/monitorraspberry@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now monitorraspberry@collect.service monitorraspberry@sync.service
echo "Edite $config e reinicie os serviços. Reinicie o Pi para ativar as interfaces."
