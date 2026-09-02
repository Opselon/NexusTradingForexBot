# agents/APISkill.md — NEXUS API AGENT SKILL (CANONICAL OPERATIONAL CONTRACT)

> **Machine-oriented contract for Hermes/Nexus agents** working with the Nexus
> Scalp Engine APIs. Read this file before touching any API: it tells you WHAT
> exists, WHICH endpoint answers WHICH question, HOW to call it, WHAT it
> depends on, WHAT can fail, HOW to test it, and WHAT MUST NEVER be done.
>
> - Spec of record (design): `docs/api/API_PLATFORM_V1.md`
> - Developer reference (usage): `docs/api/API_REFERENCE.md`
> - **This file**: agent-facing operational contract + drift-protected registry
> - Drift validator: `scripts/dev/api_skill_drift_check.py` (exit 0 = in sync)
>
> Maintainer: Hermes-Main (API Platform owner, TASK-API-PLATFORM, CHG-0043).
> Companion file convention: see `agents/skill.md` (architecture map).

---

## 0. STATE AT A GLANCE (verify before trusting)

| fact | value | how to re-verify |
|---|---|---|
| canonical tree | `src/nexus_scalp/web/api_v1/` | `ls src/nexus_scalp/web/api_v1/` |
| mount point | ONE call: `web/api_v1_wiring.register_api_v1(app)` at end of `web/server.create_app()` | `grep -n "register_api_v1" src/nexus_scalp/web/server.py` |
| live operation count | **68** (63 GET + 5 POST) | `.venv/Scripts/python.exe scripts/dev/api_contract_check.py` |
| legacy dashboard routes | **280** decorators, still served, additive-untouched | grep `@(app\|router).(get\|post...)` over `web/*.py` excluding `api_v1*` |
| superseded tree | `src/nexus_scalp/api/v1` — **REMOVED** (was 45 routes, commit `e51a910` removed it) | must NOT exist; do not resurrect |
| envelope | `{data, meta}` / `{error{...}}` | §4 |
| auth | none (localhost bind; no broker mutations by design) | §7 |
| platform owner registry | TASK-API-PLATFORM → VERIFIED (CHG-0043) | `agents/taskboard.md` |

---

## 1. API LANDSCAPE: FOUR STATUS CLASSES

Every API path in this repository is exactly ONE of these. Never blur them.

| class | meaning | examples |
|---|---|---|
| **CURRENT / CANONICAL** | `/api/v1/*` — the platform of record. Build here. | all 68 operations in §4/§5 |
| **LEGACY / HISTORICAL** | the ~280 dashboard routes (`/api/status`, `/api/live/state`, `/api/models/*`, `/api/research/*`, `/api/diagnostics/*`, `/api/db/console/*`, …). Still served for the Web UI. Do NOT build new client work on them; consume via `/api/v1` equivalents where they exist. | §2 |
| **DEPRECATED / SUPERSEDED** | legacy paths whose truth now has a canonical v1 endpoint. Keep working (UI depends on them) but prefer v1. | `/api/status` → `/api/v1/system/status`; `/api/models/champion` → `/api/v1/model/champion` |
| **REMOVED** | trees deleted from the codebase. They do not exist. Any doc claiming otherwise is a defect. | `src/nexus_scalp/api/v1/*` (the first 45-route v1, superseded envelope) — removed in consolidation commit `e51a910` |

**Hard rule**: an agent must never resurrect `src/nexus_scalp/api/v1`, never
mount a second v1 tree, and never add routes outside `web/api_v1/` for new
platform capabilities. Extension workflow: §14.

---

## 2. LEGACY / HISTORICAL INVENTORY (verified from source, not memory)

Current decorator scan of `web/*.py` (excluding `api_v1*`): **280 routes** across
10 route modules + `server.py` static/JS file routes. Families (count = decorators):

| module | count | family / path pattern | status | canonical v1 replacement |
|---|---|---|---|---|
| `debug_research_routes.py` | 50 | `/api/debug/*`, `/api/research/*`, `/api/experience/*`, `/api/account/*` | LEGACY (UI-owned) | `/api/v1/research/*`, `/api/v1/decisions/*`, `/api/v1/audit/*` |
| `model_governance_routes.py` | 45 | `/api/models/*` (train/worker/shadow/governance/promotion/emergency) | LEGACY — operator+UI only | `/api/v1/model/*` (read-only truth); POST governance actions stay legacy |
| `diagnostics_state_routes.py` | 43 | `/api/status`, `/health`, `/api/live/*`, `/api/account/*`, `/api/engine/*`, `/api/config`, `/api/settings/*`, `/api/db/*`, `/api/rules/*` | LEGACY — UI control plane | `/api/v1/system/*`, `/api/v1/config/*`, `/api/v1/database/*` |
| `news_liquidity_mslie_routes.py` | 23 | `/api/news/*`, `/api/liquidity/*`, `/api/mslie/*` | LEGACY | none in v1 (news/liquidity domain not yet versioned) |
| `intelligence_routes.py` | 10 | `/api/intelligence/*` | LEGACY | none in v1 |
| `server.py` (inline) | 9 | `/api/ticks/stream` (SSE), `/api/chart`, `/api/mt5`, `/api/positions`, `/api/status` helpers, `/ws` | LEGACY | `/api/v1/market/*`, `/api/v1/positions` (polling; SSE remains UI-owned) |
| `command_center_integration.py` | 8 | `/api/command-center/*` | LEGACY | none in v1 |
| `operator_routes.py` | 6 | operator console routes | LEGACY | none in v1 |
| `factory_routes.py` | ~18 | `/api/factory/*`, `/loop/*`, `/generations/*`, `/llm-config/*`, `/analyze/*` | LEGACY | none in v1 |
| `replay_routes.py` | 5 | `/api/replay/*` | LEGACY | none in v1 (replay sessions are CHG-0043-adjacent, not yet versioned) |
| `db_console.py` | 10 | `/api/db/console/*` (SQL console, API keys) | LEGACY — read-only by contract, UI-owned | `/api/v1/database/*` (metadata only; v1 NEVER executes user SQL) |
| `dependency_routes.py` | 9 | `/api/dependency/*` | LEGACY | none in v1 |
| `news_intelligence_routes.py` | 7 | `/pro/*`, `/apikey/*`, `/vendor/*` | LEGACY | none in v1 |
| `server.py` static files | ~10 | `/app.js`, `/api_client.js`, `*.css` | static assets (not APIs) | n/a |

**Migration rule**: v1 duplicates legacy **read** truths only where a stable
contract was worth publishing; legacy **control** endpoints (engine toggle,
model training, promotion, SQL console) are deliberately NOT in v1 — they stay
operator-console/CLI owned. Do not search for them under `/api/v1`.

---

