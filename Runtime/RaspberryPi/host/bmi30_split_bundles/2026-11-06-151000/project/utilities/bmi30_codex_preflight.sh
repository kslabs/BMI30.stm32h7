#!/usr/bin/env bash
set -u

codex_home="${CODEX_HOME:-${HOME}/.codex}"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}"
log_file="${state_dir}/bmi30-codex-preflight.log"
log_db_limit_bytes="${BMI30_CODEX_LOG_DB_LIMIT_BYTES:-134217728}"
log_wal_limit_bytes="${BMI30_CODEX_LOG_WAL_LIMIT_BYTES:-33554432}"
webview_timeout_ms="${BMI30_CODEX_WEBVIEW_TIMEOUT_MS:-90000}"
lock_dir="${XDG_RUNTIME_DIR:-/tmp}"
if [[ ! -d "$lock_dir" || ! -w "$lock_dir" ]]; then
    lock_dir="/tmp"
fi
lock_file="${lock_dir}/bmi30-codex-preflight.lock"

mkdir -p "$state_dir"
if [[ -f "$log_file" ]] && [[ "$(stat -c %s "$log_file" 2>/dev/null || echo 0)" -gt 131072 ]]; then
    mv -f "$log_file" "${log_file}.1"
fi

exec 9>"$lock_file"
if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
    exit 0
fi

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*" >>"$log_file"
}

codex_app_server_running() {
    pgrep -u "$(id -u)" -af '/codex([[:space:]].*)?[[:space:]]app-server' >/dev/null 2>&1
}

latest_codex_extension_dir() {
    local extension_dir=""
    local candidate=""
    local candidates=()

    shopt -s nullglob
    candidates=("${HOME}"/.vscode/extensions/openai.chatgpt-*-linux-*)
    for candidate in "${candidates[@]}"; do
        if [[ -z "$extension_dir" || "$candidate" -nt "$extension_dir" ]]; then
            extension_dir="$candidate"
        fi
    done
    printf '%s\n' "$extension_dir"
}

rotate_large_log_database() {
    local db_path="${codex_home}/logs_2.sqlite"
    local db_size=0
    local wal_size=0
    local backup_root="${codex_home}/maintenance-backups"
    local backup_dir=""
    local stamp=""
    local path=""

    [[ -f "$db_path" ]] || return 0

    if codex_app_server_running; then
        log "log database rotation skipped: Codex app-server is already running"
        return 0
    fi

    db_size="$(stat -c %s "$db_path" 2>/dev/null || printf '0')"
    wal_size="$(stat -c %s "${db_path}-wal" 2>/dev/null || printf '0')"
    if (( db_size < log_db_limit_bytes && wal_size < log_wal_limit_bytes )); then
        log "log database within limits: db_bytes=${db_size} wal_bytes=${wal_size}"
        return 0
    fi

    stamp="$(date '+%Y%m%dT%H%M%S')"
    backup_dir="${backup_root}/logs-${stamp}"
    mkdir -p "$backup_dir"
    for path in "$db_path" "${db_path}-wal" "${db_path}-shm"; do
        if [[ -e "$path" ]]; then
            mv "$path" "$backup_dir/"
        fi
    done
    printf '%s\n' \
        "Rotated by bmi30_codex_preflight.sh at ${stamp}." \
        "Restore only while every Codex app-server process is stopped." \
        >"${backup_dir}/RESTORE.txt"
    log "large log database rotated: db_bytes=${db_size} wal_bytes=${wal_size} backup=${backup_dir}"
    prune_old_log_backups "$backup_root" "$backup_dir"
}

prune_old_log_backups() {
    local backup_root="$1"
    local keep_dir="$2"
    local old_dir=""
    local unexpected=""

    while IFS= read -r old_dir; do
        [[ -n "$old_dir" && "$old_dir" != "$keep_dir" ]] || continue
        case "$old_dir" in
            "${backup_root}"/logs-*) ;;
            *)
                log "old log backup cleanup refused: unexpected path=${old_dir}"
                continue
                ;;
        esac
        unexpected="$(find "$old_dir" -mindepth 1 -maxdepth 1 -type f \
            ! -name 'logs_2.sqlite' ! -name 'logs_2.sqlite-wal' \
            ! -name 'logs_2.sqlite-shm' ! -name 'RESTORE.txt' -print -quit 2>/dev/null)"
        if [[ -n "$unexpected" ]]; then
            log "old log backup cleanup skipped: unexpected file=${unexpected}"
            continue
        fi
        find "$old_dir" -mindepth 1 -maxdepth 1 -type f -delete
        if rmdir "$old_dir"; then
            log "old log backup removed: path=${old_dir}"
        fi
    done < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name 'logs-*' -print 2>/dev/null | sort)
}

