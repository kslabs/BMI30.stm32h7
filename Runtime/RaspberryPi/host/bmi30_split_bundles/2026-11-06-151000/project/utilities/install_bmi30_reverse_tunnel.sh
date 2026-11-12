#!/usr/bin/env bash
# Prepare a unique BMI30 tunnel key and install the persistent systemd unit.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
SERVER_HOST="65.21.225.43"
SERVER_SSH_PORT="22"
SERVER_USER="bmi30-tunnel"
LOCAL_PORT="80"
REMOTE_PORT=""
ENABLE_SERVICE=0
SCAN_HOST_KEY=0
REMOVE_SERVICE=0
TUNNEL_USER="techaid"
CONFIG_DIR="/etc/bmi30"
CONFIG_FILE="$CONFIG_DIR/reverse_tunnel.env"
STATE_DIR="/var/lib/bmi30-reverse-tunnel"
RUNTIME_DST="/usr/local/bin/bmi30-reverse-tunnel"
UNIT_DST="/etc/systemd/system/bmi30-reverse-tunnel.service"

usage() {
    cat <<'EOF'
Usage:
  sudo ./utilities/install_bmi30_reverse_tunnel.sh [options]

Options:
  --server HOST          Tunnel server (default: 65.21.225.43)
  --ssh-port PORT        Server SSH port (default: 22)
  --ssh-user USER        Restricted server account (default: bmi30-tunnel)
  --remote-port PORT     Public portal port; default is stable from device ID
  --local-port PORT      Local BMI30 Portal port (default: 80)
  --scan-host-key        Pin the currently advertised server SSH host key
  --enable               Enable and start the service after installation
  --remove               Disable/remove service and config; preserve SSH keys
  -h, --help             Show this help

Safe enrollment sequence:
  1. Run without --enable to generate a unique device key and enrollment data.
  2. Add the printed public key and exact REMOTE_PORT on the server.
  3. Re-run with --scan-host-key --enable after verifying the server fingerprint.
EOF
}

fail() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

info() {
    printf '[INFO] %s\n' "$*"
}

require_root() {
    [[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Run with sudo"
}

require_value() {
    [[ $# -ge 2 && -n "${2:-}" ]] || fail "Missing value after $1"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --server)
                require_value "$@"
                SERVER_HOST="$2"
                shift 2
                ;;
            --ssh-port)
                require_value "$@"
                SERVER_SSH_PORT="$2"
                shift 2
                ;;
            --ssh-user)
                require_value "$@"
                SERVER_USER="$2"
                shift 2
                ;;
            --remote-port)
                require_value "$@"
                REMOTE_PORT="$2"
                shift 2
                ;;
            --local-port)
                require_value "$@"
                LOCAL_PORT="$2"
                shift 2
                ;;
            --scan-host-key)
                SCAN_HOST_KEY=1
                shift
                ;;
            --enable)
                ENABLE_SERVICE=1
                shift
                ;;
            --remove)
                REMOVE_SERVICE=1
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                fail "Unknown option: $1"
                ;;
        esac
    done
}

