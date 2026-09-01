#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMAGE=${CASH_IMAGE:-localhost/cash-27:latest}

exec podman build --tag "${IMAGE}" --file "${PROJECT_ROOT}/Containerfile" \
  "${PROJECT_ROOT}"
