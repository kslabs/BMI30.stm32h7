#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

ETH_IFACE="${BMI30_ETH_PORTAL_IFACE:-eth0}"
ETH_CONN="${BMI30_ETH_PORTAL_CONN:-BMI30-Ethernet-Portal}"
ETH_ADDR_CIDR="${BMI30_ETH_PORTAL_ADDR:-10.43.0.1/24}"
ETH_ADDR="${ETH_ADDR_CIDR%%/*}"
DHCP_RANGE_START="${BMI30_ETH_PORTAL_DHCP_START:-10.43.0.10}"
DHCP_RANGE_END="${BMI30_ETH_PORTAL_DHCP_END:-10.43.0.200}"
DHCP_LEASE="${BMI30_ETH_PORTAL_DHCP_LEASE:-12h}"

DNSMASQ_NAME="bmi30-ethernet-portal-dnsmasq"
DNSMASQ_CONF="/etc/dnsmasq.d/${DNSMASQ_NAME}.conf"
DNSMASQ_SERVICE="/etc/systemd/system/${DNSMASQ_NAME}.service"

PORTAL_SERVICE_NAME="bmi30-hotspot-info"
PORTAL_SERVICE_PATH="/etc/systemd/system/${PORTAL_SERVICE_NAME}.service"
PORTAL_SERVER_DST="/usr/local/bin/bmi30-hotspot-info-server.py"

log() {
    local level="$1"
    shift
    printf '[%s] %s\n' "$level" "$*"
}

format_copy_bytes() {
    local bytes="${1:-0}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || printf '%sB' "$bytes"
}

install_with_copy_stats() {
    local label="$1"
    local source_path="$2"
    local mode="$3"
    local target_path="$4"
    local start_ts end_ts elapsed_s bytes rate

    start_ts="$(date +%s)"
    install -m "$mode" "$source_path" "$target_path"
    end_ts="$(date +%s)"
    elapsed_s=$((end_ts - start_ts))
    if (( elapsed_s <= 0 )); then
        elapsed_s=1
    fi
    bytes="$(stat -c '%s' "$target_path" 2>/dev/null || printf '0')"
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    rate=$((bytes / elapsed_s))
    log INFO "$label: длительность ${elapsed_s}с, средняя скорость $(format_copy_bytes "$rate")/с, объем $(format_copy_bytes "$bytes")"
}

require_root() {
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        return
    fi

    if command -v sudo >/dev/null 2>&1; then
        exec sudo -E bash "$0" "$@"
    fi

    log ERROR "Нужны права root. Запустите: sudo ./$SCRIPT_NAME $*"
    exit 1
}

usage() {
    cat <<EOF
Использование:
  sudo ./utilities/setup_ethernet_portal.sh install
  sudo ./utilities/setup_ethernet_portal.sh remove
  ./utilities/setup_ethernet_portal.sh status

Назначение:
  Делает из ${ETH_IFACE} прямой Ethernet portal mode для подключения компьютера по кабелю:
  Raspberry Pi выдает IP, отвечает DNS и открывает локальную web-страницу устройства.

Что настраивает:
  1. Профиль NetworkManager с фиксированным IP ${ETH_ADDR_CIDR}.
  2. Локальный dnsmasq на ${ETH_IFACE} с DHCP и wildcard DNS на ${ETH_ADDR}.
  3. Web-портал BMI30 на порту 80.

Важно:
  Этот режим рассчитан на прямое подключение ПК к Raspberry Pi по Ethernet.
  Если подключить ${ETH_IFACE} в существующую LAN, Raspberry Pi начнет раздавать там свой DHCP.
EOF
}

