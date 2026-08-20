# Docker Setup & Operations Guide (Nexus Scalp Engine)

This is the canonical reference for the Docker startup, environment contract,
configuration precedence, health/readiness semantics, persistence, and
troubleshooting of the Nexus Scalp Engine stack.

> Companion: `agents/skill.md` §Docker Runtime, `.env.example`, `scripts/start.sh|ps1`.

---

## 1. Why Docker exists in this project

Docker provides a **container-safe evaluation/development environment** for the
engine: the Web UI, REST API, SQLite databases, model pipeline, research worker
and news worker run inside a Linux container in **PAPER mode** (in-memory
simulated execution — no MetaTrader 5, no broker, no real orders).

**What Docker is NOT for:**

- **LIVE trading** — the container has no MT5 terminal. `NSE_EXECUTION__MODE=LIVE`
  is rejected at the entrypoint. Live execution runs on Windows against a real
  MT5 terminal (see `docs/70D_PRODUCTION_DEPLOYMENT.md`).
- **PostgreSQL** — the project's authoritative persistence is **per-domain
  SQLite** (`artifacts/audit.db`, `artifacts/news.db`, `artifacts/candle_intel.db`)
  plus the settings DB. No service in `src/` connects to PostgreSQL; the legacy
  `postgres` service was removed from the compose file.

---

## 2. Quick start (first run)

```bash
git clone <repo>
cd NexusTradingForexBot
docker compose up -d --build
```

That one command performs the full startup sequence:

```
1. build the image (multi-stage, dependency-cached)
2. create the internal network + named volumes
3. start redis (internal only) and wait for its healthcheck
4. start core — entrypoint:
   a. environment validation   (fail-fast, actionable messages)
   b. directory bootstrap      (artifacts/, data/ — idempotent)
   c. database migration gate  (canonical `nexus db` engine, per-domain SQLite)
   d. startup summary
   e. exec the engine          (PAPER mode, API on 0.0.0.0:9090)
5. Docker healthcheck polls GET /health until READY/DEGRADED
```

The UI is then at **http://localhost:9090** (or `${NSE_WEB_PORT}`).

Windows PowerShell / CMD equivalent:

```powershell
.\scripts\start.ps1 up
```

Both `scripts/start.sh` and `scripts/start.ps1` are thin wrappers that copy
`.env.example` to `.env` when missing, then run `docker compose up -d --build`.
They contain **no duplicated logic** — the configuration lives exclusively in
`docker-compose.yml` / `.env`.

---

## 3. Environment variables (`.env.example` is the contract)

Copy once, edit only what you need:

```bash
cp .env.example .env        # Windows: Copy-Item .env.example .env
```

### Classification

| Variable | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `NSE_EXECUTION__MODE` | defaulted | `PAPER` | `PAPER`/`SHADOW` only in containers; `LIVE` fails fast |
| `NSE_EXECUTION__SYMBOL` | defaulted | `XAUUSD` | symbol served by the engine |
| `NSE_WEB_PORT` | defaulted | `9090` | host port mapping (container port fixed at 9090) |
| `NSE_LOG_LEVEL` | defaulted | `INFO` | engine log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `TZ` | defaulted | `UTC` | container timezone — keep UTC for trading consistency |
| `NSE_MODEL__MODEL_ARTIFACT_PATH` | defaulted | `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` | model path **inside the container** |
| `NSE_RISK__MAX_ACCOUNT_DRAWDOWN_PCT` | defaulted | `2.0` | drawdown safety limit |
| `NSE_RISK__RISK_PER_TRADE_PCT` | defaulted | `0.5` | risk per trade |
| `NSE_NEWS__ENABLED` | defaulted | `true` | news worker on/off (degrades, never crashes core) |
| `NSE_TELEGRAM__ENABLED` | defaulted | `false` | Telegram on/off (credentials via settings store/UI, never `.env`) |

**No secrets live in `.env.example`.** Telegram bot tokens, broker credentials
and API keys are managed by the runtime settings architecture (settings DB /
secure secret store / `NEXUS_TELEGRAM_BOT_TOKEN` env override) — not by Docker.
`.env` is git-ignored; the repository must never contain real secrets.

### Why `NSE_...` naming?

