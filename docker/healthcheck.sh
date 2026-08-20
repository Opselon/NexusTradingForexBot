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
if ! pgrep -f "nexus_scalp.cli.main start" >/dev/null 2>&1; then
    echo "unhealthy: engine process not found"
    exit 1
fi

# API reachability.
if ! curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "unhealthy: /health not reachable on port ${PORT}"
    exit 1
fi

# Verdict gate: healthy == READY or DEGRADED (optional subsystems may be
# degraded without failing the container).
verdict="$(curl -fsS --max-time 4 "http://127.0.0.1:${PORT}/health" | python -c "import sys,json; print(json.load(sys.stdin).get('verdict','UNKNOWN'))" 2>/dev/null || echo UNKNOWN)"
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