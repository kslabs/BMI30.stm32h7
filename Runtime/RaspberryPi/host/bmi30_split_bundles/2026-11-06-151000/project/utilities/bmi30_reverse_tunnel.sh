#!/usr/bin/env bash
# Persistent outbound reverse SSH tunnel for one BMI30 portal.

set -euo pipefail

CONFIG_FILE="${BMI30_REVERSE_TUNNEL_CONFIG:-/etc/bmi30/reverse_tunnel.env}"

fail() {
    printf '[ERROR] %s\n' "$*" >&2
    exit 1
}

[[ -r "$CONFIG_FILE" ]] || fail "Tunnel config is unavailable: $CONFIG_FILE"

unset SERVER_HOST SERVER_SSH_PORT SERVER_USER REMOTE_PORT LOCAL_PORT IDENTITY_FILE KNOWN_HOSTS_FILE
# shellcheck source=/dev/null
source "$CONFIG_FILE"

: "${SERVER_HOST:?SERVER_HOST is required}"
: "${SERVER_SSH_PORT:?SERVER_SSH_PORT is required}"
: "${SERVER_USER:?SERVER_USER is required}"
: "${REMOTE_PORT:?REMOTE_PORT is required}"
: "${LOCAL_PORT:?LOCAL_PORT is required}"
: "${IDENTITY_FILE:?IDENTITY_FILE is required}"
: "${KNOWN_HOSTS_FILE:?KNOWN_HOSTS_FILE is required}"

[[ "$SERVER_HOST" =~ ^[A-Za-z0-9._:-]+$ ]] || fail "Invalid SERVER_HOST"
[[ "$SERVER_USER" =~ ^[A-Za-z_][A-Za-z0-9._-]*$ ]] || fail "Invalid SERVER_USER"
[[ "$SERVER_SSH_PORT" =~ ^[0-9]+$ ]] && (( SERVER_SSH_PORT >= 1 && SERVER_SSH_PORT <= 65535 )) \
    || fail "Invalid SERVER_SSH_PORT"
[[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] && (( REMOTE_PORT >= 20000 && REMOTE_PORT <= 39999 )) \
    || fail "REMOTE_PORT must be in 20000..39999"
[[ "$LOCAL_PORT" =~ ^[0-9]+$ ]] && (( LOCAL_PORT >= 1 && LOCAL_PORT <= 65535 )) \
    || fail "Invalid LOCAL_PORT"
[[ -r "$IDENTITY_FILE" ]] || fail "Tunnel identity is unavailable: $IDENTITY_FILE"
[[ -s "$KNOWN_HOSTS_FILE" ]] || fail "Pinned server host key is unavailable: $KNOWN_HOSTS_FILE"

exec /usr/bin/ssh -NT \
    -p "$SERVER_SSH_PORT" \
    -i "$IDENTITY_FILE" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS_FILE" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o ConnectTimeout=15 \
    -o ConnectionAttempts=1 \
    -o RequestTTY=no \
    -o LogLevel=ERROR \
    -R "0.0.0.0:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" \
    "${SERVER_USER}@${SERVER_HOST}"
