#!/usr/bin/env bash
set -euo pipefail

# Google Drive adapter for Cash 27's portable archive format. This service has
# no database mount. It sees only backup state and rclone configuration.

umask 077

readonly STATE_ROOT="${CASH_BACKUP_STATE_ROOT:-/state}"
readonly STATUS_FILE="${STATE_ROOT}/status.conf"
readonly ACTIVITY_FILE="${STATE_ROOT}/activity.tsv"
readonly DISABLED_FILE="${STATE_ROOT}/automatic-backups-disabled"
readonly RCLONE_REMOTE="${CASH_BACKUP_REMOTE:-}"
readonly RCLONE_PATH="${CASH_BACKUP_PATH:-Cash 27 Backups}"
readonly BACKUP_CONFIGURED="${CASH_BACKUP_CONFIGURED:-0}"
readonly INTERVAL_SECONDS="${CASH_BACKUP_INTERVAL_SECONDS:-86400}"
readonly RETRY_SECONDS="${CASH_BACKUP_RETRY_SECONDS:-3600}"

CURRENT_BACKUP=""
CURRENT_ARCHIVE=""
FAILURE_MESSAGE="Backup service stopped unexpectedly."
RCLONE_OK="0"
SCHEDULER_OK="1"
FINAL_RECORDED="0"

read_value() {
    local name="$1" file="$2" line
    [[ -f "${file}" ]] || return 0
    line="$(grep -m1 -E "^${name}=" "${file}" || true)"
    printf '%s' "${line#*=}"
}

safe_line() {
    printf '%s' "$1" | tr '\t\r\n' '   '
}

now_utc() {
    date -u '+%Y-%m-%dT%H:%M:%SZ'
}

prepare_state() {
    mkdir -p -- "${STATE_ROOT}/outbox" "${STATE_ROOT}/restore"
    chmod 0700 -- "${STATE_ROOT}" "${STATE_ROOT}/outbox" \
        "${STATE_ROOT}/restore"
    touch "${ACTIVITY_FILE}"
    chmod 0600 -- "${ACTIVITY_FILE}"
}

append_activity() {
    local at="$1" state="$2" message="$3" backup_name="$4" trimmed
    printf '%s\t%s\t%s\t%s\n' \
        "${at}" "${state}" "$(safe_line "${message}")" \
        "$(safe_line "${backup_name}")" >>"${ACTIVITY_FILE}"
    if [[ "$(wc -l <"${ACTIVITY_FILE}")" -gt 200 ]]; then
        trimmed="$(mktemp "${STATE_ROOT}/.activity.XXXXXX")"
        tail -n 200 "${ACTIVITY_FILE}" >"${trimmed}"
        chmod 0600 -- "${trimmed}"
        mv -f -- "${trimmed}" "${ACTIVITY_FILE}"
    fi
}

record_status() {
    local state="$1" message="$2" backup_name="${3:-}"
    local at last_success last_success_name temporary
    at="$(now_utc)"
    last_success="$(read_value last_success_at "${STATUS_FILE}")"
    last_success_name="$(read_value backup_name "${STATUS_FILE}")"
    if [[ "${state}" == "success" ]]; then
        last_success="${at}"
        last_success_name="${backup_name}"
    fi
    temporary="$(mktemp "${STATE_ROOT}/.status.XXXXXX")"
    {
        printf 'state=%s\n' "${state}"
        printf 'last_attempt_at=%s\n' "${at}"
        printf 'last_success_at=%s\n' "${last_success}"
        printf 'rclone_ok=%s\n' "${RCLONE_OK}"
        printf 'scheduler_ok=%s\n' "${SCHEDULER_OK}"
        printf 'backup_name=%s\n' "$(safe_line "${last_success_name}")"
        printf 'message=%s\n' "$(safe_line "${message}")"
    } >"${temporary}"
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${STATUS_FILE}"
    append_activity "${at}" "${state}" "${message}" "${backup_name}"
}

fail() {
    FAILURE_MESSAGE="$*"
    printf 'Backup stopped: %s\n' "$*" >&2
    return 1
}

finish_once() {
    local result=$?
    trap - EXIT
    if [[ "${result}" -ne 0 && "${FINAL_RECORDED}" != "1" ]]; then
        if [[ -n "${CURRENT_ARCHIVE}" && -f "${CURRENT_ARCHIVE}" ]]; then
            mv -f -- "${CURRENT_ARCHIVE}" \
                "${STATE_ROOT}/failed-latest.tar.gz" || true
        fi
        record_status "failure" "${FAILURE_MESSAGE}" "${CURRENT_BACKUP}" || true
    fi
    exit "${result}"
}

lock_backup() {
    command -v flock >/dev/null 2>&1 \
        || fail "flock is required by the backup service."
    exec 9>"${STATE_ROOT}/backup.lock"
    flock --nonblock 9 \
        || fail "another backup or restore preparation is already running."
}

