#!/usr/bin/env bash
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly OPERATOR_CONFIG="${CASH_OPERATOR_CONFIG:-/etc/cash-27/operator.conf}"

fail() {
    printf 'Powerwash stopped: %s\n' "$*" >&2
    exit 1
}

read_value() {
    local name="$1" file="$2" line
    line="$(grep -m1 -E "^${name}=" "${file}" || true)"
    printf '%s' "${line#*=}"
}

overlaps() {
    [[ "$1" == "$2" || "$1" == "$2/"* || "$2" == "$1/"* ]]
}

validate_directory() {
    local directory="$1" owner="$2"
    [[ "${directory}" == /* && "${directory}" != / \
        && "$(dirname -- "${directory}")" != / ]] \
        || fail "refusing unsafe directory: ${directory}"
    [[ -d "${directory}" && ! -L "${directory}" \
        && "$(realpath -e -- "${directory}")" == "${directory}" ]] \
        || fail "directory must exist and have a canonical, non-symlink path: ${directory}"
    [[ "$(stat -c %u -- "${directory}")" == "${owner}" ]] \
        || fail "directory is not owned by the installed runtime account: ${directory}"
    if overlaps "${directory}" "${PROJECT_ROOT}"; then
        fail "the reset directory overlaps the checkout: ${directory}"
    fi
    if [[ "${OPERATOR_CONFIG}" == "${directory}/"* ]]; then
        fail "the operator configuration must be outside the reset directories"
    fi
}

main() {
    local service_user service_home service_uid installed_root passwd_entry
    local account_name password_field group_id comment_field account_home shell_field
    local config_dir compose_env data_root confirmation command_name

    (( $# == 0 )) || fail "usage: ./powerwash.sh (there is no unattended mode)"
    [[ "$(id -u)" == 0 ]] || fail "run this script as root from the production checkout"
    [[ -t 0 ]] || fail "an interactive terminal is required for both confirmations"
    for command_name in getent realpath stat systemctl loginctl userdel; do
        command -v "${command_name}" >/dev/null 2>&1 \
            || fail "${command_name} is required"
    done
    [[ -f "${OPERATOR_CONFIG}" && ! -L "${OPERATOR_CONFIG}" ]] \
        || fail "the installed operator configuration was not found: ${OPERATOR_CONFIG}"
    service_user="$(read_value CASH_SERVICE_USER "${OPERATOR_CONFIG}")"
    service_home="$(read_value CASH_SERVICE_HOME "${OPERATOR_CONFIG}")"
    installed_root="$(read_value CASH_PROJECT_ROOT "${OPERATOR_CONFIG}")"
    [[ "${installed_root}" == "${PROJECT_ROOT}" ]] \
        || fail "run powerwash.sh from the installed checkout: ${installed_root}"
    [[ "${service_user}" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "${service_user}" != root ]] \
        || fail "the installed runtime username is invalid"
    passwd_entry="$(getent passwd "${service_user}")" \
        || fail "the installed runtime account does not exist"
    IFS=: read -r account_name password_field service_uid group_id comment_field \
        account_home shell_field <<<"${passwd_entry}"
    [[ "${account_name}" == "${service_user}" && "${service_uid}" =~ ^[0-9]+$ \
        && "${service_uid}" -ne 0 && "${account_home}" == "${service_home}" ]] \
        || fail "the runtime account no longer matches the installed configuration"

    config_dir="${CASH_CONFIG_DIR:-${service_home}/.config/cash-27}"
    compose_env="${CASH_COMPOSE_ENV:-${config_dir}/compose.env}"
    [[ -f "${compose_env}" ]] || fail "installed Compose settings were not found"
    data_root="$(read_value CASH_DATA_ROOT "${compose_env}")"
    validate_directory "${service_home}" "${service_uid}"
    validate_directory "${data_root}" "${service_uid}"
    [[ "${service_home}" != "${data_root}" && "${service_home}" != "${data_root}/"* ]] \
        || fail "the data directory must not contain the runtime home"
    [[ "${config_dir}" == "${service_home}/"* \
        && "$(realpath -e -- "${config_dir}")" == "${config_dir}" \
        && "${compose_env}" == "${config_dir}/compose.env" \
        && ! -L "${compose_env}" ]] \
        || fail "powerwash requires private configuration inside the runtime home"
    if overlaps "${data_root}" "${config_dir}"; then
        fail "the data directory overlaps the private configuration"
    fi

    printf '\nThis permanently removes the Cash 27 installation data:\n'
    printf '  Linux account: %s (UID %s)\n' "${service_user}" "${service_uid}"
    printf '  Entire home:   %s\n' "${service_home}"
    printf '  Data directory: %s\n' "${data_root}"
    printf '  Operator record: %s\n' "${OPERATOR_CONFIG}"
    printf 'This includes the ledger, documents, reports, ALL local backups,\n'
    printf 'passwords, installed CSV copies, HTTPS certificates, and Podman storage.\n'
    printf 'The checkout at %s and CSV files in it are kept.\n' "${PROJECT_ROOT}"
    printf 'Keep your original accounts.csv and classes.csv outside the directories above.\n'
    printf 'No backup is created and setup will not run automatically.\n\n'
    IFS= read -r -p 'Are you really sure? Type yes to continue: ' confirmation \
        || fail "cancelled; nothing was stopped or deleted"
    [[ "${confirmation}" == yes ]] || fail "cancelled; nothing was stopped or deleted"
    IFS= read -r -p 'Are you really, really sure? Type yes again to permanently erase it: ' confirmation \
        || fail "cancelled; nothing was stopped or deleted"
    [[ "${confirmation}" == yes ]] || fail "cancelled; nothing was stopped or deleted"

    printf '\nStopping and disabling Cash 27...\n'
    export CASH_CONFIG_DIR="${config_dir}" CASH_COMPOSE_ENV="${compose_env}"
    "${PROJECT_ROOT}/deploy/as-runtime-user.sh" \
        systemctl --user disable --now cash-27-compose.service \
        || fail "service shutdown failed; data was not deleted"
    # Also handles containers started directly while the systemd unit was inactive.
    "${PROJECT_ROOT}/deploy/compose.sh" down --remove-orphans \
        || fail "container shutdown failed; data was not deleted"
    printf 'Removing the runtime account\047s Podman containers and storage...\n'
    "${PROJECT_ROOT}/deploy/as-runtime-user.sh" podman system reset --force \
        || fail "Podman reset failed; account and application data were not deleted"
    loginctl terminate-user "${service_user}" \
        || fail "could not terminate the runtime account; account and application data were not deleted"
    loginctl disable-linger "${service_user}" \
        || fail "could not disable lingering; account and application data were not deleted"

    # Recheck after shutdown, before crossing the application-data deletion boundary.
    validate_directory "${service_home}" "${service_uid}"
    validate_directory "${data_root}" "${service_uid}"
    printf 'Deleting application data and the runtime account...\n'
    rm -rf --one-file-system -- "${data_root}" \
        || fail "data removal failed; services remain stopped"
    userdel --remove -- "${service_user}" \
        || fail "account removal failed; services remain stopped"
    [[ ! -e "${service_home}" && ! -L "${service_home}" ]] \
        || fail "the account was removed but its home needs manual cleanup"
    rm -f -- "${OPERATOR_CONFIG}"

    printf '\nPowerwash complete. Cash 27 remains stopped.\n'
    printf 'Run ./setup.sh as root from %s to create a fresh installation.\n' "${PROJECT_ROOT}"
    printf 'You will need the domain, new passwords, and the original account/class CSVs.\n'
}

main "$@"
