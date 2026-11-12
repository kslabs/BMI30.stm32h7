#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
if (( EUID == 0 )) || [[ "${1:-}" == backend ]]; then
    exec python3 "$SCRIPT_DIR/bmi30_server_update.py" "$@"
fi
exec sudo python3 "$SCRIPT_DIR/bmi30_server_update.py" "$@"
