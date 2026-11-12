#!/usr/bin/env bash
# Verify or restore a BMI30 project recovery archive on another Raspberry Pi.

set -euo pipefail

ARCHIVE=""
TARGET="/home/techaid/Documents"
APPLY=0
VERIFY_ONLY=0
ACTIVATE_ID=""
ASSUME_YES=0

usage() {
    cat <<'EOF'
Использование:
  ./utilities/restore_bmi30_recovery_archive.sh ARCHIVE --verify-only
  sudo ./utilities/restore_bmi30_recovery_archive.sh ARCHIVE --apply [опции]

Опции:
  --target PATH        Каталог Documents на целевом Raspberry Pi.
  --apply              Применить архив после проверки.
  --activate saved     Развернуть сохранённый активный полный комплект.
  --activate ID        Развернуть указанный bundle ID.
  --yes                Не спрашивать подтверждение.
  --verify-only        Только проверить SHA-256, структуру и bundle.

Восстановление выполняется через rsync без --delete. Текущий проект сначала
сохраняется в target/backups/pre_recovery_restore_*.tar.gz. Секреты, .git,
venv и системные пакеты в recovery-архив не входят.
EOF
}

die() {
    printf '[ERR] %s\n' "$*" >&2
    exit 1
}

info() {
    printf '[INFO] %s\n' "$*"
}

parse_args() {
    [[ $# -gt 0 ]] || {
        usage
        exit 1
    }

    ARCHIVE="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target)
                [[ $# -ge 2 ]] || die "После --target нужен путь"
                TARGET="$2"
                shift 2
                ;;
            --apply)
                APPLY=1
                shift
                ;;
            --activate)
                [[ $# -ge 2 ]] || die "После --activate нужен saved или bundle ID"
                ACTIVATE_ID="$2"
                shift 2
                ;;
            --yes)
                ASSUME_YES=1
                shift
                ;;
            --verify-only)
                VERIFY_ONLY=1
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                die "Неизвестный параметр: $1"
                ;;
        esac
    done
}

verify_sidecar() {
    local sidecar="${ARCHIVE}.sha256"
    local expected actual
    if [[ ! -f "$sidecar" ]]; then
        info "Файл ${sidecar##*/} отсутствует; проверка продолжится по внутренним SHA256SUMS"
        return 0
    fi
    expected="$(awk 'NR == 1 {print $1}' "$sidecar")"
    actual="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
    [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || die "Некорректный SHA-256 в $sidecar"
    [[ "${expected,,}" == "${actual,,}" ]] || die "SHA-256 recovery-архива не совпадает"
    info "SHA-256 recovery-архива: PASS"
}

verify_tar_paths() {
    local entry
    while IFS= read -r entry; do
        case "$entry" in
            /*|../*|*/../*|*/..)
                die "Небезопасный путь в архиве: $entry"
                ;;
        esac
    done < <(tar -tzf "$ARCHIVE")
}

create_safety_backup() {
    [[ -d "$TARGET" ]] || return 0
    local backup_dir archive_name target_parent target_name
    backup_dir="$TARGET/backups"
    mkdir -p "$backup_dir"
    archive_name="$backup_dir/pre_recovery_restore_$(date +%Y%m%d_%H%M%S).tar.gz"
    target_parent="$(dirname -- "$TARGET")"
    target_name="$(basename -- "$TARGET")"

    tar -czf "$archive_name" \
        --exclude="$target_name/.git" \
        --exclude="$target_name/.venv" \
        --exclude="$target_name/.usbvenv" \
        --exclude="$target_name/.codex" \
        --exclude="$target_name/.agents" \
        --exclude="$target_name/backups" \
        --exclude="$target_name/.bmi30_cloud_sync" \
        --exclude="$target_name/secrets" \
        --exclude="$target_name/host/bmi30_active_runtime" \
        --exclude='*.log' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        -C "$target_parent" "$target_name"
    info "Safety backup текущего проекта: $archive_name"
}

main() {
    parse_args "$@"
    [[ -f "$ARCHIVE" ]] || die "Архив не найден: $ARCHIVE"
    command -v tar >/dev/null || die "Не найден tar"
    command -v rsync >/dev/null || die "Не найден rsync"

    verify_sidecar
    verify_tar_paths

    local workdir root recovery_env saved_id
    workdir="$(mktemp -d /tmp/bmi30-recovery-restore.XXXXXX)"
    trap 'rm -rf -- "${workdir:-}"' EXIT
    tar -xzf "$ARCHIVE" -C "$workdir"
    root="$workdir/BMI30-project-recovery"
    [[ -d "$root/Documents/host/bmi30_split_bundles" ]] || die "В архиве нет полных BMI30 bundle"
    [[ -x "$root/Documents/switch_bmi30_split_versions.sh" ]] || die "В архиве нет executable переключателя"
    [[ -f "$root/RECOVERY.env" ]] || die "В архиве нет RECOVERY.env"
    [[ -f "$root/RECOVERY_SHA256SUMS" ]] || die "В архиве нет RECOVERY_SHA256SUMS"

    if ! (cd "$root" && sha256sum -c RECOVERY_SHA256SUMS >/dev/null); then
        die "Контрольные суммы файлов внутри recovery-архива не совпадают"
    fi
    info "Внутренние SHA-256 всех файлов: PASS"

    unset BMI30_RECOVERY_ACTIVE_BUNDLE
    # shellcheck source=/dev/null
    source "$root/RECOVERY.env"
    saved_id="${BMI30_RECOVERY_ACTIVE_BUNDLE:-}"

    "$root/Documents/switch_bmi30_split_versions.sh" --validate
    info "Структура recovery-архива и полные bundle: PASS"

    if (( VERIFY_ONLY == 1 || APPLY == 0 )); then
        info "Проверка завершена без применения"
        if (( APPLY == 0 && VERIFY_ONLY == 0 )); then
            info "Для восстановления повторите с --apply"
        fi
        return 0
    fi

    if (( ASSUME_YES == 0 )); then
        printf 'Восстановить проект в %s? [y/N] ' "$TARGET"
        local answer
        read -r answer
        [[ "$answer" =~ ^[Yy]$ ]] || die "Восстановление отменено"
    fi

    create_safety_backup
    mkdir -p "$TARGET"
    rsync -a \
        --exclude='/host/bmi30_split_active_version.env' \
        --exclude='/host/bmi30_active_runtime/' \
        "$root/Documents/" "$TARGET/"
    chmod +x \
        "$TARGET/switch_bmi30_split_versions.sh" \
        "$TARGET/utilities/create_bmi30_split_bundle.sh" \
        "$TARGET/utilities/restore_bmi30_recovery_archive.sh"
    "$TARGET/switch_bmi30_split_versions.sh" --validate
    info "Проект и полные bundle восстановлены в $TARGET"

    if [[ -n "$ACTIVATE_ID" ]]; then
        if [[ "$ACTIVATE_ID" == "saved" ]]; then
            ACTIVATE_ID="$saved_id"
        fi
        [[ -n "$ACTIVATE_ID" ]] || die "В RECOVERY.env не указан активный bundle"
        "$TARGET/switch_bmi30_split_versions.sh" --activate "$ACTIVATE_ID"
    else
        info "Runtime не переключался. Выберите комплект: $TARGET/switch_bmi30_split_versions.sh"
    fi
}

main "$@"
