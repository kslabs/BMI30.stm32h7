#!/usr/bin/env bash
# setup_portal.sh — Быстрая установка BMI30 Captive Portal (без полного install-скрипта)
# Запуск: sudo bash setup_portal.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
SERVER_SRC="$SCRIPT_DIR/hotspot_info_server.py"
SERVER_DST="/usr/local/bin/bmi30-hotspot-info-server.py"
SERVICE_PATH="/etc/systemd/system/bmi30-hotspot-info.service"
DNSMASQ_DIR="/etc/NetworkManager/dnsmasq-shared.d"
DNSMASQ_CONF="$DNSMASQ_DIR/90-bmi30-captive-portal.conf"
HOTSPOT_IP="$(/usr/bin/python3 -c 'import json; from pathlib import Path; print(json.loads(Path("/etc/bmi30-network/config.json").read_text())["ap_address"].split("/")[0])')"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()  { echo -e "${GRN}[OK]${NC}  $*"; }
err() { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }
inf() { echo -e "${YLW}[..]${NC}  $*"; }

[[ $EUID -eq 0 ]] || err "Запустите скрипт от root: sudo bash setup_portal.sh"
[[ -f "$SERVER_SRC" ]] || err "Файл $SERVER_SRC не найден. Запускайте из папки проекта."

PYTHONPATH=/opt/bmi30-network /usr/bin/python3 - <<'BMI30_DNS_DEPENDENCIES'
from network import Network
import dns_policy
if not {'dns_policy', 'render', 'syntax_check', 'OWNED_FILES'}.issubset(Network.prepare_ap.__code__.co_names):
    raise RuntimeError('Canonical DNS owner module must be installed together')
BMI30_DNS_DEPENDENCIES

# ── 1. Копируем сервер ──────────────────────────────────────────────────────
inf "Устанавливаю сервер → $SERVER_DST"
install -m 755 "$SERVER_SRC" "$SERVER_DST"
ok "Сервер скопирован"

# ── 2. systemd unit ─────────────────────────────────────────────────────────
inf "Создаю systemd unit: bmi30-hotspot-info.service"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=BMI30 Captive Portal / Hotspot Info
After=network-online.target NetworkManager.service bmi30-hotspot.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $SERVER_DST
Restart=always
RestartSec=3
EnvironmentFile=-/etc/default/bmi30-hotspot-info
Environment=BMI30_HOTSPOT_IP=$HOTSPOT_IP

[Install]
WantedBy=multi-user.target
EOF
ok "Unit создан"

# 3. Canonical DNS: validated no-op or one AP-only activation.
PYTHONPATH=/opt/bmi30-network /usr/bin/python3 - <<'BMI30_CANONICAL_DNS'
import json
from pathlib import Path
from network import Network
import dns_policy
if not {'dns_policy', 'render', 'syntax_check', 'OWNED_FILES'}.issubset(Network.prepare_ap.__code__.co_names):
    raise RuntimeError('Canonical DNS owner module must be installed together')
c = json.loads(Path('/etc/bmi30-network/config.json').read_text())
changed = Network(c).prepare_ap()
print('BMI30 canonical DNS: ' + ('applied to wlan0ap' if changed else 'unchanged'))
BMI30_CANONICAL_DNS

# ── 4. Разрешаем порт 80 в ufw (если активен) ────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi 'status: active'; then
    inf "ufw: разрешаю порт 80/tcp"
    ufw allow 80/tcp >/dev/null 2>&1 && ok "ufw: порт 80 открыт" || true
fi

# ── 5. Включаем и запускаем сервис ─────────────────────────────────────────
inf "Перезагружаю systemd daemon"
systemctl daemon-reload

inf "Включаю и запускаю bmi30-hotspot-info"
systemctl enable --now bmi30-hotspot-info >/dev/null 2>&1
ok "Сервис запущен"

# DNS was already applied by prepare_ap; no NetworkManager/eth0 restart.

# ── Итог ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GRN}═══════════════════════════════════════════════════${NC}"
echo -e "${GRN}  Captive Portal установлен и запущен!${NC}"
echo -e "${GRN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Страница доступна: http://$HOTSPOT_IP/"
echo "  JSON API:          http://$HOTSPOT_IP/api/status"
echo ""
echo "  Проверить статус:  systemctl status bmi30-hotspot-info"
echo "  Логи сервиса:      journalctl -u bmi30-hotspot-info -f"
echo ""
echo "  Подключите устройство к Wi-Fi хотспоту BMI30 —"
echo "  страница с информацией откроется автоматически."
echo ""
