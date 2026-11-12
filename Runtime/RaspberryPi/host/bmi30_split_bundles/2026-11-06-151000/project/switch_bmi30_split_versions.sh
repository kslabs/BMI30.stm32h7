#!/usr/bin/env bash
# Safe menu/CLI for complete BMI30 websplit runtime bundles.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
HOST_DIR="$SCRIPT_DIR/host"
BUNDLES_DIR="$HOST_DIR/bmi30_split_bundles"
ACTIVE_ENV="$HOST_DIR/bmi30_split_active_version.env"
ACTIVE_RUNTIME_DIR="$HOST_DIR/bmi30_active_runtime"
CORE_SERVICE="${BMI30_CORE_SERVICE:-bmi30-core.service}"
PORTAL_SERVICE="${BMI30_PORTAL_SERVICE:-bmi30-hotspot-info.service}"
PORTAL_DST="${BMI30_PORTAL_DST:-/usr/local/bin/bmi30-hotspot-info-server.py}"
PORTAL_CONFIG_DST="${BMI30_PORTAL_CONFIG_DST:-/etc/bmi30/portal_config.json}"
INSTALLED_CONFIG_DST="${BMI30_INSTALLED_CONFIG_DST:-/usr/local/bin/host/bmi30_config.json}"
PERSISTENT_CONFIG_PATH="${BMI30_PERSISTENT_CONFIG_PATH:-$HOST_DIR/bmi30_config.json}"
PERSISTENT_MODE_STATE_PATH="${BMI30_PERSISTENT_MODE_STATE_PATH:-$HOST_DIR/bmi30_sel.json}"
PORTAL_CONFIG_DROPIN="${BMI30_PORTAL_CONFIG_DROPIN:-/etc/systemd/system/${PORTAL_SERVICE}.d/10-bmi30-persistent-config.conf}"
PULSE_DEFAULT_PA="${BMI30_PULSE_DEFAULT_PA:-/etc/pulse/default.pa}"
SERVICE_URL_DEFAULT="http://127.0.0.1:8765"
RELEASE_MANIFEST_OVERRIDE="${BMI30_RELEASE_MANIFEST_OVERRIDE:-}"
PREVIOUS_ACTIVE_ENV_OVERRIDE="${BMI30_PREVIOUS_ACTIVE_ENV_OVERRIDE:-}"
SELECTED_BY_OVERRIDE="${BMI30_SPLIT_SELECTED_BY_OVERRIDE:-terminal-menu}"
AGENT_BOOTSTRAP="${BMI30_AGENT_BOOTSTRAP:-$SCRIPT_DIR/utilities/install_bmi30_agent_from_project.sh}"
PORTABLE_ASSETS="$SCRIPT_DIR/utilities/bmi30_portable_assets.py"

declare -a BUNDLE_IDS=()

offline_activation() {
    [[ "${BMI30_OFFLINE_ACTIVATION:-0}" == "1" ]]
}

runtime_systemctl() {
    offline_activation && return 0
    sudo systemctl "$@"
}

current_menu_month() {
    local month="${BMI30_VERSION_MENU_MONTH:-$(date +%Y-%m)}"
    [[ "$month" =~ ^[0-9]{4}-[0-9]{2}$ ]] || die "Некорректный месяц меню: $month"
    printf '%s\n' "$month"
}

usage() {
    cat <<'EOF'
Использование:
  ./switch_bmi30_split_versions.sh                 Интерактивное меню текущего месяца
  ./switch_bmi30_split_versions.sh --list          Список комплектов текущего месяца
  ./switch_bmi30_split_versions.sh --validate      Проверить все комплекты
  ./switch_bmi30_split_versions.sh --validate ID   Проверить один комплект
  ./switch_bmi30_split_versions.sh --activate ID   Полностью переключить runtime

В обычный список входят только полные version bundles. Старые core-only файлы
остаются на диске как история, но не могут быть выбраны и смешаны с общими
GUI/portal/config/usb_vendor. Интерактивный список показывает только версии
текущего календарного месяца; архивные комплекты остаются на диске.
EOF
}

die() {
    printf '[ERR] %s\n' "$*" >&2
    exit 1
}

warn() {
    printf '[WARN] %s\n' "$*" >&2
}

info() {
    printf '[INFO] %s\n' "$*"
}

restore_workspace_file_owner() {
    (( EUID == 0 )) || return 0
    chown --reference="$SCRIPT_DIR" -- "$@" || {
        warn "Не удалось вернуть владельца project-файла: $*"
        return 1
    }
}

pause_menu() {
    printf '\nНажмите Enter, чтобы вернуться в меню...'
    read -r _
}

load_active_env() {
    unset BMI30_SPLIT_VERSION BMI30_SPLIT_LABEL BMI30_SPLIT_SELECTED_BY BMI30_SPLIT_SELECTED_AT
    unset BMI30_SPLIT_BUNDLE_ID BMI30_BUNDLE_ORIGIN BMI30_CORE_PATH BMI30_ENGINE_SOURCE
    unset BMI30_GUI_PATH BMI30_PORTAL_PATH BMI30_PROJECT_CONFIG_PATH BMI30_FIRMWARE_MANIFEST
    unset BMI30_CONFIG_JSON BMI30_MODE_STATE_JSON BMI30_SERVICE_URL

    if [[ -f "$ACTIVE_ENV" ]]; then
        # shellcheck source=/dev/null
        source "$ACTIVE_ENV"
    fi

    : "${BMI30_SPLIT_BUNDLE_ID:=}"
    : "${BMI30_CORE_PATH:=}"
    : "${BMI30_ENGINE_SOURCE:=}"
    : "${BMI30_GUI_PATH:=}"
    : "${BMI30_PORTAL_PATH:=}"
    : "${BMI30_SERVICE_URL:=$SERVICE_URL_DEFAULT}"
}