check_drive() {
    local remote_list
    [[ "${BACKUP_CONFIGURED}" == "1" && -n "${RCLONE_REMOTE}" ]] \
        || fail "Google Drive backup is not configured; rerun ./setup.sh."
    [[ "${RCLONE_REMOTE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
        || fail "The configured rclone remote name is invalid."
    command -v rclone >/dev/null 2>&1 \
        || fail "rclone is required by the backup service."
    remote_list="$(rclone listremotes)"
    grep -Fxq "${RCLONE_REMOTE}:" <<<"${remote_list}" \
        || fail "The configured rclone remote is missing."
    rclone config show "${RCLONE_REMOTE}" \
        | grep -Eq '^type[[:space:]]*=[[:space:]]*drive$' \
        || fail "The configured rclone remote is not Google Drive."
    rclone lsf "${RCLONE_REMOTE}:${RCLONE_PATH}" --max-depth 1 >/dev/null \
        || fail "Google Drive could not be reached."
    RCLONE_OK="1"
}

run_once() {
    local stamp archive_path destination
    prepare_state
    trap finish_once EXIT
    lock_backup
    if [[ -f "${DISABLED_FILE}" ]]; then
        record_status "disabled" \
            "Automatic backups are turned off in Cash 27 Settings."
        FINAL_RECORDED="1"
        return
    fi
    record_status "running" "Automatic backup started."
    check_drive
    command -v python >/dev/null 2>&1 \
        || fail "Python is required by the backup service."

    stamp="$(python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))')"
    CURRENT_BACKUP="cash-27-full-${stamp}.tar.gz"
    archive_path="${STATE_ROOT}/outbox/${CURRENT_BACKUP}"
    CURRENT_ARCHIVE="${archive_path}"
    destination="${RCLONE_REMOTE}:${RCLONE_PATH%/}/${CURRENT_BACKUP}"

    python -m cash.backup_client create outbox "${CURRENT_BACKUP}" \
        || fail "Cash 27 could not create a verified backup archive."
    [[ -s "${archive_path}" ]] \
        || fail "Cash 27 did not produce the expected backup archive."
    python -m cash.ops verify-archive "${archive_path}" \
        || fail "The newly created backup failed independent verification."
    rclone copyto "${archive_path}" "${destination}" \
        --checksum --immutable --retries 3 \
        || fail "The backup archive could not be uploaded to Google Drive."

    mv -f -- "${archive_path}" "${STATE_ROOT}/latest-local.tar.gz"
    CURRENT_ARCHIVE=""
    record_status "success" "Backup uploaded to Google Drive." \
        "${CURRENT_BACKUP}"
    FINAL_RECORDED="1"
    printf 'Uploaded %s\n' "${destination}"
}

run_loop() {
    local delay
    trap 'exit 0' TERM INT
    while true; do
        if "${BASH_SOURCE[0]}" once; then
            delay="${INTERVAL_SECONDS}"
        else
            delay="${RETRY_SECONDS}"
        fi
        sleep "${delay}" &
        wait $!
    done
}

list_backups() {
    check_drive
    rclone lsf "${RCLONE_REMOTE}:${RCLONE_PATH}" --files-only \
        --include 'cash-27-full-*.tar.gz' | sort -r
}

valid_backup_name() {
    [[ "$1" =~ ^cash-27-full-[0-9]{8}T[0-9]{6}([0-9]{6})?Z\.tar\.gz$ ]]
}

fetch_backup() {
    local name="${1:-}" temporary destination
    valid_backup_name "${name}" || fail "Choose a valid Cash 27 backup name."
    prepare_state
    check_drive
    destination="${STATE_ROOT}/restore/${name}"
    temporary="${destination}.${$}.partial"
    trap 'rm -f -- "${temporary:-}"' RETURN
    rclone copyto "${RCLONE_REMOTE}:${RCLONE_PATH%/}/${name}" "${temporary}" \
        --checksum --retries 3 \
        || fail "The selected backup could not be downloaded from Google Drive."
    [[ -s "${temporary}" ]] || fail "The downloaded backup is empty."
    python -m cash.ops verify-archive "${temporary}" \
        || fail "The downloaded backup failed Cash 27 verification."
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${destination}"
    trap - RETURN
    printf '%s\n' "${destination}"
}

local_archive() {
    local name="${1:-}" destination
    valid_backup_name "${name}" || fail "Choose a valid Cash 27 backup name."
    prepare_state
    lock_backup
    destination="${STATE_ROOT}/restore/${name}"
    python -m cash.backup_client create restore "${name}" >/dev/null
    [[ -s "${destination}" ]] \
        || fail "Cash 27 did not produce the current-state safety archive."
    printf '%s\n' "${destination}"
}

case "${1:-loop}" in
    loop) run_loop ;;
    once) run_once ;;
    list) list_backups ;;
    fetch) fetch_backup "${2:-}" ;;
    local) local_archive "${2:-}" ;;
    verify) python -m cash.ops verify-archive "${2:?archive path required}" ;;
    *) fail "usage: backup-to-drive.sh loop|once|list|fetch NAME|local NAME|verify PATH" ;;
esac
