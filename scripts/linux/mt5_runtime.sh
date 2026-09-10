#!/usr/bin/env bash
# =============================================================================
# Nexus MT5 Linux runtime toolkit (TEST/PAPER use)
# -----------------------------------------------------------------------------
# Manages an isolated Wine prefix containing the official MetaTrader 5
# terminal for the Nexus Linux test platform.
#
#   LAYOUT (override via env):
#     NEXUS_MT5_ROOT    = /home/ubuntu/nexus-mt5     (everything MT5 lives here)
#     $ROOT/test/prefix                              Wine prefix (WINEARCH=win64)
#     $ROOT/test/prefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#     $ROOT/cache/mt5setup.exe                       official MetaQuotes installer
#     $ROOT/logs/                                    launch/health logs (timestamped)
#
#   COMMANDS
#     install      one-time: download + silent-install the official MT5 build
#     start        launch terminal64.exe /portable under Xvfb (headless-safe)
#     stop         graceful wineserver shutdown (terminal exits with code 0)
#     status       is the terminal process alive?
#     health       process + terminal-log freshness check (exit 0 = healthy)
#     logs         tail the current terminal log (UTF-16 -> UTF-8)
#     restart      stop && start with health verification
#
#   SAFETY
#     - Test prefix only. Credentials are NEVER written here; the login flow
#       stays manual in the terminal UI or via explicit operator-supplied
#       config mounted into the prefix's Config directory.
#     - No sudo required at runtime (only the one-time apt install of wine).
# =============================================================================
set -u

ROOT="${NEXUS_MT5_ROOT:-/home/ubuntu/nexus-mt5}"
PREFIX="$ROOT/test/prefix"
CACHE="$ROOT/cache"
LOGDIR="$ROOT/logs"
WINE_BIN="${NEXUS_MT5_WINE:-/opt/wine-staging/bin/wine}"
WINE_SERVER="${NEXUS_MT5_WINESERVER:-/opt/wine-staging/bin/wineserver}"
MT5DIR="$PREFIX/drive_c/Program Files/MetaTrader 5"
TERMINAL="$MT5DIR/terminal64.exe"
DISPLAY_NUM="${NEXUS_MT5_DISPLAY:-99}"

export WINEPREFIX="$PREFIX"
export WINEARCH="${NEXUS_MT5_WINEARCH:-win64}"
export WINEDEBUG="${NEXUS_MT5_WINEDEBUG:--all}"
export WINEDLLOVERRIDES="${NEXUS_MT5_DLLOVERRIDES:-mscoree,mshtml=}"

log() { printf '[nexus-mt5] %s\n' "$*"; }

ensure_dirs() { mkdir -p "$PREFIX" "$CACHE" "$LOGDIR"; }

terminal_running() {
    pgrep -f "terminal64.exe" >/dev/null 2>&1
}

xvfb_up() {
    # xdpyinfo is optional (x11-utils); fall back to socket existence probe
    if command -v xdpyinfo >/dev/null 2>&1; then
        xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1
        return $?
    fi
    [ -S "/tmp/.X11-unix/X$DISPLAY_NUM" ]
}

ensure_xvfb() {
    if xvfb_up; then return 0; fi
    if ! command -v Xvfb >/dev/null 2>&1; then
        log "ERROR: Xvfb binary not found (apt-get install xvfb)"
        return 1
    fi
    log "starting Xvfb :$DISPLAY_NUM"
    (Xvfb ":$DISPLAY_NUM" -screen 0 1280x1024x24 >/dev/null 2>&1 &)
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        xvfb_up && return 0
    done
    # fallback: wait longer (slow box) then give up
    sleep 5
    xvfb_up
}