## 3. CURRENT CANONICAL API MAP (per-domain operational entries)

Format per operation: Purpose / Returns / Dependencies / Errors / Agent use /
CLI / Tests / Smoke. All GETs unless marked POST. All paths below are verified
against the live OpenAPI (`scripts/dev/api_skill_drift_check.py` enforces this).

### 3.1 SYSTEM — module `web/api_v1/system.py`

#### GET /api/v1/system/status
- Purpose: one-shot operational snapshot (health verdict + version + mode + freshness + check counts).
- Returns: `data{health_verdict, critical_failures[], version{product,version,commit,channel}, runtime{engine_attached,engine_running,mode,freshness_overall}, checks_count}`.
- Dependencies: HealthEngine (3s TTL cache + prewarm thread); engine attrs if attached.
- Errors: 503 `DEPENDENCY_UNAVAILABLE` (health engine itself failed).
- Agent use: FIRST call to answer "what is the engine doing right now?" — but read §11 truth rules: `health_verdict` is a layered verdict, not a trading guarantee.
- CLI: `nexus api status` (human rendering).
- Tests: `tests/unit/test_api_v1_contracts.py`, `tests/integration/test_api_v1_platform.py`.
- Smoke: `python scripts/dev/api_smoke.py --embedded|--live`.

#### GET /api/v1/system/health
- Purpose: full HealthEngine check list (verbatim per-layer entries).
- Returns: `data{verdict, checks[{category,verdict,...}], critical_failures[]}`.
- Dependencies: HealthEngine (cached 3s).
- Errors: 503 `DEPENDENCY_UNAVAILABLE`.
- Agent use: WHEN a status layer failed and you need the per-layer detail.
- CLI: `nexus api health`.

#### GET /api/v1/system/readiness
- Purpose: can the system accept work? Required layers (SYSTEM/RUNTIME/CONFIGURATION/DATABASE/MODEL/FEATURE_SCHEMA) must have no FAIL.
- Returns: `data{ready: bool, verdict, required_layers[], optional_layers[]}`.
- Errors: 503 `DEPENDENCY_UNAVAILABLE`.
- Agent use: gate any automation on `data.ready` — never on HTTP 200 alone.

#### GET /api/v1/system/version
- Purpose: build/revision identity.
- Returns: full `get_version_info()` dict (product, version, commit, channel, python, platform, mode…).
- Dependencies: build metadata (memoized).
- Errors: 503 `DEPENDENCY_UNAVAILABLE`.
- CLI: `nexus api version`.

#### GET /api/v1/system/runtime
- Purpose: runtime environment + mode + warmup + inference flag + freshness contract.
- Returns: `data{engine_attached, engine_running, warmup_state, inference_enabled, freshness, mode, effective_mode}`.
- Errors: truthful nulls when engine absent (HTTP 200, `engine_attached:false`).

#### GET /api/v1/system/capabilities
- Purpose: platform discovery — domains + endpoint counts + pagination model + read_only flag; built from the REAL mounted route table.
- Returns: `data{api_version, spec, read_only, domains{}, domain_count, endpoint_count, pagination{...}, generated_at}`.
- Agent use: always the first discovery step on an unfamiliar deployment.
- CLI: `nexus api capabilities`.

#### GET /api/v1/system/workers
- Purpose: worker lifecycle (accounting/incident/hygiene/model-lifecycle workers).
- Returns: `data{workers[{name,state,attached}], engine_attached}`; honest `NOT_ATTACHED` when absent.

#### POST /api/v1/system/refresh
- Purpose: safe refresh of runtime-observable state (re-read account/symbol snapshots, bump versioner).
- Body: none. Headers: optional `Idempotency-Key` (echoed in `meta.idempotency_key`).
- Returns: `data{refreshed{account{available}}, at}`.
- Dependencies: **ENGINE + adapter** → 503 `ENGINE_UNAVAILABLE` when absent.
- Side effects: NONE that reach a broker (snapshot reads only).

#### POST /api/v1/system/diagnostics/run
- Purpose: run the bounded observability selftest; caches result on app.state for `/diagnostics`.
- Returns: selftest result dict. Idempotency-Key echoed.

#### GET /api/v1/system/diagnostics
- Purpose: latest diagnostics = adapter `diagnostics_summary()` (or null) + last selftest + version.
- Returns: `data{mt5, last_selftest, version}`; never 503 — truthful nulls when no adapter.

### 3.2 RUNTIME — module `web/api_v1/runtime.py`

#### GET /api/v1/runtime/mode
- Purpose: configured mode + effective `_runtime_mode` + replay flag.
- Returns: `data{mode, effective_mode, engine_attached, replaying}`; HTTP 200 with nulls when engine absent.
- CLI: `nexus api mode`.

#### GET /api/v1/runtime/freshness
- Purpose: the live-freshness contract (market/inference stages + overall) from `engine.compute_live_freshness()`.
- Returns: `data{freshness, available}`.
- Errors: 503 `ENGINE_UNAVAILABLE`.

#### POST /api/v1/runtime/mode/validate
- Purpose: pure transition validation of a PROPOSED mode — **never applies**.
- Body: `{"mode": "PAPER"}` (ModeProposal). Returns `data{valid, current_mode, proposed_mode, errors[], warnings[]}`; invalid → HTTP 422 with the same body (valid:false).
- Agent use: pre-flight any mode reasoning; `LIVE` proposals always carry a warning that v1 has no mutation path.

#### POST /api/v1/runtime/mode/preview
- Purpose: impact matrix of a proposed mode (which subsystems it would touch).
- Body: ModeProposal. Returns `data{validation, impact{touches[],matrix{...}}}`; 422 on invalid mode.

### 3.3 MARKET — module `web/api_v1/market.py` (ALL → 503 `ENGINE_UNAVAILABLE` without engine)

#### GET /api/v1/market/snapshot — `data{symbol, account, symbol_spec}` from real adapter snapshots (`?symbol=` override).
#### GET /api/v1/market/quote — `data` = broker tick (bid/ask/spread/freshness/stale) from `get_broker_tick`.
#### GET /api/v1/market/bars — `?limit≤500` completed M-bars from the engine aggregator (engine-local scope, stated in response).
#### GET /api/v1/market/regime — `data{regime, evidence}` from `_last_regime_state`; `None` before first inference (truthful, not 404).
#### GET /api/v1/market/symbols — configured symbol(s) + spec block.

### 3.4 SIGNALS — module `web/api_v1/signals.py` (audit_signals DB)

#### GET /api/v1/signals/latest — most recent audit_signals row (ORDER BY id DESC) + sanitized parsed payload; 404 truthful when table empty.
#### GET /api/v1/signals/history — paginated; filters `symbol`, `action` (upper-cased), `hours_back` (≤720); 422 pagination errors.

