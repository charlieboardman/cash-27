#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly CONFIG_DIR="${CASH_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly COMPOSE_ENV="${CASH_COMPOSE_ENV:-${CONFIG_DIR}/compose.env}"
readonly HEALTH_URL="${CASH_UPGRADE_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
readonly HEALTH_ATTEMPTS="${CASH_UPGRADE_HEALTH_ATTEMPTS:-30}"
readonly HEALTH_DELAY="${CASH_UPGRADE_HEALTH_DELAY:-1}"
COMPOSE_BACKUP=""

fail() {
    printf 'Upgrade stopped: %s\n' "$*" >&2
    exit 1
}

read_value() {
    local name="$1" line
    line="$(grep -m1 -E "^${name}=" "${COMPOSE_ENV}" || true)"
    printf '%s' "${line#*=}"
}

write_image() {
    local app_image="$1" temporary
    temporary="$(mktemp "${CONFIG_DIR}/.compose.env.XXXXXX")"
    if ! awk -v app="${app_image}" '
        BEGIN { app_count = 0 }
        /^CASH_APP_IMAGE=/ {
            print "CASH_APP_IMAGE=" app; app_count += 1; next
        }
        { print }
        END { if (app_count != 1) exit 42 }
    ' "${COMPOSE_ENV}" >"${temporary}"; then
        rm -f -- "${temporary}"
        fail "${COMPOSE_ENV} must contain exactly one application image."
    fi
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${COMPOSE_ENV}"
}

wait_for_health() {
    local attempt
    for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt += 1)); do
        if curl --silent --show-error --fail --max-time 2 \
            "${HEALTH_URL}" >/dev/null 2>&1 &&
                CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
                    exec -T document-worker python -m cash.document_worker health >/dev/null 2>&1; then
            return 0
        fi
        sleep "${HEALTH_DELAY}"
    done
    return 1
}

cleanup() {
    if [[ -n "${COMPOSE_BACKUP}" ]]; then
        rm -f -- "${COMPOSE_BACKUP}"
    fi
}

main() {
    local old_app new_app command_name
    [[ "${EUID}" -ne 0 ]] \
        || fail "run this as the normal account that operates cash-27, not as root."
    for command_name in podman curl; do
        command -v "${command_name}" >/dev/null 2>&1 \
            || fail "${command_name} is required; run ./setup.sh first."
    done
    command -v podman-compose >/dev/null 2>&1 \
        || fail "podman-compose is required; run ./setup.sh first."
    [[ -f "${COMPOSE_ENV}" ]] \
        || fail "no installed Compose configuration was found; run ./setup.sh first."

    old_app="$(read_value CASH_APP_IMAGE)"
    [[ -n "${old_app}" ]] \
        || fail "the installed Compose application image is missing."
    new_app="$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" app)"

    printf 'Building the application image...\n'
    "${PROJECT_ROOT}/deploy/podman-build.sh"

    COMPOSE_BACKUP="$(mktemp)"
    trap cleanup EXIT
    cp -- "${COMPOSE_ENV}" "${COMPOSE_BACKUP}"
    write_image "${new_app}"
    if ! CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
            up --detach --remove-orphans || ! wait_for_health; then
        printf 'The new application did not become healthy; restoring prior images...\n' >&2
        CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
            ps >&2 || true
        cp -- "${COMPOSE_BACKUP}" "${COMPOSE_ENV}"
        chmod 0600 -- "${COMPOSE_ENV}"
        CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
            up --detach --remove-orphans \
            || fail "the prior image references were restored, but the Compose stack could not start."
        if wait_for_health; then
            fail "the new images failed their health check. The prior images are running again. Database migrations, if any, were not reversed."
        fi
        CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
            ps >&2 || true
        fail "both the new images and attempted code rollback failed their health checks."
    fi

    printf '\ncash-27 was upgraded successfully.\n'
    printf 'Previous application image: %s\n' "${old_app}"
    printf 'Running application image:  %s\n' "${new_app}"
    printf 'The environment, Caddy state, database, and documents were retained.\n'
}

main "$@"
