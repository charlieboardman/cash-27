#!/usr/bin/env bash
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

fail() {
    printf 'Upgrade stopped: %s\n' "$*" >&2
    exit 1
}

main() {
    local git_status

    command -v git >/dev/null 2>&1 || fail "git is required."
    git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
        || fail "${PROJECT_ROOT} is not a Git checkout."

    git_status="$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=normal)"
    if [[ -n "${git_status}" ]]; then
        printf 'Upgrade stopped: the production checkout has local changes:\n%s\n' \
            "${git_status}" >&2
        printf 'Commit, discard, or move those changes before upgrading.\n' >&2
        exit 1
    fi

    [[ -x "${PROJECT_ROOT}/deploy/backup-to-drive.sh" ]] \
        || fail "the Google Drive backup job is missing; rerun ./setup.sh."
    printf 'Checking pre-upgrade backup protection...\n'
    "${PROJECT_ROOT}/deploy/backup-to-drive.sh" \
        || fail "the pre-upgrade backup failed. No source or application changes were made."

    printf 'Fetching the newest cash-27 revision...\n'
    git -C "${PROJECT_ROOT}" pull --ff-only

    # Load the deployment procedure from the revision that was just pulled,
    # not from the potentially older version that began this process. Keeping
    # this launcher inside a function also means Bash parses it before Git can
    # replace the file beneath the running process.
    exec "${PROJECT_ROOT}/deploy/upgrade-installed.sh"
}

main "$@"
