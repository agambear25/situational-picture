#!/usr/bin/env bash
# Launch wrapper for the preview tool: load env + Postgres on PATH, then serve the read-only API.
set -e
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
set -a; [ -f .env ] && source .env; set +a
exec .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port "${PORT:-8000}"
