# Nexus Scalp Engine (NSE) — Master Skill

> **Audience:** AI coding agents. **Purpose:** High-signal source of truth for safe implementation.
> **Rule:** Repository code wins over this document. If a claim cannot be verified, mark uncertain — never invent.
> Historical bug forensics → `agents/bugs.md`. Multi-agent workflow → `agents/multi-agent-git-contract.md`.
> Registries: `contracts.md` · `runtime_invariants.md` · `change_control.md` · `taskboard.md` · `repository_state.md` · `locks.yaml`.

## 1. Project Identity

**Nexus Scalp Engine v9.0** — production-grade, high-frequency multi-timeframe scalp system in PyTorch + MetaTrader 5. Primary market **XAUUSD M1**. Package `nexus-scalp-engine` (`src/nexus_scalp`), Python ≥3.11. Licensed Proprietary. Paper vs Live execution is a first-class mode switch; default container/paper is safe.

## 2. Architecture Map

| Layer | Directories | Responsibility | Boundary |
|---|---|---|---|
| Domain | `domain/` (`models.py`, `enums.py`) | Immutable Pydantic `frozen=True` contracts (`TickData`, `TradeProposal`, `Position`, `AccountInfo`) | Never mutate; `.model_copy(update={...})` |
| Ports | `ports/` (`mt5_port.py`, `gateway_port.py`) | Protocol interfaces (dependency inversion) | Changing `IMT5Port` → update all adapters |
| Adapters | `adapters/` (`mt5/`, `paper/`, `database/`) | MT5 IPC (Win32), paper sim, SQLite WAL (`AuditRepository`) with background writer thread | No sync DB on hot path |
| Features | `features/` (`scalp_features.py`, `schema.py`, `schema_contract.py`, `features70.py`, `liquidity_engine.py`, `regime_classifier.py`) | 50D base → 70D canonical assembly, regime | Schema is SSoT; see §5 |
| Models | `models/` (`scalp_net.py` — `ScalpNet`) | Dual-path: 2D MLP (single tick) / 3D TCN+attention (sequence). `num_features` parametrized, `num_classes=4` (`0=NO_TRADE 1=BUY 2=SELL 3=WAIT`) | Input dim must equal feature schema dim |
| Training | `training/` (`walk_forward_trainer.py`), `labeling/` | Purged walk-forward + triple-barrier, Polars | Embargo/purge required; no lookahead |
| Signals | `signals/`, `strategies/`, `research/` | Policy, rule matrix (~30 rules), research/shadow proposals | Never holds adapter/risk handle |
| Risk | `risk/` (`risk_engine.py`) | `calculate_dynamic_volume()` → margin-clamped, tier-capped sizing | Authoritative for boundaries |
| Execution | `execution/` (`order_manager.py`, 6215 lines) | 60-scenario router, 11 position states, BE lock, `HARD_MAX_LOTS=10.0`, `MAX_TOTAL_EXPOSURE=1` | Authoritative for dispatch |
| Accounting | `accounting/` (`core.py`, `aggregation.py`) | Ledger, PnL, market calendar, retention | Historical rows immutable |
| Application | `application/` (`live_engine.py`, 4636 lines) | Async event loop `_process_tick_pipeline`, bar aggregation, hygiene cycle, state sync | Never block event loop |
| Web/API | `web/` (`server.py`, `factory_routes.py`, `db_console.py`, `debug_snapshot.py`), `Web/` frontend | FastAPI, SSE `/api/ticks/stream`, WebSocket `/web`, `serialize_enums()` for JSON | Background tasks in `app.state.background_tasks` |
| Configuration | `configuration/` (`config.py`, `runtime_config.py`), `settings/` (`service.py`, `secret_store.py`) | `AppConfig` is bootstrap/import/export; `RuntimeConfiguration` snapshot via `RuntimeConfigStore.get_snapshot()` is authoritative live state (versioned, hot-reload) | Telegram creds only via `settings_service.set_telegram()` |
| Observability | `observability/` (`logging.py`, `telegram_notifier.py`), `hygiene/`, `incidents/`, `forensics/` | Structured logging, hygiene worker (AUDIT_ONLY default), incidents diagnostic-only, forensics health | Incidents never mutate trading/risk/models/DB |
| Model lifecycle / Governance | `model_lifecycle/`, `governance/`, `shadow/`, `mslie/`, `experience/` | Candidate training, 10-gate load gate, shadow comparison, promotion `READY_FOR_REVIEW→APPROVED→CHAMPION` | Auto-promotion forbidden; shadow never mutates execution |

