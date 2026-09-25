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
# 1b. Config bootstrap — the engine's --config target must exist.
# The image ships base.yaml + live.yaml.example (live.yaml is a per-operator
# file, never committed). If configs/live.yaml is absent, derive it from the
# example; compose env vars + settings DB remain the authoritative knobs.
# (docker-repair 2026-09-09: "Config missing: configs/live.yaml" exit 1.)
# ------------------------------------------------------------
if [ ! -f "$APP_DIR/configs/live.yaml" ] && [ -f "$APP_DIR/configs/live.yaml.example" ]; then
    cp "$APP_DIR/configs/live.yaml.example" "$APP_DIR/configs/live.yaml" \
        && log "bootstrapped configs/live.yaml from live.yaml.example" \
        || die "cannot create configs/live.yaml from live.yaml.example"
fi

# Per-user config for the HealthEngine (release/paths.get_user_config_path()):
# without it /health reports CONFIGURATION FAIL -> verdict NOT READY -> the
# container is marked unhealthy forever. Copy the safe PAPER template on
# first run (same logic as `nexus setup`, non-interactive).
USER_CFG="$(python -c 'from nexus_scalp.release import paths; print(paths.get_user_config_path())' 2>/dev/null || true)"
if [ -n "$USER_CFG" ] && [ ! -f "$USER_CFG" ] && [ -f "$APP_DIR/configs/base.yaml" ]; then
    mkdir -p "$(dirname "$USER_CFG")" \
        && cp "$APP_DIR/configs/base.yaml" "$USER_CFG" \
        && log "bootstrapped user config: $USER_CFG" \
        || log "WARN: could not bootstrap user config at $USER_CFG"
fi

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
# NOTE: `nexus db migrate/status` carry NO --workspace option; the workspace
# is the process CWD (db_path_for_domain resolves artifacts/ relative to it).
# (docker-repair 2026-09-09: `--workspace "$APP_DIR"` made the gate fail
# with "No such option" even though migrations were healthy.)
# ------------------------------------------------------------
log "database migration gate (workspace=$APP_DIR)"
cd "$APP_DIR" || die "cannot chdir to $APP_DIR"
if ! python -m nexus_scalp.cli.main db migrate >/dev/null 2>&1; then
    if python -m nexus_scalp.cli.main db status >/dev/null 2>&1; then
        log "no pending migrations"
    else
        die "database migration gate failed. Run: docker compose exec core python -m nexus_scalp.cli.main db status"
    fi
fi
log "database ready"

# ------------------------------------------------------------
# 3b. First-run config bootstrap (idempotent)
# HealthEngine checks the USER config (~appuser/.nexusscalpengine/config/
# nexus.yaml), which does not exist on a fresh container. `nexus repair`
# provisions it from configs/base.yaml (PAPER-safe template) plus the
# settings/logs dirs — same path an interactive `nexus setup` uses.
# (docker-repair 2026-09-09: /health reported CONFIGURATION FAIL -> 503
# NOT READY forever on a fresh volume because nothing created the file.)
# ------------------------------------------------------------
if ! python -m nexus_scalp.cli.main repair --no-verify >/dev/null 2>&1; then
    log "WARNING: nexus repair reported a problem (continuing; /health will reflect it)"
fi
log "config bootstrap done"

# ------------------------------------------------------------
# 3c. Model artifact provisioning (PAPER starter, idempotent).
# The artifacts volume starts empty; the engine fail-closes a missing model
# (MODEL_LOAD_REJECTED -> container restart loop, docker-repair 2026-09-09).
# Provision a real ScalpNet 70D/3-class starter bundle with matching manifest
# digest (verified class) — same provisioning contract as CI runtime_gate.
# A trained champion mounted into the volume is detected (digest verified)
# and left untouched.
# ------------------------------------------------------------
if python /app/docker/provision_model.py; then
    log "model bundle ready (starter or existing verified champion)"
else
    log "WARN: model provisioning failed — /health MODEL category will report FAIL"
fi

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