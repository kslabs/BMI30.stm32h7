#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Использование:
    sudo ./utilities/migrate_system_to_usb.sh [--yes] [--skip-eeprom] [--no-pause-services]

Назначение:
    Копирует систему с eMMC на USB-диск.
    USB всегда переразмечается и форматируется. Если первое копирование завершится
    ошибкой, USB повторно форматируется и копирование запускается ещё один раз.

Опции:
    --yes                Не спрашивать подтверждение.
    --skip-eeprom        Не менять BOOT_ORDER в EEPROM.
    --no-pause-services  Не останавливать bmi30-core.service на время rootfs-копии.
EOF
    exit 0
fi

exec "$SCRIPT_DIR/migrate_system_between_disks.sh" \
    --source-role internal \
    --target-role usb \
    --force-format-and-retry \
    "$@"
