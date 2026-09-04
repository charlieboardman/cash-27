#!/usr/bin/env bash
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly OPERATOR_CONFIG="${CASH_OPERATOR_CONFIG:-/etc/cash-27/operator.conf}"

fail() {
    printf 'Upgrade stopped: %s\n' "$*" >&2
    exit 1
}

main() {
    local git_status installed_root

    command -v git >/dev/null 2>&1 || fail "git is required."
    git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || fail "${PROJECT_ROOT} is not a Git checkout."

    if [[ "${EUID}" -eq 0 ]]; then
        [[ -f "${OPERATOR_CONFIG}" ]] \
            || fail "${OPERATOR_CONFIG} is missing; rerun ./setup.sh."
        installed_root="$(grep -m1 '^CASH_PROJECT_ROOT=' "${OPERATOR_CONFIG}" || true)"
        installed_root="${installed_root#*=}"
        [[ "${installed_root}" == "${PROJECT_ROOT}" ]] \
            || fail "this is not the installed checkout (${installed_root:-unknown})."
    fi

    git_status="$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=normal)"
    if [[ -n "${git_status}" ]]; then
        printf 'Upgrade stopped: the production checkout has local changes:\n%s\n' \
            "${git_status}" >&2
        printf 'Commit, discard, or move those changes before upgrading.\n' >&2
        exit 1
    fi

    [[ -x "${PROJECT_ROOT}/deploy/compose.sh" ]] \
        || fail "the Compose deployment helper is missing; rerun ./setup.sh."
    printf 'Checking pre-upgrade backup protection...\n'
    "${PROJECT_ROOT}/deploy/compose.sh" run --rm --no-deps drive-backup once \
        || fail "the pre-upgrade backup failed. No source or application changes were made."

    printf 'Fetching the newest cash-27 revision...\n'
    git -C "${PROJECT_ROOT}" pull --ff-only

    # Load the deployment procedure from the revision that was just pulled,
    # not from the potentially older version that began this process. Keeping
    # this launcher inside a function also means Bash parses it before Git can
    # replace the file beneath the running process.
    if [[ "${EUID}" -eq 0 ]]; then
        exec "${PROJECT_ROOT}/deploy/as-runtime-user.sh" \
            "${PROJECT_ROOT}/deploy/upgrade-installed.sh"
    fi
    exec "${PROJECT_ROOT}/deploy/upgrade-installed.sh"
}

main "$@"
