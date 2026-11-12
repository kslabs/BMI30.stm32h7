#!/bin/bash
set -euo pipefail

PACKAGE_VERSION="0.2.7"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER_URL="https://www.teiots.net/bmi30"
LOCAL_PORT=80
ENABLE_AGENT=0
RESET_IDENTITY=0

usage() {
    cat <<EOF
BMI30 Agent installer ${PACKAGE_VERSION}

Usage:
  sudo ./install_bmi30_agent.sh [options]

Options:
  --server URL          Production Hub URL (must be ${SERVER_URL})
  --local-port PORT     Local BMI30 HTTP port (default: ${LOCAL_PORT})
  --enable-agent        Run one manual check-in, then enable/start the agent
  --reset-identity      Explicitly replace this device's BMI30 key and API token
  -h, --help            Show this help

The obsolete bmi30-reverse-tunnel.service and its dedicated files are backed
up and removed.  Production uses bmi30-tunnel.service only.
EOF
}

while (($#)); do
    case "$1" in
        --server)
            SERVER_URL=${2:?missing value for --server}
            shift 2
            ;;
        --local-port)
            LOCAL_PORT=${2:?missing value for --local-port}
            shift 2
            ;;
        --enable-agent)
            ENABLE_AGENT=1
            shift
            ;;
        --reset-identity)
            RESET_IDENTITY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ((EUID != 0)); then
    echo "Run this installer with sudo/root." >&2
    exit 1
fi

for command_name in python3 ssh ssh-keygen systemctl awk install sha256sum; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Required command is missing: $command_name" >&2
        exit 1
    fi
done

if [[ "$SERVER_URL" != "https://www.teiots.net/bmi30" ]]; then
    echo "Production server must be https://www.teiots.net/bmi30" >&2
    exit 2
fi
if ! [[ "$LOCAL_PORT" =~ ^[0-9]+$ ]] || ((LOCAL_PORT < 1 || LOCAL_PORT > 65535)); then
    echo "Invalid --local-port: $LOCAL_PORT" >&2
    exit 2
fi

CONFIG_DIR=/etc/bmi30-agent
CONFIG_FILE="$CONFIG_DIR/config.json"
PRIVATE_KEY="$CONFIG_DIR/id_ed25519"
PUBLIC_KEY="$PRIVATE_KEY.pub"
KNOWN_HOSTS="$CONFIG_DIR/known_hosts"
TUNNEL_ENV="$CONFIG_DIR/tunnel.env"
STATE_DIR=/var/lib/bmi30-agent
STATE_FILE="$STATE_DIR/state.json"
TOKEN_FILE="$STATE_DIR/device_api_token"
BOUND_SERIAL_FILE="$STATE_DIR/bound_raspberry_serial"
IDENTITY_LOCK_FILE="$STATE_DIR/identity.lock"
INSTALL_DIR=/opt/bmi30-agent
AGENT_CTL=/usr/local/sbin/bmi30-agent-ctl
AGENT_UNIT=/etc/systemd/system/bmi30-agent.service
TUNNEL_UNIT=/etc/systemd/system/bmi30-tunnel.service
LEGACY_TUNNEL_UNIT=/etc/systemd/system/bmi30-reverse-tunnel.service
LEGACY_TUNNEL_WRAPPER=/usr/local/bin/bmi30-reverse-tunnel
LEGACY_TUNNEL_CONFIG=/etc/bmi30/reverse_tunnel.env
LEGACY_PRIVATE_KEY=/home/techaid/.ssh/id_ed25519_bmi30_tunnel
LEGACY_PUBLIC_KEY=/home/techaid/.ssh/id_ed25519_bmi30_tunnel.pub
LEGACY_KNOWN_HOSTS=/home/techaid/.ssh/known_hosts_bmi30_tunnel
BACKUP_ROOT=/var/backups/bmi30-agent
STAMP="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

install -d -m 0700 "$BACKUP_ROOT" "$BACKUP_DIR"

backup_if_exists() {
    local source_path=$1
    local relative_name=$2
    if [[ -e "$source_path" || -L "$source_path" ]]; then
        mkdir -p "$(dirname "$BACKUP_DIR/$relative_name")"
        cp -a -- "$source_path" "$BACKUP_DIR/$relative_name"
    fi
}