### 3.5 DECISIONS — module `web/api_v1/decisions.py` (audit_signals; decision_id = request_id)

#### GET /api/v1/decisions — paginated history; filters `symbol/action/execution_mode/decision_stage/hours_back`.
#### GET /api/v1/decisions/latest — full summary (`decision_id`, action, confidence, gates-adjacent fields, risk_checks).
#### GET /api/v1/decisions/stats — `?hours_back` window; `data{window_hours,total,by_action{},by_stage{}}`.
#### GET /api/v1/decisions/no-trade — NO_TRADE analytics: `data{total,reasons[],latest}`.
#### GET /api/v1/decisions/no-trade/reasons — `data{total, reasons{reason_code:count}}`.
#### GET /api/v1/decisions/{decision_id} — detail by request_id; 404 truthful.
#### GET /api/v1/decisions/{decision_id}/evidence — raw payload (sanitized of secret-shaped keys).
#### GET /api/v1/decisions/{decision_id}/gates — ordered gate trace: `decision_stage`, `blocked_by`, `reason_code`, `guardian_status`, `risk_allowed` each with `{value,passed:bool}`.
#### GET /api/v1/decisions/{decision_id}/explanation — human-readable sentence composed ONLY from real fields.

### 3.6 POSITIONS / EXECUTION / MODEL / FEATURES — module `web/api_v1/positions.py`

#### GET /api/v1/positions — real `adapter.get_all_positions()` snapshots (MT5 field contract); 503 `ENGINE_UNAVAILABLE` without engine.
#### GET /api/v1/positions/{ticket} — one open position; 404 truthful if ticket not open.
#### GET /api/v1/positions/history/{ticket} — ledger row + broker deals for a historical ticket (DB-backed; works without engine).
#### GET /api/v1/execution/status — `data{adapter_present, connection_state, connected, engine_running, mode}`; 503 without engine.
#### GET /api/v1/execution/history — paginated `audit_executions` (order events), filters `status,symbol`.
#### GET /api/v1/model/status — `data{bundle_loaded, inference_enabled, warmup_state, runtime_mode}`; 503 without engine.
#### GET /api/v1/model/identity — bundle manifest identity + `feature_schema_hash`; `{available:false}` truthful when no bundle.
#### GET /api/v1/model/contracts — `expected_schema_ids()` + `compatible_model_schemas()` matrix.
#### GET /api/v1/model/champion — champion identity from the SERVING bundle (authoritative for "what is actually serving"); not the governance registry.
#### GET /api/v1/features/status — warmup + last-vector availability + missing features from last proposal.
#### GET /api/v1/features/contract — canonical 70D SSoT: SCHEMA_ID, feature_count, schema hash, family groups, registry_canonical.
#### GET /api/v1/features/groups — family → names/counts.
#### GET /api/v1/features/current — last computed vector (dimension + named dict or honest absence).

### 3.7 RISK — module `web/api_v1/risk.py`

#### GET /api/v1/risk/status — last proposal `risk_checks` (sanitized) + risk config block; 503 without engine.
#### GET /api/v1/risk/summary — open exposure by symbol (volume/profit) + account margin metrics from the adapter; `exposure.available:false` truthful.

### 3.8 RESEARCH — module `web/api_v1/research.py` (research store/registry over audit DB)

#### GET /api/v1/research/status — health + registry summaries; 503 `DEPENDENCY_UNAVAILABLE` if stores unreadable.
#### GET /api/v1/research/strategies — paginated registry entries; `?lifecycle=`.
#### GET /api/v1/research/strategies/{strategy_id} — detail + invariant_check; 404 truthful.
#### GET /api/v1/research/runs — paginated run inventory; `?strategy_id=`.
#### GET /api/v1/research/datasets — distinct dataset_ids + run counts (real rows only).

### 3.9 SHADOW — module `web/api_v1/shadow.py` (ShadowStore + Shadow70Store)

#### GET /api/v1/shadow/status — both stores' `summary()`; per-block failure isolation (`null` block ≠ fake zero).
#### GET /api/v1/shadow/runs — paginated PHASE-11 run inventory.
#### GET /api/v1/shadow/runs/{run_id} — run + comparison + promotion; 404 truthful.
#### GET /api/v1/shadow/decisions — paginated decision records; `?run_id=`.
#### GET /api/v1/shadow/70d — 70D observer: summary + disagreement counts (valid-only) + drift alerts + feature health.

### 3.10 OBSERVABILITY / AUDIT / INCIDENTS / DATABASE / CONFIG — module `web/api_v1/incidents.py`

#### GET /api/v1/observability/events — paginated audit_events tail; `?event_type=`.
#### GET /api/v1/observability/metrics — real process counters (threads; POSIX rusage; `UNAVAILABLE` on Windows — never fabricated) + SSE diag + engine peak equity.
#### GET /api/v1/audit/events — paginated audit_ledger tail; `?status=`.
#### GET /api/v1/incidents — paginated incident inventory; filters `status/severity/category/component`.
#### GET /api/v1/incidents/stats — counts + by-component + recurring fingerprints.
#### GET /api/v1/incidents/{incident_id} — full Incident.as_dict(); 404 truthful.
#### GET /api/v1/incidents/{incident_id}/timeline — timeline events; 404 truthful.
#### GET /api/v1/database/status — O(1) metadata (filename, size, table_count); `integrity_endpoint` pointer. Never per-call PRAGMA.
#### GET /api/v1/database/integrity — `quick_check` + bounded row counts (explicit heavier route).
#### GET /api/v1/database/tables — table names only (no row dumps ever).
#### GET /api/v1/config — full engine config **sanitized** (secret-shaped keys → `***`); 503 without engine.
#### GET /api/v1/config/schema — AppConfig section/field inventory (pydantic-derived).
#### POST /api/v1/config/validate — validate a partial config dict against real pydantic models; **no apply** (422 with field errors on invalid; `applied:false` always).

---

## 4. MACHINE-READABLE REGISTRY (drift-protected)

Every current operation. Columns: METHOD | PATH | DOMAIN | MODULE | READ/WRITE | ENGINE | DB | primary errors | tests | CLI.
`E` = requires engine (503 otherwise) · `E-soft` = degrades truthfully without engine · `S` = static/config/schema-backed.

