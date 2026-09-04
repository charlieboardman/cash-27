#!/bin/sh
set -eu

umask 077

DATA_ROOT=${1:-}
ENV_FILE=${2:-}
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")
CONTAINER_NAME=${CASH_CONTAINER_NAME:-cash-27}
BIND_ADDRESS=${CASH_BIND_ADDRESS:-127.0.0.1}
HOST_PORT=${CASH_HOST_PORT:-8000}

if [ -z "${DATA_ROOT}" ] || [ -z "${ENV_FILE}" ]; then
  echo "Usage: $0 /absolute/path/to/data /path/to/cash-27.env" >&2
  exit 2
fi
case "${DATA_ROOT}" in
  /*) ;;
  *) echo "The persistent data path must be absolute." >&2; exit 2 ;;
esac
if [ ! -f "${ENV_FILE}" ]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 2
fi

mkdir -p "${DATA_ROOT}"
chmod 0700 "${DATA_ROOT}"

exec podman run --detach --replace \
  --name "${CONTAINER_NAME}" \
  --userns keep-id \
  --user "$(id -u):$(id -g)" \
  --cap-drop all \
  --security-opt no-new-privileges \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=128m \
  --pids-limit 256 \
  --restart unless-stopped \
  --publish "${BIND_ADDRESS}:${HOST_PORT}:8000" \
  --volume "${DATA_ROOT}:/data:Z" \
  --env-file "${ENV_FILE}" \
  --env CASH_DATA_ROOT=/data \
  --env HOME=/tmp \
  "${IMAGE}"