extend_webview_startup_timeout() {
    local extension_dir=""
    local extension_js=""

    command -v python3 >/dev/null 2>&1 || return 0
    extension_dir="$(latest_codex_extension_dir)"
    [[ -n "$extension_dir" ]] || return 0
    extension_js="${extension_dir}/out/extension.js"
    [[ -f "$extension_js" && -w "$extension_js" ]] || {
        log "webview timeout patch skipped: not writable path=${extension_js}"
        return 0
    }

    python3 - "$extension_js" "$webview_timeout_ms" >>"$log_file" 2>&1 <<'PY'
import os
import shutil
import sys
import tempfile

path = sys.argv[1]
timeout_ms = int(sys.argv[2])
if timeout_ms < 30_000 or timeout_ms > 300_000:
    raise SystemExit(f"webview_timeout_error=out_of_range value={timeout_ms}")

with open(path, "rb") as handle:
    data = handle.read()

old = b"this.onTimeout()},3e4))"
new = f"this.onTimeout()}},{timeout_ms}))".encode("ascii")

if data.count(new) == 1 and data.count(old) == 0:
    print(f"webview_timeout_ms={timeout_ms} status=already_patched path={path}")
    raise SystemExit(0)
if data.count(old) != 1:
    print(
        f"webview_timeout_ms={timeout_ms} status=pattern_mismatch "
        f"original_count={data.count(old)} patched_count={data.count(new)} path={path}"
    )
    raise SystemExit(0)

backup = path + ".bmi30-30s-original"
shutil.copy2(path, backup)

patched = data.replace(old, new, 1)
directory = os.path.dirname(path)
fd, temporary = tempfile.mkstemp(prefix=".extension.js.", dir=directory)
try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(patched)
        handle.flush()
        os.fsync(handle.fileno())
    shutil.copymode(path, temporary)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
print(f"webview_timeout_ms={timeout_ms} status=patched path={path} backup={backup}")
PY
}

maintain_databases() {
    command -v python3 >/dev/null 2>&1 || return 0
    [[ -d "$codex_home" ]] || return 0

    if codex_app_server_running; then
        log "database maintenance skipped: Codex app-server is already running"
        return 0
    fi

    python3 - "$codex_home" >>"$log_file" 2>&1 <<'PY'
import glob
import os
import sqlite3
import sys

codex_home = sys.argv[1]
for path in sorted(glob.glob(os.path.join(codex_home, "*.sqlite"))):
    name = os.path.basename(path)
    try:
        db = sqlite3.connect(path, timeout=15)
        db.execute("PRAGMA busy_timeout = 15000")
        before_pages = db.execute("PRAGMA page_count").fetchone()[0]
        free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
        checkpoint_before = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

        db.execute("PRAGMA optimize")
        checkpoint_after = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        after_pages = db.execute("PRAGMA page_count").fetchone()[0]
        db.close()
        print(
            f"database={name} pages={before_pages}->{after_pages} "
            f"free_before={free_pages} checkpoint={checkpoint_before}/{checkpoint_after}"
        )
    except sqlite3.Error as exc:
        print(f"database={name} maintenance_error={exc}")
PY
}

warm_file() {
    local path="$1"
    [[ -f "$path" && -r "$path" ]] || return 0
    dd if="$path" of=/dev/null bs=8M status=none 2>/dev/null || true
}

warm_codex_files() {
    local extension_dir=""
    local candidate=""

    extension_dir="$(latest_codex_extension_dir)"

    if [[ -n "$extension_dir" ]]; then
        warm_file "$extension_dir/out/extension.js"
        warm_file "$extension_dir/bin/linux-aarch64/codex"
        for candidate in "$extension_dir"/webview/assets/app-initial-*.js \
                         "$extension_dir"/webview/assets/register-*.js; do
            warm_file "$candidate"
        done
    fi

    for candidate in "$codex_home"/*.sqlite; do
        warm_file "$candidate"
    done
}

started_at="$(date +%s)"
log "preflight started db_limit_bytes=${log_db_limit_bytes} wal_limit_bytes=${log_wal_limit_bytes} webview_timeout_ms=${webview_timeout_ms}"
rotate_large_log_database
maintain_databases
extend_webview_startup_timeout
warm_codex_files
finished_at="$(date +%s)"
log "preflight completed duration_seconds=$((finished_at - started_at))"
