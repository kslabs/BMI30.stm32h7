#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
DEFAULT_INTERFACE="${BMI30_ETH_MONITOR_IFACE:-eth0}"
DEFAULT_POLL_SEC="${BMI30_ETH_MONITOR_POLL_SEC:-5}"
DEFAULT_REPORT_EVERY_SEC="${BMI30_ETH_MONITOR_REPORT_EVERY_SEC:-30}"
INSTALL_SERVICE_MODE=0
RUN_ONCE_MODE=0
INTERFACE="$DEFAULT_INTERFACE"
POLL_SEC="$DEFAULT_POLL_SEC"
REPORT_EVERY_SEC="$DEFAULT_REPORT_EVERY_SEC"
SERVICE_NAME="bmi30-ethernet-monitor.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
INSTALL_PATH="/usr/local/sbin/bmi30-ethernet-monitor.sh"

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
    cat <<'EOF'
Использование:
  ./utilities/monitor_ethernet_link.sh [--interface eth0] [--poll-sec 5] [--report-every-sec 30]
  sudo ./utilities/monitor_ethernet_link.sh --install-service

Назначение:
  Периодически опрашивает Ethernet-интерфейс, пишет изменение состояния в stdout/journal
  и показывает, появился ли линк и IPv4-адрес.

Опции:
  --install-service      Установить и запустить systemd-сервис.
  --once                 Выполнить один опрос и завершиться.
  --interface IFACE      Какой интерфейс проверять. По умолчанию: eth0.
  --poll-sec N           Интервал опроса в секундах. По умолчанию: 5.
  --report-every-sec N   Периодический повтор статуса даже без изменений. По умолчанию: 30.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install-service)
                INSTALL_SERVICE_MODE=1
                shift
                ;;
            --once)
                RUN_ONCE_MODE=1
                shift
                ;;
            --interface)
                [[ $# -ge 2 ]] || { log ERROR "После --interface нужен аргумент"; exit 1; }
                INTERFACE="$2"
                shift 2
                ;;
            --poll-sec)
                [[ $# -ge 2 ]] || { log ERROR "После --poll-sec нужен аргумент"; exit 1; }
                POLL_SEC="$2"
                shift 2
                ;;
            --report-every-sec)
                [[ $# -ge 2 ]] || { log ERROR "После --report-every-sec нужен аргумент"; exit 1; }
                REPORT_EVERY_SEC="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log ERROR "Неизвестный параметр: $1"
                exit 1
                ;;
        esac
    done

    [[ "$POLL_SEC" =~ ^[0-9]+$ ]] || { log ERROR "--poll-sec должен быть целым числом"; exit 1; }
    [[ "$REPORT_EVERY_SEC" =~ ^[0-9]+$ ]] || { log ERROR "--report-every-sec должен быть целым числом"; exit 1; }
    (( POLL_SEC > 0 )) || { log ERROR "--poll-sec должен быть > 0"; exit 1; }
    (( REPORT_EVERY_SEC > 0 )) || { log ERROR "--report-every-sec должен быть > 0"; exit 1; }
}

resolve_source_path() {
    local source_path="$0"

    if command -v readlink >/dev/null 2>&1; then
        source_path="$(readlink -f "$source_path" 2>/dev/null || printf '%s' "$source_path")"
    fi

    printf '%s' "$source_path"
}

install_systemd_service() {
    local source_path
    source_path="$(resolve_source_path)"

    [[ -r "$source_path" ]] || { log ERROR "Не удалось прочитать исходный скрипт: $source_path"; exit 1; }

    log INFO "Устанавливаю systemd-сервис мониторинга Ethernet: $SERVICE_NAME"
    mkdir -p "$(dirname "$INSTALL_PATH")" "$(dirname "$SERVICE_PATH")"
    install_with_copy_stats "Копирование скрипта мониторинга" "$source_path" 755 "$INSTALL_PATH"

    cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=BMI30 Ethernet link monitor
After=network-online.target
Wants=network-online.target
ConditionPathExists=${INSTALL_PATH}

[Service]
Type=simple
ExecStart=${INSTALL_PATH} --interface ${INTERFACE} --poll-sec ${POLL_SEC} --report-every-sec ${REPORT_EVERY_SEC}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    log INFO "Сервис запущен: $SERVICE_NAME"
}

get_carrier() {
    local iface="$1"
    local carrier_path="/sys/class/net/${iface}/carrier"

    if [[ -r "$carrier_path" ]]; then
        tr -d '[:space:]' < "$carrier_path"
        return
    fi

    printf 'unknown'
}

get_operstate() {
    local iface="$1"
    local operstate_path="/sys/class/net/${iface}/operstate"

    if [[ -r "$operstate_path" ]]; then
        tr -d '[:space:]' < "$operstate_path"
        return
    fi

    printf 'unknown'
}

get_mac() {
    local iface="$1"
    local mac_path="/sys/class/net/${iface}/address"

    if [[ -r "$mac_path" ]]; then
        tr -d '[:space:]' < "$mac_path"
        return
    fi

    printf '-'
}

get_ipv4_list() {
    local iface="$1"
    local ipv4_list

    ipv4_list="$(ip -o -4 addr show dev "$iface" 2>/dev/null | awk '{print $4}' | paste -sd ',' -)"
    if [[ -z "$ipv4_list" ]]; then
        printf 'none'
        return
    fi

    printf '%s' "$ipv4_list"
}

get_link_state() {
    local carrier="$1"
    local operstate="$2"

    if [[ "$carrier" == "1" || "$operstate" == "up" ]]; then
        printf 'UP'
        return
    fi

    if [[ "$carrier" == "0" || "$operstate" == "down" ]]; then
        printf 'DOWN'
        return
    fi

    printf 'UNKNOWN'
}

build_snapshot() {
    local iface="$1"

    if [[ ! -d "/sys/class/net/${iface}" ]]; then
        printf 'iface=%s|state=MISSING|carrier=missing|operstate=missing|ipv4=none|mac=-' "$iface"
        return
    fi

    local carrier operstate mac ipv4 link_state
    carrier="$(get_carrier "$iface")"
    operstate="$(get_operstate "$iface")"
    mac="$(get_mac "$iface")"
    ipv4="$(get_ipv4_list "$iface")"
    link_state="$(get_link_state "$carrier" "$operstate")"

    printf 'iface=%s|state=%s|carrier=%s|operstate=%s|ipv4=%s|mac=%s' \
        "$iface" "$link_state" "$carrier" "$operstate" "$ipv4" "$mac"
}

report_snapshot() {
    local snapshot="$1"
    local reason="$2"
    local formatted

    formatted="$(printf '%s' "$snapshot" | sed 's/|/, /g')"
    log INFO "[$reason] $formatted"
}

monitor_loop() {
    local iface="$1"
    local last_snapshot=""
    local last_report_ts=0
    local now snapshot

    while true; do
        now="$(date +%s)"
        snapshot="$(build_snapshot "$iface")"

        if [[ "$snapshot" != "$last_snapshot" ]]; then
            report_snapshot "$snapshot" "change"
            last_snapshot="$snapshot"
            last_report_ts="$now"
        elif (( now - last_report_ts >= REPORT_EVERY_SEC )); then
            report_snapshot "$snapshot" "periodic"
            last_report_ts="$now"
        fi

        if (( RUN_ONCE_MODE == 1 )); then
            return
        fi

        sleep "$POLL_SEC"
    done
}

main() {
    parse_args "$@"

    if (( INSTALL_SERVICE_MODE == 1 )); then
        require_root "$@"
        install_systemd_service
        exit 0
    fi

    monitor_loop "$INTERFACE"
}

main "$@"