backup_if_exists "$CONFIG_FILE" etc/bmi30-agent/config.json
backup_if_exists "$PRIVATE_KEY" etc/bmi30-agent/id_ed25519
backup_if_exists "$PUBLIC_KEY" etc/bmi30-agent/id_ed25519.pub
backup_if_exists "$KNOWN_HOSTS" etc/bmi30-agent/known_hosts
backup_if_exists "$TUNNEL_ENV" etc/bmi30-agent/tunnel.env
backup_if_exists "$STATE_FILE" var/lib/bmi30-agent/state.json
backup_if_exists "$TOKEN_FILE" var/lib/bmi30-agent/device_api_token
backup_if_exists "$BOUND_SERIAL_FILE" var/lib/bmi30-agent/bound_raspberry_serial
backup_if_exists "$INSTALL_DIR/bmi30_agent.py" opt/bmi30-agent/bmi30_agent.py
backup_if_exists "$INSTALL_DIR/run_bmi30_tunnel.sh" opt/bmi30-agent/run_bmi30_tunnel.sh
backup_if_exists "$INSTALL_DIR/VERSION" opt/bmi30-agent/VERSION
backup_if_exists "$AGENT_CTL" usr/local/sbin/bmi30-agent-ctl
backup_if_exists "$AGENT_UNIT" etc/systemd/system/bmi30-agent.service
backup_if_exists "$TUNNEL_UNIT" etc/systemd/system/bmi30-tunnel.service
backup_if_exists "$LEGACY_TUNNEL_UNIT" etc/systemd/system/bmi30-reverse-tunnel.service
backup_if_exists "$LEGACY_TUNNEL_WRAPPER" usr/local/bin/bmi30-reverse-tunnel
backup_if_exists "$LEGACY_TUNNEL_CONFIG" etc/bmi30/reverse_tunnel.env
backup_if_exists "$LEGACY_PRIVATE_KEY" home/techaid/.ssh/id_ed25519_bmi30_tunnel
backup_if_exists "$LEGACY_PUBLIC_KEY" home/techaid/.ssh/id_ed25519_bmi30_tunnel.pub
backup_if_exists "$LEGACY_KNOWN_HOSTS" home/techaid/.ssh/known_hosts_bmi30_tunnel

{
    echo "BMI30 Agent pre-change audit"
    echo "UTC=$STAMP"
    echo
    systemctl --no-pager --full status \
        bmi30-agent.service bmi30-tunnel.service bmi30-reverse-tunnel.service 2>&1 || true
    echo
    systemctl cat \
        bmi30-agent.service bmi30-tunnel.service bmi30-reverse-tunnel.service 2>&1 || true
    echo
    sha256sum \
        "$CONFIG_FILE" "$INSTALL_DIR/bmi30_agent.py" "$INSTALL_DIR/run_bmi30_tunnel.sh" \
        "$AGENT_CTL" "$AGENT_UNIT" "$TUNNEL_UNIT" 2>/dev/null || true
} >"$BACKUP_DIR/AUDIT.txt"
chmod 0600 "$BACKUP_DIR/AUDIT.txt"

RPI_SERIAL=""
if [[ -r /proc/cpuinfo ]]; then
    RPI_SERIAL="$(awk -F: 'tolower($1) ~ /^serial/ {gsub(/[[:space:]]/, "", $2); print toupper($2); exit}' /proc/cpuinfo)"
fi
if [[ "$RPI_SERIAL" =~ ^[0-9A-F]{16}$ ]]; then
    DEVICE_ID="BMI30-$RPI_SERIAL"
else
    echo "Raspberry CPU serial is unavailable; refusing a saved or copied DEVICE_ID." >&2
    exit 1
fi

OLD_DEVICE_ID=""
if [[ -f "$PUBLIC_KEY" ]]; then
    OLD_DEVICE_ID="$(awk 'NF >= 3 && $3 ~ /^BMI30-[0-9A-F]{16}@bmi30-tunnel$/ {sub(/@bmi30-tunnel$/, "", $3); print $3; exit}' "$PUBLIC_KEY")"
elif [[ -f "$CONFIG_FILE" ]]; then
    OLD_DEVICE_ID="$(python3 - "$CONFIG_FILE" <<'PY' 2>/dev/null || true
import json, re, sys
try:
    value = str(json.load(open(sys.argv[1], encoding='utf-8')).get('device_id', ''))
except Exception:
    value = ''
print(value if re.fullmatch(r'BMI30-[A-F0-9]{16}', value) else '')
PY
)"
fi

if [[ -n "$OLD_DEVICE_ID" && "$OLD_DEVICE_ID" != "$DEVICE_ID" ]]; then
    RESET_IDENTITY=1
    echo "Cloned identity detected: saved DEVICE_ID does not match this Raspberry." >&2
fi

if [[ -f "$BOUND_SERIAL_FILE" ]]; then
    BOUND_RPI_SERIAL="$(tr -d '[:space:]' <"$BOUND_SERIAL_FILE" | tr '[:lower:]' '[:upper:]')"
    if [[ ! "$BOUND_RPI_SERIAL" =~ ^[0-9A-F]{16}$ || "$BOUND_RPI_SERIAL" != "$RPI_SERIAL" ]]; then
        RESET_IDENTITY=1
        echo "Cloned identity detected: saved Raspberry binding does not match this board." >&2
    fi