Ports/adapters isolates MT5 IPC from domain. `LiveEngine` orchestrates tick → features → regime → signals → risk → execution → accounting → web.

## 3. Critical Entrypoints

| Entrypoint | Path | Notes |
|---|---|---|
| **Primary launcher** | `NexusTradingForexBot.py` | Bootstraps `src/` onto `sys.path`, binds `DirectMT5Adapter` or `RemoteMT5GatewayAdapter`, launches `LiveEngine` + Uvicorn web. Also invoked via `main.py` redirect. |
| CLI | `src/nexus_scalp/cli/main.py` (`nse` / `nexus` scripts, Typer) | `start`, `setup`, `db`, `forensic`, `update`, `model-artifacts`; `--mode paper` persisted to settings DB; `--doctor` diagnostics |
| Web server | `src/nexus_scalp/web/server.py` `create_app()` | FastAPI `app` factory; canonical port `9090` in container, `8080→find_available_port` in launcher |
| Config bootstrap | `src/nexus_scalp/configuration/config.py` (`AppConfig`) + `runtime_config.py` (`RuntimeConfigStore`) | Read live state via `get_snapshot()`, not cached constructor values |
| Docker | `Dockerfile` + `docker-compose.yml` (service `core` → `nexus-scalp-core`) | Single container engine+web on `0.0.0.0:9090`, Redis `redis:6379`, volumes `nexus-artifacts` + `nexus-data`; PAPER by default; SQLite `audit.db` in `artifacts/` |
| Quality gate | `beforePush.ps1` / `beforePush.sh` + `pyproject.toml` | ruff lint+format, mypy `src`, pytest critical suite (779 tests, `--cov=src`, xdist `availableGB/1.5`), junit/coverage/html, forensic deploy gate |

## 4. Core Data Flow

```
TickData (MT5/paper)
  → ScalpFeatureEngine.to_tensor_input() → 50D base (scalp_v1, FEATURE_NAMES, [-3,3], finite)
  → assemble_70d / build_70d_vector → 70D canonical (Base 0..49 | News 50..59 | Liquidity 60..69)
  → inference_validator (scaler dim == feature dim, hash check) → ScalpNet logits (4) → confidence gate 0.35
  → regime_classifier (Regime Guardian)
  → signals/policy + rule_matrix (≈30 rules, TTL 5s cache)
  → risk/RiskEngine.calculate_dynamic_volume() + evaluate_proposal() (free margin 20%, tier caps, HARD_MAX_LOTS)
  → execution/OrderManager (60-scenario router, MAX_TOTAL_EXPOSURE=1, 30s pending re-quote lock, 1.0×ATR drift)
  → accounting (TradeOutcome / ACCOUNT_SNAPSHOT) + AuditRepository (SQLite WAL, async worker)
  → web SSE/WebSocket + Telegram (read-only consumer) + experience/research/shadow (never order authority)
```

Bar aggregation and broker snapshot are cached off hot path. News context is cache-only on tick.

## 5. Canonical Contracts

### 5.1 Feature schemas (registry `src/nexus_scalp/features/schema.py` — `FEATURE_SCHEMAS`)

| Schema | Dim | Status | Meaning |
|---|---|---|---|
| `scalp_v1` | **50** | **ACTIVE live contract** | `ACTIVE_SCHEMA_ID = "scalp_v1"` — base protected; what live engine emits today |
| `scalp_v3` | **70** | Candidate (canonical 70D SSoT) | `features/schema_contract.py` — Base 50 + News 10 + Liquidity 10. Defines hash. See 5.2 |
| `scalp_v4` | 70 | Candidate (geometrically interchangeable with v3, `70D_FAMILY`) | `liquidity_runtime.py` compatible ids `{scalp_v3, scalp_v4}` |
| `scalp_v2` | 60 | Candidate | `schema_augment.py` causal augmentation |
| `scalp_liquidity_v1` | 60 | Candidate | Liquidity-only 60D (`liquidity_features_enabled` flag, default false) |

`ACTIVE_SCHEMA_ID` must remain `scalp_v1` until explicit promotion. `FEATURE_SCHEMAS` is append-only; re-register requires `replace=True` and fails if dimension changes. `resolve()` is strict (unknown id raises).

