#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Использование:
    sudo ./utilities/sync_system_to_usb.sh [--yes] [--skip-eeprom] [--no-pause-services]

Назначение:
    Синхронизирует текущую eMMC-систему на USB без переразметки диска.

Важно:
    На USB уже должны существовать разделы boot (vfat) и root (ext4).
    Копирование выполняется через rsync --delete, поэтому лишние файлы на цели будут удалены.
    При копировании живой системы bmi30-core.service временно останавливается.
EOF
    exit 0
fi

exec "$SCRIPT_DIR/migrate_system_between_disks.sh" --source-role internal --target-role usb --sync-only "$@"