fi

# Replace any prior BMI30 agent and remove the obsolete reverse-tunnel
# implementation after its files have been captured in BACKUP_DIR.  BMI20
# services, keys, and configuration use different names and are not touched.
systemctl disable --now bmi30-agent.service >/dev/null 2>&1 || true
systemctl stop bmi30-tunnel.service >/dev/null 2>&1 || true
systemctl disable --now bmi30-reverse-tunnel.service >/dev/null 2>&1 || true
rm -f -- \
    "$LEGACY_TUNNEL_UNIT" \
    "$LEGACY_TUNNEL_WRAPPER" \
    "$LEGACY_TUNNEL_CONFIG" \
    "$LEGACY_PRIVATE_KEY" \
    "$LEGACY_PUBLIC_KEY" \
    "$LEGACY_KNOWN_HOSTS"

install -d -m 0700 -o root -g root "$CONFIG_DIR" "$STATE_DIR"
install -d -m 0755 -o root -g root "$INSTALL_DIR"

if ((RESET_IDENTITY == 1)); then
    rm -f -- "$PRIVATE_KEY" "$PUBLIC_KEY" "$TOKEN_FILE" "$TUNNEL_ENV" "$STATE_FILE" "$BOUND_SERIAL_FILE"
fi

if [[ -f "$PRIVATE_KEY" && ! -f "$PUBLIC_KEY" ]]; then
    KEY_DATA="$(ssh-keygen -y -f "$PRIVATE_KEY")"
    printf '%s %s@bmi30-tunnel\n' "$KEY_DATA" "$DEVICE_ID" >"$PUBLIC_KEY.tmp"
    mv "$PUBLIC_KEY.tmp" "$PUBLIC_KEY"
fi
if [[ ! -f "$PRIVATE_KEY" ]]; then
    ssh-keygen -q -t ed25519 -N '' -f "$PRIVATE_KEY" -C "$DEVICE_ID@bmi30-tunnel"
fi
if [[ ! -f "$PUBLIC_KEY" ]]; then
    KEY_DATA="$(ssh-keygen -y -f "$PRIVATE_KEY")"
    printf '%s %s@bmi30-tunnel\n' "$KEY_DATA" "$DEVICE_ID" >"$PUBLIC_KEY"
fi

KEY_TYPE="$(awk '{print $1}' "$PUBLIC_KEY")"
KEY_DATA="$(awk '{print $2}' "$PUBLIC_KEY")"
KEY_COMMENT="$(awk 'NF >= 3 {print $3; exit}' "$PUBLIC_KEY")"
if [[ "$KEY_TYPE" != "ssh-ed25519" || -z "$KEY_DATA" || "$KEY_COMMENT" != "$DEVICE_ID@bmi30-tunnel" ]]; then
    echo "Existing BMI30 key does not match $DEVICE_ID; use --reset-identity after reviewing the backup." >&2
    exit 1
fi
printf '%s %s %s@bmi30-tunnel\n' "$KEY_TYPE" "$KEY_DATA" "$DEVICE_ID" >"$PUBLIC_KEY.tmp"
mv "$PUBLIC_KEY.tmp" "$PUBLIC_KEY"
chown root:root "$PRIVATE_KEY" "$PUBLIC_KEY"
chmod 0600 "$PRIVATE_KEY"
chmod 0644 "$PUBLIC_KEY"

if [[ -f "$TOKEN_FILE" ]]; then
    python3 - "$TOKEN_FILE" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
token = path.read_text(encoding='ascii').strip()
if not 40 <= len(token) <= 512 or any(char.isspace() for char in token):
    raise SystemExit('Existing API token is invalid and was not replaced; use --reset-identity explicitly')
PY
else
    python3 - "$TOKEN_FILE" <<'PY'
import os, pathlib, secrets, sys
path = pathlib.Path(sys.argv[1])
temp = path.with_name(path.name + f'.tmp.{os.getpid()}')
temp.write_text(secrets.token_urlsafe(48) + '\n', encoding='ascii')
os.chmod(temp, 0o600)
os.replace(temp, path)
PY
fi
chown root:root "$TOKEN_FILE"
chmod 0600 "$TOKEN_FILE"

python3 - "$BOUND_SERIAL_FILE" "$RPI_SERIAL" <<'PY'
import os, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
serial = sys.argv[2]
if not re.fullmatch(r'[0-9A-F]{16}', serial):
    raise SystemExit('Refusing invalid Raspberry serial binding')
