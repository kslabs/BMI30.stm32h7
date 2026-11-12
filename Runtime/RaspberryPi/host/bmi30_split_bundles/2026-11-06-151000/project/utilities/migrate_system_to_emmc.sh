#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Использование:
    sudo ./utilities/migrate_system_to_emmc.sh [--yes] [--no-pause-services]

Назначение:
    Копирует систему с USB-диска на eMMC.

Опции:
    --yes                Не спрашивать подтверждение.
    --no-pause-services  Не останавливать bmi30-core.service на время rootfs-копии.

Важно:
    Скрипт нельзя запускать, если текущая система загружена с eMMC и eMMC является целью.
    В этом случае загрузитесь с USB и повторите запуск.
EOF
    exit 0
fi

exec "$SCRIPT_DIR/migrate_system_between_disks.sh" --source-role usb --target-role internal "$@"