require_commands() {
    local missing=()
    local cmd

    for cmd in nmcli systemctl ip; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if ! command -v dnsmasq >/dev/null 2>&1; then
        missing+=("dnsmasq")
    fi

    if (( ${#missing[@]} > 0 )); then
        log ERROR "Не найдены команды: ${missing[*]}"
        log ERROR "Установите отсутствующие пакеты и повторите запуск"
        exit 1
    fi
}

install_nm_profile() {
    command -v nmcli >/dev/null 2>&1 || return 1

    if nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$ETH_CONN"; then
        log INFO "Обновляю профиль NetworkManager: $ETH_CONN"
        nmcli connection modify "$ETH_CONN" \
            connection.id "$ETH_CONN" \
            connection.interface-name "$ETH_IFACE" \
            connection.autoconnect yes \
            ipv4.method manual \
            ipv4.addresses "$ETH_ADDR_CIDR" \
            ipv4.never-default yes \
            ipv6.method ignore >/dev/null
    else
        log INFO "Создаю профиль NetworkManager: $ETH_CONN"
        nmcli connection add type ethernet ifname "$ETH_IFACE" con-name "$ETH_CONN" >/dev/null
        nmcli connection modify "$ETH_CONN" \
            connection.interface-name "$ETH_IFACE" \
            connection.autoconnect yes \
            ipv4.method manual \
            ipv4.addresses "$ETH_ADDR_CIDR" \
            ipv4.never-default yes \
            ipv6.method ignore >/dev/null
    fi

    nmcli connection reload >/dev/null 2>&1 || true
    nmcli connection up "$ETH_CONN" >/dev/null 2>&1 || true
}

install_dnsmasq_files() {
    log INFO "Настраиваю dnsmasq для Ethernet portal"
    mkdir -p /etc/dnsmasq.d

    cat > "$DNSMASQ_CONF" <<EOF
bind-dynamic
interface=${ETH_IFACE}
except-interface=lo
listen-address=${ETH_ADDR}
dhcp-authoritative
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},255.255.255.0,${DHCP_LEASE}
dhcp-option=option:router,${ETH_ADDR}
dhcp-option=option:dns-server,${ETH_ADDR}
no-resolv
address=/#/${ETH_ADDR}
EOF

    cat > "$DNSMASQ_SERVICE" <<EOF
[Unit]
Description=BMI30 Ethernet portal DHCP/DNS
After=network-online.target NetworkManager.service
Wants=network-online.target
ConditionPathExists=${DNSMASQ_CONF}

[Service]
Type=simple
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=${DNSMASQ_CONF}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
}

install_portal_service() {
    local server_src=""

    for candidate in \
        "${BMI30_PORTAL_SERVER_SRC:-}" \
        "/home/techaid/Documents/hotspot_info_server.py" \
        "$WORKSPACE_DIR/hotspot_info_server.py" \
        "/usr/local/hotspot_info_server.py"; do
        if [[ -n "$candidate" && -f "$candidate" ]]; then
            server_src="$candidate"
            break
        fi
    done

    if [[ -z "$server_src" ]]; then
        log ERROR "Не найден исходный hotspot_info_server.py (проверьте BMI30_PORTAL_SERVER_SRC)"
        exit 1
    fi

    log INFO "Источник web-портала: $server_src"

    log INFO "Устанавливаю web-портал BMI30"
    install_with_copy_stats "Копирование web-портала" "$server_src" 755 "$PORTAL_SERVER_DST"

    cat > "$PORTAL_SERVICE_PATH" <<EOF
[Unit]
Description=BMI30 Hotspot Info Web Page
After=network-online.target NetworkManager.service ${DNSMASQ_NAME}.service
Wants=network-online.target ${DNSMASQ_NAME}.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${PORTAL_SERVER_DST}
Restart=always
RestartSec=2
EnvironmentFile=-/etc/default/bmi30-hotspot-info
Environment=BMI30_HOTSPOT_IP=10.42.0.1

[Install]
WantedBy=multi-user.target
EOF
}

configure_firewall() {
    if ! command -v ufw >/dev/null 2>&1; then
        return
    fi

    if ! ufw status 2>/dev/null | grep -qi '^status: active'; then
        return
    fi

    log INFO "Открываю UFW для Ethernet portal"
    ufw allow in on "$ETH_IFACE" to any port 67 proto udp >/dev/null 2>&1 || true
    ufw allow in on "$ETH_IFACE" to any port 53 proto udp >/dev/null 2>&1 || true
    ufw allow in on "$ETH_IFACE" to any port 53 proto tcp >/dev/null 2>&1 || true
    ufw allow in on "$ETH_IFACE" to any port 80 proto tcp >/dev/null 2>&1 || true
}

enable_services() {
    systemctl daemon-reload
    systemctl enable --now "$DNSMASQ_NAME" >/dev/null
    systemctl enable --now "$PORTAL_SERVICE_NAME" >/dev/null
}

remove_services() {
    log INFO "Отключаю Ethernet portal"
    systemctl disable --now "$PORTAL_SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl disable --now "$DNSMASQ_NAME" >/dev/null 2>&1 || true
    rm -f "$PORTAL_SERVICE_PATH" "$DNSMASQ_SERVICE" "$DNSMASQ_CONF"
    systemctl daemon-reload >/dev/null 2>&1 || true

    if command -v nmcli >/dev/null 2>&1 && nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$ETH_CONN"; then
        nmcli connection down "$ETH_CONN" >/dev/null 2>&1 || true
        nmcli connection delete "$ETH_CONN" >/dev/null 2>&1 || true
    fi
}

show_status() {
    printf 'Ethernet portal status\n'
    printf '======================\n'
    printf 'Interface:   %s\n' "$ETH_IFACE"
    printf 'Connection:  %s\n' "$ETH_CONN"
    printf 'Portal IP:   %s\n' "$ETH_ADDR"
    printf '\n'

    if command -v ip >/dev/null 2>&1; then
        ip -br addr show "$ETH_IFACE" 2>/dev/null || true
        printf '\n'
    fi

    if command -v nmcli >/dev/null 2>&1; then
        nmcli -t -f NAME,DEVICE,TYPE,STATE connection show --active 2>/dev/null | grep -E "^${ETH_CONN}:|:${ETH_IFACE}:" || true
        printf '\n'
    fi

    systemctl --no-pager --full status "$DNSMASQ_NAME" "$PORTAL_SERVICE_NAME" 2>/dev/null || true
}

install_all() {
    require_commands
    install_nm_profile
    install_dnsmasq_files
    install_portal_service
    configure_firewall
    enable_services

    printf '\n'
    log INFO "Готово: Ethernet portal mode установлен"
    log INFO "Подключайте ПК кабелем к ${ETH_IFACE}"
    log INFO "Raspberry Pi будет доступен по адресу: http://${ETH_ADDR}/"
}

main() {
    local action="${1:-install}"

    case "$action" in
        install)
            require_root "$@"
            install_all
            ;;
        remove)
            require_root "$@"
            remove_services
            ;;
        status)
            show_status
            ;;
        --help|-h|help)
            usage
            ;;
        *)
            log ERROR "Неизвестное действие: $action"
            usage
            exit 1
            ;;
    esac
}

main "$@"
