#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly CONFIG_DIR="${CASH_BACKUP_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly CONFIG_FILE="${CONFIG_DIR}/backup.conf"
readonly CONTAINER_NAME="${CASH_CONTAINER_NAME:-cash-27}"

DATA_ROOT="${HOME}/.local/share/cash-27"
RCLONE_REMOTE=""
RCLONE_PATH=""
BACKUP_CONFIGURED="0"
STATE_ROOT=""
STATUS_FILE=""
ACTIVITY_FILE=""
DISABLED_FILE=""
CURRENT_BACKUP=""
CURRENT_ARCHIVE=""
FAILURE_MESSAGE="Backup job stopped unexpectedly."
RCLONE_OK="0"
TIMER_OK="0"
FINAL_RECORDED="0"

read_value() {
    local name="$1"
    local file="$2"
    local line
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

append_activity() {
    local at="$1" state="$2" message="$3" backup_name="$4"
    local trimmed
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
        printf 'timer_ok=%s\n' "${TIMER_OK}"
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

finish() {
    local result=$?
    trap - EXIT
    if [[ "${result}" -ne 0 && "${FINAL_RECORDED}" != "1" \
            && -n "${STATE_ROOT}" && -d "${STATE_ROOT}" ]]; then
        if [[ -n "${CURRENT_ARCHIVE}" && -f "${CURRENT_ARCHIVE}" ]]; then
            mv -f -- "${CURRENT_ARCHIVE}" \
                "${STATE_ROOT}/failed-latest.tar.gz" || true
        fi
        record_status "failure" "${FAILURE_MESSAGE}" "${CURRENT_BACKUP}" || true
    fi
    exit "${result}"
}

main() {
    local configured_data remote_list stamp archive_path destination command_name

    trap finish EXIT
    if [[ -f "${CONFIG_FILE}" ]]; then
        configured_data="$(read_value CASH_DATA_ROOT "${CONFIG_FILE}")"
        DATA_ROOT="${configured_data:-${DATA_ROOT}}"
        RCLONE_REMOTE="$(read_value CASH_BACKUP_REMOTE "${CONFIG_FILE}")"
        RCLONE_PATH="$(read_value CASH_BACKUP_PATH "${CONFIG_FILE}")"
        BACKUP_CONFIGURED="$(read_value CASH_BACKUP_CONFIGURED "${CONFIG_FILE}")"
    fi
    case "${DATA_ROOT}" in
        /*) ;;
        *) fail "The configured persistent data path is not absolute."; return ;;
    esac

    STATE_ROOT="${DATA_ROOT}/backups/drive"
    STATUS_FILE="${STATE_ROOT}/status.conf"
    ACTIVITY_FILE="${STATE_ROOT}/activity.tsv"
    DISABLED_FILE="${STATE_ROOT}/automatic-backups-disabled"
    mkdir -p -- "${STATE_ROOT}/outbox"
    chmod 0700 -- "${STATE_ROOT}" "${STATE_ROOT}/outbox"
    touch "${ACTIVITY_FILE}"
    chmod 0600 -- "${ACTIVITY_FILE}"

    command -v flock >/dev/null 2>&1 \
        || fail "flock is required for automatic backups."
    exec 9>"${STATE_ROOT}/backup.lock"
    if ! flock --nonblock 9; then
        FINAL_RECORDED="1"
        printf 'Backup stopped: another backup is already running.\n' >&2
        return 1
    fi

    if [[ -f "${DISABLED_FILE}" ]]; then
        record_status "disabled" \
            "Automatic backups are turned off in Cash 27 Settings."
        FINAL_RECORDED="1"
        return
    fi

    record_status "running" "Automatic backup started."
    [[ "${BACKUP_CONFIGURED}" == "1" && -n "${RCLONE_REMOTE}" ]] \
        || fail "Google Drive backup is not configured; rerun ./setup.sh."
    [[ "${RCLONE_REMOTE}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] \
        || fail "The configured rclone remote name is invalid."
    for command_name in rclone podman systemctl; do
        command -v "${command_name}" >/dev/null 2>&1 \
            || fail "${command_name} is required for automatic backups."
    done

    systemctl --user is-enabled --quiet cash-27-backup.timer \
        || fail "The automatic backup timer is not enabled."
    systemctl --user is-active --quiet cash-27-backup.timer \
        || fail "The automatic backup timer is not running."
    TIMER_OK="1"

    remote_list="$(rclone listremotes)"
    grep -Fxq "${RCLONE_REMOTE}:" <<<"${remote_list}" \
        || fail "The configured rclone remote is missing."
    rclone lsf "${RCLONE_REMOTE}:${RCLONE_PATH}" --max-depth 1 >/dev/null \
        || fail "Google Drive could not be reached."
    RCLONE_OK="1"

    [[ "$(podman container inspect --format '{{.State.Running}}' \
        "${CONTAINER_NAME}" 2>/dev/null)" == "true" ]] \
        || fail "The cash-27 application container is not running."

    stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
    CURRENT_BACKUP="cash-27-full-${stamp}.tar.gz"
    archive_path="${STATE_ROOT}/outbox/${CURRENT_BACKUP}"
    CURRENT_ARCHIVE="${archive_path}"
    destination="${RCLONE_REMOTE}:${RCLONE_PATH%/}/${CURRENT_BACKUP}"

    podman exec "${CONTAINER_NAME}" python -m cash.ops backup-archive \
        /data "/data/backups/drive/outbox/${CURRENT_BACKUP}" \
        || fail "Cash 27 could not create a verified backup archive."
    [[ -s "${archive_path}" ]] \
        || fail "Cash 27 did not produce the expected backup archive."

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

main "$@"
