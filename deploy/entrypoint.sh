#!/bin/sh
# Container entrypoint (ADR-005).
# With LITESTREAM_REPLICA_URL set (Cloud Run) the SQLite file lives on the instance's
# ephemeral disk: restore it from the replica on start, then run the app under
# `litestream replicate` so every write is streamed back. Without it (local,
# docker compose) the command runs as is.
set -eu

if [ -z "${LITESTREAM_REPLICA_URL:-}" ]; then
  exec "$@"
fi

export LITESTREAM_DB_PATH="${LITESTREAM_DB_PATH:-/data/debate_arena.db}"
# The app must open the very file Litestream replicates.
export DATABASE_URL="sqlite+aiosqlite:///${LITESTREAM_DB_PATH}"
mkdir -p "$(dirname "$LITESTREAM_DB_PATH")"

litestream restore -config /app/deploy/litestream.yml \
  -if-db-not-exists -if-replica-exists "$LITESTREAM_DB_PATH"
exec litestream replicate -config /app/deploy/litestream.yml -exec "$*"
