# 70D System Flow Forensic Map (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> This document maps the REAL flows discovered in the repository code at
> absorbed HEAD (b531243 + swarm commits), NOT the desired architecture.
> Status badges: 🟢 VERIFIED (code-proven) · 🟡 PARTIALLY VERIFIED ·
> 🔴 CONTRADICTED · 🟠 NEEDS INVESTIGATION · ⚫ DEAD/UNUSED

## 1. Canonical 70D Contract (single source of truth)

| Item | Value | Where | Status |
| :--- | :--- | :--- | :--- |
| Schema id (canonical 70D) | `scalp_v3` | `features/schema_contract.py::SCHEMA_ID` | 🟢 |
| Dimension | 70 | `schema_contract.py::DIMENSION` | 🟢 |
| Family layout | BASE 0..49 · NEWS 50..59 · LIQUIDITY 60..69 | `schema_contract.py` (BASE_END/NEWS_START/NEWS_END/LIQUIDITY_START/LIQUIDITY_END) | 🟢 |
| Schema hash | SHA-256 of canonical registry JSON, prefix 16 | `feature_schema_hash()` | 🟢 (235b8fccc96b7e0e measured) |
| News 10D names | fields 0..8 + news_state (index 10) of news_context_v1 | `NEWS_10D_NAMES` | 🟢 |
| Liquidity 10D names | `liquidity_engine.LIQUIDITY_FEATURE_NAMES` as_vector order | `LIQUIDITY_10D_NAMES` | 🟢 |
| Live active schema | `scalp_v1` (50D) — 70D is CANDIDATE-ONLY | `features/schema.py::ACTIVE_SCHEMA_ID` | 🟢 |
| 70D registry entries | scalp_v3 (70D) + scalp_v4 (70D, TASK-02 integration) + scalp_liquidity_v1 (60D) | `features/schema.py` FEATURE_SCHEMAS | 🟢 |
| Runtime shadow schema id | `scalp_v3` (models.py SHADOW70_SCHEMA_ID — corrected from the stale docstring "scalp_v4") | `shadow/shadow70/models.py:33` | 🟢 |

## 2. FLOW A — Data Flow (tick → 70D vector → model → decision)

```
MT5 terminal / Paper adapter
   │ symbol_info_tick / paper tick
   ▼
LiveEngine._process_tick_pipeline (application/live_engine.py)
   │ TickData (frozen, UTC-normalized)
   ▼
BarAggregator (market_data/bar_aggregator.py)
   │ M1 completed bars (REPLACE+ALIGN reseed, BUG-058)
   ▼
ScalpFeatureEngine.compute_from_bars (features/scalp_features.py)
   │ 50D base vector (scalp_v1 contract; FEATURE_NAMES; to_tensor_input)
   ▼
  ├─ Champion path: MarketRegimeClassifier → _infer_probabilities (ScalpNet)
  │    → SignalPolicy → RiskEngine → OrderManager (execution)
  ├─ 50D shadow: ShadowEngine.record_shadow_decision (governance/shadow)
  └─ 70D shadow: LiveEngine._record_shadow70_observation (BUG-105 FIXED)
       │
       ├─ base50  = live 50D features (0..49)
       ├─ news10  = build_news_10(vectorize_news_context(news_ctx)) (50..59)
       ├─ liq10   = build_liquidity_10(self, tick) (60..69)
       ├─ vector70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
       │            (features/liquidity_runtime.py — strict 50+10+10, no pad)
       ├─ schema_hash = feature_schema_hash() (per-observation identity)
       ▼
Shadow70Runtime.observe(...) → Shadow70Observation (frozen, simulated=True)
       │ validates: dimension=70, finite, [-3,+3], schema hash, freshness, provenance
       ▼
Shadow70Worker.enqueue → Shadow70Store.save_observation (audit.db queued writes,
       INSERT OR IGNORE on deterministic observation_id)
```

Verification evidence (this task):

