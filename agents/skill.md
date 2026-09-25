# Nexus Scalp Engine (NSE) — Master Skill

> **Audience:** AI coding agents. **Purpose:** High-signal source of truth for safe implementation.
> **Rule:** Repository code wins over this document. If a claim cannot be verified, mark uncertain — never invent.
> Historical bug forensics → `agents/bugs.md`. Forensic recovery & provenance index → `docs/forensics/README.md`. Multi-agent workflow → `agents/multi-agent-git-contract.md`.
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
| Web/API | `web/` (`server.py`, `factory_routes.py`, `db_console.py`, `debug_snapshot.py`, `go_api_bootstrap.py`), `go-api/` (`cmd/nexus-api`, `internal/api`, `internal/security/auth`, `internal/infrastructure/python`), `Web/` frontend | **Two planes:** Go (`nexus-api`) is the API entrypoint — routing, auth, envelope, correlation; Python (FastAPI) is the fact authority behind it — ML/MT5/DB facts, error classification, sole token minter. SSE `/api/ticks/stream`, WebSocket `/web`, `serialize_enums()` for JSON | Background tasks in `app.state.background_tasks`; see §5.4 for the binding Go-plane rules |
| Configuration | `configuration/` (`config.py`, `runtime_config.py`), `settings/` (`service.py`, `secret_store.py`) | `AppConfig` is bootstrap/import/export; `RuntimeConfiguration` snapshot via `RuntimeConfigStore.get_snapshot()` is authoritative live state (versioned, hot-reload) | Telegram creds only via `settings_service.set_telegram()` |
| Observability | `observability/` (`logging.py`, `telegram_notifier.py`, `ci_telegram_reporter.py`, `ci_ai_triage.py`), `hygiene/`, `incidents/`, `forensics/` | Structured logging, hygiene worker (AUDIT_ONLY default), incidents diagnostic-only, forensics health; CI Telegram feed + BUG-300 AI failure triage (reachability-gated, rules fallback, advisory-only) | Incidents never mutate trading/risk/models/DB; AI triage never gates CI verdicts |
| Model lifecycle / Governance | `model_lifecycle/`, `governance/`, `shadow/`, `mslie/`, `experience/` | Candidate training, 10-gate load gate, shadow comparison, promotion `READY_FOR_REVIEW→APPROVED→CHAMPION` | Auto-promotion forbidden; shadow never mutates execution |

Ports/adapters isolates MT5 IPC from domain. `LiveEngine` orchestrates tick → features → regime → signals → risk → execution → accounting → web.

## 3. Critical Entrypoints

| Entrypoint | Path | Notes |
|---|---|---|
| **Primary launcher** | `NexusTradingForexBot.py` | Bootstraps `src/` onto `sys.path`, binds `DirectMT5Adapter` or `RemoteMT5GatewayAdapter`, launches `LiveEngine` + Uvicorn web. Also invoked via `main.py` redirect. |
| CLI | `src/nexus_scalp/cli/main.py` (`nse` / `nexus` scripts, Typer) | `start`, `setup`, `db`, `forensic`, `update`, `model-artifacts`; `--mode paper` persisted to settings DB; `--doctor` diagnostics |
| Web server | `src/nexus_scalp/web/server.py` `create_app()` | FastAPI `app` factory; canonical port `9090` in container, `8080→find_available_port` in launcher. Behind the Go plane it is the **fact authority**, not the public surface |
| Go API server | `go-api/cmd/nexus-api` (binary `nexus-api`) | The product's API entrypoint. Default `-addr` `:8087` (`NSE_GO_ADDR`); `-python-origin` (`NSE_PYTHON_ORIGIN`) = Python origin, empty = no engine attached and endpoints answer `DEPENDENCY_UNAVAILABLE` rather than fabricating state; `-log-level` (`NSE_GO_LOG_LEVEL`, default `info`). On boot it serves 466 operations; every non-public path enforces WEB-AUTH-P0 |
| Go API bootstrap | `src/nexus_scalp/web/go_api_bootstrap.py` — `boot_go_api()`, `GoApiSupervisor` | Called from `cli/engine_boot.py` after Python's origin is known, before uvicorn serves. Builds (content-hash cached) + spawns the Go child, polls `/health` (200 **or 503** = plane ready), tears it down at shutdown. Returns `None` on any failure → Python serves the whole API alone; the boot still succeeds. In a packaged release the binary is shipped prebuilt |
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

