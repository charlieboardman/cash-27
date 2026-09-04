#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CONFIG_DIR="${CASH_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly COMPOSE_ENV="${CASH_COMPOSE_ENV:-${CONFIG_DIR}/compose.env}"
readonly HEALTH_URL="${CASH_RESTORE_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
readonly HEALTH_ATTEMPTS="${CASH_RESTORE_HEALTH_ATTEMPTS:-30}"
readonly HEALTH_DELAY="${CASH_RESTORE_HEALTH_DELAY:-1}"
SERVICES_STOPPED="0"

fail() {
    printf 'Restore stopped: %s\n' "$*" >&2
    exit 1
}

compose() {
    CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" "$@"
}

read_value() {
    local name="$1" line
    line="$(grep -m1 -E "^${name}=" "${COMPOSE_ENV}" || true)"
    printf '%s' "${line#*=}"
}

valid_backup_name() {
    [[ "$1" =~ ^cash-27-full-[0-9]{8}T[0-9]{6}([0-9]{6})?Z\.tar\.gz$ ]]
}

wait_for_health() {
    local attempt
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt += 1)); do
        if curl --silent --show-error --fail --max-time 2 \
                "${HEALTH_URL}" >/dev/null 2>&1; then
            return 0
        fi
        sleep "${HEALTH_DELAY}"
    done
    return 1
}

start_stack_on_exit() {
    local result=$?
    trap - EXIT
    if [[ "${SERVICES_STOPPED}" == "1" ]]; then
        compose up --detach --remove-orphans >/dev/null 2>&1 || true
    fi
    exit "${result}"
}