- 🟢 70D assembly runs end-to-end deterministically:
  `scratch/trace_70d_vector_assembly.py` → base 50 finite, news 12→10,
  liq 10, build_70d_vector == assemble_70d, validate_70d_vector PASS.
  Per-index mapping written to `artifacts/forensics/feature_vector_trace.json`.
- 🟢 Wrong-dimension rejection: 50D/60D vector → `SHADOW_FEATURE_INVALID`
  (probe, 2026-08-19).
- 🟢 BUG-105 fixed: the 70D hook now runs on EVERY tick, independent of the
  50D shadow gate (see bug matrix; regression TEST-SHADOW-36..39).

## 3. FLOW B — Worker Flow

Workers constructed in `LiveEngine.__init__` and kicked via
`asyncio.to_thread(worker.tick)` from `run_loop` (NEVER in the tick
pipeline — INV-001):

| Worker | File | Interval | State | Checkpoint | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| AccountingWorker | accounting/worker.py | loop-throttled | start/stop | derived cache only | 🟢 |
| BrokerHistorySyncWorker | adapters/database/broker_history_sync.py | interval_sec (60?) | running/cycle_count/last_error | broker_history_meta | 🟢 |
| IntelligenceWorker | intelligence/worker.py | 30s | start/stop + checkpoint | intelligence_worker_state | 🟢 |
| ResearchWorker | research/worker.py | 60s | start/stop + checkpoint | research_worker_state | 🟢 |
| TrainingWorker | model_lifecycle/worker.py | throttled | DISABLED by default (auto_train off) | training state | 🟢 INV-016 |
| ShadowWorker | shadow/worker.py | throttled | start/stop | shadow_runs | 🟢 |
| NewsWorker | news/worker.py | 60s | start/stop + checkpoint | news_worker_state | 🟢 |
| DatabaseHygieneWorker | hygiene/worker_runner.py | 6h | AUDIT_ONLY default | hygiene state | 🟢 |
| Shadow70Worker | shadow/shadow70/worker.py | batch flush 5s | thread + bounded queue 2000 | — (INSERT OR IGNORE) | 🟢 |

Worker truthfulness (no "RUNNING but doing nothing"):

- Every worker has `cycle_count`, `last_error`, real result payloads, throttles
  by wall-clock — none can silently claim progress.
- ResearchWorker seeds builtin candidates each cycle BEFORE dataset/discovery
  (seed step) and persists checkpoints in research_worker_state.
- Shadow70Worker: bounded queue (max 2000), drop/coalesce telemetry
  (SHADOW_BACKPRESSURE), flush persists through the AuditRepository queue.
- BUG-105 was precisely the class of "RUNNING but doing nothing": the 70D
  observation hook appeared active but produced zero observations on the
  happy path (dead code inside the except block).

## 4. FLOW C — Chart Flow

```
Browser (Web/app.js + api_client.js)
   │ GET /api/chart/history?count=900
   ▼
server.py get_chart_history
   │ 1) engine.adapter.get_rate_history(symbol, timeframe, count)  // MT5 official
   │    → validated OHLC bars (time_utc/OHLC/tick_volume/spread/real_volume)
   │    → RESYNC: engine.aggregator.reseed(seeded) + sync_chart_state (BUG-054)
   │ 2) fallback (explicit provenance): engine.aggregator.get_completed_bars()
   │    → source = ENGINE_STATE (never synthetic)
   ▼
   {bars, source, symbol, timeframe, requested, returned, first/last timestamp,
    generated_at, error, visual_overlays}
   ▼
Frontend chart renderer (candles + SMC overlays + liquidity pools + news markers)
```

- 🟢 Broker-first, engine-fallback, provenance explicit (`source` field).
- 🟢 `reseed()` REPLACE+ALIGN (dedupe, sort, completed-only, forming bar at
  next-minute boundary) — verified in `market_data/bar_aggregator.py:116-181`.
- 🟢 SSE protocol `event: state|tick|heartbeat` with monotonic state_version
  and out-of-order guard (server.py:5999-6039).
- 🟢 Overlays from real engine state (`visual_overlays` with liq_markers etc).

