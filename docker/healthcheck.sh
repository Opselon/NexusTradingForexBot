#!/usr/bin/env sh
# ============================================================
# Nexus Scalp Engine — container healthcheck
# ============================================================
# Verifies, in order:
#   1. the process is alive
#   2. the REST API answers on the configured port
#   3. the underlying health verdict is READY or DEGRADED
#      (NOT READY => unhealthy; the container restarts per policy)
set -e

PORT="${NSE_WEB_PORT:-9090}"

# Process liveness (cheap first gate — avoids HTTP noise during pauses).
# NOTE: debian-slim ships no procps (no pgrep/pidof); walk /proc directly so
# the image stays minimal (docker-repair 2026-09-09: healthcheck always
# failed with "pgrep: not found" -> permanently unhealthy container).
engine_alive=0
for p in /proc/[0-9]*/cmdline; do
    cmd="$(tr '\0' ' ' < "$p" 2>/dev/null)"
    case "$cmd" in
        *nexus_scalp.cli.main\ start*) engine_alive=1; break ;;
    esac
done
if [ "$engine_alive" -eq 0 ]; then
    echo "unhealthy: engine process not found"
    exit 1
fi

# Verdict gate: healthy == READY or DEGRADED (optional subsystems may be
# degraded without failing the container).
# NOTE: the engine's /health returns HTTP 503 whenever the verdict is
# NOT READY **or DEGRADED** (web/diagnostics_state_routes.py health_probe:
# 503 {verdict, checks} for anything non-READY). So "curl -f" alone is not a
# health signal — parse the verdict from the 503 body instead
# (docker-repair 2026-09-09: DEGRADED container with a working engine was
# reported unhealthy forever because curl exit 22 aborted first).
verdict="$(curl -sS --max-time 4 "http://127.0.0.1:${PORT}/health" 2>/dev/null | python -c "import sys,json; d=json.load(sys.stdin); print((d.get('detail') or d).get('verdict','UNKNOWN'))" 2>/dev/null || echo UNKNOWN)"
case "$verdict" in
    READY|DEGRADED)
        echo "healthy (verdict=$verdict)"
        exit 0
        ;;
    *)
        echo "unhealthy (verdict=$verdict)"
        exit 1
        ;;
esac