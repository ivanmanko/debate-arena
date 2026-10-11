FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
# SQLite replication for Cloud Run (ADR-005); idle unless LITESTREAM_REPLICA_URL is set.
# 0.3.x on purpose: 0.5.x GCS replicas hang silently (benbjohnson/litestream#1512).
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream

WORKDIR /app

# Dependencies first for layer caching; project source after.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY deploy ./deploy
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8000

EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["rational-check"]
