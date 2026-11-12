#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
NETWORK_NAME_PREFIX="BMI30-"
NETWORK_SERIAL_SUFFIX_LEN=9
NETWORK_SERIAL_TAIL_LEN=12
MANUFACTURER="Vineta BMI s.r.o."
MODEL="BMI30"
WORKGROUP_DEFAULT="WORKGROUP"
FIRST_BOOT_MODE=0
INSTALL_SERVICE_MODE=0
SERVICE_NAME="bmi30-refresh-network-identity.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
INSTALL_PATH="/usr/local/sbin/bmi30-refresh-network-identity.sh"

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

    log ERROR "Нужны права root. Запустите: sudo ./$SCRIPT_NAME"
    exit 1
}

detect_serial() {
    local serial=""
    local source=""

    if [[ -r /proc/device-tree/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /proc/device-tree/serial-number || true)"
        [[ -n "$serial" ]] && source="/proc/device-tree/serial-number"
    fi

    if [[ -z "$serial" && -r /sys/firmware/devicetree/base/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /sys/firmware/devicetree/base/serial-number || true)"
        [[ -n "$serial" ]] && source="/sys/firmware/devicetree/base/serial-number"
    fi

    if [[ -z "$serial" && -r /proc/cpuinfo ]]; then
        serial="$(awk -F': ' '/^Serial/ {print $2; exit}' /proc/cpuinfo | tr -d ' \t\n' || true)"
        [[ -n "$serial" ]] && source="/proc/cpuinfo"
    fi

    serial="$(printf '%s' "$serial" | tr -cd '0-9A-Fa-f')"
    if [[ -z "$serial" || ${#serial} -lt 12 ]]; then
        log ERROR "Не удалось определить аппаратный серийный номер Raspberry Pi"
        log ERROR "Отказ от fallback через /etc/machine-id: после клонирования системы он может совпадать на разных платах"
        exit 1
    fi

    printf '[INFO] Источник серийного номера: %s\n' "${source:-unknown}" >&2
    printf '%s' "${serial^^}"
}

build_common_name() {
    local serial_full="$1"
    local suffix="${serial_full: -$NETWORK_SERIAL_SUFFIX_LEN}"
    printf '%s%s' "$NETWORK_NAME_PREFIX" "$suffix"
}

build_serial_tail() {
    local serial_full="$1"
    printf '%s' "${serial_full: -$NETWORK_SERIAL_TAIL_LEN}"
}

sync_installed_hotspot_script() {
    local hotspot_script="/usr/local/bin/bmi30-hotspot.sh"

    [[ -f "$hotspot_script" ]] || return 0

    log INFO "Синхронизирую startup-скрипт hotspot: $hotspot_script"
    python3 - "$hotspot_script" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
updated = text

updated = re.sub(
    r'\n[ \t]*if \[\[ -z "\$serial" && -r /etc/machine-id \]\]; then\n'
    r'[ \t]*serial="\$\(tr -d .*? true\)"\n'
    r'[ \t]*fi\n',
    '\n',
    updated,
    count=1,
    flags=re.S,
)

updated = re.sub(
    r'if \[\[ -z "\$serial" \]\]; then\n'
    r'([ \t]*)echo "Unable to determine serial" >&2\n'
    r'[ \t]*exit 1\n'
    r'[ \t]*fi',
    'if [[ -z "$serial" || ${#serial} -lt 12 ]]; then\n'
    r'\1echo "Unable to determine Raspberry Pi hardware serial" >&2\n'
    r'\1exit 1\n'
    '    fi',
    updated,
    count=1,
)

if updated != text:
    path.write_text(updated, encoding="utf-8")
PY
    chmod 755 "$hotspot_script" >/dev/null 2>&1 || true
}

install_systemd_service() {
    local source_path="$0"

    if command -v readlink >/dev/null 2>&1; then
        source_path="$(readlink -f "$source_path" 2>/dev/null || printf '%s' "$source_path")"
    fi

    if [[ ! -r "$source_path" ]]; then
        log ERROR "Не удалось прочитать исходный скрипт для установки сервиса: $source_path"
        exit 1
    fi

    log INFO "Устанавливаю постоянное обновление сетевой идентичности: $SERVICE_NAME"
    mkdir -p "$(dirname "$INSTALL_PATH")" "$(dirname "$SERVICE_PATH")"
    install_with_copy_stats "Копирование скрипта сетевой идентичности" "$source_path" 755 "$INSTALL_PATH"

    cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Refresh BMI30 network identity on every boot
After=local-fs.target NetworkManager.service
Wants=NetworkManager.service
ConditionPathExists=${INSTALL_PATH}

[Service]
Type=oneshot
ExecStart=${INSTALL_PATH}

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
}

update_hostname_files() {
    local new_name="$1"

    log INFO "Обновляю hostname: $new_name"
    printf '%s\n' "$new_name" > /etc/hostname

    if [[ -f /etc/hosts ]]; then
        python3 - "$new_name" <<'PY'
from pathlib import Path
import sys

hostname = sys.argv[1]
path = Path("/etc/hosts")
lines = path.read_text(encoding="utf-8").splitlines()
out = []
found = False

for line in lines:
    stripped = line.strip()
    if stripped.startswith("127.0.1.1"):
        out.append(f"127.0.1.1\t{hostname}")
        found = True
    else:
        out.append(line)

if not found:
    out.append(f"127.0.1.1\t{hostname}")

path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
    fi

    hostnamectl set-hostname "$new_name" >/dev/null 2>&1 || true
}

cleanup_browser_profile_locks() {
    local current_hostname="$1"
    local home_dir user_name user_id profile_dir lock_path lock_target lock_host lock_pid

    for home_dir in /home/*; do
        [[ -d "$home_dir" ]] || continue

        user_name="$(basename "$home_dir")"
        user_id="$(id -u "$user_name" 2>/dev/null || true)"
        [[ -n "$user_id" && "$user_id" -ge 1000 ]] || continue

        for profile_dir in "$home_dir/.config/chromium" "$home_dir/.config/google-chrome"; do
            [[ -d "$profile_dir" ]] || continue

            lock_path="$profile_dir/SingletonLock"
            if [[ -L "$lock_path" ]]; then
                lock_target="$(readlink "$lock_path" 2>/dev/null || true)"
                lock_host="${lock_target%-*}"
                lock_pid="${lock_target##*-}"
            else
                lock_target=""
                lock_host=""
                lock_pid=""
            fi

            if pgrep -u "$user_name" -x chromium >/dev/null 2>&1 \
                || pgrep -u "$user_name" -x chrome >/dev/null 2>&1 \
                || pgrep -u "$user_name" -x google-chrome >/dev/null 2>&1; then
                continue
            fi

            if [[ -L "$lock_path" || -e "$lock_path" ]]; then
                if [[ "$lock_host" != "$current_hostname" || ! "$lock_pid" =~ ^[0-9]+$ || ! -d "/proc/$lock_pid" ]]; then
                    log INFO "Очищаю устаревшую блокировку браузерного профиля: $profile_dir"
                    rm -f "$profile_dir/SingletonLock" "$profile_dir/SingletonSocket" "$profile_dir/SingletonCookie"
                fi
            fi
        done
    done
}

update_hostapd_ssid() {
    local ssid="$1"

    [[ -f /etc/hostapd/hostapd.conf ]] || return 0

    log INFO "Обновляю SSID в hostapd: $ssid"
    python3 - "$ssid" <<'PY'
from pathlib import Path
import sys

ssid = sys.argv[1]
path = Path("/etc/hostapd/hostapd.conf")
lines = path.read_text(encoding="utf-8").splitlines()
out = []
found = False

for line in lines:
    if line.strip().startswith("ssid="):
        out.append(f"ssid={ssid}")
        found = True
    else:
        out.append(line)

if not found:
    out.append(f"ssid={ssid}")

path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
PY
}

update_nm_hotspots() {
    local ssid="$1"

    command -v nmcli >/dev/null 2>&1 || return 0

    log INFO "Обновляю hotspot-профили NetworkManager: $ssid"

    local -a hotspot_uuids=()
    local connection_name connection_uuid connection_type

    while IFS=: read -r connection_name connection_uuid connection_type; do
        [[ "$connection_type" == "802-11-wireless" || "$connection_type" == "wifi" ]] || continue

        local mode ipv4_method current_ssid current_id current_if lowered_name lowered_id lowered_ssid
        mode="$(nmcli -g 802-11-wireless.mode connection show "$connection_uuid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
        ipv4_method="$(nmcli -g ipv4.method connection show "$connection_uuid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
        current_ssid="$(nmcli -g 802-11-wireless.ssid connection show "$connection_uuid" 2>/dev/null || true)"
        current_id="$(nmcli -g connection.id connection show "$connection_uuid" 2>/dev/null || true)"
        current_if="$(nmcli -g connection.interface-name connection show "$connection_uuid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
        lowered_name="$(printf '%s' "$connection_name" | tr '[:upper:]' '[:lower:]')"
        lowered_id="$(printf '%s' "$current_id" | tr '[:upper:]' '[:lower:]')"
        lowered_ssid="$(printf '%s' "$current_ssid" | tr '[:upper:]' '[:lower:]')"

        if [[ "$mode" == "ap" || "$ipv4_method" == "shared" || "$current_if" == "wlan0ap" || "$lowered_name" == hotspot* || "$lowered_id" == hotspot* || "$lowered_ssid" == bmi30* ]]; then
            hotspot_uuids+=("$connection_uuid")
        fi
    done < <(nmcli -t -f NAME,UUID,TYPE connection show 2>/dev/null || true)

    local hotspot_uuid
    for hotspot_uuid in "${hotspot_uuids[@]}"; do
        nmcli connection modify "$hotspot_uuid" \
            connection.id "$ssid" \
            802-11-wireless.ssid "$ssid" >/dev/null 2>&1 || true
    done

    nmcli connection reload >/dev/null 2>&1 || true

    while IFS=: read -r active_uuid active_type active_name; do
        [[ "$active_type" == "802-11-wireless" || "$active_type" == "wifi" ]] || continue

        local mode ipv4_method
        mode="$(nmcli -g 802-11-wireless.mode connection show "$active_uuid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
        ipv4_method="$(nmcli -g ipv4.method connection show "$active_uuid" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
        [[ "$mode" == "ap" || "$ipv4_method" == "shared" ]] || continue

        log INFO "Переактивирую hotspot: ${active_name:-$active_uuid}"
        nmcli connection down "$active_uuid" >/dev/null 2>&1 || true
        nmcli connection up "$ssid" >/dev/null 2>&1 || nmcli connection up "$active_uuid" >/dev/null 2>&1 || true
    done < <(nmcli -t -f UUID,TYPE,NAME connection show --active 2>/dev/null || true)
}

update_avahi_identity() {
    local safe_hostname="$1"
    local display_name="$2"
    local serial_tail="$3"

    [[ -d /etc/avahi || -f /etc/avahi/avahi-daemon.conf ]] || return 0

    log INFO "Обновляю Avahi/mDNS: $display_name"
    mkdir -p /etc/avahi/services

    python3 - "$safe_hostname" <<'PY'
from pathlib import Path
import sys

hostname = sys.argv[1]
path = Path('/etc/avahi/avahi-daemon.conf')
text = path.read_text(encoding='utf-8') if path.exists() else '[server]\n'
lines = text.splitlines()

section = None
server_found = False
host_written = False
out = []

for line in lines:
    stripped = line.strip()
    if stripped.startswith('[') and stripped.endswith(']'):
        if section == 'server' and not host_written:
            out.append(f'host-name={hostname}')
            host_written = True
        section = stripped.strip('[]').strip().lower()
        if section == 'server':
            server_found = True
        out.append(line)
        continue
    if section == 'server' and stripped.startswith('host-name='):
        out.append(f'host-name={hostname}')
        host_written = True
        continue
    out.append(line)

if not server_found:
    if out and out[-1] != '':
        out.append('')
    out.append('[server]')
    out.append(f'host-name={hostname}')
elif section == 'server' and not host_written:
    out.append(f'host-name={hostname}')

path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY

    cat > /etc/avahi/services/bmi30-device.service <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="no">${display_name}</name>

  <service>
    <type>_workstation._tcp</type>
    <port>9</port>
  </service>

  <service>
    <type>_device-info._tcp</type>
    <port>9</port>
    <txt-record>model=${MODEL}</txt-record>
    <txt-record>manufacturer=${MANUFACTURER}</txt-record>
    <txt-record>serial=${serial_tail}</txt-record>
    <txt-record>friendly-name=${display_name}</txt-record>
  </service>

  <service>
    <type>_smb._tcp</type>
    <port>445</port>
    <txt-record>friendly-name=${display_name}</txt-record>
    <txt-record>manufacturer=${MANUFACTURER}</txt-record>
  </service>
</service-group>
EOF
}

update_samba_identity() {
    local display_name="$1"
    local netbios_name="$2"
    local workgroup="$3"

    [[ -f /etc/samba/smb.conf ]] || return 0

    log INFO "Обновляю Samba/NetBIOS: $display_name"
    python3 - "$display_name" "$MANUFACTURER" "$netbios_name" "$workgroup" <<'PY'
from pathlib import Path
import sys

display_name, manufacturer, netbios_name, workgroup = sys.argv[1:5]
path = Path('/etc/samba/smb.conf')
text = path.read_text(encoding='utf-8') if path.exists() else '[global]\n'
lines = text.splitlines()

updates = {
    'workgroup': workgroup,
    'netbios name': netbios_name,
    'server string': f'{display_name} | {manufacturer}',
    'mdns name': 'mdns',
    'name resolve order': 'bcast host lmhosts wins',
}

section = None
global_found = False
seen = set()
out = []

for line in lines:
    stripped = line.strip()
    if stripped.startswith('[') and stripped.endswith(']'):
        if section == 'global':
            for key, value in updates.items():
                if key not in seen:
                    out.append(f'   {key} = {value}')
            seen.clear()
        section = stripped.strip('[]').strip().lower()
        if section == 'global':
            global_found = True
        out.append(line)
        continue

    if section == 'global' and '=' in line and not stripped.startswith(('#', ';')):
        key = line.split('=', 1)[0].strip().lower()
        if key in updates:
            out.append(f'   {key} = {updates[key]}')
            seen.add(key)
            continue

    out.append(line)

if not global_found:
    if out and out[-1] != '':
        out.append('')
    out.append('[global]')
    for key, value in updates.items():
        out.append(f'   {key} = {value}')
else:
    if section == 'global':
        for key, value in updates.items():
            if key not in seen:
                out.append(f'   {key} = {value}')

path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY
}

update_wsdd_identity() {
    local wsdd_name="$1"
    local workgroup="$2"
    local serial_tail="$3"

    local wsdd_bin=""
    local override_dir="/etc/systemd/system/wsdd.service.d"
    local override_path="${override_dir}/override.conf"

    if command -v wsdd >/dev/null 2>&1; then
        wsdd_bin="$(command -v wsdd)"
    elif [[ -x /usr/sbin/wsdd ]]; then
        wsdd_bin="/usr/sbin/wsdd"
    else
        [[ -f "$override_path" ]] || return 0
    fi

    if [[ -z "$wsdd_bin" ]]; then
        log INFO "Исправляю существующий override WSDD: $wsdd_name"
        python3 - "$override_path" "$wsdd_name" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
name = sys.argv[2]
text = path.read_text(encoding='utf-8') if path.exists() else ''
updated = re.sub(r'(--hostname|-n)\s+\S+', rf'\1 {name}', text)
if updated != text:
    path.write_text(updated, encoding='utf-8')
PY
        return 0
    fi

    local help_text wsdd_args wsdd_exec
    help_text="$("$wsdd_bin" --help 2>&1 || true)"
    wsdd_args=()

    if grep -q -- '--hostname' <<<"$help_text"; then
        wsdd_args+=(--hostname "$wsdd_name")
    elif grep -q -- '-n' <<<"$help_text"; then
        wsdd_args+=(-n "$wsdd_name")
    fi

    if grep -q -- '--preserve-case' <<<"$help_text"; then
        wsdd_args+=(--preserve-case)
    elif grep -q -- '-p' <<<"$help_text"; then
        wsdd_args+=(-p)
    fi

    if grep -q -- '--workgroup' <<<"$help_text"; then
        wsdd_args+=(--workgroup "$workgroup")
    elif grep -q -- '-w' <<<"$help_text"; then
        wsdd_args+=(-w "$workgroup")
    fi

    if grep -q -- '--manufacturer' <<<"$help_text"; then
        wsdd_args+=(--manufacturer "$MANUFACTURER")
    elif grep -q -- '--vendor' <<<"$help_text"; then
        wsdd_args+=(--vendor "$MANUFACTURER")
    fi

    if grep -q -- '--model' <<<"$help_text"; then
        wsdd_args+=(--model "$MODEL")
    fi

    if grep -q -- '--serial' <<<"$help_text"; then
        wsdd_args+=(--serial "$serial_tail")
    fi

    [[ ${#wsdd_args[@]} -gt 0 ]] || return 0

    wsdd_exec="$(python3 - "$wsdd_bin" "${wsdd_args[@]}" <<'PY'
import shlex
import sys

print(' '.join(shlex.quote(arg) for arg in sys.argv[1:]))
PY
)"

    log INFO "Обновляю WSDD: $wsdd_name"
    mkdir -p "$override_dir"
    cat > "$override_path" <<EOF
[Service]
ExecStart=
ExecStart=${wsdd_exec}
EOF
}

update_snmp_identity() {
    local display_name="$1"
    local serial_tail="$2"
    local snmp_contact snmp_location snmp_community snmp_description

    [[ -f /etc/snmp/snmpd.conf || -f /etc/default/snmpd || -d /etc/snmp ]] || return 0

    snmp_contact="${SNMP_CONTACT:-${MANUFACTURER}}"
    snmp_location="${SNMP_LOCATION:-BMI30 device}"
    snmp_community="${SNMP_COMMUNITY:-public}"
    snmp_description="${MANUFACTURER} ${MODEL} ${display_name} serial ${serial_tail}"

    log INFO "Обновляю SNMP: $display_name"
    mkdir -p /etc/snmp
    cat > /etc/snmp/snmpd.conf <<EOF
agentaddress udp:161,udp6:[::]:161
sysName ${display_name}
sysDescr ${snmp_description}
sysContact ${snmp_contact}
sysLocation ${snmp_location}
rocommunity ${snmp_community} default -V systemonly
rocommunity6 ${snmp_community} default -V systemonly
view systemonly included .1.3.6.1.2.1.1
EOF

    if [[ -f /etc/default/snmpd ]]; then
        python3 - <<'PY'
from pathlib import Path

path = Path('/etc/default/snmpd')
lines = path.read_text(encoding='utf-8').splitlines()
out = []
found = False

for line in lines:
    stripped = line.strip()
    if stripped.startswith('SNMPDOPTS='):
        out.append('SNMPDOPTS="-LSwd -Lf /dev/null -u Debian-snmp -g Debian-snmp -I -smux -p /run/snmpd.pid"')
        found = True
    else:
        out.append(line)

if not found:
    out.append('SNMPDOPTS="-LSwd -Lf /dev/null -u Debian-snmp -g Debian-snmp -I -smux -p /run/snmpd.pid"')

path.write_text('\n'.join(out).rstrip() + '\n', encoding='utf-8')
PY
    fi
}

update_lldp_identity() {
    local display_name="$1"
    local serial_tail="$2"
    local lldp_description lldp_platform

    [[ -f /etc/lldpd.conf ]] || return 0

    lldp_description="${MANUFACTURER} ${MODEL} ${display_name} serial ${serial_tail}"
    lldp_platform="${MANUFACTURER} ${MODEL}"

    log INFO "Обновляю LLDP: $display_name"
    cat > /etc/lldpd.conf <<EOF
configure system hostname ${display_name}
configure system description ${lldp_description}
configure system platform ${lldp_platform}
EOF
}

restart_related_services() {
    local ssid="${1:-}"

    systemctl daemon-reload >/dev/null 2>&1 || true

    if systemctl list-unit-files NetworkManager.service >/dev/null 2>&1; then
        systemctl reload NetworkManager >/dev/null 2>&1 || systemctl restart NetworkManager >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files hostapd.service >/dev/null 2>&1; then
        systemctl restart hostapd >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files bmi30-hotspot.service >/dev/null 2>&1; then
        systemctl restart bmi30-hotspot.service >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files avahi-daemon.service >/dev/null 2>&1; then
        systemctl restart avahi-daemon >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files smbd.service >/dev/null 2>&1; then
        systemctl restart smbd >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files nmbd.service >/dev/null 2>&1; then
        systemctl restart nmbd >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files wsdd.service >/dev/null 2>&1; then
        systemctl restart wsdd >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files snmpd.service >/dev/null 2>&1; then
        systemctl restart snmpd >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files lldpd.service >/dev/null 2>&1; then
        systemctl restart lldpd >/dev/null 2>&1 || true
    fi

    if command -v nmcli >/dev/null 2>&1; then
        nmcli connection reload >/dev/null 2>&1 || true
        if [[ -n "$ssid" ]]; then
            log INFO "Активирую hotspot с именем: $ssid"
            nmcli connection up "$ssid" >/dev/null 2>&1 || true
        fi
    fi
}

cleanup_first_boot_service() {
    local service_name="bmi30-refresh-network-identity-once.service"
    local service_path="/etc/systemd/system/${service_name}"
    local wants_link="/etc/systemd/system/multi-user.target.wants/${service_name}"

    [[ "$FIRST_BOOT_MODE" == "1" ]] || return 0

    rm -f "$wants_link"
    systemctl disable "$service_name" >/dev/null 2>&1 || true
    rm -f "$service_path"
    systemctl daemon-reload >/dev/null 2>&1 || true
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --first-boot)
                FIRST_BOOT_MODE=1
                shift
                ;;
            --install-service)
                INSTALL_SERVICE_MODE=1
                shift
                ;;
            --help|-h)
                cat <<'EOF'
Использование:
  sudo ./utilities/refresh_network_identity.sh

Назначение:
  Пересчитывает имя устройства и сетевые объявления по серийному номеру текущего устройства.

Опции:
  --install-service  Установить systemd-сервис для обновления идентичности на каждом старте.
  --first-boot  Служебный режим для одноразового запуска после миграции системы.
EOF
                exit 0
                ;;
            *)
                log ERROR "Неизвестный параметр: $1"
                exit 1
                ;;
        esac
    done
}

main() {
    parse_args "$@"
    require_root "$@"

    if [[ "$INSTALL_SERVICE_MODE" == "1" ]]; then
        install_systemd_service
    fi

    local serial_full serial_tail common_name workgroup
    serial_full="$(detect_serial)"
    serial_tail="$(build_serial_tail "$serial_full")"
    common_name="$(build_common_name "$serial_full")"
    workgroup="${WORKGROUP:-$WORKGROUP_DEFAULT}"

    log INFO "Серийный номер: $serial_full"
    log INFO "Новое сетевое имя: $common_name"

    update_hostname_files "$common_name"
    cleanup_browser_profile_locks "$common_name"
    update_avahi_identity "$common_name" "$common_name" "$serial_tail"
    update_samba_identity "$common_name" "$common_name" "$workgroup"
    update_wsdd_identity "$common_name" "$workgroup" "$serial_tail"
    update_hostapd_ssid "$common_name"
    update_nm_hotspots "$common_name"
    update_snmp_identity "$common_name" "$serial_tail"
    update_lldp_identity "$common_name" "$serial_tail"
    sync_installed_hotspot_script
    restart_related_services "$common_name"
    cleanup_first_boot_service

    log INFO "Обновление сетевой идентичности завершено"
}

main "$@"