| METHOD | PATH | DOMAIN | MODULE | READ/WRITE | ENGINE | DB | ERRORS | TESTS | CLI |
|---|---|---|---|---|---|---|---|---|---|
| GET | /api/v1/system/status | system | system.py | READ_ONLY | E-soft | S | 503 DEP | contracts+integration | `nexus api status` |
| GET | /api/v1/system/health | system | system.py | READ_ONLY | E-soft | S | 503 DEP | contracts+integration | `nexus api health` |
| GET | /api/v1/system/readiness | system | system.py | READ_ONLY | E-soft | S | 503 DEP | contracts | — |
| GET | /api/v1/system/version | system | system.py | READ_ONLY | N | S | 503 DEP | contracts+integration | `nexus api version` |
| GET | /api/v1/system/runtime | system | system.py | READ_ONLY | E-soft | N | — | contracts | — |
| GET | /api/v1/system/capabilities | system | system.py | READ_ONLY | N | N | — | contracts+integration | `nexus api capabilities` |
| GET | /api/v1/system/workers | system | system.py | READ_ONLY | E-soft | N | — | contracts | — |
| POST | /api/v1/system/refresh | system | system.py | REFRESH | E (adapter) | N | 503 ENGINE | integration | — |
| POST | /api/v1/system/diagnostics/run | system | system.py | DIAGNOSTIC | N | N | 500 | contracts | `nexus api diagnostics --run` |
| GET | /api/v1/system/diagnostics | system | system.py | READ_ONLY | N (adapter null) | N | — | integration | `nexus api diagnostics` |
| GET | /api/v1/runtime/mode | runtime | runtime.py | READ_ONLY | E-soft | N | — | contracts+integration | `nexus api mode` |
| GET | /api/v1/runtime/freshness | runtime | runtime.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| POST | /api/v1/runtime/mode/validate | runtime | runtime.py | SAFE_VALIDATION | E-soft | N | 422 | contracts | — |
| POST | /api/v1/runtime/mode/preview | runtime | runtime.py | SAFE_VALIDATION | E-soft | N | 422 | contracts | — |
| GET | /api/v1/market/snapshot | market | market.py | READ_ONLY | E | N | 503 ENGINE | integration | — |
| GET | /api/v1/market/quote | market | market.py | READ_ONLY | E | N | 503 ENGINE | contracts+integration | — |
| GET | /api/v1/market/bars | market | market.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/market/regime | market | market.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/market/symbols | market | market.py | READ_ONLY | E | N | 503 ENGINE | integration | — |
| GET | /api/v1/signals/latest | signals | signals.py | READ_ONLY | N | Y | 404 | contracts+integration | `nexus api signals` |
| GET | /api/v1/signals/history | signals | signals.py | READ_ONLY | N | Y | 422 | contracts+integration | — |
| GET | /api/v1/decisions | decisions | decisions.py | READ_ONLY | N | Y | 422 | contracts+integration | `nexus api decisions` |
| GET | /api/v1/decisions/latest | decisions | decisions.py | READ_ONLY | N | Y | 404 | contracts+integration | `nexus api decisions --latest` |
| GET | /api/v1/decisions/stats | decisions | decisions.py | READ_ONLY | N | Y | — | integration | — |
| GET | /api/v1/decisions/no-trade | decisions | decisions.py | READ_ONLY | N | Y | — | integration | — |
| GET | /api/v1/decisions/no-trade/reasons | decisions | decisions.py | READ_ONLY | N | Y | — | integration | — |
| GET | /api/v1/decisions/{decision_id} | decisions | decisions.py | READ_ONLY | N | Y | 404 | contracts+integration | `nexus api decisions --id X` |
| GET | /api/v1/decisions/{decision_id}/evidence | decisions | decisions.py | READ_ONLY | N | Y | 404 | integration | — |
| GET | /api/v1/decisions/{decision_id}/gates | decisions | decisions.py | READ_ONLY | N | Y | 404 | contracts+integration | `nexus api decisions --id X --gates` |
| GET | /api/v1/decisions/{decision_id}/explanation | decisions | decisions.py | READ_ONLY | N | Y | 404 | contracts | — |
| GET | /api/v1/positions | positions | positions.py | READ_ONLY | E | N | 503 ENGINE, 503 DEP | contracts+integration | — |
| GET | /api/v1/positions/{ticket} | positions | positions.py | READ_ONLY | E | N | 404, 503 ENGINE | integration | — |
| GET | /api/v1/positions/history/{ticket} | positions | positions.py | READ_ONLY | N | Y | 404 | integration | — |
| GET | /api/v1/execution/status | execution | positions.py | READ_ONLY | E | N | 503 ENGINE | contracts+integration | — |
| GET | /api/v1/execution/history | execution | positions.py | READ_ONLY | N | Y | 422 | contracts+integration | — |
| GET | /api/v1/model/status | model | positions.py | READ_ONLY | E | N | 503 ENGINE | contracts+integration | — |
| GET | /api/v1/model/identity | model | positions.py | READ_ONLY | E | N | 503 ENGINE | contracts+integration | `nexus api model` |
| GET | /api/v1/model/contracts | model | positions.py | READ_ONLY | E-soft | N | — | contracts | — |
| GET | /api/v1/model/champion | model | positions.py | READ_ONLY | E-soft | N | — | contracts | — |
| GET | /api/v1/features/status | features | positions.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/features/contract | features | positions.py | READ_ONLY | N | S | 503 DEP | contracts+integration | `nexus api features` |
| GET | /api/v1/features/groups | features | positions.py | READ_ONLY | N | S | 503 DEP | contracts | — |
| GET | /api/v1/features/current | features | positions.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/risk/status | risk | risk.py | READ_ONLY | E | N | 503 ENGINE | contracts+integration | — |
| GET | /api/v1/risk/summary | risk | risk.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/research/status | research | research.py | READ_ONLY | N | Y | 503 DEP | contracts+integration | — |
| GET | /api/v1/research/strategies | research | research.py | READ_ONLY | N | Y | 422, 503 DEP | contracts | — |
| GET | /api/v1/research/strategies/{strategy_id} | research | research.py | READ_ONLY | N | Y | 404, 503 DEP | contracts | — |
| GET | /api/v1/research/runs | research | research.py | READ_ONLY | N | Y | 422, 503 DEP | contracts+integration | — |
| GET | /api/v1/research/datasets | research | research.py | READ_ONLY | N | Y | 503 DEP | contracts | — |
| GET | /api/v1/shadow/status | shadow | shadow.py | READ_ONLY | N | Y | — | contracts+integration | — |
| GET | /api/v1/shadow/runs | shadow | shadow.py | READ_ONLY | N | Y | 422 | contracts+integration | — |
| GET | /api/v1/shadow/runs/{run_id} | shadow | shadow.py | READ_ONLY | N | Y | 404 | contracts | — |
| GET | /api/v1/shadow/decisions | shadow | shadow.py | READ_ONLY | N | Y | 422 | contracts | — |
| GET | /api/v1/shadow/70d | shadow | shadow.py | READ_ONLY | N | Y | — | contracts | — |
| GET | /api/v1/observability/events | observability | incidents.py | READ_ONLY | N | Y | 422 | contracts | — |
| GET | /api/v1/observability/metrics | observability | incidents.py | READ_ONLY | E-soft | N | — | contracts+integration | — |
| GET | /api/v1/audit/events | audit | incidents.py | READ_ONLY | N | Y | 422 | contracts+integration | — |
| GET | /api/v1/incidents | incidents | incidents.py | READ_ONLY | N | Y | 422 | contracts+integration | — |
| GET | /api/v1/incidents/stats | incidents | incidents.py | READ_ONLY | N | Y | — | contracts | — |
| GET | /api/v1/incidents/{incident_id} | incidents | incidents.py | READ_ONLY | N | Y | 404 | contracts+integration | — |
| GET | /api/v1/incidents/{incident_id}/timeline | incidents | incidents.py | READ_ONLY | N | Y | 404 | contracts | — |
| GET | /api/v1/database/status | database | incidents.py | READ_ONLY | N | Y | — | contracts+integration | — |
| GET | /api/v1/database/integrity | database | incidents.py | READ_ONLY | N | Y | 503 DEP | contracts | — |
| GET | /api/v1/database/tables | database | incidents.py | READ_ONLY | N | Y | 503 DEP | contracts | — |
| GET | /api/v1/config | config | incidents.py | READ_ONLY | E | N | 503 ENGINE | contracts | — |
| GET | /api/v1/config/schema | config | incidents.py | READ_ONLY | N | S | — | contracts+integration | — |
| POST | /api/v1/config/validate | config | incidents.py | SAFE_VALIDATION | N | S | 422 | contracts+integration | — |

