#!/usr/bin/env bash
# Run a portal-triggered firmware update or one-step rollback outside the
# portal service cgroup, so restarting the portal cannot interrupt the job.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
WORKSPACE_DIR="${BMI30_FIRMWARE_WORKSPACE:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
STATE_DIR="${STATE_DIR:-$WORKSPACE_DIR/.bmi30_cloud_sync}"
OPERATION_FILE="${BMI30_PROGRESS_FILE:-$STATE_DIR/portal_operation.json}"
ROLLBACK_FILE="${BMI30_ROLLBACK_FILE:-$STATE_DIR/rollback_state.env}"
LOCK_FILE="$STATE_DIR/portal_operation.lock"
LOG_FILE="$STATE_DIR/portal_operation.log"
ACTION="${1:-}"

json_escape() {
    local value="${1:-}"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    value="${value//$'\t'/\\t}"
    printf '%s' "$value"
}

write_operation() {
    local status="$1"
    local progress="$2"
    local message="$3"
    local error="${4:-}"
    local temporary

    mkdir -p "$STATE_DIR"
    temporary="$STATE_DIR/.portal_operation.tmp.$$"
    {
        printf '{"action":"%s","status":"%s","progress":%d,' \
            "$(json_escape "$ACTION")" \
            "$(json_escape "$status")" \
            "$progress"
        printf '"message":"%s","error":"%s","updated_at":%d}\n' \
            "$(json_escape "$message")" \
            "$(json_escape "$error")" \
            "$(date +%s)"
    } > "$temporary"
    mv -f -- "$temporary" "$OPERATION_FILE"
}

fail_operation() {
    local message="$1"
    write_operation "failed" "${2:-1}" "Operation failed." "$message"
    printf '[ERROR] %s\n' "$message" >> "$LOG_FILE"
    exit 1
}

main() {
    [[ "$ACTION" == "update" || "$ACTION" == "rollback" ]] \
        || fail_operation "Expected operation: update or rollback."

    mkdir -p "$STATE_DIR"
    exec 9>"$LOCK_FILE"
    flock -n 9 || fail_operation "Another firmware operation is already running."
    : > "$LOG_FILE"

    export BMI30_PROGRESS_FILE="$OPERATION_FILE"
    if "$SCRIPT_DIR/server_firmware.sh" "$ACTION" --project "$WORKSPACE_DIR" >> "$LOG_FILE" 2>&1; then
        exit 0
    fi
    fail_operation "Server firmware operation failed. See $LOG_FILE for details." 95
}

main "$@"
