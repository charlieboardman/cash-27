#!/usr/bin/env bash
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -eq 0 && "${CASH_RUNTIME_DELEGATED:-0}" != "1" ]]; then
    exec "${PROJECT_ROOT}/deploy/as-runtime-user.sh" "$0" "$@"
fi

readonly CONFIG_DIR="${CASH_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly COMPOSE_ENV="${CASH_COMPOSE_ENV:-${CONFIG_DIR}/compose.env}"

[[ -f "${COMPOSE_ENV}" ]] || {
    printf 'Compose configuration not found: %s\nRun ./setup.sh first.\n' \
        "${COMPOSE_ENV}" >&2
    exit 2
}

export CASH_BUILD_REVISION="${CASH_BUILD_REVISION:-$(git -C "${PROJECT_ROOT}" rev-parse HEAD)}"
export PODMAN_COMPOSE_PROVIDER="${PODMAN_COMPOSE_PROVIDER:-podman-compose}"

# Derive the directory from the existing installation; no setup rerun is needed.
# Mount the directory so atomic replacement of cash-27.env works on both sides.
app_env_file="${CASH_APP_ENV_FILE:-$(sed -n 's/^CASH_APP_ENV_FILE=//p' "${COMPOSE_ENV}" | head -n 1)}"
[[ -n "${app_env_file}" ]] || {
    printf 'CASH_APP_ENV_FILE is missing from the Compose configuration.\n' >&2
    exit 2
}
export CASH_APP_CONFIG_DIR="$(dirname -- "${app_env_file}")"
export CASH_APP_ENV_NAME="$(basename -- "${app_env_file}")"

exec podman compose --env-file "${COMPOSE_ENV}" \
    --file "${PROJECT_ROOT}/compose.yaml" "$@"