### 5.4 Go API plane — routing authority (BINDING RULES)

Merged as `930e6912` (PR #461). The Go API server is the product's API entrypoint — every HTTP request reaches Go — and it proxies the Python runtime for every fact it does not own. Go is an **acceleration plane**, never a fact source.

| Rule | Why |
|---|---|
| **ALL API traffic routes through Go.** No new Python route may be served directly; it must be added to the Go route table. | One entrypoint for auth, envelope and correlation. A Python-only route is unreachable through the product's port. |
| **The route table is GENERATED, not handwritten.** `go-api/internal/api/routes/table_gen.go` (466 operations) is emitted from the live FastAPI dump (`routes_ground_truth.json`, `create_app()` + `create_v1_app()`); regenerate with `scratch_gen_table.py`. | Drift — a path Python serves that Go does not, or a dead Go route — is a visible mismatch, not a silent 404. `scratch_drift_check.py` diffs the two. |
| **Declaration order is the match contract.** `internal/api/router/router.go` is a first-match-in-declaration-order regex router. Go 1.22 `ServeMux` is insufficient: the surface has ambiguous literal-vs-`{id}` pairs (`POST /api/news/{article_id}/restore` vs `POST /api/news/analyze/{article_id}`) that ServeMux panics on instead of resolving. | The React client depends on Starlette's "first registered wins" priority. 404-vs-405 follows for free: path matches but method does not → 405. |
| **Envelope split, never unified.** `/api/v1/*` → `{data, meta}` (`request_id` before `generated_at` in `meta` — insertion order, not a Go map); legacy `/api/*` → raw JSON. | Both envelopes are parsed by name/shape by different React code paths (`getV1` vs `getLegacy`). |
| **A legacy `{"detail":...}` body is an ANSWER, not a boundary failure.** `LegacyResponse` is replayed byte-for-byte with Python's status; only a valid `/api/v1` error envelope counts as a contract response (`BoundaryError`). | Reclassifying a FastAPI 404/422/403 as `DEPENDENCY_UNAVAILABLE` fabricates an outage Python never reported. |
| **Auth is fail-closed and ported, but Go never mints a token.** `internal/security/auth/middleware.go` resolves `NSE_WEB_AUTH_TOKEN` env > repo-root `.env` (where `auth_boot.publish()` writes it) > `ErrTokenUnresolvable` → 500 `AUTH_CONFIG_ERROR`. Constant-time compare, length checked first; `PUBLIC_PATHS` allowlist; traversal (`..`, `\`) never public. The middleware calls `IsPublicPathMethod(path, method)`, not the path-only `IsPublicPath`: the static-shell rule below is GET/HEAD-only, so POST/PUT/DELETE/PATCH can never widen the surface. | Python owns the DPAPI secret store. A second Go-minted value would enforce a different token and 401 every request. |
| **A dotless unmatched path is a public SPA shell — CONTRACT frozen decision #7.** Mirrors `auth.py::_is_public_static_shell`: a path whose LAST segment contains no `.` and starts with no deny prefix (`/api`, `/ws`, `/web`, `/alt`) is public, so a tokenless first navigation to `/trading` renders the app shell instead of a 500. A dot marks a file/asset and needs an explicit allowlist entry — `/trading.js` is NOT public — and `/altx` / `/alternative-api` stay private because `/alt` is a deny prefix, not a string prefix. | Without it every SPA deep link 500'd once Go was the entrypoint: Go hit fail-closed where Python served the shell. This was a real production bug (fixed `0db38646`, PR #472), found by probing `/trading` against a built binary, not by reading either auth file alone. Port the whole decision function, not the constant tables. |
| **Failure isolation is the contract.** Missing/broken toolchain, build failure, bound port, or readiness timeout → the bootstrap logs a warning, returns `None`, and the FastAPI app serves the full API on its own port. | Go is an acceleration plane. The product must still boot without it — this is the only fallback path in the subsystem. |
| **Startup is invisible.** `go_api_bootstrap.boot_go_api()` builds + supervises the child; the user only opens the app. | Operator never runs a second command or knows Go exists. |

Boundary client: `internal/infrastructure/python/client.go` — localhost HTTP, never a per-request subprocess; 30s timeout (a slow-but-successful answer is not a circuit failure), circuit breaker after 3 consecutive failures (5s cooldown); forwards `Authorization` + `X-Request-ID` on every hop; `DoRaw` streams byte-for-byte when the caller must preserve Python's exact output.

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

Gates (async): **ruff lint** `ruff check`, **ruff format** check, **mypy** `src --junit-xml`, **pytest** critical suite (`tests/critical_suite.txt` manifest consumed directly by pytest/beforePush — no wrapper script exists, `pytest -n <RAM-aware> --dist loadgroup --cov=src` + junit/coverage/html), then `ci-results/` summary + deploy gate.

Targeted suites for risky areas:
- Features/70D: `pytest tests/unit -k \"70 or schema or inference or liquidity\"`
- Risk: add `-k risk`
- Execution: add `-k order or execution`
- Accounting/ledger: add `-k accounting or pnl`
- Web: add `-k web or api`
- Governance/shadow: `tests/unit/test_model_governance*`, `shadow70`, `drift`

Additional: `scripts/ci/scan_secrets.py` (never dummy keys in source), OSV scan, lockfile diff, JS tests, Docker `ci-results` artifacts (`manifest` + `SHA256SUMS`). Mypy/Ruff exclude `scratch/`, `.venv`, `release`, `artifacts`.

### 9.1 Go API plane — test strategy

Three layers, each proving something different. All are required after any `go-api/` or route-table change.

| Layer | Command | What it proves |
|---|---|---|
| **Go unit tests** | `cd go-api && go test ./...` | Router declaration-order priority + 405-vs-404 derivation (`internal/api/router`), pagination `int_parsing` vs `greater_than_equal`/`less_than_equal` distinction (`internal/api/handlers/read.go`), both envelope shapes + meta key order (`internal/api/respond`), fail-closed auth + token precedence + traversal-never-public + the CONTRACT #7 dotless-shell rule and its GET/HEAD-only restriction (`internal/security/auth`, `TestDotlessShellIsPublicForGET`), generated-table shape (`internal/api/routes`). |
| **Python bootstrap tests** | `pytest tests/unit/test_go_api_bootstrap.py` | The failure-isolation contract with **no toolchain and no network**: absent `go` binary → WARN + Python-only boot + exit 0; build failure → WARN + compiler-output tail; port bound → next free port; child never ready → kill + fallback. Written by a parallel lane; path is the contract. |
| **Live A/B parity harness** | `python scratch_parity_full.py` (evidence: `api/migration/parity/full_surface_parity.json`) | Paired per-request py→go probing of the whole route table. Byte comparison after masking volatile live readings (timestamps, `*_ms` timings, hashes, `request_id`, `state_version`). Mutations probed LAST and py-then-go back-to-back, because the two servers share ONE Python backend — a state change between calls would read as a false divergence. SSE/WebSocket routes are skipped (streaming, not request/response). Current evidence: **443/444 PASS**. |

**Known gap (honest, not skipped):** `go test -race` cannot run on the migration host — `CGO_ENABLED=1` needs a C compiler and none is installed. Recorded as a known limitation; the race detector is not part of the verification claim on this branch.

## 10. Agent Workflow

1. Read **contract gates before coding**: `agents/multi-agent-git-contract.md` then `contracts.md` → `runtime_invariants.md` → `change_control.md` → `taskboard.md` → `repository_state.md` → `locks.yaml`; inspect `git status/log` and preserve unrelated WIP.
2. **Design before code.** For feature-dim/model/risk/execution/accounting/persistence/API changes, cite the canonical contract (§5–§6) and the verification command (§9) in the plan.
3. **Implement + test + commit per coherent step** (`<AGENT>:<task>` commits). Re-`git add` before commit (parallel agents may `restore --staged`). Verify `git log --all -- <path>` — parallels may absorb your tree; do not re-do absorbed work.
4. Before completion: update registries (`contracts.md` / `runtime_invariants.md` additive), `taskboard.md`, `repository_state.md`; add regression tests; create handoff; report exact verification state and unresolved risks. No auto-push; no trading logic/model/runtime/test change to make docs pass.
5. Task-branch workflow (see `agents/git_governance.md` §3): `git fetch origin` then `git switch -c agent/<kind>/<topic> origin/main`; never develop on or branch from local `main` (it is a tracking mirror). Fork-based PRs via contract; keep branch names short (Windows length cap); patches via `python write_bytes + py_compile` not `patch` tool (CRLF).

## 11. What Not to Do (negative scope)

No directory-tree dump, no per-method file inventory, no history/P0-P3 recommendations, no generic advice discoverable via `ls`, no speculative dimensions, no marketing, no duplicated research/intelligence tables, no stale 50D-only narrative. Keep this document short; details live in the modules and in `agents/contracts.md`/`runtime_invariants.md`/`bugs.md`.

Go plane negative scope:

- **Do not add a Python route without adding it to the Go route table** — it will be unreachable through the product's port. Regenerate the table from the resolved route dump (§12), never hand-edit `table_gen.go`.
- **Do not mint an auth token in Go.** Python owns the DPAPI secret store; a second Go-generated value enforces a different token and 401s every client. Go resolves, never creates.
- **Do not unify the two envelopes.** `/api/v1` `{data,meta}` and legacy raw JSON/`{"detail":...}` are separate client contracts.
- **Do not reclassify a legacy `{"detail":...}` answer as a boundary failure** — it upgrades every FastAPI 4xx to a fabricated 503.
- **Do not remove the Python-only fallback.** A Go failure must degrade one plane, not kill the boot.

## 12. Quick Checks for Common Tasks

| Task | Inspect first | Must not break |
|---|---|---|
| ML/inference | `features/schema_contract.py`, `inference_validator.py`, `models/scalp_net.py`, scaler file | Dim/hash/bounds, scaler dim match, `[-3,3]` |
| Risk | `risk/risk_engine.py`, `execution/order_manager.py` clamp | `calculate_dynamic_volume` → clamp pipeline, margin, tier |
| Execution | `execution/order_manager.py`, `domain/enums.py` `PositionState` | 11 states, BE lock, giveback, 30s lock, ATR drift |
| Accounting | `accounting/core.py`, `aggregation.py`, `adapters/database/audit_repository.py` | Immutable history, market calendar, WAL |
| Persistence | `database/engine.py`, `manifest.py`, `migrate_engine.py` | Additive migrations, volume durability |
| API/runtime | `web/server.py`, `configuration/runtime_config.py`, `src/nexus_scalp/settings/service.py`, `go-api/internal/api/routes/table_gen.go` | `serialize_enums`, `get_snapshot()`, settings DB, Go/Python route parity |
| Config change | `configuration/config.py` + `runtime_config.py` scope table | LIVE_IMMEDIATE vs NEXT_DECISION |
| Docker/deploy | `docker-compose.yml`, `Dockerfile`, `docker/healthcheck.sh` | PAPER default, single service, healthcheck, volumes |
| Go route table | `scratch_ground_truth.py` → `scratch_gen_table.py` → `scratch_drift_check.py` | Table matches Python's resolved dump; no drift either direction |
| Go plane parity | `go-api/` + `python scratch_parity_full.py` | 443/444 PASS, both envelopes, fail-closed auth |

## 13. Version

NSE v9.0 (`pyproject.toml`). Skill generation: 2026-08-23. Go API plane merged as `930e6912` (PR #461), documented here 2026-09-25. Prior 3373-line skill backed up to `agents/skill.md.bak_20260823` (git-untracked; add if retention desired). Treat this file as the current authoritative master skill; code still wins conflicts.