detect_device_id() {
    local serial="" host=""
    if [[ -r /proc/device-tree/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /proc/device-tree/serial-number || true)"
    elif [[ -r /sys/firmware/devicetree/base/serial-number ]]; then
        serial="$(tr -d '\000\r\n ' < /sys/firmware/devicetree/base/serial-number || true)"
    fi
    serial="$(printf '%s' "$serial" | tr -cd '0-9A-Fa-f')"
    if [[ -n "$serial" ]]; then
        printf 'BMI30-%s' "${serial^^}"
        return
    fi
    host="$(hostname -s 2>/dev/null | tr -cd 'A-Za-z0-9._-' || true)"
    [[ -n "$host" ]] || host="unknown"
    printf '%s' "$host"
}

default_remote_port() {
    local device_id="$1" digest numeric
    digest="$(printf '%s' "$device_id" | sha256sum | awk '{print substr($1, 1, 8)}')"
    numeric=$((16#$digest))
    printf '%d' "$((20000 + (numeric % 20000)))"
}

validate_settings() {
    [[ "$SERVER_HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || fail "Invalid server host"
    [[ "$SERVER_USER" =~ ^[A-Za-z_][A-Za-z0-9._-]*$ ]] || fail "Invalid SSH user"
    [[ "$SERVER_SSH_PORT" =~ ^[0-9]+$ ]] && (( SERVER_SSH_PORT >= 1 && SERVER_SSH_PORT <= 65535 )) \
        || fail "Invalid SSH port"
    [[ "$LOCAL_PORT" =~ ^[0-9]+$ ]] && (( LOCAL_PORT >= 1 && LOCAL_PORT <= 65535 )) \
        || fail "Invalid local port"
    [[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] && (( REMOTE_PORT >= 20000 && REMOTE_PORT <= 39999 )) \
        || fail "Remote port must be in 20000..39999"
    id "$TUNNEL_USER" >/dev/null 2>&1 || fail "Local user does not exist: $TUNNEL_USER"
    command -v ssh >/dev/null 2>&1 || fail "ssh is not installed"
    command -v ssh-keygen >/dev/null 2>&1 || fail "ssh-keygen is not installed"
}

remove_service() {
    systemctl disable --now bmi30-reverse-tunnel.service >/dev/null 2>&1 || true
    rm -f -- "$UNIT_DST" "$RUNTIME_DST" "$CONFIG_FILE"
    systemctl daemon-reload
    info "Tunnel service and config removed; device SSH key was preserved"
}

main() {
    parse_args "$@"
    require_root
    if (( REMOVE_SERVICE == 1 )); then
        remove_service
        return
    fi

    local device_id user_home key_file public_key_file known_hosts_file owner_group temp_config
    device_id="$(detect_device_id)"
    [[ -n "$REMOTE_PORT" ]] || REMOTE_PORT="$(default_remote_port "$device_id")"
    validate_settings

    user_home="$(getent passwd "$TUNNEL_USER" | awk -F: '{print $6}')"
    [[ -n "$user_home" && -d "$user_home" ]] || fail "Home directory not found for $TUNNEL_USER"
    owner_group="$(id -gn "$TUNNEL_USER")"
    key_file="$user_home/.ssh/id_ed25519_bmi30_tunnel"
    public_key_file="$key_file.pub"
    known_hosts_file="$user_home/.ssh/known_hosts_bmi30_tunnel"

    install -d -m 0700 -o "$TUNNEL_USER" -g "$owner_group" "$user_home/.ssh"
    if [[ ! -f "$key_file" ]]; then
        runuser -u "$TUNNEL_USER" -- ssh-keygen \
            -q -t ed25519 -N '' -C "${device_id}@bmi30-tunnel" -f "$key_file"
        info "Generated a unique tunnel key: $key_file"
    fi
    chown "$TUNNEL_USER:$owner_group" "$key_file" "$public_key_file"
    chmod 0600 "$key_file"
    chmod 0644 "$public_key_file"

    if (( SCAN_HOST_KEY == 1 )); then
        local scanned_host_keys
        scanned_host_keys="$(mktemp)"
        trap 'rm -f -- "${scanned_host_keys:-}" "${temp_config:-}"' EXIT
        ssh-keyscan -T 10 -p "$SERVER_SSH_PORT" "$SERVER_HOST" > "$scanned_host_keys" 2>/dev/null \
            || fail "Server SSH host key is unavailable: $SERVER_HOST:$SERVER_SSH_PORT"
        [[ -s "$scanned_host_keys" ]] || fail "Server returned no SSH host keys"
        install -m 0644 -o "$TUNNEL_USER" -g "$owner_group" "$scanned_host_keys" "$known_hosts_file"
        info "Pinned the advertised SSH host key; compare its fingerprint with the server report"
        ssh-keygen -lf "$known_hosts_file"
    fi

    install -d -m 0755 "$CONFIG_DIR" "$STATE_DIR"
    temp_config="$(mktemp)"
    {
        printf 'SERVER_HOST=%q\n' "$SERVER_HOST"
        printf 'SERVER_SSH_PORT=%q\n' "$SERVER_SSH_PORT"
        printf 'SERVER_USER=%q\n' "$SERVER_USER"
        printf 'REMOTE_PORT=%q\n' "$REMOTE_PORT"
        printf 'LOCAL_PORT=%q\n' "$LOCAL_PORT"
        printf 'IDENTITY_FILE=%q\n' "$key_file"
        printf 'KNOWN_HOSTS_FILE=%q\n' "$known_hosts_file"
        printf 'DEVICE_ID=%q\n' "$device_id"
    } > "$temp_config"
    install -m 0644 "$temp_config" "$CONFIG_FILE"
    install -m 0755 "$SCRIPT_DIR/bmi30_reverse_tunnel.sh" "$RUNTIME_DST"
    install -m 0644 "$SCRIPT_DIR/bmi30-reverse-tunnel.service" "$UNIT_DST"
    rm -f -- "$temp_config"
    temp_config=""

    {
        printf 'DEVICE_ID=%q\n' "$device_id"
        printf 'REMOTE_PORT=%q\n' "$REMOTE_PORT"
        printf 'SERVER_HOST=%q\n' "$SERVER_HOST"
        printf 'SERVER_USER=%q\n' "$SERVER_USER"
        printf 'PUBLIC_KEY=%q\n' "$(<"$public_key_file")"
    } > "$STATE_DIR/enrollment.env"
    chmod 0644 "$STATE_DIR/enrollment.env"
    systemctl daemon-reload

    printf '\n=== BMI30 tunnel enrollment ===\n'
    printf 'DEVICE_ID=%s\n' "$device_id"
    printf 'REMOTE_PORT=%s\n' "$REMOTE_PORT"
    printf 'PUBLIC_URL=http://%s:%s/\n' "$SERVER_HOST" "$REMOTE_PORT"
    printf 'PUBLIC_KEY=%s\n' "$(<"$public_key_file")"
    printf 'SERVER_AUTHORIZED_KEY_OPTIONS=restrict,port-forwarding,permitlisten="0.0.0.0:%s"\n' "$REMOTE_PORT"

    if (( ENABLE_SERVICE == 1 )); then
        [[ -s "$known_hosts_file" ]] \
            || fail "Use --scan-host-key after server SSH is ready, then verify the fingerprint"
        systemctl enable --now bmi30-reverse-tunnel.service
        info "Tunnel service enabled; it will reconnect automatically"
    else
        systemctl disable --now bmi30-reverse-tunnel.service >/dev/null 2>&1 || true
        info "Enrollment prepared. Add the public key on the server, then re-run with --scan-host-key --enable"
    fi
}

main "$@"
