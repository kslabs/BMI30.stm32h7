#!/usr/bin/env bash
set -euo pipefail

BOOT_ORDER_USB_FIRST="0xf2614"
BOOT_SIZE_MIB=512
SOURCE_ROLE=""
TARGET_ROLE=""
ASSUME_YES=0
SKIP_EEPROM=0
SYNC_ONLY=0
FORCE_FORMAT_AND_RETRY=0
PAUSE_SOURCE_SERVICES=1
BMI30_CORE_SERVICE="${BMI30_CORE_SERVICE:-bmi30-core.service}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
declare -a PAUSED_SOURCE_SERVICES=()
declare -a BMI30_IDENTITY_FILES=(
    etc/bmi30-agent/id_ed25519
    etc/bmi30-agent/id_ed25519.pub
    etc/bmi30-agent/known_hosts
    etc/bmi30-agent/tunnel.env
    var/lib/bmi30-agent/device_api_token
    var/lib/bmi30-agent/bound_raspberry_serial
    var/lib/bmi30-agent/state.json
)
BMI30_IDENTITY_PRESERVED=0
BMI30_IDENTITY_STATUS="not-applicable"
BMI30_HARDWARE_SERIAL=""

SCRIPT_NAME="$(basename "$0")"
TOTAL_STEPS=14
CURRENT_STEP=0

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
BLU='\033[0;34m'
NC='\033[0m'

info() {
    printf "%b[INFO]%b %s\n" "$BLU" "$NC" "$*"
}

warn() {
    printf "%b[WARN]%b %s\n" "$YLW" "$NC" "$*" >&2
}

ok() {
    printf "%b[ OK ]%b %s\n" "$GRN" "$NC" "$*"
}

die() {
    printf "%b[ERR ]%b %s\n" "$RED" "$NC" "$*" >&2
    exit 1
}

usage() {
    cat <<EOF
Использование:
  sudo ./$SCRIPT_NAME --source-role internal|usb --target-role internal|usb [--yes] [--skip-eeprom] [--sync-only] [--force-format-and-retry] [--no-pause-services]

Скрипт выполняет файловую миграцию системы между eMMC и USB:
  1. Определяет исходный и целевой диски по ролям.
  2. Перед полным копированием создаёт локальный safety snapshot проекта.
  3. По умолчанию переразбивает целевой диск.
  4. С флагом --sync-only использует существующие разделы цели без переразметки.
  5. Копирует boot и root через rsync с прогрессом.
     При копировании живого rootfs временно останавливает bmi30-core.service.
  6. Записывает новые PARTUUID в cmdline.txt и fstab на целевом диске.

Опция --force-format-and-retry предназначена для полного копирования на USB:
  - ошибки необязательного local snapshot и настройки EEPROM не блокируют копирование;
  - при первой ошибке rsync USB повторно размечается и форматируется, затем boot/root
    копируются ещё один раз;
  - ошибка повторной попытки остаётся фатальной и возвращает ненулевой код.

Примеры:
  sudo ./$SCRIPT_NAME --source-role internal --target-role usb
  sudo ./$SCRIPT_NAME --source-role usb --target-role internal
  sudo ./$SCRIPT_NAME --source-role usb --target-role internal --sync-only
EOF
}

require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Запустите скрипт через sudo"
}

require_cmds() {
    local missing=()
    local cmd
    for cmd in lsblk findmnt blkid mount mountpoint umount rsync sfdisk wipefs partprobe udevadm mkfs.vfat mkfs.ext4 ssh-keygen awk sed grep sync sort df du numfmt timeout; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        die "Не найдены команды: ${missing[*]}"
    fi
}

run_precopy_backup() {
    if (( SYNC_ONLY == 1 )); then
        info "Sync-only режим: pre-copy local snapshot не требуется"
        return
    fi
    local backup_script="$SCRIPT_DIR/server_firmware.sh"
    [[ -x "$backup_script" ]] || die "Не найдена утилита локального снимка: $backup_script"
    info "Перед полным копированием сохраняю локальный комплект и настройки; номер версии не меняется"
    "$backup_script" backup-local --project "$WORKSPACE_DIR"
}

step() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    printf "\n%b[%d/%d]%b %s\n" "$GRN" "$CURRENT_STEP" "$TOTAL_STEPS" "$NC" "$*"
}

format_copy_duration() {
    local total_s="${1:-0}"
    [[ "$total_s" =~ ^[0-9]+$ ]] || total_s=0

    local hours minutes seconds
    hours=$((total_s / 3600))
    minutes=$(((total_s % 3600) / 60))
    seconds=$((total_s % 60))

    if (( hours > 0 )); then
        printf '%dч %02dм %02dс' "$hours" "$minutes" "$seconds"
    elif (( minutes > 0 )); then
        printf '%dм %02dс' "$minutes" "$seconds"
    else
        printf '%dс' "$seconds"
    fi
}

format_copy_bytes() {
    local bytes="${1:-0}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || printf '%sB' "$bytes"
}

format_copy_rate() {
    local bytes="${1:-0}"
    local elapsed_s="${2:-0}"
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    [[ "$elapsed_s" =~ ^[0-9]+$ ]] || elapsed_s=0

    if (( bytes <= 0 )); then
        printf 'н/д'
        return
    fi
    if (( elapsed_s <= 0 )); then
        elapsed_s=1
    fi

    numfmt --to=iec --suffix=B/s "$((bytes / elapsed_s))" 2>/dev/null || printf '%sB/s' "$((bytes / elapsed_s))"
}