### 5.2 70D canonical contract — SSoT `src/nexus_scalp/features/schema_contract.py`

- **Identity:** `SCHEMA_ID = "scalp_v3"`, `DIMENSION = 70`, `SCHEMA_VERSION = "1.0.0"`.
- **Layout:** `0..49` Base (scalp_v1 `FEATURE_NAMES`, protected) · `50..59` News 10D (`NEWS_10D_NAMES` = `news_context_v1` fields `0..8` + `news_state` index 10; `source_consensus` excluded; § `model_generation/models.py`) · `60..69` Liquidity 10D (`LIQUIDITY_10D_NAMES` ≡ `LiquidityFeatures.as_vector()`).
- **Hash:** `feature_schema_hash(schema_id)` = SHA-256 over canonical JSON `{index, name, family}×70 + schema_id` (prefix 16). Training, inference, replay, manifest compare the same serialization. Reordering changes the hash.
- **Validation:** `validate_70d_vector` and `inference_validator.InferenceContractValidator` enforce exact dim 70, finite, `[-3.0,+3.0]`, optional hash match; scaler dim must equal feature dim (`SCALER_MISMATCH` blocks). `assert_canonical_registry()` guards `scalp_v3==70D` and `ACTIVE==scalp_v1` at import.
- **Assembly:** `features70.assemble_70d(base50, news10|None, liquidity10|None) → Feature70Snapshot` and `liquidity_runtime.build_70d_vector(features50, liquidity10, family_10)` — both strict dim checks; missing blocks require explicit neutral vectors (`FEATURE_DISABLED`), never silent fabrication; `FEATURE_UNAVAILABLE` blocks. `LIQUIDITY_BLOCK 60..69`, `BASE_50D`/`LIQUIDITY_DIM`=10.
- **Compatibility:** 60D model + 70D runtime → `MODEL_INPUT_DIMENSION_MISMATCH` block (historical 2026-08-19 UI state). Compat family logic in `liquidity_runtime.py`.

### 5.3 Other contracts (index in `agents/contracts.md` — additive only)

`TRADE_EXECUTION_CONTEXT` v2 (parent-child lineage), `TRADE_OUTCOME` v3, `ACCOUNT_SNAPSHOT`, `MT5_BROKER_SNAPSHOT`, `NEWS_CONTEXT` v1, `EXIT_CLASSIFICATION` v3 (evidence sources `ENGINE_FORCED`/`BROKER_DEAL_REASON`/`…/SL_GEOMETRY`/`TP_GEOMETRY`/`FALLBACK_HEURISTIC`; reason 4=SL never TP; UNKNOWN stays UNKNOWN), `MODEL_GOVERNANCE` v2, `MODEL_LOAD_GATE`/`SHADOW_PARITY`/`PROMOTION_STATE_MACHINE`, `LIQUIDITY_RUNTIME` v2 / `LIQUIDITY_API` v1, `ACCOUNTING_SNAPSHOT`, `DB_MIGRATION`, `INCIDENT_RESPONSE`, `VERSION_CONSISTENCY`, `FORENSIC_HEALTH`. Respect §26: dimension change is never a minor refactor.

## 6. Non-Negotiable Invariants

Reference: `agents/runtime_invariants.md` and `agents/contracts.md`. Every change to shared runtime code must consider these (new invariant requires `agents/decisions/DEC-XXXX`).

