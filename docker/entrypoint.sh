#!/usr/bin/env sh
set -e

echo "[NSE ENTRYPOINT] Starting Nexus Scalp Engine Container..."
echo "[NSE ENTRYPOINT] Environment Mode: ${NSE_EXECUTION__MODE:-PAPER}"

mkdir -p data/raw artifacts/logs artifacts/models

exec "$@"
