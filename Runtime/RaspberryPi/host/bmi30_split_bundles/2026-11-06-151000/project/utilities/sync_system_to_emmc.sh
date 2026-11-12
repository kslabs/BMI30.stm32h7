#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Использование:
    sudo ./utilities/sync_system_to_emmc.sh [--yes] [--no-pause-services]

Назначение:
    Синхронизирует текущую USB-систему на eMMC без переразметки диска.

Важно:
    На eMMC уже должны существовать разделы boot (vfat) и root (ext4).
    Копирование выполняется через rsync --delete, поэтому лишние файлы на цели будут удалены.
    При копировании живой системы bmi30-core.service временно останавливается.
EOF
    exit 0
fi

exec "$SCRIPT_DIR/migrate_system_between_disks.sh" --source-role usb --target-role internal --sync-only "$@"
