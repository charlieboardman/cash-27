#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")

if [ -z "${CASH_IMAGE:-}" ]; then
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

exec podman build --format docker --tag "${IMAGE}" \
  --label "org.opencontainers.image.revision=${REVISION}" \
  --file "${PROJECT_ROOT}/Containerfile" \
  "${PROJECT_ROOT}"
