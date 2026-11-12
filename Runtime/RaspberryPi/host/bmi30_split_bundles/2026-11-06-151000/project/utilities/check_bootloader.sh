#!/usr/bin/env bash
set -euo pipefail

get_boot_order() {
    local order=""

    if command -v vcgencmd >/dev/null 2>&1; then
        order="$(vcgencmd bootloader_config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}' || true)"
    fi

    if [[ -z "$order" ]] && command -v rpi-eeprom-config >/dev/null 2>&1; then
        order="$(rpi-eeprom-config 2>/dev/null | awk -F= '/^BOOT_ORDER=/{print $2; exit}' || true)"
    fi

    printf '%s' "$order"
}

describe_boot_order() {
    local order="${1,,}" digits mode label index step=0 usb_step=-1 internal_step=-1
    local separator="" terminal=0
    order="${order//[[:space:]]/}"
    if [[ ! "$order" =~ ^0x[0-9a-f]{1,8}$ ]]; then
        printf 'Не удалось разобрать BOOT_ORDER: %s\n' "$1" >&2
        return 1
    fi
    digits="${order#0x}"
    printf 'Порядок попыток загрузки:\n  '
    # Raspberry Pi EEPROM BOOT_ORDER is read from right to left.
    # https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#BOOT_ORDER
    for ((index=${#digits}-1; index>=0; index--)); do
        mode="${digits:index:1}"
        step=$((step + 1))
        case "$mode" in
            0) label='SD, затем ожидание смены карты'; (( internal_step >= 0 )) || internal_step=$step; terminal=1 ;;
            1) label='eMMC/SD'; (( internal_step >= 0 )) || internal_step=$step ;;
            2) label='сеть' ;;
            3) label='RPIBOOT (ожидание USB-хоста без таймаута)'; terminal=1 ;;
            4) label='USB'; (( usb_step >= 0 )) || usb_step=$step ;;
            5) label='USB 2.0 (BCM)'; (( usb_step >= 0 )) || usb_step=$step ;;
            6) label='NVMe' ;;
            7) label='HTTP по сети' ;;
            e) label='остановка'; terminal=1 ;;
            f) label='повтор с начала'; terminal=1 ;;
            *) label="неизвестный режим 0x$mode" ;;
        esac
        printf '%s%s' "$separator" "$label"
        separator=' → '
        (( terminal == 0 )) || break
    done
    printf '\n\nПриоритет USB и eMMC/SD:\n  '
    if (( usb_step >= 0 && internal_step >= 0 )); then
        if (( usb_step < internal_step )); then
            printf 'USB раньше eMMC/SD.\n'
        else
            printf 'eMMC/SD раньше USB.\n'
        fi
    elif (( usb_step >= 0 )); then
        printf 'В последовательности есть USB; попытки eMMC/SD нет.\n'
    elif (( internal_step >= 0 )); then
        printf 'В последовательности есть eMMC/SD; попытки USB нет.\n'
    else
        printf 'Попыток загрузки с USB и eMMC/SD в этой последовательности нет.\n'
    fi
    printf '\nСледующий источник пробуется, если с предыдущего загрузиться не удалось.\n'
}

main() {
    local boot_order
    boot_order="$(get_boot_order)"
    printf 'Состояние загрузчика Raspberry Pi\n===============================\n'

    if [[ -z "$boot_order" ]]; then
        printf 'BOOT_ORDER: не удалось прочитать конфигурацию загрузчика.\n'
        return 1
    fi
    printf 'BOOT_ORDER: %s\n\n' "$boot_order"
    describe_boot_order "$boot_order"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