`AppConfig` (`src/nexus_scalp/configuration/config.py`) is a
`pydantic-settings` model with `env_prefix="NSE_"` and nested delimiter `__`.
For example `NSE_EXECUTION__MODE` maps to `config.execution.mode`,
`NSE_MODEL__MODEL_ARTIFACT_PATH` maps to `config.model.model_artifact_path`.
The compose file passes exactly these names — no translation layer.

---

## 4. Configuration precedence

```
code defaults (pydantic)  <  YAML config (configs/*.yaml)  <  env vars (NSE_*)  <  runtime settings DB
```

- The **runtime configuration architecture remains authoritative** (settings DB
  `app_settings.db` + `execution.mode` key, secure secret store for Telegram).
- `live.yaml` is a legacy/compatibility surface: the engine reads it for
  bootstrap values, migrates legacy secrets once, then blanks them. Docker does
  **not** reintroduce `live.yaml` as a source of truth — env vars in compose are
  bootstrap knobs only.
- In containers the engine runs with `--config configs/live.yaml` (the image
  ships `base.yaml` + `live.yaml.example`; the entrypoint validates the
  environment before any engine code runs).

---

## 5. Services & dependency map

```text
docker compose up -d
        │
        ▼
   ┌──────────┐   healthcheck (redis-cli ping)
   │  redis   │◄── internal only, never exposed to host
   └──────────┘
        │ depends_on: service_healthy
        ▼
   ┌──────────┐   entrypoint: env-validate → mkdir → db migrate → exec engine
   │   core   │   /health → READY | DEGRADED
   └──────────┘   ports: 9090 (host) ← 9090 (container)
```

| Service | Role | Classification | Exposed ports |
| :--- | :--- | :--- | :--- |
| `core` | engine + Web UI + REST API + workers (research/training/news/shadow, all internally gated) | CORE | `9090` |
| `redis` | telemetry/cache (declared for parity with the architecture; internal) | CORE (infra) | none |

Deliberately **absent** from the default stack: PostgreSQL (no consumer),
training/research workers as separate containers (they run **inside** the core
process, are bound by their own intervals and `auto_train_enabled=False` —
research only runs when the dataset changes; **no expensive autonomous work is
started by `docker compose up`**), Telegram (disabled by default), database
admin, debug tools (add `--profile dev` slots when needed).

---

## 6. Health & readiness

### Container healthcheck (`docker/healthcheck.sh`)

1. engine process alive (`pgrep`)
2. `GET /health` reachable on `${NSE_WEB_PORT:-9090}`
3. verdict ∈ {`READY`, `DEGRADED`} → healthy; `NOT READY`/error → unhealthy

### Application `/health` endpoint (`src/nexus_scalp/web/server.py`)

Runs the canonical `HealthEngine` (the same engine `nexus doctor` uses):

- `READY` (200) — SYSTEM/RUNTIME/CONFIGURATION/DATABASE/MODEL/FEATURE_SCHEMA all PASS
- `DEGRADED` (200) — a non-critical subsystem (NEWS, WORKERS, TELEGRAM, …) warns
- `NOT READY` (503) — a critical category FAILED (e.g. model missing)

Optional subsystems may be DEGRADED without failing the container; the core
must be READY for the stack to be considered healthy.

Startup states: `STARTING` (during entrypoint + first healthcheck window) →
`READY` (engine migrated, configured, model provisioned, API serving).

---

## 7. Persistence & volumes

| Volume | Mount | Contents |
| :--- | :--- | :--- |
| `nexus-artifacts` | `/app/artifacts` | SQLite DBs (`audit.db`, `news.db`, `candle_intel.db`), `models/`, research artifacts/registry, logs |
| `nexus-data` | `/app/data` | raw/validated data |
| `redis-data` | `/data` | (appendonly disabled; ephemeral by design) |

- `docker compose down` then `docker compose up -d` **preserves data**.
- Only an explicit destructive reset removes volumes:
  `docker compose down -v` (or `scripts/reset-dev.ps1`, which asks for `YES`).
- **Backup:** `.\scripts\backup-db.ps1` — exports `audit/news/candle_intel.db`
  via sqlite3 `.backup` to `backups/<timestamp>/` (safe online backup).
- **Restore:** copy the file over `/app/artifacts/<name>` then
  `docker compose restart core`.

### Models

Model artifacts must use container paths — never Windows paths:

```text
NSE_MODEL__MODEL_ARTIFACT_PATH=artifacts/models/scalp/XAUUSD/v1.0.0/model.pt
```