copy_source_size_bytes() {
    local path="$1"
    local bytes

    bytes="$(du -sbx "$path" 2>/dev/null | awk 'NR == 1 {print $1}' || true)"
    if ! [[ "$bytes" =~ ^[0-9]+$ ]]; then
        bytes="$(du -sb "$path" 2>/dev/null | awk 'NR == 1 {print $1}' || true)"
    fi
    [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
    printf '%s' "$bytes"
}

copy_result_message() {
    local label="$1"
    local bytes="${2:-0}"
    local elapsed_s="${3:-0}"
    local bytes_label="${4:-объем}"

    printf 'Копирование %s: длительность %s, средняя скорость %s' \
        "$label" \
        "$(format_copy_duration "$elapsed_s")" \
        "$(format_copy_rate "$bytes" "$elapsed_s")"
    if [[ "$bytes" =~ ^[0-9]+$ ]] && (( bytes > 0 )); then
        printf ', %s %s' "$bytes_label" "$(format_copy_bytes "$bytes")"
    fi
}

rsync_result_message() {
    local label="$1"
    local expected_bytes="${2:-0}"
    local elapsed_s="${3:-0}"

    printf 'Копирование %s: длительность %s' \
        "$label" \
        "$(format_copy_duration "$elapsed_s")"
    if [[ "$expected_bytes" =~ ^[0-9]+$ ]] && (( expected_bytes > 0 )); then
        printf ', оценочный размер источника %s' "$(format_copy_bytes "$expected_bytes")"
    fi
    printf ' (rsync передает только отличающиеся данные)'
}

install_with_copy_stats() {
    local label="$1"
    local source_path="$2"
    local mode="$3"
    local target_path="$4"
    local start_ts end_ts elapsed_s bytes

    start_ts="$(date +%s)"
    install -m "$mode" "$source_path" "$target_path"
    end_ts="$(date +%s)"
    elapsed_s=$((end_ts - start_ts))
    bytes="$(copy_source_size_bytes "$target_path")"
    ok "$(copy_result_message "$label" "$bytes" "$elapsed_s")"
}

run_rsync_with_heartbeat() {
    local label="$1"
    local target_mount="$2"
    local expected_bytes="${3:-0}"
    shift 3

    local hb_interval=20
    local start_ts now_ts end_ts elapsed used_bytes used_human rsync_status

    start_ts="$(date +%s)"

    "$@" &
    local rsync_pid=$!

    while kill -0 "$rsync_pid" 2>/dev/null; do
        sleep "$hb_interval"
        if ! kill -0 "$rsync_pid" 2>/dev/null; then
            break
        fi

        now_ts="$(date +%s)"
        elapsed=$((now_ts - start_ts))
        used_bytes="$(df -B1 --output=used "$target_mount" 2>/dev/null | awk 'NR==2 {print $1}' || true)"

        if [[ -n "$used_bytes" ]]; then
            used_human="$(numfmt --to=iec "$used_bytes" 2>/dev/null || printf "%s" "$used_bytes")"
            info "[heartbeat] $label: процесс активен, прошло ${elapsed}с, всего занято на цели ${used_human}"
        else
            info "[heartbeat] $label: процесс активен, прошло ${elapsed}с"
        fi
    done

    rsync_status=0
    wait "$rsync_pid" || rsync_status=$?
    end_ts="$(date +%s)"
    elapsed=$((end_ts - start_ts))

    if (( rsync_status == 0 )); then
        ok "$(rsync_result_message "$label" "$expected_bytes" "$elapsed")"
    else
        warn "$(rsync_result_message "$label завершилось с кодом $rsync_status" "$expected_bytes" "$elapsed")"
    fi

    return "$rsync_status"
}

partition_path() {
    local disk="$1"
    local number="$2"

    if [[ "$disk" =~ [0-9]$ ]]; then
        printf "%sp%s\n" "$disk" "$number"
    else
        printf "%s%s\n" "$disk" "$number"
    fi
}

read_current_raspberry_serial() {
    local serial=""

    if [[ -r /proc/cpuinfo ]]; then
        serial="$(awk -F: 'tolower($1) ~ /^serial/ {gsub(/[[:space:]]/, "", $2); print toupper($2); exit}' /proc/cpuinfo)"
    fi
    [[ "$serial" =~ ^[0-9A-F]{16}$ ]] || return 1
    printf '%s\n' "$serial"
}

validate_bmi30_identity_root() {
    local root="${1%/}"
    local serial="$2"
    local private_key="$root/etc/bmi30-agent/id_ed25519"
    local public_key="$root/etc/bmi30-agent/id_ed25519.pub"
    local token_file="$root/var/lib/bmi30-agent/device_api_token"
    local bound_file="$root/var/lib/bmi30-agent/bound_raspberry_serial"
    local expected_comment="BMI30-${serial}@bmi30-tunnel"
    local bound_serial key_comment derived_key public_key_data path

    for path in "$private_key" "$public_key" "$token_file" "$bound_file"; do
        [[ -f "$path" && ! -L "$path" ]] || return 1
    done

    bound_serial="$(tr -d '[:space:]' < "$bound_file" | tr '[:lower:]' '[:upper:]')"
    [[ "$bound_serial" == "$serial" ]] || return 1

    key_comment="$(awk 'NF >= 3 {print $3; exit}' "$public_key")"
    [[ "$key_comment" == "$expected_comment" ]] || return 1

    if ! awk '
        NR == 1 && length($0) >= 40 && length($0) <= 512 && $0 !~ /[[:space:]]/ { valid = 1 }
        NR > 1 { valid = 0 }
        END { exit(valid ? 0 : 1) }
    ' "$token_file"; then
        return 1
    fi

    derived_key="$(ssh-keygen -y -f "$private_key" 2>/dev/null | awk 'NF >= 2 {print $1, $2; exit}')" || return 1
    public_key_data="$(awk 'NF >= 2 {print $1, $2; exit}' "$public_key")"
    [[ "$derived_key" == "$public_key_data" ]] || return 1
}

bmi30_identity_has_auth_conflict() {
    local root="${1%/}"
    local state_file="$root/var/lib/bmi30-agent/state.json"

    [[ -f "$state_file" && ! -L "$state_file" ]] || return 1
    grep -Eq '"http_status"[[:space:]]*:[[:space:]]*(401|409)([[:space:]]*,|[[:space:]]*})' \
        "$state_file"
}

preserve_target_bmi30_identity() {
    local target_root_dev target_root_type identity_root mounted_here=0
    local preserved_root rel source_path target_path

    [[ "$TARGET_ROLE" == "internal" ]] || return 0

    BMI30_HARDWARE_SERIAL="$(read_current_raspberry_serial)" || \
        die "Не удалось прочитать аппаратный serial Raspberry; безопасное сохранение BMI30 identity невозможно"
    BMI30_IDENTITY_STATUS="new-enrollment"
    target_root_dev="$(partition_path "$TARGET_DISK" 2)"

    if [[ ! -b "$target_root_dev" ]]; then
        info "На новой eMMC ещё нет root-раздела с BMI30 identity"
        return 0
    fi
    target_root_type="$(blkid -s TYPE -o value "$target_root_dev" 2>/dev/null || true)"
    if [[ "$target_root_type" != "ext4" ]]; then
        info "На eMMC нет существующего ext4 root с BMI30 identity"
        return 0
    fi

    identity_root="$(findmnt -rn -S "$target_root_dev" -o TARGET 2>/dev/null | awk 'NR == 1 {print; exit}' || true)"
    if [[ -z "$identity_root" ]]; then
        identity_root="$WORKDIR/target-identity"
        mkdir -p "$identity_root"
        mount -o ro "$target_root_dev" "$identity_root" || \
            die "Не удалось безопасно проверить прежнюю BMI30 identity на $target_root_dev"
        mounted_here=1
    fi

    if validate_bmi30_identity_root "$identity_root" "$BMI30_HARDWARE_SERIAL"; then
        if bmi30_identity_has_auth_conflict "$identity_root"; then
            BMI30_IDENTITY_STATUS="rejected-emmc-auth-conflict"
            warn "BMI30 identity eMMC принадлежит этой Raspberry, но её последний check-in получил HTTP 401/409; конфликтные credentials не сохраняю"
        else
            preserved_root="$WORKDIR/preserved-bmi30-identity"
            mkdir -m 0700 -p "$preserved_root"
            for rel in "${BMI30_IDENTITY_FILES[@]}"; do
                source_path="$identity_root/$rel"
                [[ -f "$source_path" && ! -L "$source_path" ]] || continue
                target_path="$preserved_root/$rel"
                mkdir -p "$(dirname "$target_path")"
                cp -a -- "$source_path" "$target_path"
            done
            BMI30_IDENTITY_PRESERVED=1
            BMI30_IDENTITY_STATUS="preserved-from-emmc"
            ok "Сохранена BMI30 identity eMMC для BMI30-$BMI30_HARDWARE_SERIAL; ключ и token не выводятся"
        fi
    elif [[ -e "$identity_root/etc/bmi30-agent/id_ed25519" || \
            -e "$identity_root/var/lib/bmi30-agent/device_api_token" || \
            -e "$identity_root/var/lib/bmi30-agent/bound_raspberry_serial" ]]; then
        warn "Существующая BMI30 identity eMMC не принадлежит текущей Raspberry или повреждена; она не будет использована"
    else
        info "На eMMC нет ранее зарегистрированной BMI30 identity"
    fi

    if (( mounted_here == 1 )); then
        umount "$identity_root"
    fi
}

remove_target_bmi30_identity_files() {
    local rel

    for rel in "${BMI30_IDENTITY_FILES[@]}"; do
        rm -f -- "$TARGET_ROOT_MNT/$rel"
    done
    rm -f -- "$TARGET_ROOT_MNT/var/lib/bmi30-agent/identity.lock"
}

restore_or_initialize_target_bmi30_identity() {
    local preserved_root rel source_path target_path

    [[ "$TARGET_ROLE" == "internal" ]] || return 0
    [[ "$BMI30_HARDWARE_SERIAL" =~ ^[0-9A-F]{16}$ ]] || \
        die "Аппаратный serial Raspberry потерян во время миграции"

    mkdir -p "$TARGET_ROOT_MNT/etc/bmi30-agent" "$TARGET_ROOT_MNT/var/lib/bmi30-agent"

    if (( BMI30_IDENTITY_PRESERVED == 1 )); then
        preserved_root="$WORKDIR/preserved-bmi30-identity"
        remove_target_bmi30_identity_files
        for rel in "${BMI30_IDENTITY_FILES[@]}"; do
            source_path="$preserved_root/$rel"
            [[ -f "$source_path" && ! -L "$source_path" ]] || continue
            target_path="$TARGET_ROOT_MNT/$rel"
            mkdir -p "$(dirname "$target_path")"
            cp -a -- "$source_path" "$target_path"
        done
        chown -R root:root "$TARGET_ROOT_MNT/etc/bmi30-agent" "$TARGET_ROOT_MNT/var/lib/bmi30-agent"
        chmod 0700 "$TARGET_ROOT_MNT/etc/bmi30-agent" "$TARGET_ROOT_MNT/var/lib/bmi30-agent"
        chmod 0600 \
            "$TARGET_ROOT_MNT/etc/bmi30-agent/id_ed25519" \
            "$TARGET_ROOT_MNT/var/lib/bmi30-agent/device_api_token" \
            "$TARGET_ROOT_MNT/var/lib/bmi30-agent/bound_raspberry_serial"
        chmod 0644 "$TARGET_ROOT_MNT/etc/bmi30-agent/id_ed25519.pub"
        for target_path in \
            "$TARGET_ROOT_MNT/etc/bmi30-agent/known_hosts" \
            "$TARGET_ROOT_MNT/etc/bmi30-agent/tunnel.env" \
            "$TARGET_ROOT_MNT/var/lib/bmi30-agent/state.json"
        do
            [[ -f "$target_path" ]] && chmod 0600 "$target_path"
        done
        validate_bmi30_identity_root "$TARGET_ROOT_MNT" "$BMI30_HARDWARE_SERIAL" || \
            die "Восстановленная BMI30 identity не прошла проверку"
        ok "BMI30 identity текущей Raspberry возвращена на eMMC после копирования"
        return 0
    fi

    if validate_bmi30_identity_root "$TARGET_ROOT_MNT" "$BMI30_HARDWARE_SERIAL"; then
        BMI30_IDENTITY_STATUS="copied-current-board-identity"
        ok "Источник уже содержит BMI30 identity текущей Raspberry; сохраняю её без изменения"
        return 0
    fi

    remove_target_bmi30_identity_files
    chmod 0700 "$TARGET_ROOT_MNT/etc/bmi30-agent" "$TARGET_ROOT_MNT/var/lib/bmi30-agent"
    BMI30_IDENTITY_STATUS="new-enrollment"
    warn "Чужая identity с USB удалена с eMMC; при первом запуске будет создана identity для BMI30-$BMI30_HARDWARE_SERIAL"
}

prepare_target_bmi30_agent_boot() {
    local backup_dir tmpfiles_src tmpfiles_dir tmpfiles_dst wants_dir
    local agent_unit tunnel_unit agent_program tunnel_program agent_config

    [[ "$TARGET_ROLE" == "internal" ]] || return 0

    backup_dir="$TARGET_ROOT_MNT/var/backups/bmi30-agent"
    tmpfiles_src="$SCRIPT_DIR/bmi30-agent-tmpfiles.conf"
    tmpfiles_dir="$TARGET_ROOT_MNT/etc/tmpfiles.d"
    tmpfiles_dst="$tmpfiles_dir/bmi30-agent.conf"
    wants_dir="$TARGET_ROOT_MNT/etc/systemd/system/multi-user.target.wants"
    agent_unit="$TARGET_ROOT_MNT/etc/systemd/system/bmi30-agent.service"
    tunnel_unit="$TARGET_ROOT_MNT/etc/systemd/system/bmi30-tunnel.service"
    agent_program="$TARGET_ROOT_MNT/opt/bmi30-agent/bmi30_agent.py"
    tunnel_program="$TARGET_ROOT_MNT/opt/bmi30-agent/run_bmi30_tunnel.sh"
    agent_config="$TARGET_ROOT_MNT/etc/bmi30-agent/config.json"

    [[ -f "$tmpfiles_src" ]] || \
        die "Не найдено правило восстановления каталога BMI30 Agent: $tmpfiles_src"
    for required_path in \
        "$agent_unit" \
        "$tunnel_unit" \
        "$agent_program" \
        "$tunnel_program" \
        "$agent_config"
    do
        [[ -f "$required_path" && ! -L "$required_path" ]] || \
            die "На целевой eMMC отсутствует обязательный файл BMI30 Agent: $required_path"
    done

    # Архивы credentials намеренно не копируются с исходного носителя. Сам
    # каталог при этом обязателен для ReadWritePaths= в bmi30-agent.service:
    # если его нет, systemd не сможет создать sandbox и не запустит Agent.
    install -d -m 0700 "$backup_dir"
    install -d -m 0755 "$tmpfiles_dir" "$wants_dir"
    install -m 0644 "$tmpfiles_src" "$tmpfiles_dst"

    # Агент выполняет check-in при каждой загрузке и сам запускает туннель
    # только после approved-ответа Hub. Отдельно включать tunnel unit нельзя:
    # на новой плате он не должен стартовать со скопированным назначением.
    ln -sfn ../bmi30-agent.service "$wants_dir/bmi30-agent.service"

    ok "BMI30 Agent включён на eMMC; каталог backup будет восстановлен до старта службы"
}

pause_source_services() {
    local service

    (( PAUSE_SOURCE_SERVICES == 1 )) || return 0
    [[ "${SOURCE_ROOT_COPY_MNT:-}" == "/" ]] || return 0

    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl недоступен, сервисы перед rootfs-копией не останавливаю"
        return
    fi

    service="$BMI30_CORE_SERVICE"
    [[ -n "$service" ]] || return 0

    if ! systemctl cat "$service" >/dev/null 2>&1; then
        info "Сервис $service не найден, пауза перед rootfs-копией не требуется"
        return
    fi

    if ! systemctl is-active --quiet "$service"; then
        info "Сервис $service не был запущен, после копирования стартовать не буду"
        return
    fi

    info "Останавливаю $service на время копирования rootfs"
    if timeout 30s systemctl stop "$service"; then
        PAUSED_SOURCE_SERVICES+=("$service")
        info "Сбрасываю файловые буферы перед rootfs-копией"
        # Flush only the source filesystems. A global sync also waits for the
        # freshly formatted target and can hide a target-device I/O lockup at
        # this otherwise source-only preparation step.
        sync -f "$SOURCE_ROOT_COPY_MNT"
        if [[ "$SOURCE_BOOT_COPY_MNT" != "$SOURCE_ROOT_COPY_MNT" ]]; then
            sync -f "$SOURCE_BOOT_COPY_MNT"
        fi
    else
        warn "Не удалось остановить $service за 30 секунд; продолжаю копирование живой системы"
    fi
}

resume_source_services() {
    local service

    (( ${#PAUSED_SOURCE_SERVICES[@]} > 0 )) || return 0

    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl недоступен, не могу вернуть сервисы после rootfs-копии: ${PAUSED_SOURCE_SERVICES[*]}"
        PAUSED_SOURCE_SERVICES=()
        return
    fi

    for service in "${PAUSED_SOURCE_SERVICES[@]}"; do
        info "Запускаю $service после копирования rootfs"
        timeout 30s systemctl start "$service" || warn "Не удалось запустить $service автоматически"
    done

    PAUSED_SOURCE_SERVICES=()
}

cleanup() {
    set +e
    resume_source_services
    if [[ -n "${WORKDIR:-}" ]] && mountpoint -q "$WORKDIR/target-identity" 2>/dev/null; then
        umount "$WORKDIR/target-identity"
    fi
    if mountpoint -q "$TARGET_BOOT_MNT" 2>/dev/null; then
        umount "$TARGET_BOOT_MNT"
    fi
    if mountpoint -q "$TARGET_ROOT_MNT" 2>/dev/null; then
        umount "$TARGET_ROOT_MNT"
    fi
    if mountpoint -q "$SOURCE_BOOT_MNT" 2>/dev/null; then
        umount "$SOURCE_BOOT_MNT"
    fi
    if mountpoint -q "$SOURCE_ROOT_MNT" 2>/dev/null; then
        umount "$SOURCE_ROOT_MNT"
    fi
    rm -rf "$WORKDIR"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source-role)
                [[ $# -ge 2 ]] || die "После --source-role нужна роль"
                SOURCE_ROLE="$2"
                shift 2
                ;;
            --target-role)
                [[ $# -ge 2 ]] || die "После --target-role нужна роль"
                TARGET_ROLE="$2"
                shift 2
                ;;
            --yes)
                ASSUME_YES=1
                shift
                ;;
            --skip-eeprom)
                SKIP_EEPROM=1
                shift
                ;;
            --sync-only)
                SYNC_ONLY=1
                shift
                ;;
            --force-format-and-retry)
                FORCE_FORMAT_AND_RETRY=1
                shift
                ;;
            --no-pause-services)
                PAUSE_SOURCE_SERVICES=0
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                die "Неизвестный параметр: $1"
                ;;
        esac
    done

    [[ "$SOURCE_ROLE" == "internal" || "$SOURCE_ROLE" == "usb" ]] || die "Нужен --source-role internal|usb"
    [[ "$TARGET_ROLE" == "internal" || "$TARGET_ROLE" == "usb" ]] || die "Нужен --target-role internal|usb"
    [[ "$SOURCE_ROLE" != "$TARGET_ROLE" ]] || die "Источник и цель должны быть разными ролями"
    if (( FORCE_FORMAT_AND_RETRY == 1 )); then
        [[ "$TARGET_ROLE" == "usb" ]] || die "--force-format-and-retry разрешён только для целевого USB"
        (( SYNC_ONLY == 0 )) || die "--force-format-and-retry несовместим с --sync-only"
    fi
}

detect_current_devices() {
    CURRENT_ROOT_DEV="$(findmnt -no SOURCE /)"
    [[ -b "$CURRENT_ROOT_DEV" ]] || die "Не удалось определить устройство для /"
    CURRENT_ROOT_DISK="/dev/$(lsblk -no PKNAME "$CURRENT_ROOT_DEV")"

    CURRENT_BOOT_DEV="$(findmnt -no SOURCE /boot/firmware 2>/dev/null || true)"
    if [[ -n "$CURRENT_BOOT_DEV" && -b "$CURRENT_BOOT_DEV" ]]; then
        CURRENT_BOOT_DISK="/dev/$(lsblk -no PKNAME "$CURRENT_BOOT_DEV")"
    else
        CURRENT_BOOT_DISK=""
    fi
}

get_current_boot_order() {
    local order=""

    if command -v vcgencmd >/dev/null 2>&1; then
        order="$(vcgencmd bootloader_config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
    fi

    if [[ -z "$order" ]] && command -v rpi-eeprom-config >/dev/null 2>&1; then
        order="$(rpi-eeprom-config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
    fi

    printf "%s\n" "$order"
}

list_candidates() {
    local role="$1"
    local exclude_disk="${2:-}"
    local name transport size_bytes

    while read -r name; do
        [[ -n "$name" ]] || continue
        [[ -n "$exclude_disk" && "$name" == "$exclude_disk" ]] && continue

        case "$name" in
            /dev/mmcblk*boot*|/dev/mmcblk*rpmb)
                continue
                ;;
        esac

        transport="$(lsblk -dnro TRAN "$name" 2>/dev/null | awk 'NR==1 {print $1}')"
        size_bytes="$(lsblk -dnbro SIZE "$name" 2>/dev/null | awk 'NR==1 {print $1}')"
        [[ -n "$size_bytes" && "$size_bytes" -gt 0 ]] || continue

        if [[ "$role" == "usb" ]]; then
            [[ "$transport" == "usb" ]] || continue
        else
            [[ "$transport" != "usb" ]] || continue
        fi

        printf "%s\n" "$name"
    done < <(lsblk -dpno NAME,TYPE | awk '$2 == "disk" {print $1}')
}

