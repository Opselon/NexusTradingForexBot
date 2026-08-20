#!/usr/bin/env sh
# ============================================================
# Nexus Scalp Engine — developer lifecycle helpers (POSIX)
# ============================================================
# Keep the core configuration in docker-compose.yml / .env; these
# scripts are thin convenience wrappers so nobody has to memorize
# compose flags. Windows users have the same commands in start.ps1.
# ============================================================
set -e

cd "$(dirname "$0")/.."

COMPOSE="docker compose"
ENV_FILE=".env"

cmd="${1:-up}"

case "$cmd" in
    up)
        if [ ! -f "$ENV_FILE" ]; then
            echo "[nexus] .env not found — copying .env.example (safe dev defaults)"
            cp .env.example "$ENV_FILE"
        fi
        shift 2>/dev/null || true
        $COMPOSE up -d --build "$@"
        echo "[nexus] stack started. UI: http://localhost:${NSE_WEB_PORT:-9090}"
        ;;
    down)
        shift 2>/dev/null || true
        $COMPOSE down "$@"
        ;;
    logs)
        shift 2>/dev/null || true
        $COMPOSE logs -f --tail=100 "$@"
        ;;
    ps)
        $COMPOSE ps
        ;;
    restart)
        $COMPOSE restart core redis
        ;;
    reset)
        echo "[nexus] WARNING: removing containers AND named volumes (databases, models, research artifacts will be DELETED)."
        read -r -p "Type YES to proceed: " confirm
        if [ "$confirm" = "YES" ]; then
            $COMPOSE down -v
            echo "[nexus] volumes removed."
        else
            echo "[nexus] aborted."
        fi
        ;;
    doctor)
        docker info >/dev/null 2>&1 || { echo "[nexus] FAIL: Docker daemon not running."; exit 1; }
        $COMPOSE config --quiet && echo "[nexus] compose config OK"
        if [ ! -f "$ENV_FILE" ]; then
            echo "[nexus] WARNING: .env missing (defaults will be used)"
        fi
        ;;
    *)
        echo "usage: $0 {up|down|logs|ps|restart|reset|doctor}" >&2
        exit 2
        ;;
esac