temp = path.with_name(path.name + f'.tmp.{os.getpid()}')
temp.write_text(serial + '\n', encoding='ascii')
os.chmod(temp, 0o600)
os.replace(temp, path)
PY
touch "$IDENTITY_LOCK_FILE"
chown root:root "$BOUND_SERIAL_FILE" "$IDENTITY_LOCK_FILE"
chmod 0600 "$BOUND_SERIAL_FILE" "$IDENTITY_LOCK_FILE"

install -m 0755 "$SCRIPT_DIR/src/bmi30_agent.py" "$INSTALL_DIR/bmi30_agent.py"
install -m 0755 "$SCRIPT_DIR/src/run_bmi30_tunnel.sh" "$INSTALL_DIR/run_bmi30_tunnel.sh"
install -m 0644 "$SCRIPT_DIR/VERSION" "$INSTALL_DIR/VERSION"
install -m 0755 "$SCRIPT_DIR/src/bmi30-agent-ctl" "$AGENT_CTL"
install -m 0644 "$SCRIPT_DIR/systemd/bmi30-agent.service" "$AGENT_UNIT"
install -m 0644 "$SCRIPT_DIR/systemd/bmi30-tunnel.service" "$TUNNEL_UNIT"

python3 - "$CONFIG_FILE" "$SERVER_URL" "$DEVICE_ID" "$PRIVATE_KEY" "$PUBLIC_KEY" "$KNOWN_HOSTS" "$TOKEN_FILE" "$LOCAL_PORT" <<'PY'
import json, os, sys
path, server, device_id, private_key, public_key, known_hosts, token, local_port = sys.argv[1:]
old = {}
try:
    with open(path, encoding='utf-8') as handle:
        old = json.load(handle)
except Exception:
    pass
value = {
    'schema_version': 3,
    'server_url': server.rstrip('/'),
    'ssh_private_key_path': private_key,
    'ssh_public_key_path': public_key,
    'ssh_known_hosts_path': known_hosts,
    'device_api_token_path': token,
    'local_port': int(local_port),
    'heartbeat_seconds': max(10, int(old.get('heartbeat_seconds', 30))),
    'request_timeout_seconds': max(3, int(old.get('request_timeout_seconds', 10))),
    'auth_error_retry_seconds': 60,
}
temp = path + f'.tmp.{os.getpid()}'
with open(temp, 'w', encoding='utf-8', newline='\n') as handle:
    json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write('\n')
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temp, 0o600)
os.replace(temp, path)
PY
chown root:root "$CONFIG_FILE"
chmod 0600 "$CONFIG_FILE"

# state.json is derived runtime data.  Never carry an approval or port from a
# previous Hub into a fresh installation; the required manual production
# check-in below will recreate the state without replacing identity secrets.
python3 - "$STATE_FILE" <<'PY'
import json, os, sys
path = sys.argv[1]
value = {
    'last_checkin_unix': None,
    'remote_port': None,
    'server_message': 'Awaiting manual production check-in',
    'server_state': 'not_checked_in',
}
temp = path + f'.tmp.{os.getpid()}'
with open(temp, 'w', encoding='utf-8', newline='\n') as handle:
    json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write('\n')
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temp, 0o600)
os.replace(temp, path)
PY
chown root:root "$STATE_FILE"
chmod 0600 "$STATE_FILE"

systemctl daemon-reload
/usr/bin/python3 "$INSTALL_DIR/bmi30_agent.py" validate-identity

if ((ENABLE_AGENT == 1)); then
    echo "Running the required approved production check-in..."
    /usr/bin/python3 "$INSTALL_DIR/bmi30_agent.py" once-approved
    systemctl enable --now bmi30-agent.service >/dev/null
fi

PUBLIC_FINGERPRINT="$(ssh-keygen -lf "$PUBLIC_KEY" -E sha256 | awk '{print $2}')"
cat <<EOF

BMI30 Agent ${PACKAGE_VERSION} installed.

DEVICE_ID=${DEVICE_ID}
CHECKIN_HOSTNAME=${DEVICE_ID}
SSH_PUBLIC_KEY_FINGERPRINT=${PUBLIC_FINGERPRINT}
BOUND_RASPBERRY_SERIAL=${RPI_SERIAL}
SERVER_URL=${SERVER_URL}
LOCAL_HTTP_PORT=${LOCAL_PORT}
BACKUP_DIR=${BACKUP_DIR}
LEGACY_TUNNEL_REMOVED=$([[ ! -e "$LEGACY_TUNNEL_UNIT" ]] && echo yes || echo no)
AGENT_AUTOSTART=$([[ "$ENABLE_AGENT" -eq 1 ]] && echo enabled || echo disabled-pending-manual-check)

Manual validation sequence:
  sudo bmi30-agent-ctl checkin
  sudo bmi30-agent-ctl status
  sudo systemctl enable --now bmi30-agent.service

The production tunnel starts only after Hub state=approved.
EOF
