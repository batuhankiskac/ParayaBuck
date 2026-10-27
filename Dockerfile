FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PARAYABUCK_CONTAINER=YES \
    HOST=0.0.0.0 \
    PORT=8000 \
    OPEN_BROWSER=0 \
    CONFIG_PATH=/app/config/settings.yaml

WORKDIR /app

RUN groupadd --system parayabuck && useradd --system --gid parayabuck --home /app parayabuck
# Note: this image has no git and .git is excluded, so the engine's tracked-secret
# start gate cannot list tracked files and refuses engine starts (fail closed, S2-7-2).

# Install the SAME locked dependency versions CI tests against, then the
# package itself without re-resolving dependencies.
COPY requirements.lock ./
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
# pip is pinned (same version CI uses); the build tooling is not hashed yet (open item
# S2-3-1 in docs/audit/raw-r7.md).
RUN python -m pip install pip==26.2.1 \
    && python -m pip install --require-hashes -r requirements.lock \
    && python -m pip install . --no-deps

# Only the state directories are writable by the service user. The source and the
# installed package stay root-owned, so a compromised process cannot rewrite the code
# it runs.
RUN mkdir -p /app/data/runtime /app/reports \
    && chown -R parayabuck:parayabuck /app/config /app/data /app/reports

USER parayabuck
# config is a volume as well as data and reports: the operator UI persists
# runtime settings to /app/config/settings.yaml, and those mutations must
# survive container recreation. An empty named volume is seeded from the
# image's default config on first start.
VOLUME ["/app/config", "/app/data", "/app/reports"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

STOPSIGNAL SIGTERM
CMD ["parayabuck", "web"]
