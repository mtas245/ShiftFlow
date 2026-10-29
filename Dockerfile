FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY environments/python/requirements-runtime.lock /tmp/requirements-runtime.lock
RUN python -m pip install --requirement /tmp/requirements-runtime.lock \
    && groupadd --system --gid 10001 shiftflow \
    && useradd --system --uid 10001 --gid shiftflow --home-dir /app shiftflow \
    && rm /tmp/requirements-runtime.lock

COPY --chown=shiftflow:shiftflow alembic.ini ./
COPY --chown=shiftflow:shiftflow alembic ./alembic
COPY --chown=shiftflow:shiftflow src ./src

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
