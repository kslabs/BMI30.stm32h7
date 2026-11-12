#!/usr/bin/env bash
# Install the versioned BMI30 Agent carried by a cloud firmware archive.
# Device credentials stay local; they are never taken from another Raspberry.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="${BMI30_AGENT_PROJECT_ROOT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
BACKUP_ROOT="${BMI30_AGENT_BACKUP_ROOT:-/var/backups/bmi30-agent}"
CHECK_ONLY=0
PACKAGE_ROOT=""
EXTRACT_DIR=""

log() {
    printf '[BMI30 Agent] %s\n' "$*"
}

warn() {
    printf '[BMI30 Agent][WARN] %s\n' "$*" >&2
}

fail() {
    printf '[BMI30 Agent][ERROR] %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$EXTRACT_DIR" && -d "$EXTRACT_DIR" ]]; then
        rm -rf -- "$EXTRACT_DIR"
    fi
}

trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage:
  sudo ./utilities/install_bmi30_agent_from_project.sh
  ./utilities/install_bmi30_agent_from_project.sh --check

Options:
  --check               Validate the bundled Agent without changing the system.
  --project-root PATH   Use another extracted project root (test/recovery only).
EOF
}

while (($#)); do
    case "$1" in
        --check)
            CHECK_ONLY=1
            shift
            ;;
        --project-root)
            [[ $# -ge 2 ]] || fail "После --project-root нужен путь"
            PROJECT_ROOT="$(cd -- "$2" && pwd)"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Неизвестный параметр: $1"
            ;;
    esac
done

[[ -d "$PROJECT_ROOT" ]] || fail "Корень проекта не найден: $PROJECT_ROOT"
command -v python3 >/dev/null 2>&1 || fail "python3 не установлен"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum не установлен"

read_package_version() {
    local version_file="$PROJECT_ROOT/host/BMI30_Agent/VERSION"
    [[ -f "$version_file" ]] || return 1
    tr -d '\r\n[:space:]' < "$version_file"
}

validate_package_tree() {
    local root="$1"
    local version="$2"
    [[ -f "$root/SHA256SUMS.txt" ]] || return 1
    [[ -x "$root/install_bmi30_agent.sh" ]] || return 1
    [[ -f "$root/src/bmi30_agent.py" ]] || return 1
    [[ -f "$root/systemd/bmi30-agent.service" ]] || return 1
    [[ -f "$root/systemd/bmi30-tunnel.service" ]] || return 1
    (cd "$root" && sha256sum -c SHA256SUMS.txt >/dev/null 2>&1) || return 1
    grep -Fqx "AGENT_VERSION = \"$version\"" "$root/src/bmi30_agent.py" || return 1
    grep -Fqx "PACKAGE_VERSION=\"$version\"" "$root/install_bmi30_agent.sh" || return 1
    grep -Eq '^ReadWritePaths=.*(^|[[:space:]])/var/backups/bmi30-agent([[:space:]]|$)' \
        "$root/systemd/bmi30-agent.service" || return 1
}

extract_package_archive() {
    local archive="$1"
    EXTRACT_DIR="$(mktemp -d /tmp/bmi30-agent-cloud.XXXXXXXX)"
    python3 - "$archive" "$EXTRACT_DIR" <<'PY'
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with zipfile.ZipFile(archive) as handle:
    for item in handle.infolist():
        member = PurePosixPath(item.filename)
        if member.is_absolute() or ".." in member.parts:
            raise SystemExit(f"unsafe archive member: {item.filename}")
        if not member.parts or member.parts[0] != "BMI30_Agent":
            raise SystemExit(f"unexpected archive root: {item.filename}")
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise SystemExit(f"symlink is not permitted in package: {item.filename}")
    handle.extractall(destination)
    for item in handle.infolist():
        mode = (item.external_attr >> 16) & 0o777
        target = destination / PurePosixPath(item.filename)
        if mode and target.exists():
            target.chmod(mode)
PY
    PACKAGE_ROOT="$EXTRACT_DIR/BMI30_Agent"
}

PACKAGE_VERSION="$(read_package_version || true)"
[[ "$PACKAGE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "Некорректная версия Agent в проекте: ${PACKAGE_VERSION:-<empty>}"

EXPANDED_PACKAGE="$PROJECT_ROOT/host/BMI30_Agent"
PACKAGE_ARCHIVE="$PROJECT_ROOT/host/BMI30_Agent_${PACKAGE_VERSION}.zip"
if validate_package_tree "$EXPANDED_PACKAGE" "$PACKAGE_VERSION"; then
    PACKAGE_ROOT="$EXPANDED_PACKAGE"
elif [[ -f "$PACKAGE_ARCHIVE" ]]; then
    warn "Развёрнутый package неполон; использую целостный ZIP для совместимости со старым updater"
    extract_package_archive "$PACKAGE_ARCHIVE"
    validate_package_tree "$PACKAGE_ROOT" "$PACKAGE_VERSION" \
        || fail "Проверка SHA-256 пакета из ZIP не прошла: $PACKAGE_ARCHIVE"
else
    fail "Проверенный пакет BMI30 Agent $PACKAGE_VERSION не найден"
fi

log "Пакет BMI30 Agent $PACKAGE_VERSION проверен: $PACKAGE_ROOT"
if (( CHECK_ONLY == 1 )); then
    exit 0
fi

(( EUID == 0 )) || fail "Установка Agent должна выполняться через sudo/root"
for command_name in systemctl ssh-keygen install cp find stat; do
    command -v "$command_name" >/dev/null 2>&1 \
        || fail "Не найдена обязательная команда: $command_name"
done

raspberry_serial() {
    local serial=""
    if [[ -r /proc/device-tree/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /proc/device-tree/serial-number || true)"
    fi
    if [[ -z "$serial" && -r /sys/firmware/devicetree/base/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /sys/firmware/devicetree/base/serial-number || true)"
    fi
    if [[ -z "$serial" && -r /proc/cpuinfo ]]; then
        serial="$(awk -F: 'tolower($1) ~ /^serial/ {gsub(/[[:space:]]/, "", $2); print $2; exit}' /proc/cpuinfo)"
    fi
    serial="$(printf '%s' "$serial" | tr '[:lower:]' '[:upper:]' | tr -cd '0-9A-F')"
    [[ "$serial" =~ ^[0-9A-F]{16}$ ]] || return 1
    printf '%s' "$serial"
}

backup_file() {
    local source="$1"
    local relative="$2"
    local backup_dir="$3"
    [[ -e "$source" || -L "$source" ]] || return 0
    install -d -m 0700 "$backup_dir/$(dirname -- "$relative")"
    cp -a -- "$source" "$backup_dir/$relative"
}

create_preinstall_backup() {
    local backup_dir
    install -d -m 0700 "$BACKUP_ROOT"
    backup_dir="$BACKUP_ROOT/cloud-update-$(date -u +%Y%m%d_%H%M%S)-$$"
    install -d -m 0700 "$backup_dir"
    backup_file /etc/bmi30-agent/config.json etc/bmi30-agent/config.json "$backup_dir"
    backup_file /etc/bmi30-agent/id_ed25519 etc/bmi30-agent/id_ed25519 "$backup_dir"
    backup_file /etc/bmi30-agent/id_ed25519.pub etc/bmi30-agent/id_ed25519.pub "$backup_dir"
    backup_file /etc/bmi30-agent/known_hosts etc/bmi30-agent/known_hosts "$backup_dir"
    backup_file /etc/bmi30-agent/tunnel.env etc/bmi30-agent/tunnel.env "$backup_dir"
    backup_file /var/lib/bmi30-agent/device_api_token var/lib/bmi30-agent/device_api_token "$backup_dir"
    backup_file /var/lib/bmi30-agent/state.json var/lib/bmi30-agent/state.json "$backup_dir"
    backup_file /opt/bmi30-agent/bmi30_agent.py opt/bmi30-agent/bmi30_agent.py "$backup_dir"
    backup_file /opt/bmi30-agent/run_bmi30_tunnel.sh opt/bmi30-agent/run_bmi30_tunnel.sh "$backup_dir"
    backup_file /etc/systemd/system/bmi30-agent.service etc/systemd/system/bmi30-agent.service "$backup_dir"
    backup_file /etc/systemd/system/bmi30-tunnel.service etc/systemd/system/bmi30-tunnel.service "$backup_dir"
    {
        printf 'BMI30 Agent cloud update backup\n'
        printf 'UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'TARGET_VERSION=%s\n' "$PACKAGE_VERSION"
        printf 'SECRETS_COPIED_TO_CLOUD=NO\n'
    } > "$backup_dir/AUDIT.txt"
    chmod 0600 "$backup_dir/AUDIT.txt"
    printf '%s' "$backup_dir"
}

public_key_device_id() {
    local path="$1"
    [[ -f "$path" ]] || return 0
    awk 'NF >= 3 && $3 ~ /^BMI30-[0-9A-F]{16}@bmi30-tunnel$/ {sub(/@bmi30-tunnel$/, "", $3); print $3; exit}' "$path"
}

state_is_approved() {
    local path="$1"
    [[ -f "$path" ]] || return 1
    python3 - "$path" <<'PY' >/dev/null 2>&1
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
raise SystemExit(0 if value.get("server_state") == "approved" else 1)
PY
}

token_is_valid() {
    local path="$1"
    [[ -f "$path" ]] || return 1
    python3 - "$path" <<'PY' >/dev/null 2>&1
from pathlib import Path
import sys
token = Path(sys.argv[1]).read_text(encoding="ascii").strip()
raise SystemExit(0 if 40 <= len(token) <= 512 and not any(ch.isspace() for ch in token) else 1)
PY
}

key_pair_is_valid() {
    local private_key="$1"
    local public_key="$2"
    local private_fp public_fp
    [[ -f "$private_key" && -f "$public_key" ]] || return 1
    private_fp="$(ssh-keygen -y -f "$private_key" 2>/dev/null | ssh-keygen -lf - -E sha256 2>/dev/null | awk '{print $2}')"
    public_fp="$(ssh-keygen -lf "$public_key" -E sha256 2>/dev/null | awk '{print $2}')"
    [[ -n "$private_fp" && "$private_fp" == "$public_fp" ]]
}

find_approved_identity_backup() {
    local hardware_id="$1"
    local public_key candidate_root private_key token state mtime
    local best_root="" best_mtime=-1
    [[ -d "$BACKUP_ROOT" ]] || return 1
    while IFS= read -r -d '' public_key; do
        [[ "$(public_key_device_id "$public_key")" == "$hardware_id" ]] || continue
        candidate_root="${public_key%/etc/bmi30-agent/id_ed25519.pub}"
        private_key="$candidate_root/etc/bmi30-agent/id_ed25519"
        token="$candidate_root/var/lib/bmi30-agent/device_api_token"
        state="$candidate_root/var/lib/bmi30-agent/state.json"
        key_pair_is_valid "$private_key" "$public_key" || continue
        token_is_valid "$token" || continue
        state_is_approved "$state" || continue
        mtime="$(stat -c '%Y' "$public_key" 2>/dev/null || printf '0')"
        if [[ "$mtime" =~ ^[0-9]+$ ]] && (( mtime > best_mtime )); then
            best_mtime="$mtime"
            best_root="$candidate_root"
        fi
    done < <(find "$BACKUP_ROOT" -type f -path '*/etc/bmi30-agent/id_ed25519.pub' -print0 2>/dev/null)
    [[ -n "$best_root" ]] || return 1
    printf '%s' "$best_root"
}

restore_approved_identity_if_needed() {
    local hardware_id="$1"
    local current_id current_approved=0 candidate
    current_id="$(public_key_device_id /etc/bmi30-agent/id_ed25519.pub)"
    if [[ "$current_id" == "$hardware_id" ]] \
        && state_is_approved /var/lib/bmi30-agent/state.json
    then
        current_approved=1
    fi
    (( current_approved == 0 )) || return 0

    candidate="$(find_approved_identity_backup "$hardware_id" || true)"
    [[ -n "$candidate" ]] || return 0
    log "Восстанавливаю локальные ранее approved credentials для $hardware_id из $candidate"
    install -d -m 0700 /etc/bmi30-agent /var/lib/bmi30-agent
    install -m 0600 -o root -g root "$candidate/etc/bmi30-agent/id_ed25519" /etc/bmi30-agent/id_ed25519
    install -m 0644 -o root -g root "$candidate/etc/bmi30-agent/id_ed25519.pub" /etc/bmi30-agent/id_ed25519.pub
    install -m 0600 -o root -g root "$candidate/var/lib/bmi30-agent/device_api_token" /var/lib/bmi30-agent/device_api_token
    if [[ -f "$candidate/etc/bmi30-agent/known_hosts" ]]; then
        install -m 0644 -o root -g root "$candidate/etc/bmi30-agent/known_hosts" /etc/bmi30-agent/known_hosts
    fi
}

installed_package_matches() {
    cmp -s "$PACKAGE_ROOT/src/bmi30_agent.py" /opt/bmi30-agent/bmi30_agent.py \
        && cmp -s "$PACKAGE_ROOT/src/run_bmi30_tunnel.sh" /opt/bmi30-agent/run_bmi30_tunnel.sh \
        && cmp -s "$PACKAGE_ROOT/src/bmi30-agent-ctl" /usr/local/sbin/bmi30-agent-ctl \
        && cmp -s "$PACKAGE_ROOT/systemd/bmi30-agent.service" /etc/systemd/system/bmi30-agent.service \
        && cmp -s "$PACKAGE_ROOT/systemd/bmi30-tunnel.service" /etc/systemd/system/bmi30-tunnel.service
}

HARDWARE_SERIAL="$(raspberry_serial || true)"
[[ -n "$HARDWARE_SERIAL" ]] || fail "Не удалось прочитать реальный CPU Serial Raspberry"
HARDWARE_ID="BMI30-$HARDWARE_SERIAL"
PREINSTALL_BACKUP="$(create_preinstall_backup)"
log "Локальная резервная копия перед обновлением: $PREINSTALL_BACKUP"
restore_approved_identity_if_needed "$HARDWARE_ID"

if installed_package_matches; then
    log "Agent $PACKAGE_VERSION уже установлен; credentials и сервис будут только перепроверены"
    systemctl daemon-reload
else
    log "Устанавливаю Agent $PACKAGE_VERSION без переноса credentials между устройствами"
    "$PACKAGE_ROOT/install_bmi30_agent.sh"
fi

systemctl enable bmi30-agent.service >/dev/null
systemctl restart bmi30-agent.service

effective_paths="$(systemctl show bmi30-agent.service -p ReadWritePaths --value --no-pager)"
[[ " $effective_paths " == *" /var/backups/bmi30-agent "* ]] \
    || fail "Установленный service не разрешает запись в /var/backups/bmi30-agent"
[[ "$(systemctl is-active bmi30-agent.service 2>/dev/null || true)" == "active" ]] \
    || fail "bmi30-agent.service не запустился"

agent_state=""
agent_http_status=""
agent_remote_port=""
for _ in {1..15}; do
    if [[ -f /var/lib/bmi30-agent/state.json ]]; then
        read -r agent_state agent_http_status agent_remote_port < <(
            python3 - /var/lib/bmi30-agent/state.json <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        value = json.load(handle)
except Exception:
    value = {}
print(value.get("server_state", ""), value.get("http_status", ""), value.get("remote_port", ""))
PY
        )
        [[ -n "$agent_state" ]] && break
    fi
    sleep 1
done

case "$agent_state" in
    approved)
        log "Hub approved; reverse tunnel port: ${agent_remote_port:-unknown}"
        ;;
    pending)
        warn "Agent зарегистрирован на Hub и ожидает одобрения; firmware update завершится штатно"
        ;;
    http_error)
        if [[ "$agent_http_status" == "401" || "$agent_http_status" == "409" ]]; then
            fail "Hub отклонил локальные credentials: HTTP $agent_http_status"
        fi
        warn "Agent установлен и будет повторять check-in; последний HTTP: ${agent_http_status:-unknown}"
        ;;
    blocked|rejected)
        fail "Hub вернул состояние Agent: $agent_state"
        ;;
    *)
        warn "Agent активен; первый check-in ещё не завершён"
        ;;
esac

log "Agent $PACKAGE_VERSION установлен для $HARDWARE_ID; локальные credentials сохранены"
