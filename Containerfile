FROM docker.io/library/python:3.12-slim AS cash-base

ARG CASH_BUILD_REVISION=unknown
LABEL org.opencontainers.image.revision=${CASH_BUILD_REVISION}
ENV CASH_BUILD_REVISION=${CASH_BUILD_REVISION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CASH_DATA_ROOT=/data \
    CASH_FY_START_MONTH=9

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY cash ./cash
COPY migrations ./migrations
COPY accounts.example.csv ./accounts.csv
COPY classes.example.csv ./classes.csv
COPY run_prod.py ./

FROM cash-base AS app

COPY container-entrypoint.sh ./

RUN chmod 0755 /app/container-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()"]

ENTRYPOINT ["/app/container-entrypoint.sh"]
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:8000", \
     "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", "run_prod:app"]

FROM cash-base AS drive-backup

RUN apt-get update \
    && apt-get install --yes --no-install-recommends rclone util-linux ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/backup-to-drive.sh /app/backup-to-drive.sh
RUN chmod 0755 /app/backup-to-drive.sh

ENTRYPOINT ["/app/backup-to-drive.sh"]
CMD ["loop"]
