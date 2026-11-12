#!/usr/bin/env bash
# One-time removal of retired firmware delivery jobs; keep unrelated Drive use.
set -euo pipefail
(( EUID == 0 )) || { echo 'Run through sudo' >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="${1:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
[[ -f "$PROJECT_DIR/utilities/server_firmware.sh" ]] || exit 1
account="$(stat -c '%U' "$PROJECT_DIR")"
account_dir="$(getent passwd "$account" | cut -d: -f6)"
account_uid="$(id -u "$account")"
[[ -n "$account_dir" ]] || exit 1
backup_dir="$(mktemp -d "$PROJECT_DIR/backups/retired_firmware.XXXXXXXX")"
chmod 0700 "$backup_dir"
units=(bmi30-cloud-update.timer bmi30-cloud-update.service bmi30-cloud-backup.timer bmi30-cloud-backup.service)
user_unit_dir="$account_dir/.config/systemd/user"
user_systemctl() {
    runuser -u "$account" -- env XDG_RUNTIME_DIR="/run/user/$account_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$account_uid/bus" systemctl --user "$@"
}
user_manager=0
if user_systemctl show-environment >/dev/null 2>&1; then
    user_manager=1
    for unit in "${units[@]}"; do
        if user_systemctl cat "$unit" >/dev/null 2>&1; then
            user_systemctl disable --now "$unit"
        fi
    done
fi
for unit in "${units[@]}"; do
    path="$user_unit_dir/$unit"
    if [[ -f "$path" || -L "$path" ]]; then
        mkdir -p "$backup_dir/user_units"
        cp -a -- "$path" "$backup_dir/user_units/$unit"
        rm -f -- "$path"
    fi
    for link in "$user_unit_dir"/*.wants/"$unit"; do
        [[ ! -L "$link" ]] || rm -f -- "$link"
    done
    path="/etc/systemd/system/$unit"
    if [[ -f "$path" || -L "$path" ]]; then
        systemctl disable --now "$unit"
        mkdir -p "$backup_dir/system_units"
        cp -a -- "$path" "$backup_dir/system_units/$unit"
        rm -f -- "$path"
    fi
done
(( user_manager == 0 )) || user_systemctl daemon-reload
systemctl daemon-reload
for name in backup_to_cloud.sh update_from_cloud.sh cloud_sync_now.sh cloud_sync_common.sh \
            backup_status.sh backup_to_cloud.conf backup_to_cloud.conf.example \
            backup_bmi30_recovery_to_cloud.sh restore_bmi30_recovery_from_cloud.sh; do
    path="$PROJECT_DIR/utilities/$name"
    if [[ -f "$path" || -L "$path" ]]; then
        mkdir -p "$backup_dir/utilities"
        cp -a -- "$path" "$backup_dir/utilities/$name"
        rm -f -- "$path"
    fi
done
printf 'Retired firmware jobs removed. Local backup: %s\n' "$backup_dir"
