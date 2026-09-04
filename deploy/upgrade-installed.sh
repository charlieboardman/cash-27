#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly QUADLET_FILE="${CASH_UPGRADE_QUADLET_FILE:-${HOME}/.config/containers/systemd/cash-27.container}"
readonly SERVICE_NAME="${CASH_UPGRADE_SERVICE:-cash-27.service}"
readonly HEALTH_URL="${CASH_UPGRADE_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
readonly HEALTH_ATTEMPTS="${CASH_UPGRADE_HEALTH_ATTEMPTS:-30}"
readonly HEALTH_DELAY="${CASH_UPGRADE_HEALTH_DELAY:-1}"
QUADLET_BACKUP=""

fail() {
    printf 'Upgrade stopped: %s\n' "$*" >&2
    exit 1
}

quadlet_image() {
    sed -n 's/^Image=//p' "${QUADLET_FILE}"
}

write_quadlet_image() {
    local image="$1"
    local temporary
    temporary="$(mktemp "$(dirname -- "${QUADLET_FILE}")/.cash-27.container.XXXXXX")"
    if ! awk -v image="${image}" '
        BEGIN { count = 0 }
        /^Image=/ { print "Image=" image; count += 1; next }
        { print }
        END { if (count != 1) exit 42 }
    ' "${QUADLET_FILE}" >"${temporary}"; then
        rm -f -- "${temporary}"
        fail "${QUADLET_FILE} must contain exactly one Image= line."
    fi
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${QUADLET_FILE}"
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

restore_quadlet() {
    local backup="$1"
    local temporary
    temporary="$(mktemp "$(dirname -- "${QUADLET_FILE}")/.cash-27.container.XXXXXX")"
    cp -- "${backup}" "${temporary}"
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${QUADLET_FILE}"
}

cleanup() {
    if [[ -n "${QUADLET_BACKUP}" ]]; then
        rm -f -- "${QUADLET_BACKUP}"
    fi
}

main() {
    local old_image new_image command_name
    local -a installed_images

    [[ "${EUID}" -ne 0 ]] \
        || fail "run this as the normal account that operates cash-27, not as root."
    for command_name in podman systemctl curl; do
        command -v "${command_name}" >/dev/null 2>&1 \
            || fail "${command_name} is required; run ./setup.sh first."
    done
    [[ -f "${QUADLET_FILE}" ]] \
        || fail "no installed Quadlet was found at ${QUADLET_FILE}; run ./setup.sh first."

    mapfile -t installed_images < <(quadlet_image)
    [[ "${#installed_images[@]}" -eq 1 && -n "${installed_images[0]}" ]] \
        || fail "${QUADLET_FILE} must contain exactly one non-empty Image= line."
    old_image="${installed_images[0]}"
    new_image="$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")"

    printf 'Building application image %s...\n' "${new_image}"
    "${PROJECT_ROOT}/deploy/podman-build.sh"

    QUADLET_BACKUP="$(mktemp)"
    trap cleanup EXIT
    cp -- "${QUADLET_FILE}" "${QUADLET_BACKUP}"

    write_quadlet_image "${new_image}"
    systemctl --user daemon-reload
    systemctl --user restart "${SERVICE_NAME}"

    if ! wait_for_health; then
        printf 'The new container did not become healthy; restoring %s...\n' \
            "${old_image}" >&2
        systemctl --user --no-pager status "${SERVICE_NAME}" >&2 || true
        restore_quadlet "${QUADLET_BACKUP}"
        systemctl --user daemon-reload
        systemctl --user restart "${SERVICE_NAME}"
        if wait_for_health; then
            fail "the new image failed its health check. The prior image is running again. Database migrations, if any, were not reversed."
        fi
        systemctl --user --no-pager status "${SERVICE_NAME}" >&2 || true
        fail "both the new image and the attempted code rollback failed their health checks. Inspect the service log with: journalctl --user --unit cash-27 --follow"
    fi

    printf '\ncash-27 was upgraded successfully.\n'
    printf 'Previous image: %s\n' "${old_image}"
    printf 'Running image:  %s\n' "${new_image}"
    printf 'The environment, Caddy configuration, database, and documents were retained.\n'
}

main "$@"