pick_disk_for_role() {
    local role="$1"
    local exclude_disk="${2:-}"
    local candidates=()
    local disk

    while read -r disk; do
        [[ -n "$disk" ]] || continue
        candidates+=("$disk")
    done < <(list_candidates "$role" "$exclude_disk")

    if (( ${#candidates[@]} == 0 )); then
        die "Не найден диск для роли '$role'"
    fi

    if [[ "$role" == "internal" ]]; then
        for disk in "${candidates[@]}"; do
            if [[ "$disk" =~ ^/dev/mmcblk[0-9]+$ ]]; then
                printf "%s\n" "$disk"
                return
            fi
        done
    fi

    if (( ${#candidates[@]} == 1 )); then
        printf "%s\n" "${candidates[0]}"
        return
    fi

    printf "Найдено несколько дисков для роли '%s':\n" "$role" >&2
    printf "  %s\n" "${candidates[@]}" >&2
    die "Оставьте только один диск роли '$role' или упростите конфигурацию"
}

detect_source_and_target() {
    SOURCE_DISK="$(pick_disk_for_role "$SOURCE_ROLE")"
    TARGET_DISK="$(pick_disk_for_role "$TARGET_ROLE" "$SOURCE_DISK")"

    [[ "$SOURCE_DISK" != "$TARGET_DISK" ]] || die "Источник и цель совпали"

    SOURCE_BOOT_DEV="$(partition_path "$SOURCE_DISK" 1)"
    SOURCE_ROOT_DEV="$(partition_path "$SOURCE_DISK" 2)"
    [[ -b "$SOURCE_BOOT_DEV" && -b "$SOURCE_ROOT_DEV" ]] || die "На исходном диске не найдены разделы 1 и 2"

    SOURCE_BOOT_TYPE="$(blkid -s TYPE -o value "$SOURCE_BOOT_DEV" 2>/dev/null || true)"
    SOURCE_ROOT_TYPE="$(blkid -s TYPE -o value "$SOURCE_ROOT_DEV" 2>/dev/null || true)"
    [[ "$SOURCE_BOOT_TYPE" == "vfat" ]] || die "Исходный boot-раздел должен быть vfat: $SOURCE_BOOT_DEV"
    [[ "$SOURCE_ROOT_TYPE" == "ext4" ]] || die "Исходный root-раздел должен быть ext4: $SOURCE_ROOT_DEV"

    if [[ "$TARGET_DISK" == "$CURRENT_ROOT_DISK" ]]; then
        die "Целевой диск сейчас является текущим root. Загрузитесь с другого носителя и повторите"
    fi

    if [[ -n "$CURRENT_BOOT_DISK" && "$TARGET_DISK" == "$CURRENT_BOOT_DISK" ]]; then
        warn "Целевой диск сейчас смонтирован как /boot/firmware. Перед переразметкой он будет отмонтирован"
    fi
}

check_target_emmc_command_queue() {
    local target_name cmdq_path cmdq_enabled

    [[ "$TARGET_DISK" =~ ^/dev/mmcblk[0-9]+$ ]] || return 0

    target_name="${TARGET_DISK##*/}"
    cmdq_path="/sys/block/$target_name/device/cmdq_en"
    [[ -r "$cmdq_path" ]] || return 0

    cmdq_enabled="$(cat "$cmdq_path" 2>/dev/null || true)"
    [[ "$cmdq_enabled" == "1" ]] || return 0

    die "На целевом eMMC включена Command Queueing (cmdq_en=1). На этой системе очередь может полностью зависнуть при параллельной записи. Добавьте 'dtparam=sd_cqe=0' в секцию [all] файла /boot/firmware/config.txt, перезагрузите систему и повторите миграцию"
}

confirm_plan() {
    local source_size target_size
    source_size="$(lsblk -dnro SIZE "$SOURCE_DISK")"
    target_size="$(lsblk -dnro SIZE "$TARGET_DISK")"

    info "Источник ($SOURCE_ROLE): $SOURCE_DISK ($source_size)"
    info "  boot: $SOURCE_BOOT_DEV"
    info "  root: $SOURCE_ROOT_DEV"
    info "Цель ($TARGET_ROLE):     $TARGET_DISK ($target_size)"
    if (( SYNC_ONLY == 1 )); then
        warn "Режим sync-only: переразметка не будет выполнена, данные на цели будут синхронизированы через rsync --delete"
    else
        warn "Все данные на $TARGET_DISK будут уничтожены"
    fi

    if (( ASSUME_YES == 1 )); then
        return
    fi

    read -r -p "Продолжить? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || die "Операция отменена"
}

configure_bootloader() {
    local current_order tmp_cfg

    if [[ "$TARGET_ROLE" != "usb" ]]; then
        info "EEPROM не меняю: целевой диск не USB"
        return
    fi

    if (( SKIP_EEPROM == 1 )); then
        warn "Изменение EEPROM пропущено по флагу --skip-eeprom"
        return
    fi

    if [[ -n "$CURRENT_BOOT_DISK" && "$CURRENT_BOOT_DISK" == "$TARGET_DISK" ]]; then
        warn "Изменение EEPROM отложено: текущий /boot/firmware находится на целевом USB-диске"
        return
    fi

    if ! command -v rpi-eeprom-config >/dev/null 2>&1; then
        warn "rpi-eeprom-config не найден, BOOT_ORDER не изменён"
        return
    fi

    current_order="$({ vcgencmd bootloader_config 2>/dev/null || rpi-eeprom-config; } | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
    if [[ "$current_order" == "$BOOT_ORDER_USB_FIRST" ]]; then
        ok "BOOT_ORDER уже настроен: $BOOT_ORDER_USB_FIRST"
        return
    fi

    tmp_cfg="$WORKDIR/boot.conf"
    rpi-eeprom-config > "$tmp_cfg"
    if grep -q '^BOOT_ORDER=' "$tmp_cfg"; then
        sed -i "s/^BOOT_ORDER=.*/BOOT_ORDER=$BOOT_ORDER_USB_FIRST/" "$tmp_cfg"
    else
        printf "\nBOOT_ORDER=%s\n" "$BOOT_ORDER_USB_FIRST" >> "$tmp_cfg"
    fi

    info "Применяю BOOT_ORDER=$BOOT_ORDER_USB_FIRST через EEPROM"
    rpi-eeprom-config --apply "$tmp_cfg"
    ok "EEPROM обновлён. Новый BOOT_ORDER вступит в силу после reboot"
}

unmount_target_partitions() {
    local part mountpoint
    local umount_rc

    while read -r part; do
        [[ -n "$part" ]] || continue
        while read -r mountpoint; do
            [[ -n "$mountpoint" ]] || continue
            info "Отмонтирую $mountpoint"
            umount_rc=0
            timeout 10s umount "$mountpoint" || umount_rc=$?
            if (( umount_rc == 124 )); then
                warn "umount завис на $mountpoint, пробую lazy umount"
                umount -l "$mountpoint" || die "Не удалось отмонтировать $mountpoint"
            elif (( umount_rc != 0 )); then
                if (( FORCE_FORMAT_AND_RETRY == 1 )); then
                    warn "Обычный umount не сработал для $mountpoint, пробую lazy umount"
                    umount -l "$mountpoint" || die "Не удалось отмонтировать $mountpoint"
                else
                    die "Не удалось отмонтировать $mountpoint"
                fi
            fi
        done < <(findmnt -rn -S "$part" -o TARGET | sort -r)
    done < <(lsblk -lnpo NAME,TYPE "$TARGET_DISK" | awk '$2 == "part" {print $1}')
}

check_stale_uninterruptible_rsync() {
    local stale

    stale="$(ps -eo pid,stat,cmd | awk '$2 ~ /^D/ && $3 ~ /rsync/ && $0 ~ /bmi30-disk-migrate/ && $0 ~ /target-root/ {print $1":"$2":"$3; exit}')"
    if [[ -n "$stale" ]]; then
        die "Обнаружен зависший rsync в состоянии D ($stale). Перезагрузите систему для очистки I/O и повторите миграцию"
    fi
}

partition_and_format_target() {
    local sfdisk_script
    local root_start_mib

    sfdisk_script="$WORKDIR/target.sfdisk"
    root_start_mib=$((4 + BOOT_SIZE_MIB))
    cat > "$sfdisk_script" <<EOF
label: dos

${TARGET_DISK}1 : start=4MiB, size=${BOOT_SIZE_MIB}MiB, type=c, bootable
${TARGET_DISK}2 : start=${root_start_mib}MiB, type=83
EOF

    check_stale_uninterruptible_rsync
    unmount_target_partitions
    wipefs -af "$TARGET_DISK"
    sfdisk --wipe always "$TARGET_DISK" < "$sfdisk_script"
    partprobe "$TARGET_DISK"
    udevadm settle

    TARGET_BOOT_DEV="$(partition_path "$TARGET_DISK" 1)"
    TARGET_ROOT_DEV="$(partition_path "$TARGET_DISK" 2)"
    [[ -b "$TARGET_BOOT_DEV" && -b "$TARGET_ROOT_DEV" ]] || die "После разметки не найдены целевые разделы"

    mkfs.vfat -F 32 -n BOOTFS "$TARGET_BOOT_DEV"
    # Finish inode-table and journal initialisation before mounting. This keeps
    # ext4lazyinit from competing with the boot copy for eMMC requests.
    mkfs.ext4 -F -L rootfs -m 0 \
        -E lazy_itable_init=0,lazy_journal_init=0 \
        "$TARGET_ROOT_DEV"
    partprobe "$TARGET_DISK"
    udevadm settle

    TARGET_BOOT_PARTUUID="$(blkid -s PARTUUID -o value "$TARGET_BOOT_DEV")"
    TARGET_ROOT_PARTUUID="$(blkid -s PARTUUID -o value "$TARGET_ROOT_DEV")"
    [[ -n "$TARGET_BOOT_PARTUUID" && -n "$TARGET_ROOT_PARTUUID" ]] || die "Не удалось определить PARTUUID целевых разделов"

    ok "Цель подготовлена: boot=$TARGET_BOOT_PARTUUID root=$TARGET_ROOT_PARTUUID"
}

prepare_existing_target_partitions() {
    TARGET_BOOT_DEV="$(partition_path "$TARGET_DISK" 1)"
    TARGET_ROOT_DEV="$(partition_path "$TARGET_DISK" 2)"
    [[ -b "$TARGET_BOOT_DEV" && -b "$TARGET_ROOT_DEV" ]] || die "На целевом диске не найдены разделы 1 и 2"

    TARGET_BOOT_TYPE="$(blkid -s TYPE -o value "$TARGET_BOOT_DEV" 2>/dev/null || true)"
    TARGET_ROOT_TYPE="$(blkid -s TYPE -o value "$TARGET_ROOT_DEV" 2>/dev/null || true)"
    [[ "$TARGET_BOOT_TYPE" == "vfat" ]] || die "Целевой boot-раздел должен быть vfat: $TARGET_BOOT_DEV"
    [[ "$TARGET_ROOT_TYPE" == "ext4" ]] || die "Целевой root-раздел должен быть ext4: $TARGET_ROOT_DEV"

    unmount_target_partitions

    TARGET_BOOT_PARTUUID="$(blkid -s PARTUUID -o value "$TARGET_BOOT_DEV")"
    TARGET_ROOT_PARTUUID="$(blkid -s PARTUUID -o value "$TARGET_ROOT_DEV")"
    [[ -n "$TARGET_BOOT_PARTUUID" && -n "$TARGET_ROOT_PARTUUID" ]] || die "Не удалось определить PARTUUID целевых разделов"

    ok "Найдена существующая цель: boot=$TARGET_BOOT_PARTUUID root=$TARGET_ROOT_PARTUUID"
}

mount_filesystems() {
    if [[ "$SOURCE_BOOT_DEV" == "$CURRENT_BOOT_DEV" ]]; then
        SOURCE_BOOT_COPY_MNT="/boot/firmware"
    else
        mount -o ro "$SOURCE_BOOT_DEV" "$SOURCE_BOOT_MNT"
        SOURCE_BOOT_COPY_MNT="$SOURCE_BOOT_MNT"
    fi

    if [[ "$SOURCE_ROOT_DEV" == "$CURRENT_ROOT_DEV" ]]; then
        SOURCE_ROOT_COPY_MNT="/"
    else
        mount -o ro "$SOURCE_ROOT_DEV" "$SOURCE_ROOT_MNT"
        SOURCE_ROOT_COPY_MNT="$SOURCE_ROOT_MNT"
    fi

    mount_target_filesystems
}

mount_target_filesystems() {
    mount "$TARGET_ROOT_DEV" "$TARGET_ROOT_MNT"
    mkdir -p "$TARGET_ROOT_MNT/boot/firmware"
    mount "$TARGET_BOOT_DEV" "$TARGET_BOOT_MNT"
}

unmount_target_filesystems() {
    local mount_path umount_rc

    for mount_path in "$TARGET_BOOT_MNT" "$TARGET_ROOT_MNT"; do
        mountpoint -q "$mount_path" 2>/dev/null || continue
        umount_rc=0
        timeout 15s umount "$mount_path" || umount_rc=$?
        if (( umount_rc != 0 )); then
            if (( FORCE_FORMAT_AND_RETRY == 1 )); then
                warn "Не удалось штатно отмонтировать $mount_path, выполняю lazy umount"
                umount -l "$mount_path" || die "Не удалось отмонтировать $mount_path"
            else
                die "Не удалось отмонтировать $mount_path"
            fi
        fi
    done
    udevadm settle || true
}

detach_source_virtual_mounts() {
    local source_root path
    source_root="${SOURCE_ROOT_COPY_MNT%/}"
    if [[ "$source_root" == "/" ]]; then
        source_root=""
    fi

    shopt -s nullglob
    for path in \
        "$source_root"/home/*/.cache/gvfs \
        "$source_root"/home/*/.cache/doc \
        "$source_root"/home/*/.gvfs
    do
        if mountpoint -q "$path"; then
            warn "Отключаю виртуальный mountpoint перед rsync: $path"
            umount -l "$path" || warn "Не удалось отключить $path"
        fi
    done
    shopt -u nullglob
}

copy_boot_files() {
    local source_bytes

    info "Копирую boot-файлы"
    source_bytes="$(copy_source_size_bytes "$SOURCE_BOOT_COPY_MNT")"
    run_rsync_with_heartbeat "boot" "$TARGET_BOOT_MNT" "$source_bytes" \
        rsync -aHAX --delete --human-readable --outbuf=L --info=progress2,stats1 \
        "$SOURCE_BOOT_COPY_MNT/" "$TARGET_BOOT_MNT/"
}

copy_root_files() {
    local rsync_status
    local source_bytes

    info "Копирую rootfs. Это самая долгая часть"
    detach_source_virtual_mounts
    source_bytes="$(copy_source_size_bytes "$SOURCE_ROOT_COPY_MNT")"
    rsync_status=0
    # XRDP client drives are session-only FUSE mounts inaccessible even to
    # root. Exclude the mount itself; rsync -x alone still tries to stat it.
    run_rsync_with_heartbeat "rootfs" "$TARGET_ROOT_MNT" "$source_bytes" \
        rsync -aHAXx --numeric-ids --delete --human-readable --outbuf=L --info=progress2,stats1 \
        --exclude=/boot/firmware/* \
        --exclude=/dev/* \
        --exclude=/proc/* \
        --exclude=/sys/* \
        --exclude=/tmp/* \
        --exclude=/run/* \
        --exclude=/mnt/* \
        --exclude=/media/* \
        --exclude=/var/backups/bmi30-agent/*** \
        --exclude=/home/*/thinclient_drives/*** \
        --exclude=/home/*/.cache/gvfs \
        --exclude=/home/*/.cache/gvfs/ \
        --exclude=/home/*/.cache/gvfs/*** \
        --exclude=/home/*/.cache/doc \
        --exclude=/home/*/.cache/doc/ \
        --exclude=/home/*/.cache/doc/*** \
        --exclude=/home/*/.gvfs \
        --exclude=/home/*/.gvfs/ \
        --exclude=/home/*/.gvfs/*** \
        --exclude=home/*/.cache/gvfs \
        --exclude=home/*/.cache/gvfs/ \
        --exclude=home/*/.cache/gvfs/*** \
        --exclude=home/*/.cache/doc \
        --exclude=home/*/.cache/doc/ \
        --exclude=home/*/.cache/doc/*** \
        --exclude=home/*/.gvfs \
        --exclude=home/*/.gvfs/ \
        --exclude=home/*/.gvfs/*** \
        --exclude=/lost+found \
        --exclude=/swapfile \
        "$SOURCE_ROOT_COPY_MNT"/ "$TARGET_ROOT_MNT/" || rsync_status=$?

    if (( rsync_status == 24 )); then
        warn "rsync code 24: часть файлов исчезла во время копирования; продолжаю, это нормально для живой системы"
    elif (( rsync_status != 0 )); then
        return "$rsync_status"
    fi
}

copy_boot_and_root_files() {
    local copy_status=0

    copy_boot_files || copy_status=$?
    if (( copy_status == 0 )); then
        pause_source_services
        copy_root_files || copy_status=$?
        resume_source_services
    fi

    return "$copy_status"
}

retry_copy_after_fresh_format() {
    local copy_status=0

    warn "Первая попытка копирования завершилась ошибкой. Повторно форматирую $TARGET_DISK и начинаю boot/root заново"
    resume_source_services
    unmount_target_filesystems
    partition_and_format_target
    mount_target_filesystems
    verify_target_capacity
    copy_boot_and_root_files || copy_status=$?

    if (( copy_status != 0 )); then
        die "Повторное копирование после форматирования завершилось с кодом $copy_status"
    fi

    ok "Повторное копирование после форматирования завершено успешно"
}

verify_target_capacity() {
    local src_boot_used src_root_used
    local dst_boot_size dst_root_size
    local boot_reserve root_reserve

    # Небольшой запас на служебные накладные расходы ФС.
    boot_reserve=$((32 * 1024 * 1024))
    root_reserve=$((256 * 1024 * 1024))

    src_boot_used="$(df -B1 --output=used "$SOURCE_BOOT_COPY_MNT" | awk 'NR==2 {print $1}')"
    src_root_used="$(df -B1 --output=used "$SOURCE_ROOT_COPY_MNT" | awk 'NR==2 {print $1}')"
    dst_boot_size="$(df -B1 --output=size "$TARGET_BOOT_MNT" | awk 'NR==2 {print $1}')"
    dst_root_size="$(df -B1 --output=size "$TARGET_ROOT_MNT" | awk 'NR==2 {print $1}')"

    [[ -n "$src_boot_used" && -n "$src_root_used" && -n "$dst_boot_size" && -n "$dst_root_size" ]] || \
        die "Не удалось вычислить размеры файловых систем для проверки вместимости"

    info "Проверка вместимости: source boot used=$(numfmt --to=iec "$src_boot_used"), target boot size=$(numfmt --to=iec "$dst_boot_size")"
    info "Проверка вместимости: source root used=$(numfmt --to=iec "$src_root_used"), target root size=$(numfmt --to=iec "$dst_root_size")"

    if (( src_boot_used + boot_reserve > dst_boot_size )); then
        die "Целевой boot-раздел слишком мал. Нужно минимум $(numfmt --to=iec $((src_boot_used + boot_reserve))), доступно $(numfmt --to=iec "$dst_boot_size")"
    fi

    if (( src_root_used + root_reserve > dst_root_size )); then
        die "Целевой root-раздел слишком мал. Нужно минимум $(numfmt --to=iec $((src_root_used + root_reserve))), доступно $(numfmt --to=iec "$dst_root_size")"
    fi

    ok "Вместимость цели достаточна для копирования по файлам"
}

rewrite_target_config() {
    local target_cmdline target_fstab tmp_fstab

    target_cmdline="$TARGET_BOOT_MNT/cmdline.txt"
    target_fstab="$TARGET_ROOT_MNT/etc/fstab"
    tmp_fstab="$WORKDIR/fstab.new"

    [[ -f "$target_cmdline" ]] || die "На цели не найден cmdline.txt"
    [[ -f "$target_fstab" ]] || die "На цели не найден /etc/fstab"

    sed -Ei "s#root=[^ ]+#root=PARTUUID=$TARGET_ROOT_PARTUUID#" "$target_cmdline"

    awk -v boot_puuid="$TARGET_BOOT_PARTUUID" -v root_puuid="$TARGET_ROOT_PARTUUID" '
        BEGIN {
            boot_done = 0
            root_done = 0
        }
        $2 == "/boot/firmware" {
            print "PARTUUID=" boot_puuid "  /boot/firmware  vfat    defaults          0       2"
            boot_done = 1
            next
        }
        $2 == "/" {
            print "PARTUUID=" root_puuid "  /               ext4    defaults,noatime  0       1"
            root_done = 1
            next
        }
        {
            print
        }
        END {
            if (!boot_done) {
                print "PARTUUID=" boot_puuid "  /boot/firmware  vfat    defaults          0       2"
            }
            if (!root_done) {
                print "PARTUUID=" root_puuid "  /               ext4    defaults,noatime  0       1"
            }
        }
    ' "$target_fstab" > "$tmp_fstab"

    mv "$tmp_fstab" "$target_fstab"
}

install_boot_network_identity_refresh() {
    local refresh_src refresh_dst service_path wants_dir

    refresh_src="$SCRIPT_DIR/refresh_network_identity.sh"
    refresh_dst="$TARGET_ROOT_MNT/usr/local/sbin/bmi30-refresh-network-identity.sh"
    service_path="$TARGET_ROOT_MNT/etc/systemd/system/bmi30-refresh-network-identity.service"
    wants_dir="$TARGET_ROOT_MNT/etc/systemd/system/multi-user.target.wants"

    if [[ ! -f "$refresh_src" ]]; then
        warn "Не найден $refresh_src, автообновление сетевой идентичности после миграции пропущено"
        return
    fi

    info "Устанавливаю постоянное обновление hostname и сетевых имен на каждом старте цели"
    mkdir -p "$(dirname "$refresh_dst")" "$(dirname "$service_path")" "$wants_dir"
    install_with_copy_stats "скрипта сетевой идентичности" "$refresh_src" 755 "$refresh_dst"

    cat > "$service_path" <<'EOF'
[Unit]
Description=Refresh BMI30 network identity on every boot
After=local-fs.target NetworkManager.service
Wants=NetworkManager.service
ConditionPathExists=/usr/local/sbin/bmi30-refresh-network-identity.sh

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bmi30-refresh-network-identity.sh

[Install]
WantedBy=multi-user.target
EOF

    ln -sf ../bmi30-refresh-network-identity.service "$wants_dir/bmi30-refresh-network-identity.service"
}

install_boot_ethernet_portal_enable() {
    local portal_src portal_dst portal_server_src portal_server_dst service_path wants_dir

    portal_src="$SCRIPT_DIR/setup_ethernet_portal.sh"
    portal_dst="$TARGET_ROOT_MNT/usr/local/sbin/bmi30-setup-ethernet-portal.sh"
    portal_server_src="$WORKSPACE_DIR/hotspot_info_server.py"
    portal_server_dst="$TARGET_ROOT_MNT/usr/local/hotspot_info_server.py"
    service_path="$TARGET_ROOT_MNT/etc/systemd/system/bmi30-enable-ethernet-portal.service"
    wants_dir="$TARGET_ROOT_MNT/etc/systemd/system/multi-user.target.wants"

    if [[ ! -f "$portal_src" ]]; then
        warn "Не найден $portal_src, автоподнятие Ethernet portal на целевой системе пропущено"
        return
    fi

    if [[ ! -f "$portal_server_src" ]]; then
        warn "Не найден $portal_server_src, автоподнятие Ethernet portal на целевой системе пропущено"
        return
    fi

    info "Устанавливаю автоподнятие Ethernet portal на каждом старте цели"
    mkdir -p "$(dirname "$portal_dst")" "$(dirname "$portal_server_dst")" "$(dirname "$service_path")" "$wants_dir"
    install_with_copy_stats "скрипта Ethernet portal" "$portal_src" 755 "$portal_dst"
    install_with_copy_stats "сервера Ethernet portal" "$portal_server_src" 755 "$portal_server_dst"

    cat > "$service_path" <<'EOF'
[Unit]
Description=Ensure BMI30 Ethernet portal is enabled on every boot
After=local-fs.target NetworkManager.service
Wants=NetworkManager.service
ConditionPathExists=/usr/local/sbin/bmi30-setup-ethernet-portal.sh

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/bmi30-setup-ethernet-portal.sh install
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    ln -sf ../bmi30-enable-ethernet-portal.service "$wants_dir/bmi30-enable-ethernet-portal.service"
}

configure_target_shared_desktop() {
    local xrdp_ini tmp_xrdp x11vnc_unit x11vnc_wants installer_script

    xrdp_ini="$TARGET_ROOT_MNT/etc/xrdp/xrdp.ini"
    tmp_xrdp="$WORKDIR/xrdp.ini.new"
    x11vnc_unit="$TARGET_ROOT_MNT/etc/systemd/system/bmi30-x11vnc.service"
    x11vnc_wants="$TARGET_ROOT_MNT/etc/systemd/system/graphical.target.wants/bmi30-x11vnc.service"
    installer_script="$TARGET_ROOT_MNT/home/techaid/Documents/install_bmi30_network_identity.sh"

    if [[ ! -f "$xrdp_ini" ]]; then
        info "XRDP не найден на целевой системе, настройку общего рабочего стола пропускаю"
        return
    fi

    # A copy must keep native/on-demand XRDP, including a transition staged on
    # a source whose old desktop is still in use. Do not resurrect its VNC bridge.
    if [[ -f "$TARGET_ROOT_MNT/var/lib/bmi30/rdp-on-demand/pending.json" ]] ||
       grep -Eq '^[[:space:]]*autorun[[:space:]]*=[[:space:]]*Xorg[[:space:]]*$' "$xrdp_ini"; then
        rm -f "$x11vnc_wants"
        ok "Сохранён XRDP по подключению пользователя; общий VNC desktop не включается"
        return
    fi

    info "Настраиваю XRDP на общий рабочий стол :0"

    awk '
        function emit_shared_desktop_section() {
            print ""
            print "[BMI30_SHARED_DESKTOP]"
            print "name=BMI30 Shared Desktop (:0)"
            print "lib=libvnc.so"
            print "ip=127.0.0.1"
            print "port=5901"
            print "username=na"
            print "password=ask"
            shared_section_done = 1
        }

        /^\[Globals\][[:space:]]*$/ {
            if (in_globals && !autorun_done) {
                print "autorun=BMI30_SHARED_DESKTOP"
                autorun_done = 1
            }
            in_globals = 1
            print
            next
        }

        /^\[BMI30_SHARED_DESKTOP\][[:space:]]*$/ {
            if (in_globals && !autorun_done) {
                print "autorun=BMI30_SHARED_DESKTOP"
                autorun_done = 1
            }
            in_globals = 0
            if (!shared_section_done) {
                emit_shared_desktop_section()
            }
            skip_section = 1
            next
        }

        /^\[[^]]+\][[:space:]]*$/ {
            if (in_globals && !autorun_done) {
                print "autorun=BMI30_SHARED_DESKTOP"
                autorun_done = 1
            }
            in_globals = 0
            if (!shared_section_done) {
                emit_shared_desktop_section()
            }
            skip_section = 0
        }

        skip_section {
            next
        }

        in_globals && /^[[:space:]]*autorun=/ {
            print "autorun=BMI30_SHARED_DESKTOP"
            autorun_done = 1
            next
        }

        {
            print
        }

        END {
            if (in_globals && !autorun_done) {
                print "autorun=BMI30_SHARED_DESKTOP"
            }
            if (!shared_section_done) {
                emit_shared_desktop_section()
            }
        }
    ' "$xrdp_ini" > "$tmp_xrdp"

    mv "$tmp_xrdp" "$xrdp_ini"

    if [[ -f "$x11vnc_unit" ]]; then
        sed -i 's/-rfbport 5900/-rfbport 5901/g' "$x11vnc_unit"
        mkdir -p "$(dirname "$x11vnc_wants")"
        ln -sf ../bmi30-x11vnc.service "$x11vnc_wants"
        ok "BMI30 shared desktop bridge включён на целевой системе (порт 5901)"
    else
        warn "Не найден $x11vnc_unit, общий рабочий стол XRDP не будет автоматически включён"
    fi

    if [[ -f "$installer_script" ]]; then
        sed -i \
            -e "s/'port=5900'/'port=5901'/g" \
            -e 's/-rfbport 5900/-rfbport 5901/g' \
            "$installer_script"
        ok "Инсталлятор на цели обновлён под порт 5901"
    fi
}

show_summary() {
    local boot_order_now=""

    boot_order_now="$(get_current_boot_order)"

    printf "\nГотово. Итоговая конфигурация цели:\n"
    if (( SYNC_ONLY == 1 )); then
        printf "  Режим:       %s -> %s (sync-only)\n" "$SOURCE_ROLE" "$TARGET_ROLE"
    else
        printf "  Режим:       %s -> %s (full copy)\n" "$SOURCE_ROLE" "$TARGET_ROLE"
    fi
    printf "  Диск:        %s\n" "$TARGET_DISK"
    printf "  Boot раздел: %s (PARTUUID=%s)\n" "$TARGET_BOOT_DEV" "$TARGET_BOOT_PARTUUID"
    printf "  Root раздел: %s (PARTUUID=%s)\n" "$TARGET_ROOT_DEV" "$TARGET_ROOT_PARTUUID"
    if [[ "$TARGET_ROLE" == "internal" ]]; then
        printf "  BMI30 identity: %s (hardware BMI30-%s)\n" \
            "$BMI30_IDENTITY_STATUS" "$BMI30_HARDWARE_SERIAL"
    fi

    if [[ "$TARGET_ROLE" == "usb" ]]; then
        printf "  BOOT_ORDER:  USB-first (%s), если EEPROM шаг не был пропущен\n" "$BOOT_ORDER_USB_FIRST"
    else
        printf "  BOOT_ORDER:  не изменялся"
        if [[ -n "$boot_order_now" ]]; then
            printf " (сейчас %s)" "$boot_order_now"
        fi
        printf "\n"
    fi

    printf "\nПосле reboot проверьте:\n"
    printf "  findmnt -no SOURCE /\n"
    printf "  findmnt -no SOURCE /boot/firmware\n"

    if [[ "$TARGET_ROLE" == "internal" && "$boot_order_now" == "$BOOT_ORDER_USB_FIRST" ]]; then
        printf "\n%b[INFO]%b EEPROM сейчас в режиме USB-first (%s).\n" "$BLU" "$NC" "$BOOT_ORDER_USB_FIRST"
        printf "Это подходит для сценария \"USB подключен -> грузимся с USB, USB отсутствует -> грузимся с eMMC\".\n"
        printf "Если нужно именно проверить запуск с eMMC, временно отключите USB-накопитель перед reboot.\n"
    fi
}

main() {
    parse_args "$@"
    require_root
    require_cmds
    detect_current_devices
    detect_source_and_target

    WORKDIR="$(mktemp -d /tmp/bmi30-disk-migrate.XXXXXX)"
    SOURCE_BOOT_MNT="$WORKDIR/source-boot"
    SOURCE_ROOT_MNT="$WORKDIR/source-root"
    TARGET_ROOT_MNT="$WORKDIR/target-root"
    TARGET_BOOT_MNT="$TARGET_ROOT_MNT/boot/firmware"
    mkdir -p "$SOURCE_BOOT_MNT" "$SOURCE_ROOT_MNT" "$TARGET_ROOT_MNT"
    trap cleanup EXIT INT TERM

    step "Проверка исходного и целевого диска"
    confirm_plan
    check_target_emmc_command_queue
    preserve_target_bmi30_identity

    step "Local project snapshot перед полным копированием"
    if (( FORCE_FORMAT_AND_RETRY == 1 )); then
        (run_precopy_backup) || warn "Local safety snapshot завершился ошибкой; продолжаю принудительное форматирование и копирование на USB"
    else
        run_precopy_backup
    fi

    step "Настройка BOOT_ORDER при необходимости"
    if (( FORCE_FORMAT_AND_RETRY == 1 )); then
        (configure_bootloader) || warn "Настройка EEPROM завершилась ошибкой; копирование на USB всё равно будет выполнено"
    else
        configure_bootloader
    fi

    if (( SYNC_ONLY == 1 )); then
        step "Проверка существующих разделов на целевом диске"
        prepare_existing_target_partitions
    else
        step "Разметка и форматирование целевого диска"
        partition_and_format_target
    fi

    step "Монтирование исходной и целевой систем"
    mount_filesystems

    step "Проверка вместимости целевой файловой системы"
    verify_target_capacity

    step "Копирование boot-раздела"
    copy_status=0
    copy_boot_files || copy_status=$?

    step "Копирование rootfs с прогрессом"
    if (( copy_status == 0 )); then
        pause_source_services
        copy_root_files || copy_status=$?
        resume_source_services
    fi

    if (( copy_status != 0 )); then
        if (( FORCE_FORMAT_AND_RETRY == 1 )); then
            retry_copy_after_fresh_format
        else
            die "Копирование завершилось с кодом $copy_status"
        fi
    fi

    step "Восстановление BMI30 identity и обновление cmdline.txt/fstab на цели"
    restore_or_initialize_target_bmi30_identity
    rewrite_target_config

    step "Подготовка автоматического восстановления связи BMI30 Agent на цели"
    prepare_target_bmi30_agent_boot

    step "Подготовка автокоррекции сетевой идентичности на целевой системе"
    install_boot_network_identity_refresh

    step "Подготовка автоподнятия Ethernet portal на целевой системе"
    install_boot_ethernet_portal_enable

    step "Сохранение выбранного режима удалённого рабочего стола на цели"
    configure_target_shared_desktop

    step "Синхронизация и отчёт"
    sync
    ok "Миграция завершена"
    show_summary
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
