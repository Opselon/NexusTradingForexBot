#!/usr/bin/env sh
# ============================================================
# Nexus Scalp Engine — container healthcheck
# ============================================================
# Verifies, in order:
#   1. the process is alive
#   2. the REST API answers on the configured port
#   3. the underlying health verdict is READY or DEGRADED
#      (NOT READY => unhealthy; the container restarts per policy)
#
# GO-API-GATE: when the Go API plane is up, it is the published API
# entrypoint (8087) and proxies every /api route to Python on 9090. Its
# /health is a GO-LOCAL liveness probe (it answers even while Python is
# still booting), so it is reported alongside the engine verdict as
# diagnostic context but NEVER gates the container — the engine verdict
# below remains the single authority. A downed Go plane degrades the API
# entrypoint, not the product: Python still serves the full surface.
set -e

PORT="${NSE_WEB_PORT:-9090}"
GO_PORT="${NSE_GO_API_PORT:-8087}"

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

# Go API plane liveness (diagnostic only — never gates the verdict). The
# plane is optional: NSE_GO_API_DISABLE=1, a busy port, or a failed start
# leaves Python serving :9090 directly, which is still a healthy container.
go_state="(skipped)"
if [ -n "${GO_PORT:-}" ]; then
    case "$GO_PORT" in
        ''|*[!0-9]*) go_state="(bad port '$GO_PORT')" ;;
        *)
            # curl exits non-zero on any 4xx/5xx (22/23) even though -w prints
            # the real status; `|| echo 000` would append "000" to it
            # ("503000"), misreporting a healthy 503 as an unknown HTTP code.
            go_code="$( { curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
                "http://127.0.0.1:${GO_PORT}/health" 2>/dev/null; } || true )"
            case "$go_code" in
                200|503) go_state="up(:${GO_PORT})" ;;
                000|"")   go_state="down(:${GO_PORT}, not listening — python serves :${PORT})" ;;
                *)       go_state="http${go_code}(:${GO_PORT})" ;;
            esac
            ;;
    esac
fi

case "$verdict" in
    READY|DEGRADED)
        echo "healthy (verdict=$verdict, go_api=$go_state)"
        exit 0
        ;;
    *)
        echo "unhealthy (verdict=$verdict, go_api=$go_state)"
        exit 1
        ;;
esac