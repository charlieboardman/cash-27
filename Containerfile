FROM docker.io/library/python:3.12-slim AS cash-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CASH_DATA_ROOT=/data \
    CASH_FY_START_MONTH=9

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

FROM cash-base AS app

ENV CASH_QR_ENABLED=1 \
    CASH_RECEIPT_ORIENTATION_ENABLED=1 \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
    CUDA_VISIBLE_DEVICES=""

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements-capture.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-capture.txt
RUN python -c "from pathlib import Path; import hashlib, urllib.request; p=Path('/opt/cash27-orientation/inference.onnx'); p.parent.mkdir(); urllib.request.urlretrieve('https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori_onnx/resolve/7330ab7039123e46af2dc03154b9969aa412c61d/inference.onnx', p); assert hashlib.sha256(p.read_bytes()).hexdigest() == 'af9a0a4f317ff0709ce752067807f819cb15d883f8ecad89f28df1c6ee2d9c92'"

COPY cash ./cash
COPY migrations ./migrations
COPY accounts.example.csv ./accounts.csv
COPY classes.example.csv ./classes.csv
COPY run_prod.py container-entrypoint.sh ./

# Revision metadata changes on every upgrade. Keep it after dependency/model
# installation so a new commit does not invalidate those expensive layers.
ARG CASH_BUILD_REVISION=unknown
# A cheap revision-dependent RUN also refreshes metadata on cached Podman builds.
RUN test -n "$CASH_BUILD_REVISION" && chmod 0755 /app/container-entrypoint.sh
ENV CASH_BUILD_REVISION=${CASH_BUILD_REVISION}
LABEL org.opencontainers.image.revision=${CASH_BUILD_REVISION}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

ENTRYPOINT ["/app/container-entrypoint.sh"]
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:8000", \
     "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", "run_prod:app"]
