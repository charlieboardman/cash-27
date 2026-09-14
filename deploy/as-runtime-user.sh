#!/usr/bin/env bash
set -euo pipefail

readonly OPERATOR_CONFIG="${CASH_OPERATOR_CONFIG:-/etc/cash-27/operator.conf}"

fail() {
    printf 'Runtime delegation failed: %s\n' "$*" >&2
    exit 1
}

read_value() {
    local name="$1" line
    line="$(grep -m1 -E "^${name}=" "${OPERATOR_CONFIG}" || true)"
    printf '%s' "${line#*=}"
}

main() {
    local service_user service_home service_uid service_shell passwd_entry name
    local -a runtime_environment

    (( $# > 0 )) || fail "no command was provided."
    if [[ "${EUID}" -ne 0 ]]; then
        exec "$@"
    fi

    command -v runuser >/dev/null 2>&1 || fail "runuser is required."
    command -v systemctl >/dev/null 2>&1 || fail "systemd is required."
    [[ -f "${OPERATOR_CONFIG}" ]] \
        || fail "${OPERATOR_CONFIG} is missing; run ./setup.sh first."

    service_user="$(read_value CASH_SERVICE_USER)"
    service_home="$(read_value CASH_SERVICE_HOME)"
    [[ "${service_user}" =~ ^[a-z_][a-z0-9_-]*[$]?$ \
            && "${service_user}" != "root" ]] \
        || fail "the configured runtime user is invalid."
    [[ "${service_home}" == /* ]] \
        || fail "the configured runtime home is invalid."

    passwd_entry="$(getent passwd "${service_user}" || true)"
    [[ -n "${passwd_entry}" ]] \
        || fail "runtime user ${service_user} does not exist."
    service_uid="$(printf '%s' "${passwd_entry}" | cut -d: -f3)"
    service_shell="$(printf '%s' "${passwd_entry}" | cut -d: -f7)"
    [[ "$(printf '%s' "${passwd_entry}" | cut -d: -f6)" == "${service_home}" ]] \
        || fail "runtime user ${service_user} no longer has the configured home."

    systemctl start "user@${service_uid}.service" \
        || fail "the systemd user manager for ${service_user} could not start."
    [[ -d "/run/user/${service_uid}" ]] \
        || fail "systemd did not create the runtime directory for ${service_user}."

    export CASH_RUNTIME_DELEGATED=1
    runtime_environment=(
        "HOME=${service_home}"
        "USER=${service_user}"
        "LOGNAME=${service_user}"
        "SHELL=${service_shell:-/bin/bash}"
        "XDG_RUNTIME_DIR=/run/user/${service_uid}"
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${service_uid}/bus"
        "GIT_OPTIONAL_LOCKS=0"
        "PATH=/usr/local/bin:/usr/bin:/bin"
    )
    while IFS= read -r name; do
        runtime_environment+=("${name}=${!name}")
    done < <(compgen -A variable CASH_)
    [[ -z "${TERM:-}" ]] || runtime_environment+=("TERM=${TERM}")
    [[ -z "${LANG:-}" ]] || runtime_environment+=("LANG=${LANG}")

    # SSH may start in /root, which the runtime account cannot access.
    # Podman and Compose need an accessible working directory after delegation.
    cd -- "${service_home}" || fail "cannot enter the runtime home."
    exec runuser -u "${service_user}" -- env -i \
        "${runtime_environment[@]}" "$@"
}

main "$@"