**68 documented operations** (63 GET + 5 POST). Drift-protected by
`scripts/dev/api_skill_drift_check.py`.

---

## 5. ENVELOPE CONTRACT (§3 of spec of record)

Success — `data` present:
```json
{"data": <payload>, "meta": {"request_id": "req_ab12cd34", "generated_at": "2026-09-02T10:00:00+00:00"}}
```

Error — `error` present, `data` absent:
```json
{"error": {"code": "RESOURCE_NOT_FOUND", "message": "...", "details": {}, "request_id": "req_...", "retryable": false}}
```

Rules:
- Exactly one of `data`/`error` at top level. Never both, never neither (2xx always has `data`).
- `meta.request_id`: echoes the client `X-Request-ID` when supplied (truncated 64 chars), else server-generated `req_<10hex>`.
- `meta.generated_at`: server wall clock at response build, UTC ISO-8601 (`+00:00`). It is a RESPONSE time, not an event time — never use it to order domain events (use the domain's own timestamps, e.g. `generated_at` on audit rows).
- Serialization: enums → `.value` strings; datetimes → UTC-aware ISO-8601 (naive datetimes are stamped UTC); Pydantic models → `model_dump()`. Handled centrally in `common.jsonable()`/`ok()` — routes never hand-roll it.
- `null` semantics: `None` means **unknown/unavailable/not-yet-computed** (e.g. `regime:null` before first inference, `mt5:null` without adapter). It NEVER means zero/empty/healthy.
- Unavailable subsystem blocks inside a 200 body (e.g. shadow store blocks) carry their own `null`/`available:false` and are failure-isolated — a `null` block is not a platform error.

## 6. ERROR CONTRACT (all real codes — none invented)

| code | HTTP | retryable | server condition | client/agent action |
|---|---|---|---|---|
| VALIDATION_ERROR | 422 | no | bad query/pagination/body (incl. FastAPI request validation) | fix the request; `error.details.errors[]` names fields |
| RESOURCE_NOT_FOUND | 404 | no | id unknown OR dataset legitimately empty (e.g. no incidents yet) | do NOT retry blindly; distinguish empty-vs-missing via list endpoints |
| METHOD_NOT_ALLOWED | 405 | no | wrong verb on v1 path | use the documented method |
| PAYLOAD_TOO_LARGE | 413 | no | oversized body | shrink request |
| CONFLICT | 409 | no | state conflict (reserved) | re-read state first |
| ENGINE_UNAVAILABLE | 503 | **yes** | API server running without attached engine/adapter | NOT an empty dataset, NOT zero positions, NOT a healthy engine — a missing dependency; retry after engine start or use DB-backed routes |
| DEPENDENCY_UNAVAILABLE | 503 | **yes** | backing store/registry/health-engine read failed | retry with backoff; check logs via `request_id` |
| RESOURCE_UNAVAILABLE | 503 | yes | transient resource (reserved, same family) | retry |
| TIMEOUT | 504 | yes | upstream timeout (reserved) | retry bounded |
| INTERNAL_ERROR | 500 | no | unhandled exception (full detail logged under `request_id`) | report bug with `request_id`; never assume data validity |

Handlers: `web/api_v1/errors.py` (path-guarded — legacy routes keep legacy bodies).

## 7. ENGINE-DEPENDENT BEHAVIOR MATRIX

Dependency classes (verified per route, §4 registry):

| class | behavior when ENGINE absent (server up, engine=None) | routes |
|---|---|---|
| **E** (engine/adapter hard) | **503 `ENGINE_UNAVAILABLE`** | market/* (5), positions (2 GET on adapter), risk/* (2), execution/status, model/status, model/identity, features/status, features/current, runtime/freshness, system/refresh |
| **E-soft** (engine fields degrade truthfully) | HTTP 200 with `engine_attached:false` / nulls | system/status·health·readiness·runtime·workers, runtime/mode, model/contracts, model/champion, observability/metrics |
| **DB** (audit DB-backed) | fully functional (real SQLite reads); empty tables → truthful 404/empty pages | signals/*, decisions/*, execution/history, positions/history/{ticket}, research/*, shadow/*, incidents/*, observability/events, audit/events, database/* |
| **STATIC** (schema/config/build) | fully functional offline | features/contract·groups, config/schema, system/version |

Interpretation rules for agents:
- `503 ENGINE_UNAVAILABLE` ≠ empty dataset ≠ zero positions ≠ healthy engine ≠ successful operation. It means: **the dependency was absent at request time**.
- Engine states: READY (all E routes 200), STOPPED (server may still be up; E routes may attach but return stopped-state truthfully), NOT_INITIALIZED/UNAVAILABLE (E routes → 503), DEGRADED (see `/system/health` layers).
- Environment matrix (verified):

| environment | behavior |
|---|---|
| EMBEDDED (`create_v1_app()` + TestClient) | no engine → E routes 503; DB routes need a repo on `app.state.audit_v1_repo` or default-path artifacts DB |
| LIVE uvicorn (dashboard `create_app`) | full surface; engine-dependent routes reflect the attached engine |
| PAPER/REPLAY/SHADOW/LIVE engine modes | no route-class change — modes affect engine payload content, not API availability |
| NO ENGINE at all | v1 standalone app still serves DB/STATIC routes (CLI + tests rely on this) |

## 8. READ/WRITE & SIDE-EFFECT CLASSIFICATION

| class | count | operations | broker reach |
|---|---|---|---|
| READ_ONLY | 63 | all GETs in §4 | none |
| SAFE_VALIDATION | 3 | `runtime/mode/validate`, `runtime/mode/preview`, `config/validate` — **pure functions, zero state change** | none |
| REFRESH | 1 | `system/refresh` — re-reads adapter snapshots, bumps UI versioner | none (snapshot reads only) |
| DIAGNOSTIC | 1 | `system/diagnostics/run` — bounded offline selftest, caches result | none |
| STATE_AFFECTING | 0 | — | none |
| EXECUTION_CAPABLE | 0 | **v1 can never submit/modify/close an order** | none |

**ABSOLUTE RULE: NO REAL TRADING FROM AGENT API TESTS.** The v1 platform has no
order path at all; still, tests must use TestClient / embedded apps / PAPER /
REPLAY / MOCK fixtures only. Live-mode selection, engine toggling, model
training/promotion and SQL consoles remain legacy-console/CLI owned and are
out of scope for agent API testing.

## 9. PAGINATION CONTRACT

- Params: `page` (default 1, ≥1), `page_size` (default 50, 1..200). Violations → 422 `VALIDATION_ERROR`.
- Shape: `data{items[], page, page_size, has_more}`. `has_more` is the ONLY continuation signal; **no total counts** (full-scan protection).
- Ordering: stable (`id DESC` or documented store order); repeated identical queries return identical `data` (meta timestamp varies).
- Invariants proven by tests: no duplicates across pages, no skipped rows, page k = exact slice `rows[(k-1)*s : k*s]` of the unpaginated order.
- Filters compose with pagination; indexed columns only (see §10).

Example:
```
GET /api/v1/decisions?page=2&page_size=5
→ {"data": {"items": [...5...], "page": 2, "page_size": 5, "has_more": true}, "meta": {...}}
```

## 10. FILTER CONTRACT (verified bounds)

| filter | routes | type | semantics | bound |
|---|---|---|---|---|
| symbol | signals/history, decisions, execution/history, market/quote·snapshot, positions | str ≤32 | exact match | — |
| action | signals/history, decisions | str | upper-cased exact (BUY/SELL/NO_TRADE) | — |
| status | execution/history, audit/events, incidents | str ≤24 | exact match | — |
| severity/category/component | incidents | str ≤24/32/48 | exact match | — |
| execution_mode, decision_stage | decisions | str | exact match | — |
| event_type | observability/events | str ≤48 | exact match | — |
| lifecycle | research/strategies | str ≤32 | exact match | — |
| strategy_id | research/runs | str ≤64 | exact match | — |
| run_id | shadow/decisions | str ≤64 | exact match | — |
| hours_back | signals/history, decisions | int | generated_at lower bound (UTC) | 1..720 (decisions enforce via Query ge/le; signals caps server-side) |
| limit | market/bars | int | bar count | ≤500 |

All filters are parameterized SQL/store parameters — never string-interpolated. Unbounded queries are impossible (server-side LIMIT always injected).

## 11. TIME CONTRACT

- Transport: ISO-8601, timezone-aware, **UTC** (`+00:00`). Naive datetimes are stamped UTC by `common.jsonable()` before serialization.
- `meta.generated_at` = response build time — NOT event time.
- Domain event times (e.g. `generated_at` on decisions, `timestamp` on ledger rows) are the ONLY ordering truth for domain events.
- `freshness` endpoints carry engine-model stage ages — the authoritative "is data fresh" signal.
- **WARNING**: never mix local time, UTC, and broker/server time. MT5 epochs are SERVER-LOCAL (GMT+3, BUG-070) — raw epoch fields from broker snapshots are not UTC. Convert only with the broker-aware providers; do not epoch-math in agents.

## 12. REQUEST ID / CORRELATION / IDEMPOTENCY

- `X-Request-ID` (any HTTP request): accepted on ALL v1 routes; echoed as `meta.request_id` + `X-Request-ID` response header + server log correlation. Max 64 chars.
- `Idempotency-Key` (POSTs): echoed as `meta.idempotency_key` (≤128 chars). v1 POSTs are naturally idempotent (validate/refresh/diagnostics) — no server-side dedup store; the echo is for caller-side correlation.
- Retry guidance: retryable codes (503/504) may be retried with backoff using the SAME request id for traceability; non-retryable (4xx/500) must not be retried unchanged.
- All failures are logged internally with the same `request_id` — quote it in bug reports.

## 13. VERSIONING + OPENAPI

- Single mount: `/api/v1` from `web/api_v1_wiring.py`. Incompatible changes → `/api/v2` (never break v1 in place).
- OpenAPI: `GET /openapi.json` on the v1 app (or dashboard app) — 100% operation coverage enforced (68/68).
- Regenerate snapshot: `.venv/Scripts/python.exe scripts/dev/api_openapi_snapshot.py` → `artifacts/api/openapi_snapshot.json` (sorted, timestamp-free, deterministic).
- Detect drift: `.venv/Scripts/python.exe scripts/dev/api_openapi_diff.py OLD.json NEW.json --fail-on-breaking` — BREAKING = removed endpoint/method or request-schema change → **exit 2**; additions → note, exit 0.
- APISkill drift: `.venv/Scripts/python.exe scripts/dev/api_skill_drift_check.py` — this file ↔ live OpenAPI (missing/extra ops, count mismatch) → exit 1 on drift.
- Breaking changes REQUIRE explicit review (change-control entry + taskboard row) before merge.

## 14. EXTENSION WORKFLOW (adding an API — the ONLY sanctioned path)

1. Define the contract (purpose, request, response, errors) in `docs/api/API_PLATFORM_V1.md` §7 inventory.
2. Implement the route in the matching `web/api_v1/<domain>.py` (reuse `common.ok/fail/parse_pagination/build_page/fetch_rows_bounded`; no god router, no inline SQL from user input).
3. Wire nothing new if the domain router already exists (single mount covers it); new domains require a new module + wiring import in `_include_routers`.
4. Add contract tests (`tests/unit/test_api_v1_contracts.py`) + integration tests (`tests/integration/test_api_v1_platform.py`) incl. engineless 503 or DB fixture paths.
5. `ruff check --fix && ruff format && mypy` on touched files.
6. Regenerate snapshot; run `api_openapi_diff.py OLD NEW --fail-on-breaking`; run `api_contract_check.py` (expect N+1 ops documented).
7. Update `docs/api/API_REFERENCE.md` + §3/§4 of THIS file (drift check must PASS).
8. `api_smoke.py --embedded` PASS; benchmark if performance-sensitive.
9. Commit (`<AGENT>:<task>` convention), push, verify `origin/main == HEAD`.

Removal workflow: deprecate first (mark in docs + snapshot note), name the replacement, keep serving through the compatibility window, then remove code + docs + snapshot entries + add a regression test asserting the removal — never leave dead documentation claiming the route exists.

## 15. TOOLING REFERENCE (scripts/dev/)

| tool | purpose | input | output | exit codes |
|---|---|---|---|---|
| `api_smoke.py` | per-domain reachability + truthfulness | `--embedded` (no server) or `--live BASE_URL` | per-domain ✓/○/✗ + `API_SMOKE = PASS\|FAIL` | 0/1 |
| `api_contract_check.py` | OpenAPI quality gate: route↔spec parity, summaries/tags, schema refs, secret-shape scan, min-capability floor | none (embedded app) | `API CONTRACT CHECK = PASS (68 operations…)` | 0/1 |
| `api_openapi_snapshot.py` | deterministic artifact for drift detection | `--out PATH` | sorted JSON snapshot | 0 |
| `api_openapi_diff.py` | snapshot-vs-snapshot breaking-change classifier | OLD.json NEW.json [--fail-on-breaking] | BREAKING/note lines + verdict | 0 compatible, 2 breaking, 1 IO |
| `api_benchmark.py` | bounded local perf (median/p95/p99, ≤300 req/route, read-only) | `--requests N` | per-route table + `API_BENCHMARK = PASS\|PASS_WITH_NOTES` (p95 budget 250ms) | 0 |
| `api_skill_drift_check.py` | THIS file ↔ live OpenAPI sync | none | `APISKILL DRIFT = PASS\|FAIL` | 0/1 |

## 16. CLI → API MAP (verified from `cli/api_commands.py`)

| command | endpoint(s) |
|---|---|
| `nexus api status` | `/api/v1/system/status` (human rendering) |
| `nexus api health` | `/api/v1/system/health` |
| `nexus api version` | `/api/v1/system/version` |
| `nexus api capabilities` | `/api/v1/system/capabilities` |
| `nexus api mode` | `/api/v1/runtime/mode` |
| `nexus api signals` | `/api/v1/signals/latest` |
| `nexus api decisions [--latest \| --id X [--gates]]` | `/api/v1/decisions/latest`, `/api/v1/decisions`, `/api/v1/decisions/{id}`, `/decisions/{id}/gates` |
| `nexus api model` | `/api/v1/model/identity` |
| `nexus api features` | `/api/v1/features/contract` |
| `nexus api diagnostics [--run]` | `POST /api/v1/system/diagnostics/run`, `GET /api/v1/system/diagnostics` |
| `nexus api get PATH [--page --page-size]` | any `/api/v1/*` path |
| `nexus api smoke` | all domains via the Python client → `API_SMOKE = PASS` |

All CLI commands consume `NexusApiClient` over real HTTP (`--base-url` / `NEXUS_API_BASE`, default `http://127.0.0.1:8080`) — the SAME contracts as external clients; zero duplicated logic.

## 17. NEXUSAPICLIENT CONTRACT

```python
from nexus_scalp.api_client import NexusApiClient, NexusApiError, DEFAULT_BASE_URL

client = NexusApiClient("http://127.0.0.1:8080", timeout=10.0, headers={})
client.get(path, params=...)            # → envelope dict {"data","meta"} (+ "pagination" keys inside data on lists)
client.post(path, json_body=..., idempotency_key=...)
# typed shortcuts: system_status/health/version, capabilities, runtime_mode,
# market_quote, signals_latest/history, decisions_latest/decisions/
# decision_detail/decision_gates, positions, risk_status, execution_status,
# model_status/identity, features_contract, research_status, shadow_status,
# incidents, database_status, observability_metrics, run_diagnostics
```

- Path normalization: leading `/api/v1` auto-prepended (`client.get("incidents")` works).
- **Errors raise** `NexusApiError(code, message, request_id, retryable, status, details)` — parsed from the v1 error envelope; transport failures raise the same type with `code=DEPENDENCY_UNAVAILABLE, retryable=True`.
- No automatic retries inside the client (agents decide policy per `retryable`).
- No pagination helpers beyond passing params — the envelope IS the contract.

## 18. AGENT CALLING PATTERNS (verified commands)

```bash
# discovery
nexus api capabilities
curl -s http://127.0.0.1:8080/api/v1/system/capabilities

# operational truth
nexus api status
curl -s -H "X-Request-ID: req_agent_001" http://127.0.0.1:8080/api/v1/system/status

# decision forensics
nexus api decisions --latest
curl -s http://127.0.0.1:8080/api/v1/decisions/req_ab12cd34/gates

# paginated + filtered
curl -s "http://127.0.0.1:8080/api/v1/decisions?page=2&page_size=50&symbol=XAUUSD&action=NO_TRADE"

# validation POST (no state change)
curl -s -X POST http://127.0.0.1:8080/api/v1/runtime/mode/validate \
     -H "Content-Type: application/json" -d '{"mode": "PAPER"}'

# test batteries (NO live trading — embedded/TestClient/PAPER only)
python scripts/dev/api_smoke.py --embedded
python scripts/dev/api_smoke.py --live http://127.0.0.1:8080
python scripts/dev/api_contract_check.py
python scripts/dev/api_skill_drift_check.py
.venv/Scripts/python.exe -m pytest tests/unit/test_api_v1_contracts.py tests/integration/test_api_v1_platform.py -q
```

## 19. TEST MAP (real files — do not invent others)

| domain/aspect | tests |
|---|---|
| envelope/pagination/sanitize/errors/capabilities/versioning | `tests/unit/test_api_v1_contracts.py` |
| full request paths + fixtures + fuzz + security + additive wiring | `tests/integration/test_api_v1_platform.py` |
| cross-surface state truth (CLI/API/runtime parity) | `tests/unit/test_state_truth_parity.py` |
| client E2E golden journeys (live HTTP against the API) | `tests/e2e_client/` (`e2e_harness.py`, `j1_golden.py`, `j2_mode_switch.py`, `j3_signal_decisions.py`, `j4_resilience.py`) |
| perf | `scripts/dev/api_benchmark.py` (manual, bounded) |

## 20. SECURITY RULES FOR AGENTS

MUST NOT:
- expose secrets/tokens/passwords/broker credentials (sanitizer masks `token|password|secret|apikey|api_key|credential|login|account_number`-shaped keys → `***`; report any leak as a BUG)
- expect or extract stack traces/filesystem paths/SQL from error bodies (they are contractually absent; internal detail lives in logs only)
- attempt SQL/shell/command injection via params or IDs — hostile-input handling is tested (bounded fuzz: 4xx envelope, never 500); do not try to bypass it
- execute arbitrary SQL anywhere (v1 has no SQL endpoint; the legacy db-console SQL route is UI-owned and out of scope for agents)
- call anything that could reach a broker in tests (§8: v1 has zero execution path; keep it that way — TestClient/PAPER/REPLAY only)
- hammer endpoints: bounded requests only (pagination caps exist for a reason; benchmark tool is the sanctioned load path)

## 21. PERFORMANCE CONTRACT

Protections (verified): health 3s TTL cache + daemon prewarm at mount; version memoization; O(1) `/database/status` (integrity is explicit); pagination caps; server-side LIMITs; `hours_back` windows.
- Run `api_benchmark.py` after adding routes or touching stores. Regression = p95 > 250ms on a previously-green route, or any route newly over budget.
- Do not hard-code old numbers in agent logic; measure current.

## 22. DOMAIN GUIDANCE (avoid asking the wrong endpoint)

| domain | USE WHEN | does NOT answer | dependencies | common misuse |
|---|---|---|---|---|
| decisions | why was X traded/rejected; NO_TRADE analysis; evidence replay | live market state, model weights | audit DB | treating `decision_id` as broker ticket (that's positions/{ticket}) |
| model | which artifact is SERVING (`/model/identity`), warmup/inference state | training/provenance lineage (use `/research/runs`, legacy `/api/models/*`) | engine bundle | conflating champion-registry with serving-bundle truth — `/model/champion` is bundle-authoritative |
| features | active 70D contract (SSoT), last vector | historical vectors (not stored per-tick for API) | schema contract / engine | expecting per-feature history |
| risk | last proposal risk checks, current exposure | policy edits (none exist in v1) | engine + adapter | reading `risk_checks` as a guarantee of future acceptance |
| runtime | mode/freshness truth | mode CHANGES (validate-only; apply is console/CLI) | engine | applying a mode via API (impossible by design) |
| shadow | 70D observer health/disagreements/drift | promotion decisions (governance legacy) | audit DB | interpreting observer counters as champion impact |
| research | runs/strategies/datasets lineage | live training control | audit DB | using `/research/runs` as model registry |
| incidents | incident inventory/timelines/stats | real-time alerting (that's Telegram notifier) | audit DB | polling faster than bounded pages allow |

## 23. ONE QUESTION → ONE API (routing table)

| question | endpoint |
|---|---|
| What is the engine currently doing? | `/api/v1/system/status` |
| Can the system accept work? | `/api/v1/system/readiness` (`data.ready`) |
| Which layers failed and why? | `/api/v1/system/health` (checks) |
| What model is actually serving? | `/api/v1/model/identity` |
| What feature contract is active? | `/api/v1/features/contract` |
| Why was a decision rejected? | `/api/v1/decisions/{id}/gates` (+ `/evidence`) |
| What NO_TRADE reasons occurred? | `/api/v1/decisions/no-trade` (+ `/no-trade/reasons`) |
| Is market data fresh? | `/api/v1/runtime/freshness` |
| What capabilities exist? | `/api/v1/system/capabilities` |
| What incidents exist? | `/api/v1/incidents` (+ `/stats`) |
| Current exposure/margin? | `/api/v1/risk/summary` |
| What changed in the audit ledger? | `/api/v1/audit/events` |
| Is the broker connected? | `/api/v1/execution/status` |
| Database healthy? | `/api/v1/database/status` (+ `/integrity`) |
| What config is effective (sanitized)? | `/api/v1/config` |
| Would a mode change be valid? | `POST /api/v1/runtime/mode/validate` (never apply) |

## 24. TRUTH / CONSISTENCY RULES

- Never infer READY/HEALTHY/LIVE/TRADING/MODEL_VALID/DATA_FRESH from a single field unless the contract states it. Compose: `readiness.ready` + `health.checks` + `runtime/freshness`.
- Authoritative endpoints per fact: serving model → `/model/identity` (bundle), NOT governance pages; mode → `/runtime/mode`; data freshness → `/runtime/freshness`; decision truth → audit-backed `/decisions/*`.
- Compare snapshots via `meta.request_id`/`generated_at`; different endpoints sampled at different times legitimately differ — do not manufacture contradictions from timestamp skew.
- 503 ≠ empty. null ≠ zero. `available:false` ≠ error.

## 25. DISCOVERY WORKFLOW (recommended for a fresh agent)

1. `nexus api capabilities` (or read §4 registry here)
2. `nexus api status` (operational posture)
3. consult §23 routing table → call the specific endpoint
4. verify assumptions with `api_smoke.py --embedded` / `--live`
5. extend? → §14 workflow; check drift after docs edits: `api_skill_drift_check.py`

## 26. HISTORICAL RECONCILIATION (why the old tree is gone)

`legacy 257→280 dashboard routes (UI-owned, still served)`
→ `first /api/v1 tree (src/nexus_scalp/api/v1, 45 routes, superseded envelope — committed mid-flight by a parallel session)`
→ `spec-of-record authored (docs/api/API_PLATFORM_V1.md, 65 capabilities)`
→ **consolidation decision**: the WIP `web/api_v1` tree matched the spec's envelope/pagination/error contracts, so it became canonical; the 45-route tree was DELETED (`e51a910`) and its salvable patterns (store queries, exception handlers) were ported
→ current **68 operations**, drift-protected.

**Never resurrect `src/nexus_scalp/api/v1`.** Its paths are NOT current. Any
agent finding references to it (old docs, old sessions) must treat them as
historical context only.

## 27. STALENESS / DRIFT DETECTION (contract)

Sync axes: implementation ↔ OpenAPI ↔ API reference ↔ APISkill ↔ tests.
Any mismatch = documentation/contract defect (file it, don't paper over it).

```bash
python scripts/dev/api_contract_check.py      # implementation ↔ OpenAPI
python scripts/dev/api_openapi_snapshot.py    # regenerate artifact
python scripts/dev/api_openapi_diff.py OLD NEW --fail-on-breaking   # snapshot ↔ snapshot
python scripts/dev/api_skill_drift_check.py   # THIS FILE ↔ OpenAPI
```

Update order when changing APIs: code → tests → snapshot → docs → THIS file → re-run all four checks.

## 28. SKILL SELF-AUDIT (Phase 33 — answered)

1. discover every current API? ✅ §3 + §4 registry (drift-enforced 68/68)
2. current vs legacy? ✅ §1/§2 status classes + §26 reconciliation
3. right endpoint for common questions? ✅ §23
4. engine-dependency per endpoint? ✅ §4 E/E-soft/N columns + §7 matrix
5. error semantics? ✅ §6 (all real codes)
6. construct a valid request? ✅ §5/§9/§10/§18
7. correct smoke/contract test? ✅ §15/§19
8. safe testing (no live trading)? ✅ §8/§20
9. identify breaking change? ✅ §13 (diff tool + exit codes)
10. extend without a second API tree? ✅ §14 (single-mount, single-tree rule)