(with `/app` as the working directory). Mount a trained bundle under
`/app/artifacts/models/...` or bake it into the image. At startup the engine
verifies existence/readability; a missing model degrades the stack honestly
(`/health` → `NOT READY` for the MODEL category) instead of silently loading an
old incompatible artifact.

---

## 8. Database migrations

Per-domain SQLite migrations use the **canonical TASK-10 engine**
(`database/engine.py`, CLI `nexus db ...`). Startup runs the same migration
gate the engine itself runs before entering READY:

```bash
docker compose exec core python -m nexus_scalp.cli.main db status
docker compose exec core python -m nexus_scalp.cli.main db migrate
```

Migration failures surface loudly (entrypoint dies with a clear message; the
container stays unhealthy) — the app never pretends to be healthy after a
failed migration. Concurrent replicas are not a scenario here (single core
replica), and the gate is idempotent + versioned.

---

## 9. Startup validation (fail-fast)

The entrypoint rejects, before the engine starts:

| Problem | Message |
| :--- | :--- |
| `NSE_EXECUTION__MODE=LIVE` | explicit container restriction, points to docs |
| invalid mode value | shows the value + accepted set |
| invalid `NSE_WEB_PORT` | shows the value + range |
| missing bootstrap var | names the var + the rerun command |

The engine additionally runs `nexus doctor`-style checks (HealthEngine) — a
bad YAML or missing model artifact fails `/health` clearly instead of a random
crash later. `scripts/doctor.ps1` / `scripts/start.sh doctor` pre-flight the
host: Docker CLI, daemon, compose plugin, `.env`, compose config validity, and
port availability, **before** an expensive startup.

---

## 10. Logging, timezone, resources

- **Logging:** structured structlog; `docker compose logs -f core` shows
  INFO/WARNING/ERROR with the standard structlog renderer. Logs inside the
  container are bounded (json-file driver, max-size 10m, 3 files — no unbounded
  growth).
- **Timezone:** containers run `TZ=UTC` by default (settable via `.env`).
  Engine timestamps are UTC throughout; keep UTC unless a specific workflow
  requires otherwise.
- **Resources:** the research/training/shadow/news workers run inside the core
  process with their own cadences and bounded budgets; no worker consumes
  unbounded RAM/CPU. Add explicit `deploy.resources.limits` when running
  training-heavy hosts.

---

## 11. Troubleshooting

| Symptom | Cause / fix |
| :--- | :--- |
| `docker compose up` fails at entrypoint with `FATAL: execution mode` | set `NSE_EXECUTION__MODE=PAPER|SHADOW` in `.env` (LIVE unsupported) |
| `/health` → `NOT READY`, MODEL FAIL | no model artifact in the volume; mount one or accept degraded dev state |
| DB migration gate fails | `docker compose exec core python -m nexus_scalp.cli.main db status` — repair per its output |
| port 9090 busy on host | set `NSE_WEB_PORT` in `.env`, or stop the other process |
| container restarts in a loop | `docker compose logs core` — entrypoint validation or healthcheck verdict tells why (never blind `sleep 30`) |
| stale image/behavior | `docker compose up -d --build` |
| want a totally clean slate | `scripts/reset-dev.ps1` (destructive — asks for confirmation) |
| docker compose down -v used by mistake | restore from `backups/<timestamp>/` via `scripts/backup-db.ps1` artifacts |

---

## 12. Version requirements

- Docker Engine 24+ / Docker Desktop 4.x+ (Compose v2 plugin; `docker compose`
  subcommand). The repo pins no engine API features beyond standard Compose.

---

## 13. Developer workflow summary

```bash
cp .env.example .env                 # first setup (optional — defaults exist)
scripts/start.sh doctor              # host pre-flight (Windows: .\scripts\doctor.ps1)
docker compose up -d --build         # build + start + migrate + healthy
docker compose ps                    # service / status / health / ports
docker compose logs -f core          # follow logs
docker compose restart core          # restart after config change
docker compose down                  # stop (data preserved)
scripts/reset-dev.ps1                # destructive reset (asks YES)
```

Relevant files: `Dockerfile`, `docker-compose.yml`, `.env.example`,
`.dockerignore`, `docker/entrypoint.sh`, `docker/healthcheck.sh`,
`scripts/start.{sh,ps1}`, `scripts/doctor.ps1`, `scripts/reset-dev.ps1`,
`scripts/backup-db.ps1`, `docs/docker.md`.