cmd_install() {
    ensure_dirs
    if [ -x "$TERMINAL" ] || [ -f "$TERMINAL" ]; then
        log "terminal64.exe already present: $TERMINAL"
        return 0
    fi
    if [ ! -f "$CACHE/mt5setup.exe" ]; then
        log "downloading official installer (MetaQuotes CDN)"
        curl -sSL --max-time 300 -o "$CACHE/mt5setup.exe" \
            "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" \
            || { log "ERROR: download failed"; return 1; }
    fi
    sha256sum "$CACHE/mt5setup.exe" | tee "$CACHE/mt5setup.exe.sha256"
    log "running silent install (/auto) — no sudo, user prefix only"
    wine "$CACHE/mt5setup.exe" /auto > "$LOGDIR/install_$(date +%Y%m%d_%H%M%S).log" 2>&1
    if [ ! -f "$TERMINAL" ]; then
        log "ERROR: install finished but terminal64.exe missing — inspect $LOGDIR"
        return 1
    fi
    log "installed OK"
}

cmd_start() {
    ensure_dirs
    if terminal_running; then
        log "terminal already running"
        return 0
    fi
    ensure_xvfb || { log "ERROR: Xvfb unavailable"; return 1; }
    export DISPLAY=":$DISPLAY_NUM"
    log "launching terminal64.exe /portable"
    (cd "$MT5DIR" && nohup "$WINE_BIN" terminal64.exe /portable \
        > "$LOGDIR/terminal_launch.log" 2>&1 &)
    sleep 8
    if terminal_running; then
        log "started (pid $(pgrep -f terminal64.exe | head -1))"
        return 0
    fi
    log "ERROR: terminal did not start — see $LOGDIR/terminal_launch.log"
    return 1
}

cmd_stop() {
    if ! terminal_running; then
        log "terminal not running"
        return 0
    fi
    log "stopping via wineserver -k (graceful terminal shutdown)"
    "$WINE_SERVER" -k || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        terminal_running || break
        sleep 1
    done
    terminal_running && { log "WARN: terminal still running"; return 1; }
    log "stopped"
}

cmd_status() {
    if terminal_running; then
        log "RUNNING (pid $(pgrep -f terminal64.exe | head -1))"
        return 0
    fi
    log "STOPPED"
    return 1
}

cmd_health() {
    local rc=0
    if ! terminal_running; then
        log "HEALTH: FAIL — terminal process not running"
        return 1
    fi
    log "HEALTH: process alive"
    local today logfile
    today="$(date +%Y%m%d).log"
    logfile="$MT5DIR/logs/$today"
    if [ ! -f "$logfile" ]; then
        log "HEALTH: WARN — no terminal log for today ($logfile)"
        return 0
    fi
    local mtime now age
    mtime="$(stat -c %Y "$logfile" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$(( now - mtime ))
    if [ "$age" -gt 600 ]; then
        log "HEALTH: WARN — terminal log stale (${age}s old)"
    else
        log "HEALTH: log fresh (${age}s old)"
    fi
    return $rc
}

cmd_logs() {
    local today logfile
    today="$(date +%Y%m%d).log"
    logfile="$MT5DIR/logs/$today"
    [ -f "$logfile" ] || { log "no log for today"; return 1; }
    tail -40 "$logfile" | iconv -f UTF-16LE -t UTF-8 2>/dev/null || tail -40 "$logfile"
}

cmd_restart() {
    cmd_stop || return 1
    sleep 2
    cmd_start || return 1
    sleep 15
    cmd_health
}

cmd_version() {
    if [ ! -f "$TERMINAL" ]; then
        log "terminal not installed"
        return 1
    fi
    python3 - "$TERMINAL" <<'PYEOF'
import re, sys, hashlib
p = sys.argv[1]
data = open(p, 'rb').read()
print("sha256:", hashlib.sha256(data).hexdigest())
best = None
for m in re.finditer(rb"5\.(\d{2})\.(\d{3,5})", data):
    v = m.group(0).decode()
    best = v
print("build-string (last seen):", best)
PYEOF
}

case "${1:-help}" in
    install) cmd_install ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    health)  cmd_health ;;
    logs)    cmd_logs ;;
    restart) cmd_restart ;;
    version) cmd_version ;;
    *) log "usage: mt5_runtime.sh {install|start|stop|status|health|logs|restart|version}" ;;
esac
