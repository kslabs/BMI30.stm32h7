#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

printf "BMI30 Utilities\n"
printf "===============\n"
printf "\n"
printf "Интерактивное меню:\n"
printf "  %s\n" "./utilities/menu.sh"
printf "\n"
printf "Проверка состояния:\n"
printf "  %s\n" "./utilities/check_boot_source.sh"
printf "  %s\n" "./utilities/check_bootloader.sh"
printf "  %s\n" "sudo ./utilities/set_usb_boot_priority.sh"
printf "\n"
printf "Полное копирование с переразметкой цели:\n"
printf "  %s\n" "sudo ./utilities/migrate_system_to_usb.sh --yes"
printf "  %s\n" "sudo ./utilities/migrate_system_to_emmc.sh --yes"
printf "\n"
printf "Синхронизация только изменений без переразметки:\n"
printf "  %s\n" "sudo ./utilities/sync_system_to_usb.sh --yes"
printf "  %s\n" "sudo ./utilities/sync_system_to_emmc.sh --yes"
printf "\n"
printf "Уникальные имена по серийному номеру:\n"
printf "  %s\n" "sudo ./utilities/refresh_network_identity.sh"
printf "\n"
printf "Ethernet portal mode:\n"
printf "  %s\n" "sudo ./utilities/setup_ethernet_portal.sh install"
printf "  %s\n" "./utilities/setup_ethernet_portal.sh status"
printf "\n"
printf "Версии на сервере BMI30:\n"
for action in check download update rollback restore-original publish backup-local; do
    printf '  ./utilities/server_firmware.sh %s\n' "$action"
done
printf '\n'
printf "Описание утилит:\n"
printf "  %s\n" "$BASE_DIR/README.md"
