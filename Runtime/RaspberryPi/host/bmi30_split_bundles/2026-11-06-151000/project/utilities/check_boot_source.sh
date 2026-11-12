#!/usr/bin/env bash
set -euo pipefail

classify_disk() {
    local disk="$1"
    local transport

    if [[ "$disk" =~ ^/dev/mmcblk[0-9]+$ ]]; then
        printf "eMMC/internal\n"
        return
    fi

    transport="$(lsblk -dnro TRAN "$disk" 2>/dev/null | awk 'NR==1 {print $1}')"
    if [[ "$transport" == "usb" ]]; then
        printf "USB\n"
        return
    fi

    printf "unknown\n"
}

show_partition_info() {
    local part="$1"

    if [[ -z "$part" ]]; then
        printf "  источник не смонтирован\n"
        return
    fi

    blkid "$part" 2>/dev/null | sed 's/^/  /'
}

ROOT_SOURCE="$(findmnt -no SOURCE /)"
ROOT_DISK="/dev/$(lsblk -no PKNAME "$ROOT_SOURCE")"
ROOT_KIND="$(classify_disk "$ROOT_DISK")"

BOOT_SOURCE="$(findmnt -no SOURCE /boot/firmware 2>/dev/null || true)"
if [[ -n "$BOOT_SOURCE" ]]; then
    BOOT_DISK="/dev/$(lsblk -no PKNAME "$BOOT_SOURCE")"
    BOOT_KIND="$(classify_disk "$BOOT_DISK")"
else
    BOOT_DISK=""
    BOOT_KIND="not-mounted"
fi

printf "Текущий источник загрузки\n"
printf "========================\n"
printf "Root:\n"
printf "  раздел: %s\n" "$ROOT_SOURCE"
printf "  диск:   %s\n" "$ROOT_DISK"
printf "  тип:    %s\n" "$ROOT_KIND"
show_partition_info "$ROOT_SOURCE"
printf "\nBoot:\n"
if [[ -n "$BOOT_SOURCE" ]]; then
    printf "  раздел: %s\n" "$BOOT_SOURCE"
    printf "  диск:   %s\n" "$BOOT_DISK"
    printf "  тип:    %s\n" "$BOOT_KIND"
else
    printf "  раздел: не смонтирован\n"
    printf "  диск:   -\n"
    printf "  тип:    %s\n" "$BOOT_KIND"
fi
show_partition_info "$BOOT_SOURCE"

printf "\nИтог:\n"
if [[ "$ROOT_KIND" == "USB" ]]; then
    printf "  Система загружена с USB.\n"
elif [[ "$ROOT_KIND" == "eMMC/internal" ]]; then
    printf "  Система загружена с eMMC.\n"
else
    printf "  Источник root определить однозначно не удалось.\n"
fi