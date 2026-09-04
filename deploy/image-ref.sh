#!/bin/sh
set -eu

PROJECT_ROOT=${CASH_PROJECT_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}

if [ -n "${CASH_IMAGE:-}" ]; then
  printf '%s\n' "${CASH_IMAGE}"
  exit 0
fi

if ! git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Cannot determine the application image: ${PROJECT_ROOT} is not a Git checkout." >&2
  exit 2
fi

REVISION=$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD)
printf 'localhost/cash-27:%s\n' "${REVISION}"