clear_bundle_env() {
    unset BMI30_BUNDLE_FORMAT BMI30_BUNDLE_ID BMI30_BUNDLE_LABEL BMI30_BUNDLE_ORIGIN
    unset BMI30_BUNDLE_HARDWARE_PWM BMI30_BUNDLE_PORTABLE_SYSTEM
    unset BMI30_BUNDLE_CREATED_AT BMI30_BUNDLE_SOURCE_CORE_PATH BMI30_BUNDLE_SOURCE_ENGINE_PATH
    unset BMI30_BUNDLE_CORE_REL BMI30_BUNDLE_ENGINE_REL BMI30_BUNDLE_GUI_REL BMI30_BUNDLE_PORTAL_REL
    unset BMI30_BUNDLE_PROJECT_CONFIG_REL BMI30_BUNDLE_SYSTEM_CONFIG_REL
    unset BMI30_BUNDLE_INSTALLED_CONFIG_REL BMI30_BUNDLE_USB_VENDOR_REL
    unset BMI30_BUNDLE_TAG_FILTERS_REL BMI30_BUNDLE_PLAYER_REL BMI30_BUNDLE_RELEASE_REL
}

bundle_dir() {
    local id="$1"
    [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
    printf '%s/%s\n' "$BUNDLES_DIR" "$id"
}

load_bundle() {
    local id="$1"
    local dir manifest
    dir="$(bundle_dir "$id")" || return 1
    manifest="$dir/manifest.env"
    [[ -f "$manifest" ]] || return 1
    clear_bundle_env
    # shellcheck source=/dev/null
    source "$manifest"
    [[ "${BMI30_BUNDLE_FORMAT:-}" == "1" ]] || return 1
    [[ "${BMI30_BUNDLE_ID:-}" == "$id" ]] || return 1
}

is_safe_bundle_rel() {
    local rel="$1"
    [[ -n "$rel" && "$rel" != /* && "$rel" != *'..'* ]]
}

bundle_file() {
    local id="$1"
    local rel="$2"
    is_safe_bundle_rel "$rel" || return 1
    printf '%s/%s\n' "$(bundle_dir "$id")" "$rel"
}

validate_bundle() {
    local id="$1"
    local quiet="${2:-0}"
    local dir rel path

    if ! load_bundle "$id"; then
        warn "Некорректный manifest комплекта: $id"
        return 1
    fi
    dir="$(bundle_dir "$id")"
    [[ -f "$dir/SHA256SUMS" ]] || {
        warn "Нет SHA256SUMS: $id"
        return 1
    }

    for rel in \
        "$BMI30_BUNDLE_CORE_REL" \
        "$BMI30_BUNDLE_ENGINE_REL" \
        "$BMI30_BUNDLE_GUI_REL" \
        "$BMI30_BUNDLE_PORTAL_REL" \
        "$BMI30_BUNDLE_PROJECT_CONFIG_REL" \
        "$BMI30_BUNDLE_USB_VENDOR_REL/usb_stream.py" \
        "$BMI30_BUNDLE_RELEASE_REL"
    do
        if ! path="$(bundle_file "$id" "$rel")" || [[ ! -f "$path" ]]; then
            warn "Комплект $id неполный: $rel"
            return 1
        fi
    done

    if [[ -n "${BMI30_BUNDLE_TAG_FILTERS_REL:-}" ]]; then
        if ! path="$(bundle_file "$id" "$BMI30_BUNDLE_TAG_FILTERS_REL")" || [[ ! -d "$path" ]]; then
            warn "Комплект $id не содержит папку tag response filters: $BMI30_BUNDLE_TAG_FILTERS_REL"
            return 1
        fi
        find "$path" -maxdepth 1 -type f -name '*.json' -print -quit | grep -q . || {
            warn "Комплект $id не содержит JSON-файлы tag response filters"
            return 1
        }
    fi

    if ! (cd "$dir" && sha256sum -c SHA256SUMS >/dev/null); then
        warn "SHA-256 проверка не прошла: $id"
        return 1
    fi

    if [[ "${BMI30_BUNDLE_PORTABLE_SYSTEM:-0}" == "1" ]]; then
        [[ -f "$PORTABLE_ASSETS" && -f "$dir/metadata/portable-assets.json" ]] || return 1
        python3 "$PORTABLE_ASSETS" validate --bundle "$dir" >/dev/null || return 1
    fi

    if [[ "$quiet" != "1" ]]; then
        printf '[ OK ] %s — %s\n' "$id" "$BMI30_BUNDLE_LABEL"
    fi
}

load_bundle_ids() {
    local month_filter="${1:-}"
    local manifest id
    BUNDLE_IDS=()
    [[ -d "$BUNDLES_DIR" ]] || return 0

    while IFS= read -r manifest; do
        [[ -n "$manifest" ]] || continue
        id="$(basename -- "$(dirname -- "$manifest")")"
        if [[ -n "$month_filter" && "$id" != "${month_filter}-"* ]]; then
            continue
        fi
        if validate_bundle "$id" 1; then
            BUNDLE_IDS+=("$id")
        else
            warn "Комплект скрыт из безопасного меню: $id"
        fi
    done < <(find "$BUNDLES_DIR" -mindepth 2 -maxdepth 2 -type f -name manifest.env | sort -Vr)

    load_active_env
    if [[ -n "$BMI30_SPLIT_BUNDLE_ID" ]]; then
        local ordered=()
        for id in "${BUNDLE_IDS[@]}"; do
            [[ "$id" == "$BMI30_SPLIT_BUNDLE_ID" ]] && ordered+=("$id")
        done
        for id in "${BUNDLE_IDS[@]}"; do
            [[ "$id" != "$BMI30_SPLIT_BUNDLE_ID" ]] && ordered+=("$id")
        done
        BUNDLE_IDS=("${ordered[@]}")
    fi
}

legacy_core_count() {
    find "$HOST_DIR" -maxdepth 1 -type f \( -name 'BMI30.001.py' -o -name 'BMI30.001.py.*' \) | wc -l
}

bundle_suffix() {
    local id="$1"
    load_active_env
    if [[ "$id" == "$BMI30_SPLIT_BUNDLE_ID" ]]; then
        printf '  [active]'
    fi
}

show_bundle_line() {
    local number="$1"
    local id="$2"
    load_bundle "$id"
    printf '%s) %s  (%s)%s\n' "$number" "$BMI30_BUNDLE_LABEL" "$id" "$(bundle_suffix "$id")"
}

show_list() {
    local menu_month
    menu_month="$(current_menu_month)"
    load_bundle_ids "$menu_month"
    load_active_env
    printf 'Полные BMI30 websplit-комплекты за %s: %d\n' "$menu_month" "${#BUNDLE_IDS[@]}"
    printf 'Активный комплект: %s\n' "${BMI30_SPLIT_BUNDLE_ID:-не назначен}"
    local i
    for i in "${!BUNDLE_IDS[@]}"; do
        show_bundle_line "$((i + 1))" "${BUNDLE_IDS[$i]}"
    done
}

service_state_text() {
    local service="$1"
    local state
    state="$(systemctl is-active "$service" 2>/dev/null || true)"
    case "$state" in
        active) printf 'запущено' ;;
        inactive) printf 'остановлено' ;;
        failed) printf 'ошибка' ;;
        *) printf '%s' "${state:-недоступно}" ;;
    esac
}

show_runtime_status() {
    if offline_activation; then
        printf 'Offline runtime prepared; service checks require booting this disk.\n'
        return 0
    fi
    load_active_env
    printf 'Активный полный комплект: %s\n' "${BMI30_SPLIT_BUNDLE_ID:-не назначен}"
    printf 'Core:   %s\n' "${BMI30_CORE_PATH:-не задан}"
    printf 'Engine: %s\n' "${BMI30_ENGINE_SOURCE:-не задан}"
    printf 'GUI:    %s\n' "${BMI30_GUI_PATH:-не задан}"
    printf 'Portal: %s\n' "${BMI30_PORTAL_PATH:-не задан}"
    printf 'Службы: %s=%s; %s=%s\n' \
        "$CORE_SERVICE" "$(service_state_text "$CORE_SERVICE")" \
        "$PORTAL_SERVICE" "$(service_state_text "$PORTAL_SERVICE")"
}

runtime_rel() {
    local rel="$1"
    printf 'host/bmi30_active_runtime/%s\n' "$rel"
}

remove_runtime_tree() {
    local target="$1"
    case "$target" in
        "$ACTIVE_RUNTIME_DIR"|"$HOST_DIR"/.bmi30_active_runtime.stage.*|"$HOST_DIR"/.bmi30_active_runtime.rollback.*)
            ;;
        *)
            die "Отказ от удаления неожиданного runtime-пути: $target"
            ;;
    esac
    [[ -e "$target" || -L "$target" ]] || return 0
    sudo rm -rf -- "$target"
}

legacy_monolith_pids() {
    pgrep -f '(^|[[:space:]/])host/BMI30[.]200[.]py([^[:space:]]*)?([[:space:]]|$)' || true
}

stop_legacy_monolith() {
    offline_activation && return 0
    local -a pids=()
    local pid alive
    command -v pgrep >/dev/null 2>&1 || {
        warn 'pgrep недоступен; безопасный переход с mono на split невозможен'
        return 1
    }
    mapfile -t pids < <(legacy_monolith_pids)
    (( ${#pids[@]} > 0 )) || return 0

    info "Останавливаю BMI30 mono перед запуском split (PID: ${pids[*]})"
    for pid in "${pids[@]}"; do
        kill -TERM "$pid" 2>/dev/null || sudo kill -TERM "$pid" || return 1
    done
    for _ in {1..50}; do
        alive=0
        for pid in "${pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive=1
                break
            fi
        done
        (( alive == 0 )) && return 0
        sleep 0.1
    done
    warn "BMI30 mono не завершился; split-система не будет запущена"
    return 1
}

install_project_agent() {
    if [[ "${BMI30_AGENT_BOOTSTRAPPED:-0}" == "1" ]]; then
        info 'BMI30 Agent уже проверен текущей транзакцией обновления'
        return 0
    fi
    [[ -x "$AGENT_BOOTSTRAP" ]] || {
        warn "Установщик BMI30 Agent отсутствует: $AGENT_BOOTSTRAP"
        return 1
    }
    info 'Проверяю и обновляю аппаратно-привязанный BMI30 Agent'
    if (( EUID == 0 )); then
        "$AGENT_BOOTSTRAP" || return 1
    else
        sudo "$AGENT_BOOTSTRAP" || return 1
    fi
    export BMI30_AGENT_BOOTSTRAPPED=1
}

write_active_env() {
    local id="$1"
    local selected_by="${2:-terminal-menu}"
    local tmp selected_at
    load_bundle "$id" || return 1
    selected_at="$(date -Iseconds)"
    tmp="$(mktemp "$HOST_DIR/.bmi30_split_active_version.env.XXXXXX")"

    {
        printf '# Auto-generated by switch_bmi30_split_versions.sh\n'
        printf '# All runtime components come from one validated complete bundle.\n'
        printf 'BMI30_SPLIT_VERSION=%q\n' "$id"
        printf 'BMI30_SPLIT_LABEL=%q\n' "$BMI30_BUNDLE_LABEL"
        printf 'BMI30_SPLIT_SELECTED_BY=%q\n' "$selected_by"
        printf 'BMI30_SPLIT_SELECTED_AT=%q\n' "$selected_at"
        printf 'BMI30_SPLIT_BUNDLE_ID=%q\n' "$id"
        printf 'BMI30_BUNDLE_ORIGIN=%q\n' "$BMI30_BUNDLE_ORIGIN"
        printf 'BMI30_CORE_PATH=%q\n' "$(runtime_rel "$BMI30_BUNDLE_CORE_REL")"
        # Core resolves a relative BMI30_ENGINE_SOURCE against its own HOST_DIR.
        # Use an absolute path so a nested active runtime is not prefixed twice.
        printf 'BMI30_ENGINE_SOURCE=%q\n' "$SCRIPT_DIR/$(runtime_rel "$BMI30_BUNDLE_ENGINE_REL")"
        printf 'BMI30_GUI_PATH=%q\n' "$(runtime_rel "$BMI30_BUNDLE_GUI_REL")"
        printf 'BMI30_PORTAL_PATH=%q\n' "$(runtime_rel "$BMI30_BUNDLE_PORTAL_REL")"
        printf 'BMI30_PROJECT_CONFIG_PATH=%q\n' "$(runtime_rel "$BMI30_BUNDLE_PROJECT_CONFIG_REL")"
        printf 'BMI30_CONFIG_JSON=%q\n' "$PERSISTENT_CONFIG_PATH"
        printf 'BMI30_MODE_STATE_JSON=%q\n' "$PERSISTENT_MODE_STATE_PATH"
        printf 'BMI30_DC_FILE=%q\n' "$SCRIPT_DIR/host/bmi30_active_runtime/project/host/dc_offset_samples.npz"
        printf 'BMI30_TAG_RESPONSE_FILTER_DIR=%q\n' "$SCRIPT_DIR/host/bmi30_active_runtime/project/host/tag_response_filters"
        printf 'BMI30_PLAYER_RECORD_DIR=%q\n' "$SCRIPT_DIR/host/bmi30_active_runtime/project/host/player_recordings"
        printf 'BMI30_FIRMWARE_MANIFEST=%q\n' "$SCRIPT_DIR/$(runtime_rel "$BMI30_BUNDLE_RELEASE_REL")"
        printf 'BMI30_SERVICE_URL=%q\n' "$SERVICE_URL_DEFAULT"
    } > "$tmp"
    chmod 0644 "$tmp"
    mv -- "$tmp" "$ACTIVE_ENV"
    restore_workspace_file_owner "$ACTIVE_ENV"
}

validate_staged_runtime() {
    local stage="$1"
    local id="$2"
    load_bundle "$id" || return 1

    python3 -m py_compile \
        "$stage/$BMI30_BUNDLE_CORE_REL" \
        "$stage/$BMI30_BUNDLE_ENGINE_REL" \
        "$stage/$BMI30_BUNDLE_GUI_REL" \
        "$stage/$BMI30_BUNDLE_PORTAL_REL" \
        "$stage/$BMI30_BUNDLE_USB_VENDOR_REL/usb_stream.py" || return 1
    python3 -m json.tool "$stage/$BMI30_BUNDLE_PROJECT_CONFIG_REL" >/dev/null || return 1
    if [[ -f "$stage/$BMI30_BUNDLE_SYSTEM_CONFIG_REL" ]]; then
        python3 -m json.tool "$stage/$BMI30_BUNDLE_SYSTEM_CONFIG_REL" >/dev/null || return 1
    fi
}

release_manifest_matches_bundle() {
    local manifest="$1"
    local id="$2"
    [[ -f "$manifest" ]] || return 1
    (
        unset BMI30_FIRMWARE_VERSION BMI30_FIRMWARE_BUNDLE_ID
        # shellcheck source=/dev/null
        source "$manifest"
        [[ "${BMI30_FIRMWARE_BUNDLE_ID:-}" == "$id" ]]
    )
}

deploy_system_runtime_from_dir() {
    local source_dir="$1"
    local id="$2"
    local use_release_override="${3:-1}"
    local portal_src project_config system_config installed_config release_src
    load_bundle "$id" || return 1

    portal_src="$source_dir/$BMI30_BUNDLE_PORTAL_REL"
    project_config="$source_dir/$BMI30_BUNDLE_PROJECT_CONFIG_REL"
    system_config="$source_dir/$BMI30_BUNDLE_SYSTEM_CONFIG_REL"
    installed_config="$source_dir/$BMI30_BUNDLE_INSTALLED_CONFIG_REL"
    release_src="$source_dir/$BMI30_BUNDLE_RELEASE_REL"

    sudo install -d -m 0755 "$(dirname -- "$PORTAL_DST")" "$(dirname -- "$INSTALLED_CONFIG_DST")" || return 1
    sudo install -d -m 0755 "$(dirname -- "$PORTAL_CONFIG_DST")" || return 1
    sudo install -p -m 0755 "$portal_src" "$PORTAL_DST" || return 1
    # Keep security/auth settings and the current activation's detector state
    # synchronized. New-version defaults were selected after services stopped.
    install_private_config_mirror "$PERSISTENT_CONFIG_PATH" "$PORTAL_CONFIG_DST" || return 1
    install_private_config_mirror "$PERSISTENT_CONFIG_PATH" "$INSTALLED_CONFIG_DST" || return 1
    info 'Настройки доступа и параметры активной версии синхронизированы'
    if [[ "$use_release_override" == "1" && -n "$RELEASE_MANIFEST_OVERRIDE" ]]; then
        [[ -f "$RELEASE_MANIFEST_OVERRIDE" ]] || return 1
        install -m 0644 "$RELEASE_MANIFEST_OVERRIDE" "$release_src" || return 1
    elif [[ "$use_release_override" == "1" ]] \
        && release_manifest_matches_bundle "$HOST_DIR/bmi30_firmware_release.env" "$id"
    then
        # Reactivating or restoring the exact same code bundle must retain the
        # content-addressed firmware identity.  The bundle-local manifest is a
        # bootstrap fallback and has no proof that a copied project changed.
        install -m 0644 "$HOST_DIR/bmi30_firmware_release.env" "$release_src" || return 1
        info 'Версия и content-signature неизменного комплекта сохранены'
    fi
    install -m 0644 "$release_src" "$HOST_DIR/bmi30_firmware_release.env" || return 1
    restore_workspace_file_owner "$HOST_DIR/bmi30_firmware_release.env" || return 1
}

install_private_config_mirror() {
    local source="$1" target="$2" mode=0640
    if [[ -f "$target" && "$(stat -c '%a' -- "$target")" == "600" ]]; then
        mode=0600
    fi
    sudo install -m "$mode" "$source" "$target"
}

configure_hardware_pwm() {
    local id="$1"
    load_bundle "$id" || return 1
    [[ "${BMI30_BUNDLE_HARDWARE_PWM:-0}" == "1" ]] || return 0

    local boot_config="/boot/firmware/config.txt"
    local pwm_chip="/sys/class/pwm/pwmchip0"
    local pwm_channel="$pwm_chip/pwm0"
    local tmp_config tmp_rule current_mode
    tmp_config="$(mktemp /tmp/bmi30-pwm-config.XXXXXX)"
    tmp_rule="$(mktemp /tmp/bmi30-pwm-udev.XXXXXX)"

    if [[ ! -f "$boot_config" ]]; then
        rm -f -- "$tmp_config" "$tmp_rule"
        warn "Boot config не найден: $boot_config"
        return 1
    fi

    awk '
        BEGIN { written = 0 }
        /^dtoverlay=pwm([,-]|$)/ || /^dtoverlay=pwm-2chan([,-]|$)/ {
            if (!written) {
                print "dtoverlay=pwm,pin=12,func=4"
                written = 1
            }
            next
        }
        { print }
        END {
            if (!written) {
                print "dtoverlay=pwm,pin=12,func=4"
            }
        }
    ' "$boot_config" > "$tmp_config"

    if ! cmp -s "$tmp_config" "$boot_config"; then
        current_mode="$(stat -c '%a' "$boot_config" 2>/dev/null || printf '0755')"
        if [[ ! -f "${boot_config}.bmi30-before-hardware-pwm" ]]; then
            sudo cp -a "$boot_config" "${boot_config}.bmi30-before-hardware-pwm" || {
                rm -f -- "$tmp_config" "$tmp_rule"
                return 1
            }
        fi
        sudo install -m "$current_mode" "$tmp_config" "$boot_config" || {
            rm -f -- "$tmp_config" "$tmp_rule"
            return 1
        }
        info 'Boot config: один аппаратный PWM0 закреплён за GPIO12'
    fi

    printf '%s\n' \
        'SUBSYSTEM=="pwm", ACTION!="remove", PROGRAM="/bin/sh -c '\''chgrp -R gpio /sys%p && chmod -R g=u /sys%p'\''"' \
        > "$tmp_rule"
    if ! cmp -s "$tmp_rule" /etc/udev/rules.d/99-bmi30-hardware-pwm.rules; then
        sudo install -m 0644 "$tmp_rule" /etc/udev/rules.d/99-bmi30-hardware-pwm.rules || {
            rm -f -- "$tmp_config" "$tmp_rule"
            return 1
        }
        sudo udevadm control --reload-rules || true
    fi
    rm -f -- "$tmp_config" "$tmp_rule"

    if [[ ! -d "$pwm_chip" ]]; then
        sudo dtoverlay pwm pin=12 func=4 || return 1
        for _ in {1..200}; do
            [[ -d "$pwm_chip" ]] && break
            sleep 0.01
        done
    fi
    [[ -d "$pwm_chip" ]] || {
        warn 'Аппаратный pwmchip0 не появился'
        return 1
    }

    pinctrl set 12 a0 || return 1
    if [[ ! -d "$pwm_channel" ]]; then
        printf '0' > "$pwm_chip/export" || return 1
    fi
    for _ in {1..200}; do
        if [[ -w "$pwm_channel/period" && -w "$pwm_channel/duty_cycle" && -w "$pwm_channel/enable" ]]; then
            break
        fi
        sleep 0.01
    done
    [[ -w "$pwm_channel/period" && -w "$pwm_channel/duty_cycle" && -w "$pwm_channel/enable" ]] || {
        warn 'Аппаратный PWM0 экспортирован, но недоступен пользователю BMI30'
        return 1
    }
    printf '0' > "$pwm_channel/duty_cycle" || return 1
    printf '0' > "$pwm_channel/enable" || return 1
    info 'Аппаратный PWM0_CHAN0 на GPIO12 доступен; software fallback запрещён'
}

bundle_id_from_env_file() {
    local env_file="$1"
    [[ -f "$env_file" ]] || return 1
    (
        unset BMI30_SPLIT_BUNDLE_ID
        # shellcheck source=/dev/null
        source "$env_file"
        [[ "${BMI30_SPLIT_BUNDLE_ID:-}" =~ ^[A-Za-z0-9._-]+$ ]] || exit 1
        printf '%s' "$BMI30_SPLIT_BUNDLE_ID"
    )
}

prepare_persistent_runtime_state() {
    local stage="$1" previous_id="$2" id="$3" tx_dir="$4"
    local -a recovery=()
    [[ "${BMI30_ACTIVATION_RECOVERY_STATE:-0}" == "1" ]] && recovery=(--recovery)
    python3 "$SCRIPT_DIR/utilities/bmi30_runtime_policy.py" prepare \
        --stage-host "$stage/project/host" --active-host "$ACTIVE_RUNTIME_DIR/project/host" \
        --config "$PERSISTENT_CONFIG_PATH" --mode "$PERSISTENT_MODE_STATE_PATH" \
        --transaction "$tx_dir" --host "$HOST_DIR" --current "$previous_id" --target "$id" "${recovery[@]}"
}

configure_portal_persistent_config() {
    local tmp
    tmp="$(mktemp /tmp/bmi30-portal-config-dropin.XXXXXX)"
    {
        printf '[Service]\n'
        printf 'Environment="BMI30_CONFIG_JSON=%s"\n' "$PERSISTENT_CONFIG_PATH"
        printf 'Environment="BMI30_MODE_STATE_JSON=%s"\n' "$PERSISTENT_MODE_STATE_PATH"
    } > "$tmp"
    sudo install -D -m 0644 "$tmp" "$PORTAL_CONFIG_DROPIN" || {
        rm -f -- "$tmp"
        return 1
    }
    rm -f -- "$tmp"
    runtime_systemctl daemon-reload || return 1
}

configure_bluetooth_audio_compatibility() {
    # PulseAudio 16 cannot decode the ERAZER XP6 mSBC stream reliably.  The
    # already-installed PipeWire Bluetooth backend keeps that same stream
    # continuous and exposes its PulseAudio-compatible socket to Portal/Core.
    local audio_user audio_uid runtime_dir
    local -a user_systemctl pulse_client
    audio_user="${BMI30_BLUETOOTH_AUDIO_USER:-techaid}"
    audio_uid="$(id -u "$audio_user" 2>/dev/null || true)"
    runtime_dir="/run/user/$audio_uid"
    user_systemctl=(sudo -u "$audio_user" env "XDG_RUNTIME_DIR=$runtime_dir" systemctl --user)
    pulse_client=(sudo -u "$audio_user" env "XDG_RUNTIME_DIR=$runtime_dir" "PULSE_SERVER=unix:$runtime_dir/pulse/native")
    if [[ -n "$audio_uid" ]] \
        && command -v pipewire-pulse >/dev/null 2>&1 \
        && command -v wireplumber >/dev/null 2>&1 \
        && [[ -S "$runtime_dir/bus" ]]
    then
        "${user_systemctl[@]}" mask --now --force pulseaudio.socket pulseaudio.service >/dev/null 2>&1 || true
        if "${user_systemctl[@]}" enable --now \
            pipewire.socket pipewire.service wireplumber.service pipewire-pulse.socket >/dev/null \
            && "${pulse_client[@]}" pactl info 2>/dev/null | grep -Fq 'PulseAudio (on PipeWire'
        then
            info 'Bluetooth audio uses PipeWire; mSBC wideband and pactl compatibility are active'
            return 0
        fi
        warn 'PipeWire audio activation failed; restoring PulseAudio CVSD fallback'
        "${user_systemctl[@]}" disable --now pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true
        "${user_systemctl[@]}" unmask pulseaudio.socket pulseaudio.service >/dev/null 2>&1 || true
        "${user_systemctl[@]}" enable --now pulseaudio.socket pulseaudio.service >/dev/null 2>&1 || true
    fi

    # Safe fallback for systems without the tested PipeWire Bluetooth stack.
    [[ -f "$PULSE_DEFAULT_PA" ]] || {
        warn "PulseAudio config не найден: $PULSE_DEFAULT_PA"
        return 0
    }
    if grep -Eq '^load-module[[:space:]]+module-bluetooth-discover.*enable_msbc=(no|false|0)([[:space:]]|$)' "$PULSE_DEFAULT_PA"; then
        return 0
    fi
    sudo sed -i -E \
        '/^load-module[[:space:]]+module-bluetooth-discover([[:space:]]|$)/ { s/[[:space:]]+enable_msbc=[^[:space:]]+//g; s/$/ enable_msbc=no/; }' \
        "$PULSE_DEFAULT_PA" || return 1
    grep -Eq '^load-module[[:space:]]+module-bluetooth-discover.*enable_msbc=no([[:space:]]|$)' "$PULSE_DEFAULT_PA" || return 1
    info 'PulseAudio fallback is configured for reliable narrowband CVSD'
}

wait_service_stable() {
    local service="$1"
    local required="${2:-3}"
    local attempts="${3:-30}"
    local stable=0 state i

    for ((i = 0; i < attempts; i++)); do
        state="$(systemctl is-active "$service" 2>/dev/null || true)"
        if [[ "$state" == "active" ]]; then
            stable=$((stable + 1))
            if (( stable >= required )); then
                return 0
            fi
        else
            stable=0
            if [[ "$state" == "failed" ]]; then
                return 1
            fi
        fi
        sleep 1
    done
    return 1
}

wait_core_api() {
    local i
    for ((i = 0; i < 30; i++)); do
        if curl -fsS --max-time 2 "$SERVICE_URL_DEFAULT/api/status" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

restart_runtime_services() {
    offline_activation && return 0
    sudo systemctl restart "$CORE_SERVICE" || return 1
    sudo systemctl restart "$PORTAL_SERVICE" || return 1
    wait_service_stable "$CORE_SERVICE" 3 30 || return 1
    wait_service_stable "$PORTAL_SERVICE" 2 15 || return 1
    wait_core_api || return 1
}

rollback_activation() {
    local previous_id="$1"
    local rollback_runtime="$2"
    local env_backup="$3"

    warn 'Переключение не завершилось; возвращаю предыдущий комплект'
    local assets_backup="$(dirname -- "$env_backup")/portable-assets.before"
    if [[ -f "$assets_backup/snapshot.json" ]]; then
        sudo --preserve-env=BMI30_OFFLINE_ACTIVATION python3 "$PORTABLE_ASSETS" restore --backup "$assets_backup" || return 1
    fi
    runtime_systemctl stop "$CORE_SERVICE" "$PORTAL_SERVICE" >/dev/null 2>&1 || true
    python3 "$SCRIPT_DIR/utilities/bmi30_runtime_policy.py" rollback --transaction "$(dirname -- "$env_backup")" || return 1
    remove_runtime_tree "$ACTIVE_RUNTIME_DIR"
    if [[ -d "$rollback_runtime" ]]; then
        mv -- "$rollback_runtime" "$ACTIVE_RUNTIME_DIR"
    fi
    if [[ -f "$env_backup" ]]; then
        install -m 0644 "$env_backup" "$ACTIVE_ENV"
    fi
    if [[ -n "$previous_id" && -d "$ACTIVE_RUNTIME_DIR" ]] && validate_bundle "$previous_id" 1; then
        deploy_system_runtime_from_dir "$ACTIVE_RUNTIME_DIR" "$previous_id" 0 || true
    fi
    runtime_systemctl restart "$CORE_SERVICE" "$PORTAL_SERVICE" >/dev/null 2>&1 || true
}

secure_runtime_config_mode() {
    local config="$1"
    if [[ -L "$config" ]]; then
        # Activation binds the existing private settings file into the runtime.
        # Do not follow that link with chmod or loosen the preserved file mode.
        [[ -f "$config" ]] || return 1
        [[ "$(readlink -f -- "$config")" == "$(readlink -f -- "$PERSISTENT_CONFIG_PATH")" ]] || return 1
        return 0
    fi
    local mode
    mode="$(stat -c '%a' -- "$config")" || return 1
    [[ "$mode" == "600" || "$mode" == "640" ]] && return 0
    chmod 0640 "$config"
}

activate_bundle() {
    local id="$1"
    validate_bundle "$id" 1 || return 1
    load_bundle "$id"

    local stage rollback_runtime tx_dir env_backup previous_id
    stage="$HOST_DIR/.bmi30_active_runtime.stage.$$"
    rollback_runtime="$HOST_DIR/.bmi30_active_runtime.rollback.$$"
    tx_dir="$(mktemp -d /tmp/bmi30-bundle-switch.XXXXXX)"
    env_backup="$tx_dir/active_env.before"
    remove_runtime_tree "$stage"
    remove_runtime_tree "$rollback_runtime"
    mkdir -p "$stage"

    rsync -a --delete "$(bundle_dir "$id")/project/" "$stage/project/"
    validate_staged_runtime "$stage" "$id" || {
        rm -rf -- "$stage" "$tx_dir"
        warn "Staged runtime не прошёл compile/JSON-проверку: $id"
        return 1
    }

    if ! install_project_agent; then
        rm -rf -- "$stage" "$tx_dir"
        warn 'BMI30 Agent не обновлён; runtime оставлен без изменений'
        return 1
    fi

    if [[ -n "$PREVIOUS_ACTIVE_ENV_OVERRIDE" && -f "$PREVIOUS_ACTIVE_ENV_OVERRIDE" ]]; then
        install -m 0644 "$PREVIOUS_ACTIVE_ENV_OVERRIDE" "$env_backup"
        previous_id="$(bundle_id_from_env_file "$PREVIOUS_ACTIVE_ENV_OVERRIDE" || true)"
    else
        load_active_env
        previous_id="$BMI30_SPLIT_BUNDLE_ID"
        [[ -f "$ACTIVE_ENV" ]] && install -m 0644 "$ACTIVE_ENV" "$env_backup"
    fi

    if ! stop_legacy_monolith; then
        rm -rf -- "$stage" "$tx_dir"
        return 1
    fi

    if offline_activation; then
        info "Подготавливаю комплект на отключённом загрузочном диске"
    else
        info "Останавливаю BMI30-службы для атомарной замены комплекта"
    fi
    runtime_systemctl stop "$CORE_SERVICE" "$PORTAL_SERVICE" || {
        rm -rf -- "$stage" "$tx_dir"
        return 1
    }

    # Core/Portal are stopped: no writer may resurrect old detector preferences.
    if ! prepare_persistent_runtime_state "$stage" "$previous_id" "$id" "$tx_dir"; then
        python3 "$SCRIPT_DIR/utilities/bmi30_runtime_policy.py" rollback --transaction "$tx_dir" || return 1
        runtime_systemctl restart "$CORE_SERVICE" "$PORTAL_SERVICE" || true
        rm -rf -- "$stage" "$tx_dir"
        warn "Не удалось подготовить заводские параметры новой версии"
        return 1
    fi

    if [[ -d "$ACTIVE_RUNTIME_DIR" ]]; then
        mv -- "$ACTIVE_RUNTIME_DIR" "$rollback_runtime"
    fi
    mv -- "$stage" "$ACTIVE_RUNTIME_DIR"
    # The core service runs as the workspace user and persists live settings
    # into the active project config. Activations are often launched through
    # sudo, so explicitly restore the workspace owner before starting core.
    restore_workspace_file_owner \
        "$ACTIVE_RUNTIME_DIR/$BMI30_BUNDLE_PROJECT_CONFIG_REL" || {
        rollback_activation "$previous_id" "$rollback_runtime" "$env_backup"
        rm -rf -- "$tx_dir"
        return 1
    }
    secure_runtime_config_mode "$ACTIVE_RUNTIME_DIR/$BMI30_BUNDLE_PROJECT_CONFIG_REL" || {
        rollback_activation "$previous_id" "$rollback_runtime" "$env_backup"
        rm -rf -- "$tx_dir"
        return 1
    }

    if ! write_active_env "$id" "$SELECTED_BY_OVERRIDE" \
        || ! deploy_system_runtime_from_dir "$ACTIVE_RUNTIME_DIR" "$id" \
        || { [[ -f "$(bundle_dir "$id")/metadata/portable-assets.json" ]] && \
             ! sudo --preserve-env=BMI30_OFFLINE_ACTIVATION python3 "$PORTABLE_ASSETS" apply --bundle "$(bundle_dir "$id")" \
                 --backup "$tx_dir/portable-assets.before"; } \
        || { [[ "${BMI30_RUNTIME_ONLY:-0}" != "1" ]] && ! configure_bluetooth_audio_compatibility; } \
        || ! configure_portal_persistent_config \
        || { [[ "${BMI30_RUNTIME_ONLY:-0}" != "1" ]] && ! configure_hardware_pwm "$id"; } \
        || ! restart_runtime_services
    then
        rollback_activation "$previous_id" "$rollback_runtime" "$env_backup"
        rm -rf -- "$tx_dir"
        return 1
    fi

    remove_runtime_tree "$rollback_runtime"
    rm -rf -- "$tx_dir"
    printf 'Активирован полный BMI30-комплект: %s (%s)\n' "$BMI30_BUNDLE_LABEL" "$id"
    show_runtime_status
}

run_core_service_action() {
    local action="$1"
    if sudo systemctl "$action" "$CORE_SERVICE"; then
        printf 'Готово: %s %s\n' "$CORE_SERVICE" "$action"
    else
        printf 'Команда завершилась с ошибкой: %s %s\n' "$CORE_SERVICE" "$action" >&2
        return 1
    fi
}

validate_all_bundles() {
    load_bundle_ids
    [[ ${#BUNDLE_IDS[@]} -gt 0 ]] || die "Нет полных BMI30-комплектов"
    local id failed=0
    for id in "${BUNDLE_IDS[@]}"; do
        validate_bundle "$id" || failed=1
    done
    (( failed == 0 )) || return 1
}

interactive_menu() {
    while true; do
        local menu_month
        menu_month="$(current_menu_month)"
        load_bundle_ids "$menu_month"
        [[ ${#BUNDLE_IDS[@]} -gt 0 ]] || die "Нет полных BMI30-комплектов за $menu_month"

        printf '\n==========================================\n'
        printf '   BMI30 websplit versions: %s\n' "$menu_month"
        printf '==========================================\n'
        show_runtime_status
        printf '\n'

        local i
        for i in "${!BUNDLE_IDS[@]}"; do
            show_bundle_line "$((i + 1))" "${BUNDLE_IDS[$i]}"
        done
        printf '0) Exit\n\nВведите номер: '

        local choice
        read -r choice
        if [[ "$choice" == "0" ]]; then
            return 0
        fi
        if ! [[ "$choice" =~ ^[0-9]+$ ]]; then
            echo 'Неверный выбор'
            pause_menu
            continue
        fi

        if (( choice >= 1 && choice <= ${#BUNDLE_IDS[@]} )); then
            activate_bundle "${BUNDLE_IDS[$((choice - 1))]}" || true
        else
            echo 'Неверный выбор'
        fi
        pause_menu
    done
}

main() {
    if offline_activation; then
        # Only a deliberately isolated chroot may bypass live runtime checks.
        # Never bind the host /proc, /sys or /run into that chroot.
        [[ $EUID -eq 0 && ! -e /proc/1/root && ! -d /run/systemd/system && ! -d /sys/class/pwm ]] \
            || die 'Offline activation requires an isolated root chroot without host proc/sys/run'
        export BMI30_RUNTIME_ONLY=1 BMI30_AGENT_BOOTSTRAPPED=1
    fi
    case "${1:-}" in
        '')
            interactive_menu
            ;;
        --list)
            [[ $# -eq 1 ]] || die "--list не принимает параметры"
            show_list
            ;;
        --validate)
            if [[ $# -eq 1 ]]; then
                validate_all_bundles
            elif [[ $# -eq 2 ]]; then
                validate_bundle "$2"
            else
                die "Использование: --validate [ID]"
            fi
            ;;
        --activate)
            [[ $# -eq 2 ]] || die "Использование: --activate ID"
            activate_bundle "$2"
            ;;
        --help|-h)
            usage
            ;;
        *)
            die "Неизвестный параметр: $1"
            ;;
    esac
}

main "$@"
