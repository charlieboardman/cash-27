#!/bin/sh
set -eu

PROJECT_ROOT=${CASH_PROJECT_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
COMPONENT=${1:-app}

case "${COMPONENT}" in
  app) REPOSITORY=cash-27 ;;
  *) echo "Usage: $0 [app]" >&2; exit 2 ;;
esac

if [ "${COMPONENT}" = app ] && [ -n "${CASH_IMAGE:-}" ]; then
  printf '%s\n' "${CASH_IMAGE}"
  exit 0
fi
if [ "${COMPONENT}" = app ] && [ -n "${CASH_APP_IMAGE:-}" ]; then
  printf '%s\n' "${CASH_APP_IMAGE}"
  exit 0
fi

if ! git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Cannot determine the application image: ${PROJECT_ROOT} is not a Git checkout." >&2
  exit 2
fi

REVISION=$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD)
printf 'localhost/%s:%s\n' "${REPOSITORY}" "${REVISION}"
