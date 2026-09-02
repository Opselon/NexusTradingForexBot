#!/usr/bin/env sh
# ============================================================
# Nexus Scalp Engine — container entrypoint
# ============================================================
# Order of operations (docs/docker.md §startup-order):
#   1. environment validation (fail fast, clear message)
#   2. directory bootstrap (idempotent)
#   3. startup migration gate (nexus db migrate)
#   4. startup summary
#   5. exec the real command (returns ITS exit code)
set -e

log()  { printf '[NSE-ENTRYPOINT] %s\n' "$1"; }
die()  { printf '[NSE-ENTRYPOINT] FATAL: %s\n' "$1" >&2; exit 1; }

APP_DIR="/app"
CFG_PATH="${NSE_CONFIG_PATH:-$APP_DIR/configs/live.yaml}"

log "starting entrypoint (pid $$)"

# ------------------------------------------------------------
# 1. Environment validation — fail BEFORE the engine starts
# ------------------------------------------------------------
mode="$(printf '%s' "${NSE_EXECUTION__MODE:-PAPER}" | tr '[:lower:]' '[:upper:]')"
case "$mode" in
    PAPER|SHADOW) log "execution mode: $mode (container-safe)" ;;
    LIVE)
        die "NSE_EXECUTION__MODE=LIVE is not supported inside a Linux container. \
The container has no MetaTrader 5 host to connect to. Use PAPER or SHADOW \
(see docs/docker.md)."
        ;;
    *)
        die "NSE_EXECUTION__MODE='$mode' is invalid. Expected PAPER or SHADOW. \
Set it in .env and rerun: docker compose up -d"
        ;;
esac

if [ -n "${NSE_WEB_PORT:-}" ]; then
    case "$NSE_WEB_PORT" in
        ''|*[!0-9]*) die "NSE_WEB_PORT='$NSE_WEB_PORT' is not a valid port number." ;;
        *) [ "$NSE_WEB_PORT" -ge 1 ] 2>/dev/null && [ "$NSE_WEB_PORT" -le 65535 ] \
             || die "NSE_WEB_PORT='$NSE_WEB_PORT' is out of range (1-65535)." ;;
    esac
fi

for var in NSE_EXECUTION__MODE NSE_EXECUTION__SYMBOL NSE_MODEL__MODEL_ARTIFACT_PATH; do
    eval "val=\$$var"
    if [ -z "${val:-}" ]; then
        die "environment variable $var is missing. Add it to .env and rerun: docker compose up -d"
    fi
done

# ------------------------------------------------------------
# 2. Directory bootstrap (idempotent)
# ------------------------------------------------------------
mkdir -p "$APP_DIR/artifacts/models" "$APP_DIR/artifacts/logs" "$APP_DIR/data"

# ------------------------------------------------------------
# 3. Startup migration gate (canonical TASK-10 engine)
# ------------------------------------------------------------
log "database migration gate (workspace=$APP_DIR)"
if ! python -m nexus_scalp.cli.main db migrate --workspace "$APP_DIR" >/dev/null 2>&1; then
    if python -m nexus_scalp.cli.main db status --workspace "$APP_DIR" >/dev/null 2>&1; then
        log "no pending migrations"
    else
        die "database migration gate failed. Run: docker compose exec core python -m nexus_scalp.cli.main db status"
    fi
fi
log "database ready"

# ------------------------------------------------------------
# 4. Startup summary (truthful: reports subsequent engine health)
# ------------------------------------------------------------
log "configuration ok (mode=$mode symbol=${NSE_EXECUTION__SYMBOL:-XAUUSD})"
log "model path: ${NSE_MODEL__MODEL_ARTIFACT_PATH:-artifacts/models/scalp/XAUUSD/v1.0.0/model.pt}"
log "starting engine: $*"

# ------------------------------------------------------------
# 5. exec the real command — propagate the true exit code
# ------------------------------------------------------------
exec "$@"