choose_backup() {
    local requested="${1:-}" answer index
    local -a backups=()
    mapfile -t backups < <(
        compose run --rm --no-deps drive-backup list |
            grep -E '^cash-27-full-[0-9]{8}T[0-9]{6}([0-9]{6})?Z\.tar\.gz$'
    )
    (( ${#backups[@]} > 0 )) \
        || fail "Google Drive contains no Cash 27 full backups."
    if [[ -n "${requested}" ]]; then
        valid_backup_name "${requested}" \
            || fail "the requested backup name is invalid."
        printf '%s\n' "${backups[@]}" | grep -Fxq "${requested}" \
            || fail "the requested backup was not found in Google Drive."
        printf '%s' "${requested}"
        return
    fi
    [[ -t 0 ]] || fail "provide a backup filename when running non-interactively."
    printf 'Available Google Drive backups:\n' >&2
    for index in "${!backups[@]}"; do
        printf '  %d) %s\n' "$((index + 1))" "${backups[index]}" >&2
    done
    read -r -p "Backup number to restore: " answer
    [[ "${answer}" =~ ^[0-9]+$ ]] \
        || fail "enter one of the displayed backup numbers."
    (( answer >= 1 && answer <= ${#backups[@]} )) \
        || fail "the selected backup number is out of range."
    printf '%s' "${backups[answer - 1]}"
}

restore_archive() {
    local relative_path="$1"
    compose --profile tools run --rm --no-deps restore-tool \
        restore-archive /data "/data/${relative_path}"
}

main() {
    local selected data_root relative rollback rollback_relative forensic
    local confirmation stamp rollback_available
    [[ -f "${COMPOSE_ENV}" ]] || fail "run ./setup.sh before restoring."
    command -v podman >/dev/null 2>&1 || fail "Podman is required."
    command -v curl >/dev/null 2>&1 || fail "curl is required."
    selected="$(choose_backup "${1:-}")"
    data_root="$(read_value CASH_DATA_ROOT)"
    [[ "${data_root}" == /* ]] \
        || fail "the installed persistent data path is invalid."

    printf '\nSelected: %s\n' "${selected}"
    printf 'This will replace the live ledger and its referenced documents.\n'
    if [[ "${CASH_RESTORE_CONFIRM:-}" != "RESTORE CASH 27" ]]; then
        [[ -t 0 ]] || fail "set CASH_RESTORE_CONFIRM='RESTORE CASH 27' for a non-interactive restore."
        read -r -p "Type RESTORE CASH 27 to continue: " confirmation
        [[ "${confirmation}" == "RESTORE CASH 27" ]] \
            || fail "confirmation did not match."
    fi

    printf 'Downloading and independently verifying ${selected}...\n'
    compose run --rm --no-deps drive-backup fetch "${selected}"
    relative="backups/drive/restore/${selected}"
    [[ -s "${data_root}/${relative}" ]] \
        || fail "the verified archive is not present in persistent storage."

    printf 'Pausing automatic backups and preserving the current live state...\n'
    compose stop drive-backup
    SERVICES_STOPPED="1"
    trap start_stack_on_exit EXIT
    stamp="$(date -u '+%Y%m%dT%H%M%S%6NZ')"
    rollback="cash-27-full-${stamp}.tar.gz"
    rollback_relative="backups/drive/restore/${rollback}"
    rollback_available="0"
    if compose run --rm --no-deps drive-backup local "${rollback}" >/dev/null \
            && [[ -s "${data_root}/${rollback_relative}" ]]; then
        rollback_available="1"
        printf 'Stopping the application...\n'
        compose stop app backup-core
    else
        printf 'The current database cannot produce a verified safety archive.\n' >&2
        printf 'Stopping writers and preserving its raw files for investigation...\n' >&2
        compose stop app backup-core
        forensic="backups/drive/restore/cash-27-forensic-${stamp}.tar.gz"
        compose --profile tools run --rm --no-deps restore-tool \
            forensic-archive /data "/data/${forensic}" >/dev/null \
            || fail "the current damaged state could not be preserved; no restore was attempted."
        [[ -s "${data_root}/${forensic}" ]] \
            || fail "the forensic safety archive was not created; no restore was attempted."
        printf 'Raw pre-restore files: %s/%s\n' "${data_root}" "${forensic}" >&2
    fi

    printf 'Restoring the selected archive...\n'
    if ! restore_archive "${relative}"; then
        if [[ "${rollback_available}" != "1" ]]; then
            SERVICES_STOPPED="0"
            fail "the selected backup failed to restore. The application remains stopped and the raw pre-restore files were preserved at ${data_root}/${forensic}."
        fi
        printf 'Restore failed; reinstating the verified pre-restore archive...\n' >&2
        if ! restore_archive "${rollback_relative}"; then
            SERVICES_STOPPED="0"
            fail "both restore and automatic rollback failed. The application remains stopped; preserve ${data_root} and investigate."
        fi
        fail "the selected backup failed during restore. The prior data was reinstated."
    fi

    printf 'Starting the Compose stack and checking application health...\n'
    if ! compose up --detach --remove-orphans || ! wait_for_health; then
        printf 'Restored data did not produce a healthy application; stopping it...\n' >&2
        compose stop drive-backup app backup-core
        SERVICES_STOPPED="1"
        if [[ "${rollback_available}" != "1" ]]; then
            SERVICES_STOPPED="0"
            fail "the restored application is unhealthy and no verified rollback was possible. It remains stopped; raw pre-restore files are at ${data_root}/${forensic}."
        fi
        if ! restore_archive "${rollback_relative}"; then
            SERVICES_STOPPED="0"
            fail "health validation and automatic rollback both failed. The application remains stopped; preserve ${data_root} and investigate."
        fi
        if ! compose up --detach --remove-orphans; then
            SERVICES_STOPPED="0"
            fail "the prior data was reinstated, but the Compose stack could not start."
        fi
        SERVICES_STOPPED="0"
        wait_for_health \
            || fail "the pre-restore data was reinstated, but the application is still unhealthy."
        fail "the selected backup was rejected after health validation. The prior data is running again."
    fi
    SERVICES_STOPPED="0"
    trap - EXIT

    printf '\nRestore completed successfully.\n'
    printf 'Restored: %s\n' "${selected}"
    if [[ "${rollback_available}" == "1" ]]; then
        printf 'Pre-restore safety archive: %s/%s\n' "${data_root}" "${rollback_relative}"
    else
        printf 'Raw pre-restore files: %s/%s\n' "${data_root}" "${forensic}"
    fi
}

main "$@"
