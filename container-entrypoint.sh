#!/bin/sh
set -eu

umask 027

: "${CASH_DATA_ROOT:=/data}"
: "${CASH_ADMIN_PASSWORD:?CASH_ADMIN_PASSWORD must be set}"
: "${CASH_USER_PASSWORD:?CASH_USER_PASSWORD must be set}"
: "${CASH_SECRET_KEY:?CASH_SECRET_KEY must be set}"

case "${CASH_DATA_ROOT}" in
  /*) ;;
  *) echo "CASH_DATA_ROOT must be an absolute container path" >&2; exit 2 ;;
esac

mkdir -p "${CASH_DATA_ROOT}"
if [ ! -w "${CASH_DATA_ROOT}" ]; then
  echo "CASH_DATA_ROOT is not writable: ${CASH_DATA_ROOT}" >&2
  exit 2
fi

python -m cash.container_init

exec "$@"
