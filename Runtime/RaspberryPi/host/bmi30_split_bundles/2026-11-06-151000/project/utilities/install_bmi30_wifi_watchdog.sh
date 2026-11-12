#!/usr/bin/env bash
# Install device-level Wi-Fi recovery without restarting a working connection.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
if [[ "$EUID" -ne 0 ]]; then
    exec sudo bash "$0" "$@"
fi
config_path="${1:-$project_dir/host/bmi30_config.json}"
[[ -r "$config_path" ]] || { echo "Cannot read portal config: $config_path" >&2; exit 1; }
exec python3 "$project_dir/ops/scripts/bmi30-wifi-watchdog.py" --install "$config_path"
