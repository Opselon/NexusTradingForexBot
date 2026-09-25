# Code Navigation Map — Nexus Scalp Engine

> Where to find things, for a developer (or agent) landing fresh in this repo.
> This map is an INDEX. Detailed truth lives in:
> - `agents/skill.md` — authoritative architecture map + repo contract (READ FIRST)
> - `docs/architecture/dependency-map.md` — subsystem edges + contracts
> - `docs/architecture/comment-style.md` — comment/doc conventions
> - `docs/RELEASE.md` — CLI exit codes, update protocol, release verification
> - `agents/bugs.md` — bug ledger (append-only; grep `^## BUG-` tail for next free id)

## Startup & lifecycle

| Concern | Where |
|---|---|
| Primary launcher (bootstrap sys.path, adapter bind, engine + web) | `NexusTradingForexBot.py` |
| CLI entry (`nexus` / `nse`, Typer) | `src/nexus_scalp/cli/main.py` |
| Engine construction + async tick loop | `src/nexus_scalp/application/live_engine.py` (`run_loop`, `_process_tick_pipeline`) |
| Background-daemon spawn + atomic pidfile claim (BUG-170) | `src/nexus_scalp/cli/main.py` (`_spawn_daemon`, `stop` command) |
| Engine-start regression gate (run FIRST on start bugs) | `tests/integration/test_engine_runtime_launch.py` |

## Configuration & settings

| Concern | Where |
|---|---|
| Bootstrap config (`AppConfig`) | `src/nexus_scalp/configuration/config.py` |
| Authoritative live state (versioned snapshot, hot-reload) | `src/nexus_scalp/configuration/runtime_config.py` (`RuntimeConfigStore.get_snapshot()`) |
| Settings service / secrets (Telegram creds ONLY via `set_telegram`) | `src/nexus_scalp/settings/service.py`, `secret_store.py` |
| YAML config files | `configs/` |

## Persistence

| Concern | Where |
|---|---|
| Append-only audit store (SQLite WAL + background writer, `flush()` before read-after-write) | `src/nexus_scalp/adapters/database/audit_repository.py` |
| Schema migrations (additive, idempotent) | `src/nexus_scalp/database/` (migration modules; `ddl_port.py` portability) |
| DB hygiene / quarantine / consistency rules | `src/nexus_scalp/hygiene/` (`worker.py` budget invariant) |

## Features & model contract (50D / 70D)

| Concern | Where |
|---|---|
| **Canonical 70D contract SSoT (layout, hash, schema id)** | `src/nexus_scalp/features/schema_contract.py` — `scalp_v3`, DIM 70; 0..49 base (scalp_v1) · 50..59 news · 60..69 liquidity |
| 50D base feature engine + names | `src/nexus_scalp/features/scalp_features.py` (`FEATURE_NAMES`, `FEATURE_VECTOR_50D`) |
| Schema registry (append-only, strict resolve) | `src/nexus_scalp/features/schema.py` |
| Liquidity 10D producer | `src/nexus_scalp/features/liquidity_engine.py` |
| News context / 10D selection | `src/nexus_scalp/news/`, `src/nexus_scalp/model_generation/news_bridge.py` |
| Model definition (num_features param, 4-class) | `src/nexus_scalp/models/scalp_net.py` |
| Inference validation (scaler dim, hash) | `src/nexus_scalp/` inference validator (`inference_validator`) |
| Artifact width-contract guard (BUG-141) | `src/nexus_scalp/application/live_engine.py` (`_declared_contract_dim_for_path`, `_save_model_weights_atomic`) |

## Signals → risk → execution

| Concern | Where |
|---|---|
| Policy (tick hot path, dedup, re-entry lockout, BUG-169 duplicate handling) | `src/nexus_scalp/signals/policy.py` |
| Rule matrix (~30 rules, TTL cache; note deliberate Monday-00:00 gate) | `src/nexus_scalp/signals/rule_matrix.py` |
| Risk sizing (fail-closed, tier caps, HARD_MAX_LOTS) | `src/nexus_scalp/risk/risk_engine.py` |
| Order router (60-scenario, 11 states, debounced transitions, ambiguous-fill recovery) | `src/nexus_scalp/execution/order_manager.py` |
| MT5 adapter (idempotency guards, account-identity fail-safe BUG-142) | `src/nexus_scalp/adapters/mt5/mt5_adapter.py` |
| Ports (changing IMT5Port → all adapters) | `src/nexus_scalp/ports/` |

## Web / API / UI

| Concern | Where |
|---|---|
| FastAPI app factory (SSE `/api/ticks/stream`, background tasks in `app.state`) | `src/nexus_scalp/web/server.py` (`create_app`) |
| Debug snapshot + panels | `src/nexus_scalp/web/debug_snapshot.py` |
| DB console (read-only guard) | `src/nexus_scalp/web/db_console.py` |
| Frontend | `Web/` (`index.html`, `app.js`) — enums serialized via `serialize_enums()` |

## Updater / release / verification

| Concern | Where |
|---|---|
| Update engine (discovery, SafeDownloader resume+206 validation BUG-171, digest resolution) | `src/nexus_scalp/release/updater.py` |
| Release verification (BUG-160 layout resolution) | `src/nexus_scalp/release/verify.py` |
| Update CLI exit codes (FAILED_SAFE ≠ success BUG-173) | `src/nexus_scalp/cli/main.py` (`update` commands) + `docs/RELEASE.md` |
| Release workflow (pre-stage contract before ISCC BUG-166, MEIPASS identity BUG-174) | `.github/workflows/release.yml` |

## Observability / forensics / governance

| Concern | Where |
|---|---|
| Structured logging (severity-split logs/<sev>/YYYY/MM/) | `src/nexus_scalp/observability/logging.py` |
| Telegram notifier (read-only observability) | `src/nexus_scalp/observability/telegram_notifier.py` |
| Forensic deploy gate (CHECK-*, exit semantics 0/1/2/3, BUG-162/166 fail-safe) | `src/nexus_scalp/forensics/checks.py` |
| Incidents (diagnostic-only, idempotent reconcile) | `src/nexus_scalp/incidents/` |
| Experience ledger / gate (never order authority) | `src/nexus_scalp/experience/` |
| Shadow runtime (simulated-only PnL, no auto-promotion) | `src/nexus_scalp/shadow/` |

## Research / training

| Concern | Where |
|---|---|
| Research pipeline + gates (no adapter/risk handle — INV) | `src/nexus_scalp/research/` (`pipeline.py`, `worker.py`) |
| Walk-forward trainer (purged, embargoed) | `src/nexus_scalp/training/walk_forward_trainer.py` |
| Labeling (triple barrier) | `src/nexus_scalp/labeling/triple_barrier.py` |
| Strategy framework (pure signal generators) | `src/nexus_scalp/strategies/` |
| Model lifecycle / champion governance | `src/nexus_scalp/model_lifecycle/`, `src/nexus_scalp/governance/` |

## Quality gates

| Concern | Where |
|---|---|
| beforePush (ruff, ruff format, mypy, critical pytest suite, forensic gate) | `beforePush.ps1` / `beforePush.sh`, `pyproject.toml` |
| Critical suite list | `tests/critical_suite.txt` |
| CI workflows | `.github/workflows/` (`ci.yml`, `release.yml`, `security.yml`, `docker.yml`) |
