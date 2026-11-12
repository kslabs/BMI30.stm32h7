#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BMI30_CORE_SERVICE="${BMI30_CORE_SERVICE:-bmi30-core.service}"

pause_menu() {
    printf "\nНажмите Enter, чтобы вернуться в меню..."
    read -r _
}

run_action() {
    local title="$1"
    shift

    printf "\n=== %s ===\n\n" "$title"
    if (cd "$WORKSPACE_DIR" && "$@"); then
        printf "\nГотово.\n"
    else
        local status=$?
        printf "\nКоманда завершилась с кодом %d.\n" "$status"
    fi
}

bmi30_core_state_text() {
    if ! command -v systemctl >/dev/null 2>&1; then
        printf "systemctl недоступен"
        return 0
    fi

    local state
    state="$(systemctl is-active "$BMI30_CORE_SERVICE" 2>&1 || true)"
    case "$state" in
        *"Failed to connect"*|*"System has not been booted"*|*"Host is down"*)
            printf "systemd недоступен"
            ;;
        active)
            printf "запущено"
            ;;
        inactive)
            printf "остановлено"
            ;;
        activating)
            printf "запускается"
            ;;
        deactivating)
            printf "останавливается"
            ;;
        failed)
            printf "ошибка"
            ;;
        unknown|"")
            if systemctl cat "$BMI30_CORE_SERVICE" >/dev/null 2>&1; then
                printf "неизвестно"
            else
                printf "сервис не найден"
            fi
            ;;
        *)
            printf "%s" "$state"
            ;;
    esac
}

bmi30_core_enabled_text() {
    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi

    local enabled
    enabled="$(systemctl is-enabled "$BMI30_CORE_SERVICE" 2>/dev/null || true)"
    case "$enabled" in
        enabled)
            printf "автозапуск включен"
            ;;
        disabled)
            printf "автозапуск выключен"
            ;;
        static|generated|indirect|alias)
            printf "%s" "$enabled"
            ;;
        ""|not-found|*"Failed to connect"*|*"System has not been booted"*|*"Host is down"*)
            ;;
        *)
            printf "%s" "$enabled"
            ;;
    esac
}

show_bmi30_core_status() {
    local state_text enabled_text
    state_text="$(bmi30_core_state_text)"
    enabled_text="$(bmi30_core_enabled_text)"

    printf "BMI30 split-система: %s" "$state_text"
    if [[ -n "$enabled_text" ]]; then
        printf " / %s" "$enabled_text"
    fi
    printf " (%s)\n" "$BMI30_CORE_SERVICE"
}

run_core_service_action() {
    local title="$1"
    local action="$2"

    run_action "$title" sudo systemctl "$action" "$BMI30_CORE_SERVICE"
    printf "\n"
    show_bmi30_core_status
}

show_core_service_details() {
    show_bmi30_core_status
    printf "\n"

    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi

    systemctl --no-pager --full status "$BMI30_CORE_SERVICE" || true
}

