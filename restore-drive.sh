#!/usr/bin/env bash
# Restore Cash 27 from its Drive backup: the "Cash 27" folder downloaded from
# Google Drive as one or more zip files. Builds and verifies a standard local
# archive from them, then hands it to restore-local.sh.
#
#   ./restore-drive.sh drive-download-1.zip [more.zip ...] [--database cash-27-<time>.db]
#
# Without --database the newest snapshot in the zips is used.
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -eq 0 && "${CASH_RUNTIME_DELEGATED:-0}" != "1" ]]; then
    exec "${PROJECT_ROOT}/deploy/as-runtime-user.sh" "$0" "$@"
fi

readonly CONFIG_DIR="${CASH_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly COMPOSE_ENV="${CASH_COMPOSE_ENV:-${CONFIG_DIR}/compose.env}"
STAGING=""

fail() {
    printf 'Drive restore stopped: %s\n' "$*" >&2
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

remove_staging() {
    if [[ -n "${STAGING}" ]]; then rm -rf -- "${STAGING}"; fi
}

main() {
    local data_root stamp archive database="" zip name
    local -a zips=() inside=()
    while (( $# > 0 )); do
        case "$1" in
            --database)
                (( $# >= 2 )) || fail "--database needs a snapshot name."
                database="$2"; shift 2 ;;
            --database=*) database="${1#--database=}"; shift ;;
            *) zips+=("$1"); shift ;;
        esac
    done
    (( ${#zips[@]} > 0 )) \
        || fail "usage: ./restore-drive.sh ZIP [ZIP...] [--database cash-27-<time>.db]"
    if [[ -n "${database}" && ! "${database}" =~ ^cash-27-[0-9]{8}T[0-9]{6}Z\.db$ ]]; then
        fail "--database takes a snapshot name such as cash-27-20260927T093000Z.db."
    fi
    [[ -f "${COMPOSE_ENV}" ]] || fail "run ./setup.sh before restoring."
    command -v podman >/dev/null 2>&1 || fail "Podman is required."
    data_root="$(read_value CASH_DATA_ROOT)"
    [[ "${data_root}" == /* ]] || fail "the installed persistent data path is invalid."
    for zip in "${zips[@]}"; do
        [[ -f "${zip}" && -r "${zip}" ]] || fail "cannot read ${zip}."
    done

    # The restore tool sees only the data directory, so the zips go there.
    stamp="$(date -u '+%Y%m%dT%H%M%S%6NZ')"
    STAGING="${data_root}/staging/drive-restore-${stamp}"
    trap remove_staging EXIT
    mkdir -p -- "${STAGING}"
    for index in "${!zips[@]}"; do
        name="drive-${index}.zip"
        cp -- "${zips[index]}" "${STAGING}/${name}"
        inside+=("/data/staging/drive-restore-${stamp}/${name}")
    done

    archive="backups/restore/cash-27-full-${stamp}.tar.gz"
    printf 'Building and verifying an archive from the Drive download...\n'
    compose --profile tools run --rm --no-deps restore-tool \
        drive-archive "/data/${archive}" "${inside[@]}" \
        ${database:+"--database=${database}"} \
        || fail "the Drive download could not produce a verified archive; nothing was changed."
    [[ -s "${data_root}/${archive}" ]] || fail "the archive was not created; nothing was changed."
    remove_staging
    STAGING=""
    trap - EXIT

    printf 'Archive ready: %s/%s\n' "${data_root}" "${archive}"
    exec "${PROJECT_ROOT}/restore-local.sh" "${archive}"
}

main "$@"