| ID | Invariant |
|---|---|
| INV-001 | Tick hot path has **zero sync DB**. `LiveEngine._process_tick_pipeline` never blocks; writes queued; news cache-only; rule TTL 5s; experience score TTL 30s + ≤1/s |
| INV-002 | Learning/research/strategies/experience **never hold adapter or risk handle** — no order authority |
| INV-003 | `RiskEngine` is authoritative for boundaries (`calculate_dynamic_volume`, free margin, tier caps) |
| INV-004 | `OrderManager` is authoritative for execution; enforces `HARD_MAX_LOTS=10.0` + `MAX_TOTAL_EXPOSURE=1`; 30s pending re-quote lock + 1.0×ATR drift |
| INV-005/006 | No duplicate experiences/outcomes from split fills or duplicate broker events (idempotent recovery) |
| INV-007 | Historical experience/outcome rows **immutable**; corrections via evidence→reconstruction→derived→provenance |
| INV-008 | **No lookahead.** Broker minute history REPLACE+ALIGN not append; labeling embargo+purge (Lopez de Prado); liquidity strictly causal (see 5.2 + `LIQUIDITY_60D`) |
| INV-009 | Feature ordering is schema-controlled (§26) |
| INV-010 | Telegram is read-only; creds only via `settings_service.set_telegram()` |
| INV-011 | Broker truth wins over stale local state when reconciling exposure |
| INV-012/013 | Exit `exit_mechanism` carries `exit_reason_source`+confidence; UNKNOWN never promoted; MT5 DEAL_REASON 4=SL; timeline final `POSITION_EXITED` |
| INV-013/014/015 | Model loadability requires 10-gate load gate; shadow **never mutates** execution; promotion only `READY_FOR_REVIEW→APPROVED→CHAMPION` with operator token |
| INV-019 | Liquidity features causally confirmed (`SWING_CONFIRM_BARS=5`), HTF only completed buckets, sweep needs penetration+rejection in later bar, `[-3,3]` clip, deterministic, pure |
| + | Hygiene `AUDIT_ONLY` default; update blocked while LIVE; incident/lineage/recovery governed; settings DB authoritative; see file for full list |

## 7. High-Risk Subsystems

| Subsystem | Why risky | What to inspect before changing |
|---|---|---|
| `application/live_engine.py` `_process_tick_pipeline` | Async hot path; any sync I/O, training, or DB query stalls ticks | Verify no blocking call, no `await` inside tight tick loop that requires DB, respect INV-001 |
| `execution/order_manager.py` | 60-scenario router, 11 position states, BE/profit-giveback, exposure clamps | Run scenario tests; confirm `HARD_MAX_LOTS`/`MAX_TOTAL_EXPOSURE`/lock/drift unchanged |
| `risk/risk_engine.py` | Dynamic lot sizing, margin, tier caps, kill switch | Verify `calculate_dynamic_volume` → `HARD_MAX_LOTS` clamp still applied by OrderManager |
| `features/*` + `schema_contract.py` + `inference_validator.py` | Dimension/hash/bounds contract; scaler coupling | Compare against `schema_contract.70D`; run `tests/unit/test_*70*`, `test_inference*`, `test_liquidity*`; hash-sensitive changes need retrained artifact |
| `accounting/*` + `database/engine.py`, `migrate_engine.py` | Ledger + market calendar + SQLite WAL/migrations | Immutable history; migration additive; verify `audit.db` in container volume |
| `training/walk_forward_trainer.py` + `model_generation/` + `model_lifecycle/` | Labeling, dataset, training, manifests | Purge/embargo, deterministic; check `artifacts/models/scalp/XAUUSD/v1.0.0/` + `models/production.manifest.json` |
| `configuration/runtime_config.py` + `settings/service.py` | Versioned snapshot, hot-reload, Telegram secret store | Consumers must call `get_snapshot()`; never read `AppConfig` directly for live-hot-path params |
| `web/server.py` | Large (361k), REST/SSE/WS, `serialize_enums()` | Enum serialization, background tasks, API contract in `agents/contracts.md` |

## 8. Safe Change Rules

- Domain models frozen — use `.model_copy(update={...})`.
- `IMT5Port` signature change → update `DirectMT5Adapter` + `RemoteMT5GatewayAdapter` + `PaperAdapter`.
- Never hard-code `50`/`60`/`70`; read `FEATURE_SCHEMAS` or `schema_contract.DIMENSION`.
- Never reorder feature names; never invent neutral values; `FEATURE_DISABLED` vs `FEATURE_UNAVAILABLE` is a contract distinction.
- Never block the event loop (`_process_tick_pipeline`). No sync DB, no training, no network on tick.
- Never write Telegram tokens to `live.yaml`; only `settings_service.set_telegram()`.
- Never rewrite historical experience/outcome/ledger rows to improve metrics.
- Never bypass `HARD_MAX_LOTS`/`MAX_TOTAL_EXPOSURE`/margin clamps; never add a second concurrency path around OrderManager.
- Never auto-promote shadow→champion; never let shadow import order manager/risk/adapter.
- DB migrations append-only; never delete financial truth, provenance, or research evidence; hygiene `AUDIT_ONLY` unless operator opts in.
- Config: `AppConfig` is bootstrap/import/export only; live reads go through `RuntimeConfigStore`.

## 9. Validation Requirements

Run the CI-mirror gate before any push (mirrors `.github/workflows/ci.yml`):

