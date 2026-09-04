#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if [ "$(id -u)" -eq 0 ] && [ "${CASH_RUNTIME_DELEGATED:-0}" != 1 ]; then
  exec "${PROJECT_ROOT}/deploy/as-runtime-user.sh" "$0" "$@"
fi

IMAGE=$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")

if [ -z "${CASH_IMAGE:-}" ] && [ "${CASH_ALLOW_DIRTY_BUILD:-0}" != "1" ]; then
  GIT_STATUS=$(git -C "${PROJECT_ROOT}" status --porcelain --untracked-files=normal)
  if [ -n "${GIT_STATUS}" ]; then
    echo "Refusing to build a commit-tagged image from a dirty Git worktree:" >&2
    printf '%s\n' "${GIT_STATUS}" >&2
    echo "Commit or remove these changes, then build again." >&2
    exit 2
  fi
fi

REVISION=$(git -C "${PROJECT_ROOT}" rev-parse HEAD)
echo "Building ${IMAGE}" >&2

export CASH_BUILD_REVISION="${REVISION}"
export CASH_APP_IMAGE="${CASH_APP_IMAGE:-$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" app)}"
export CASH_BACKUP_IMAGE="${CASH_BACKUP_IMAGE:-$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" backup)}"
exec "${PROJECT_ROOT}/deploy/compose.sh" build app drive-backup