## 5. Dimension-assumption audit (POST-70D special focus)

Systematic scan of `src/nexus_scalp/**/*.py` for 50/60/70 dimension
literals, slices, constructors and dim-params (263 source hits; most are
trading constants — the dimension-SENSITIVE set is below).

| Location | Pattern | Classification | Verdict |
| :--- | :--- | :--- | :--- |
| features/scalp_features.py | FEATURE_NAMES/to_tensor_input 50 | VALID_LEGACY (live contract) | OK |
| models/scalp_net.py | `num_features: int = 50` default | DIMENSION-PARAMETERIZED (ctor arg) | OK — 70D model passes 70 |
| experience/models.py | CANONICAL_FEATURE_DIMENSION = 50 | VALID_LEGACY (live) | OK |
| adapters/database/audit_repository.py | feature_dimension INTEGER DEFAULT 50 | VALID_LEGACY (schema default for live rows) | OK |
| shadow/shadow70/store.py | schema_dimension INTEGER DEFAULT 70 | ACTIVE_70D | OK |
| features/features70.py | assemble_70d + Feature70Snapshot | ACTIVE_70D (dataset/replay/inference) | OK |
| features/schema_contract.py | DIMENSION=70 + family geometry | ACTIVE_70D (canonical) | OK |
| features/liquidity_runtime.py | build_70d_vector strict 50+10+10 | ACTIVE_70D | OK |
| governance/alignment.py | 50+V2_RESERVED_SLOTS(+NEWS) math | ACTIVE_60D path | OK |
| governance/evidence.py:284 | `width = min(len(feature_window[0]), 50)` | LEGACY 50-CAP on drift stats | 🟠 see risk |
| model_generation/architectures.py | input_dim: int = 50 default | DIMENSION-PARAMETERIZED | OK |
| model_generation/schema_v2.py | 60D/70D frame builders + verify dims | ACTIVE_60D/70D | OK |
| web/server.py:946 | features payload iterates FEATURE_NAMES (50) | VALID_LEGACY (/api/status lives 50D) | OK |
| shadow/shadow70/liq_provider.py | raw[50:60] for 60D producers | ACTIVE_70D bridge | OK |

No active 50D/60D assumption feeds the 70D runtime incorrectly. The 70D
assembly is verified end-to-end with the deterministic trace.

## 6. Bugs found & fixed (evidence-linked)

| ID | Severity | Component | Symptom | Root Cause | Evidence | Fix | Regression | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| BUG-105 | HIGH | live_engine shadow70 hook | `shadow70_observations` empty in production; hook "RUNNING but doing nothing" | Hook nested inside 50D-shadow `except` (dead on happy path); `build_70d_vector` imported under `if news_ctx` → UnboundLocalError when news disabled; 50D early-return gate; `feature_schema_hash=""` skipped verification | Standalone `_record_shadow70_observation()` called every tick; imports hoisted; canonical schema hash passed | TEST-SHADOW-36..39 (4 tests) | 🟢 75+ suite green; absorbed into 14fff5a |

## 7. Remaining risks (PROVEN / NOT PROVEN / UNKNOWN)

- 🟠 governance/evidence.py:284 caps drift-stat width at 50 — a 70D vector
  fed to governance evidence would have the tail truncated silently. Today the
  governance shadow path uses the 50D champion vector only; the 70D shadow has
  its own full-width drift monitor. Fix (parametrize width) is LOW priority.
- 🟠 web/server.py /api/status features block is 50D by design — the UI cannot
  display the 70D vector from /api/status (candidate-only contract). The 70D
  panel reads the shadow70 endpoints. No drift detected.
- ⚫ shadow70 SHADOW70_SCHEMA_ID docstring still says "scalp_v4" in models.py:6
  (stale comment; the constant is scalp_v3). Cosmetic.
- ❓ No real 70D candidate artifact exists yet (registry: only scalp_v1 50D
  rows). The 70D shadow stays IDLE until a validated 70D candidate attaches.