```powershell
./beforePush.ps1            # full gate + self-test + forensic deploy gate + push prompt
./beforePush.ps1 -SkipPush  # check-only
./beforePush.ps1 -Fix -SkipPush
```

Gates (async): **ruff lint** `ruff check`, **ruff format** check, **mypy** `src --junit-xml`, **pytest** critical suite (`tests/critical_suite.txt` via `tests/helpers/run_critical.py`, `pytest -n <RAM-aware> --dist loadgroup --cov=src` + junit/coverage/html), then `ci-results/` summary + deploy gate.

Targeted suites for risky areas:
- Features/70D: `pytest tests/unit -k \"70 or schema or inference or liquidity\"`
- Risk: add `-k risk`
- Execution: add `-k order or execution`
- Accounting/ledger: add `-k accounting or pnl`
- Web: add `-k web or api`
- Governance/shadow: `tests/unit/test_model_governance*`, `shadow70`, `drift`

Additional: `scripts/ci/scan_secrets.py` (never dummy keys in source), OSV scan, lockfile diff, JS tests, Docker `ci-results` artifacts (`manifest` + `SHA256SUMS`). Mypy/Ruff exclude `scratch/`, `.venv`, `release`, `artifacts`.

## 10. Agent Workflow

1. Read **contract gates before coding**: `agents/multi-agent-git-contract.md` then `contracts.md` → `runtime_invariants.md` → `change_control.md` → `taskboard.md` → `repository_state.md` → `locks.yaml`; inspect `git status/log` and preserve unrelated WIP.
2. **Design before code.** For feature-dim/model/risk/execution/accounting/persistence/API changes, cite the canonical contract (§5–§6) and the verification command (§9) in the plan.
3. **Implement + test + commit per coherent step** (`<AGENT>:<task>` commits). Re-`git add` before commit (parallel agents may `restore --staged`). Verify `git log --all -- <path>` — parallels may absorb your tree; do not re-do absorbed work.
4. Before completion: update registries (`contracts.md` / `runtime_invariants.md` additive), `taskboard.md`, `repository_state.md`; add regression tests; create handoff; report exact verification state and unresolved risks. No auto-push; no trading logic/model/runtime/test change to make docs pass.
5. Branch `main`-based workflow; fork-based PRs via contract; keep branch names short (Windows length cap); patches via `python write_bytes + py_compile` not `patch` tool (CRLF).

## 11. What Not to Do (negative scope)

No directory-tree dump, no per-method file inventory, no history/P0-P3 recommendations, no generic advice discoverable via `ls`, no speculative dimensions, no marketing, no duplicated research/intelligence tables, no stale 50D-only narrative. Keep this document short; details live in the modules and in `agents/contracts.md`/`runtime_invariants.md`/`bugs.md`.

## 12. Quick Checks for Common Tasks

| Task | Inspect first | Must not break |
|---|---|---|
| ML/inference | `features/schema_contract.py`, `inference_validator.py`, `models/scalp_net.py`, scaler file | Dim/hash/bounds, scaler dim match, `[-3,3]` |
| Risk | `risk/risk_engine.py`, `execution/order_manager.py` clamp | `calculate_dynamic_volume` → clamp pipeline, margin, tier |
| Execution | `execution/order_manager.py`, `domain/enums.py` `PositionState` | 11 states, BE lock, giveback, 30s lock, ATR drift |
| Accounting | `accounting/core.py`, `aggregation.py`, `adapters/database/audit_repository.py` | Immutable history, market calendar, WAL |
| Persistence | `database/engine.py`, `manifest.py`, `migrate_engine.py` | Additive migrations, volume durability |
| API/runtime | `web/server.py`, `configuration/runtime_config.py`, `src/nexus_scalp/settings/service.py` | `serialize_enums`, `get_snapshot()`, settings DB |
| Config change | `configuration/config.py` + `runtime_config.py` scope table | LIVE_IMMEDIATE vs NEXT_DECISION |
| Docker/deploy | `docker-compose.yml`, `Dockerfile`, `docker/healthcheck.sh` | PAPER default, single service, healthcheck, volumes |

## 13. Version

NSE v9.0 (`pyproject.toml`). Skill generation: 2026-08-23. Prior 3373-line skill backed up to `agents/skill.md.bak_20260823` (git-untracked; add if retention desired). Treat this file as the current authoritative master skill; code still wins conflicts.