deploy_portal() {
            run_action "Деплой hotspot_info_server.py" sudo env BMI30_PORTAL_SRC="$WORKSPACE_DIR/hotspot_info_server.py" bash -c '
                set -euo pipefail
                src="$BMI30_PORTAL_SRC"
                dst="/usr/local/bin/bmi30-hotspot-info-server.py"
                start_ts="$(date +%s)"
                install -m 755 "$src" "$dst"
                end_ts="$(date +%s)"
                elapsed_s=$((end_ts - start_ts))
                if (( elapsed_s <= 0 )); then
                    elapsed_s=1
                fi
                bytes="$(stat -c "%s" "$dst" 2>/dev/null || printf "0")"
                [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
                rate=$((bytes / elapsed_s))
                size="$(numfmt --to=iec --suffix=B "$bytes" 2>/dev/null || printf "%sB" "$bytes")"
                speed="$(numfmt --to=iec --suffix=B/s "$rate" 2>/dev/null || printf "%sB/s" "$rate")"
                printf "Copy: duration %ss, average speed %s, size %s\n" "$elapsed_s" "$speed" "$size"
                systemctl restart bmi30-hotspot-info.service
                printf "OK: строк: %s\n" "$(wc -l < "$dst")"
            '
}

show_menu() {
    printf "\nУтилиты BMI30 split-системы\n==========================\n"
    show_bmi30_core_status
    cat <<'EOF'

1. Версии и обновление с сервера
2. Запустить BMI30
3. Остановить BMI30
4. Статус BMI30
5. Диски и загрузка
6. Сеть
7. Обновить страницу портала из локального кода
8. Справка и команды
0. Выход
EOF
}

versions_menu() {
    local choice
    while true; do
        cat <<'EOF'

Версии и обновление с сервера
1. Проверить доступную версию
2. Установить последнюю версию
3. Откатить последнее обновление
4. Вернуться к исходной версии
5. Сохранить текущую версию на сервере
6. Выбрать сохранённую локальную версию
7. Скачать последнюю версию без установки
0. Назад
EOF
        read -r -p 'Выберите действие: ' choice || return
        case "$choice" in
            1) run_action "Проверка версии" "$SCRIPT_DIR/server_firmware.sh" check ;;
            2) run_action "Обновление с сервера" "$SCRIPT_DIR/server_firmware.sh" update ;;
            3) run_action "Откат" "$SCRIPT_DIR/server_firmware.sh" rollback ;;
            4) run_action "Исходная версия" "$SCRIPT_DIR/server_firmware.sh" restore-original ;;
            5) run_action "Сохранение на сервере" "$SCRIPT_DIR/server_firmware.sh" publish ;;
            6) run_action "Локальные версии" bash "$WORKSPACE_DIR/switch_bmi30_split_versions.sh" ;;
            7) run_action "Скачивание без установки" "$SCRIPT_DIR/server_firmware.sh" download ;;
            0|q|Q) return ;;
            *) printf 'Неизвестный пункт.\n'; continue ;;
        esac
        pause_menu
    done
}

disks_menu() {
    local choice
    while true; do
        cat <<'EOF'

Диски и загрузка
1. Проверить источник загрузки
2. Проверить BOOT_ORDER
3. Сделать USB приоритетом загрузки
4. Полное копирование USB → eMMC
5. Синхронизировать изменения USB → eMMC
6. Полное копирование eMMC → USB
7. Синхронизировать изменения eMMC → USB
0. Назад
EOF
        read -r -p 'Выберите действие: ' choice || return
        case "$choice" in
            1) run_action "Источник загрузки" bash "$SCRIPT_DIR/check_boot_source.sh" ;;
            2) run_action "BOOT_ORDER" bash "$SCRIPT_DIR/check_bootloader.sh" ;;
            3) run_action "USB-first" sudo bash "$SCRIPT_DIR/set_usb_boot_priority.sh" ;;
            4) run_action "USB → eMMC" sudo bash "$SCRIPT_DIR/migrate_system_to_emmc.sh" ;;
            5) run_action "Синхронизация USB → eMMC" sudo bash "$SCRIPT_DIR/sync_system_to_emmc.sh" ;;
            6) run_action "eMMC → USB" sudo bash "$SCRIPT_DIR/migrate_system_to_usb.sh" ;;
            7) run_action "Синхронизация eMMC → USB" sudo bash "$SCRIPT_DIR/sync_system_to_usb.sh" ;;
            0|q|Q) return ;;
            *) printf 'Неизвестный пункт.\n'; continue ;;
        esac
        pause_menu
    done
}

network_menu() {
    local choice
    printf '\nСеть\n1. Обновить уникальное имя прибора\n2. Включить Ethernet portal mode\n0. Назад\n'
    read -r -p 'Выберите действие: ' choice || return
    case "$choice" in
        1) run_action "Имя прибора" sudo bash "$SCRIPT_DIR/refresh_network_identity.sh" ;;
        2) run_action "Ethernet portal mode" sudo bash "$SCRIPT_DIR/setup_ethernet_portal.sh" install ;;
        0|q|Q) return ;;
        *) printf 'Неизвестный пункт.\n' ;;
    esac
    pause_menu
}

main() {
    local choice
    while true; do
        show_menu
        read -r -p 'Выберите действие: ' choice || return
        case "$choice" in
            1) versions_menu; continue ;;
            2) run_core_service_action "Запуск BMI30" start ;;
            3) run_core_service_action "Остановка BMI30" stop ;;
            4) run_action "Статус BMI30" show_core_service_details ;;
            5) disks_menu; continue ;;
            6) network_menu; continue ;;
            7) deploy_portal ;;
            8) run_action "Справка и команды" bash "$SCRIPT_DIR/list_tools.sh" ;;
            0|q|Q|exit) return ;;
            *) printf 'Неизвестный пункт.\n'; continue ;;
        esac
        pause_menu
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
