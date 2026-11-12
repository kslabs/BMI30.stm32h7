#!/bin/bash
set -euo pipefail

CONFIG_DIR=/etc/bmi30-agent
CREDENTIALS_DIR=${CREDENTIALS_DIRECTORY:-}

if [[ -n "$CREDENTIALS_DIR" && -r "$CREDENTIALS_DIR/tunnel.env" ]]; then
    ENV_FILE="$CREDENTIALS_DIR/tunnel.env"
    PRIVATE_KEY="$CREDENTIALS_DIR/id_ed25519"
    PUBLIC_KEY="$CREDENTIALS_DIR/id_ed25519.pub"
    KNOWN_HOSTS="$CREDENTIALS_DIR/known_hosts"
else
    ENV_FILE="$CONFIG_DIR/tunnel.env"
    PRIVATE_KEY="$CONFIG_DIR/id_ed25519"
    PUBLIC_KEY="$CONFIG_DIR/id_ed25519.pub"
    KNOWN_HOSTS="$CONFIG_DIR/known_hosts"
fi

for required_file in "$ENV_FILE" "$PRIVATE_KEY" "$PUBLIC_KEY" "$KNOWN_HOSTS"; do
    if [[ ! -r "$required_file" ]]; then
        echo "BMI30 tunnel credential is unavailable: $required_file" >&2
        exit 78
    fi
done

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${SSH_HOST:?missing SSH_HOST}"
: "${SSH_PORT:?missing SSH_PORT}"
: "${SSH_USER:?missing SSH_USER}"
: "${LISTEN_ADDRESS:?missing LISTEN_ADDRESS}"
: "${REMOTE_PORT:?missing REMOTE_PORT}"
: "${LOCAL_PORT:?missing LOCAL_PORT}"

[[ "$SSH_HOST" == "www.teiots.net" ]] || { echo "Unexpected SSH_HOST" >&2; exit 78; }
[[ "$SSH_PORT" == "2222" ]] || { echo "Unexpected SSH_PORT" >&2; exit 78; }
[[ "$SSH_USER" == "bmi30-tunnel" ]] || { echo "Unexpected SSH_USER" >&2; exit 78; }
[[ "$LISTEN_ADDRESS" == "0.0.0.0" ]] || { echo "Unexpected LISTEN_ADDRESS" >&2; exit 78; }
if ! [[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] || (( REMOTE_PORT < 20000 || REMOTE_PORT > 39999 )); then
    echo "REMOTE_PORT outside 20000..39999" >&2
    exit 78
fi
if ! [[ "$LOCAL_PORT" =~ ^[0-9]+$ ]] || (( LOCAL_PORT < 1 || LOCAL_PORT > 65535 )); then
    echo "Invalid LOCAL_PORT" >&2
    exit 78
fi

RPI_SERIAL=""
if [[ -r /proc/cpuinfo ]]; then
    RPI_SERIAL="$(awk -F: 'tolower($1) ~ /^serial/ {gsub(/[[:space:]]/, "", $2); print toupper($2); exit}' /proc/cpuinfo)"
fi
KEY_COMMENT="$(awk 'NF >= 3 {print $3; exit}' "$PUBLIC_KEY")"
if [[ "$RPI_SERIAL" =~ ^[0-9A-F]{16}$ ]] && [[ "$KEY_COMMENT" != "BMI30-${RPI_SERIAL}@bmi30-tunnel" ]]; then
    echo "BMI30 tunnel identity does not match this Raspberry; cloned disk requires re-enrollment" >&2
    exit 78
fi

exec /usr/bin/ssh -NT \
    -i "$PRIVATE_KEY" \
    -p "$SSH_PORT" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile="$KNOWN_HOSTS" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o ConnectTimeout=10 \
    -o ConnectionAttempts=1 \
    -o RequestTTY=no \
    -o LogLevel=ERROR \
    -R "${LISTEN_ADDRESS}:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}" \
    "${SSH_USER}@${SSH_HOST}"
