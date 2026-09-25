#!/usr/bin/env sh
# ============================================================
# Nexus Scalp Engine — Go API plane bootstrap (container entrypoint shim)
# ============================================================
# GO-API-GATE: the Go API server (/app/nexus-api) is the product's API
# entrypoint — every HTTP request reaches Go, which proxies the Python
# runtime on 127.0.0.1:9090 for the facts it does not own. The image
# precompiles the binary (Dockerfile stage `go-builder`); this shim starts
# it in the background and then hands control to the real entrypoint.
#
# Process model:
#   PID 1 = docker/go-api-bootstrap.sh -> exec docker/entrypoint.sh
#   PID n = /app/nexus-api  (background, supervised sibling)
#   The engine owns the container lifecycle and the signal handling. The Go
#   plane is an ACCELERATION PLANE, never a dependency: Docker reaps it when
#   PID 1 exits.
#
# FAILURE-ISOLATION CONTRACT (mirrors src/nexus_scalp/web/go_api_bootstrap.py,
# the ONLY fallback path in the system):
#   * binary absent / not executable      -> WARN, continue, Python serves :9090
#   * port 8087 already bound             -> WARN, continue (Python-only)
#   * nexus-api exits immediately         -> WARN + exit code, continue
#   * nexus-api never answers /health     -> WARN + kill child, continue
#   This script NEVER exits non-zero because of the Go plane. The Python
#   FastAPI app remains a complete, working API surface on its own port.
# ============================================================
set -e

log()  { printf '[NSE-GO-API] %s\n' "$1"; }
warn() { printf '[NSE-GO-API] WARN: %s\n' "$1" >&2; }

APP_DIR="${NSE_APP_DIR:-/app}"
GO_BIN="${NSE_GO_API_BIN:-$APP_DIR/nexus-api}"
GO_ADDR="${NSE_GO_ADDR:-0.0.0.0:8087}"
PY_ORIGIN="${NSE_PYTHON_ORIGIN:-http://127.0.0.1:9090}"
GO_LOG="$APP_DIR/artifacts/logs/go-api.log"

# ---------------------------------------------------------------------------
# 0. Only ever start in the engine container. An operator running a bare
#    `docker run -it --entrypoint /bin/sh` override has no Python plane to
#    proxy to; skipping here keeps the shim out of the way.
# ---------------------------------------------------------------------------
if [ "${NSE_GO_API_DISABLE:-0}" = "1" ]; then
    log "NSE_GO_API_DISABLE=1 — Go plane disabled, Python serves :9090 directly"
    exec "$APP_DIR/docker/entrypoint.sh" "$@"
fi

# ---------------------------------------------------------------------------
# 1. Binary gate. A missing or non-executable binary is a build/packaging
#    failure, not a boot failure: log it and fall back to Python-only.
# ---------------------------------------------------------------------------
if [ ! -x "$GO_BIN" ]; then
    if [ -f "$GO_BIN" ]; then
        warn "go api binary at $GO_BIN is not executable (mode $(ls -l "$GO_BIN" 2>/dev/null | awk '{print $1}')) — continuing python-only"
    else
        warn "go api binary not found at $GO_BIN — continuing python-only (image build may have skipped the go-builder stage)"
    fi
    exec "$APP_DIR/docker/entrypoint.sh" "$@"
fi

# ---------------------------------------------------------------------------
# 2. Port gate. Loopback, not $GO_ADDR's bind host: the container-internal
#    boundary is always 127.0.0.1 regardless of the published address.
#    /proc/net/tcp is per-netns (not per-process), so one read sees every
#    listening socket in the container. Format: local_address column is
#    "0100007F:1F97" — little-endian IPv4 + big-endian hex port, so the port
#    token alone is a safe probe (debian-slim ships no ss/netstat/pgrep).
# ---------------------------------------------------------------------------
GO_PORT="${GO_ADDR##*:}"
case "$GO_PORT" in
    ''|*[!0-9]*) GO_PORT=8087 ;;
esac
GO_PORT_HEX="$(printf '%04X' "$GO_PORT")"

port_busy=0
for f in /proc/net/tcp /proc/net/tcp6; do
    if grep -q ":${GO_PORT_HEX} " "$f" 2>/dev/null; then
        port_busy=1
        break
    fi
done
if [ "$port_busy" -eq 1 ]; then
    warn "port $GO_PORT already bound — go api plane not started (python serves :9090 directly)"
    exec "$APP_DIR/docker/entrypoint.sh" "$@"
fi

# ---------------------------------------------------------------------------
# 3. Launch. stdout+stderr to the engine log dir so `docker logs` shows the
#    engine and `docker exec cat go-api.log` shows the plane. & so it is a
#    sibling of the eventual PID 1, not a child the exec would orphan-kill.
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$GO_LOG")" 2>/dev/null || true

log "starting go api plane: $GO_BIN -addr $GO_ADDR -python-origin $PY_ORIGIN"
"$GO_BIN" -addr "$GO_ADDR" -python-origin "$PY_ORIGIN" >>"$GO_LOG" 2>&1 &
GO_PID=$!

# ---------------------------------------------------------------------------
# 4. Readiness gate (bounded). The Go server answers /health with 200 once
#    its listener is up — even while Python is still booting, because Go's
#    own health route is a local liveness probe, not a Python dependency
#    check. So this polls ROUTING readiness, not engine readiness (the
#    container HEALTHCHECK owns the latter).
# ---------------------------------------------------------------------------
ready=0
deadline=$(( $(date +%s) + ${NSE_GO_API_READY_TIMEOUT:-15} ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$GO_PID" 2>/dev/null; then
        break
    fi
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
        "http://127.0.0.1:${GO_PORT}/health" 2>/dev/null || echo 000)
    case "$code" in
        200|503) ready=1; break ;;
    esac
    sleep 1
done

if [ "$ready" -eq 1 ]; then
    log "go api plane ready on $GO_ADDR -> $PY_ORIGIN (pid $GO_PID, log $GO_LOG)"
    if [ -n "${NSE_GO_API_PIDFILE:-}" ]; then
        printf '%s\n' "$GO_PID" >"$NSE_GO_API_PIDFILE" 2>/dev/null || true
    fi
else
    # Never fatal. Kill the child if it is still alive and fall back.
    if kill -0 "$GO_PID" 2>/dev/null; then
        warn "go api plane never became ready on $GO_PORT within ${NSE_GO_API_READY_TIMEOUT:-15}s — killing it and continuing python-only"
        kill "$GO_PID" 2>/dev/null || true
    else
        rc=0; wait "$GO_PID" 2>/dev/null || rc=$?
        warn "go api plane exited during startup (rc=$rc) — continuing python-only"
    fi
    warn "see $GO_LOG; python serves the complete API on ${PY_ORIGIN##*://}"
fi

# ---------------------------------------------------------------------------
# 5. Hand off to the real entrypoint. exec so the engine becomes PID 1 and
#    receives SIGTERM from `docker stop` directly (graceful shutdown,
#    ShutdownSupervisor). The Go plane is reaped by the kernel when PID 1
#    exits — no orphan, no supervised teardown needed in the container.
# ---------------------------------------------------------------------------
log "handing off to docker/entrypoint.sh (pid $$ -> engine)"
exec "$APP_DIR/docker/entrypoint.sh" "$@"
