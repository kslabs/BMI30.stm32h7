#!/usr/bin/env bash
set -euo pipefail

BOOT_ORDER_USB_FIRST="0xf2614"
TMP_CFG=""

cleanup() {
    if [[ -n "$TMP_CFG" && -f "$TMP_CFG" ]]; then
        rm -f "$TMP_CFG"
    fi
}

trap cleanup EXIT

require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || {
        printf "Запустите скрипт через sudo.\n" >&2
        exit 1
    }
}

require_tool() {
    command -v rpi-eeprom-config >/dev/null 2>&1 || {
        printf "Не найдена утилита rpi-eeprom-config. Невозможно изменить BOOT_ORDER.\n" >&2
        exit 1
    }
}

get_boot_order() {
    local order=""

    if command -v vcgencmd >/dev/null 2>&1; then
        order="$(vcgencmd bootloader_config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
    fi

    if [[ -z "$order" ]]; then
        order="$(rpi-eeprom-config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}')"
    fi

    printf '%s' "$order"
}

show_boot_source() {
    local root_source root_disk transport

    root_source="$(findmnt -no SOURCE / 2>/dev/null || true)"
    if [[ -z "$root_source" ]]; then
        printf "Текущий root: не удалось определить.\n"
        return
    fi

    root_disk="/dev/$(lsblk -no PKNAME "$root_source" 2>/dev/null | head -n 1)"
    if [[ "$root_disk" =~ ^/dev/mmcblk[0-9]+$ ]]; then
        transport="eMMC/internal"
    else
        transport="$(lsblk -dnro TRAN "$root_disk" 2>/dev/null | awk 'NR==1 {print $1}')"
        if [[ "$transport" == "usb" ]]; then
            transport="USB"
        elif [[ -z "$transport" ]]; then
            transport="unknown"
        fi
    fi

    printf "Текущий root: %s (%s)\n" "$root_source" "$transport"
}

apply_usb_first() {
    TMP_CFG="$(mktemp /tmp/bmi30-boot-order.XXXXXX)"
    rpi-eeprom-config > "$TMP_CFG"

    if grep -q '^BOOT_ORDER=' "$TMP_CFG"; then
        sed -i "s/^BOOT_ORDER=.*/BOOT_ORDER=$BOOT_ORDER_USB_FIRST/" "$TMP_CFG"
    else
        printf "\nBOOT_ORDER=%s\n" "$BOOT_ORDER_USB_FIRST" >> "$TMP_CFG"
    fi

    printf "Применяю BOOT_ORDER=%s ...\n" "$BOOT_ORDER_USB_FIRST"
    rpi-eeprom-config --apply "$TMP_CFG"
}

main() {
    local current_order=""

    require_root
    require_tool

    printf "USB-first для загрузчика Raspberry Pi\n"
    printf "===================================\n"
    show_boot_source
    printf "\n"

    current_order="$(get_boot_order)"
    if [[ -z "$current_order" ]]; then
        printf "Текущий BOOT_ORDER определить не удалось.\n" >&2
        exit 1
    fi

    printf "Текущий BOOT_ORDER: %s\n" "$current_order"
    if [[ "$current_order" == "$BOOT_ORDER_USB_FIRST" ]]; then
        printf "USB уже в приоритете. Изменения не нужны.\n"
        exit 0
    fi

    apply_usb_first

    printf "\nГотово. Новый BOOT_ORDER: %s\n" "$BOOT_ORDER_USB_FIRST"
    printf "После перезагрузки Raspberry Pi сначала будет пытаться загрузиться с USB,\n"
    printf "а при отсутствии USB перейдет к внутреннему накопителю.\n"
    printf "Для применения нужен reboot.\n"
}

main "$@"