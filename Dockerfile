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

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN python -m pip install .

RUN mkdir -p /app/data/runtime /app/data/universe /app/reports \
    && chown -R parayabuck:parayabuck /app

USER parayabuck
VOLUME ["/app/data", "/app/reports"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

STOPSIGNAL SIGTERM
CMD ["parayabuck", "web"]
