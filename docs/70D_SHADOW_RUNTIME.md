# 70D Shadow Runtime — Nexus Scalp Engine (NSE)

> TASK-05-70D-SHADOW (2026-08-19) · Agent: Hermes-Shadow70D
> Contracts: SHADOW_70D v1 · SHADOW_LOAD_GATE v1 · SHADOW_FEATURE_HEALTH v1 ·
> SHADOW_DRIFT v1 (agents/contracts.md) · INV-018 (agents/runtime_invariants.md)
> Central invariant: **SHADOW MAY OBSERVE EVERYTHING, BUT MUST CONTROL NOTHING.**

---

## 0. Purpose

The 70D Shadow Runtime evaluates a validated 70D candidate (scalp_v3: 50 Base
+ 10 News + 10 Liquidity) against the live Champion **without** changing
production trading. It answers: does the 70D candidate behave correctly on
live data under real market conditions while the Champion stays in control?

The 70D candidate MUST NOT place, modify, cancel or influence a trade. Its
only production interaction is: read live market state → calculate 70D
features → infer with the 70D candidate → record the shadow result.

## 1. First Gate — candidate status

Only `VALIDATED_CANDIDATE` may enter Shadow by default.

```
model status / validation status / OOS status / robustness status /
final score / research registry / candidate manifest
```

Possible states: `VALIDATED_CANDIDATE | REJECTED | INCONCLUSIVE | INVALID |
NOT_AVAILABLE`. As of 2026-08-19 the lifecycle registry holds only the 50D
Champion rows (no 70D candidate registered) → the runtime reports
`NO_VALIDATED_CANDIDATE` and stays IDLE. The infrastructure is fully tested
against a deterministic VALIDATED fixture; when the 70D series lands a
validated candidate, `POST /api/models/shadow70/attach` loads it.

## 2. Architecture

```
                    ┌──────────────────────────────────────────────┐
   live tick ──────►│ live_engine._process_tick_pipeline           │
                    │   (Champion path — production decision-maker) │
                    │   └─ _record_shadow_decision                  │
                    │        └─ [shadow70 hook, flag-guarded]       │
                    └───────────────┬──────────────────────────────┘
                                    │ observe(vector70, champion_*)
                                    ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │ shadow/shadow70/  (OBSERVABILITY ONLY — no adapter/order/risk)     │
   │                                                                    │
   │ runtime.Shadow70LoadValidator   manifest→hash→schema→dim→scaler    │
   │ runtime.Shadow70Runtime         attach()/observe()/pause/stop      │
   │ models.*                        frozen contracts + taxonomy        │
   │ health.*                        feature health + drift             │
   │ store.Shadow70Store             queued persistence (audit.db)      │
   │ worker.Shadow70Worker           bounded async batch writer         │
   │ liq_provider.py                 liquidity producer bridge (lazy)   │
   └──────────────────────────────────┬────────────────────────────────┘
                                      ▼
                    AuditRepository background queue (no sync DB on tick)
                    shadow70_observations / shadow70_events /
                    shadow70_feature_health / shadow70_drift_alerts
```

**70D vector contract** (POST_70D INV-70D-001..006):

```
indices 0..49   Base 50D   (canonical scalp_v1 contract, untouched)
indices 50..59  News 10D   (canonical 12-field news context, first 10 kept)
indices 60..69  Liquidity 10D (bsl/ssl distance, eqh/eql strength, HTF,
                               internal/external distance, confluence,
                               sweep state, displacement)
```

## 3. Shadow model contract (spec 3)

Every candidate must declare: `model_id, model_version, schema_id=scalp_v3,
dimension=70, feature_schema_hash, scaler_hash, training_dataset_id,
validation_result, artifact_hash`. The runtime verifies **all** of them —
there is no "load model if file exists".

## 4. Load validation sequence (spec 4 / 35)

```
MANIFEST → ARTIFACT HASH → SCHEMA → DIMENSION → SCALER →
MODEL LOAD → HEALTH CHECK → SHADOW READY
```

Verdicts: `SHADOW_READY | SHADOW_BLOCKED | SHADOW_DEGRADED |
SHADOW_LOAD_FAILED | NO_VALIDATED_CANDIDATE`. A malformed candidate never
enters the runtime; the failing gate is reported exactly.

## 5. Isolation guarantees (spec 5 / 8 / 9 / 10 / 36)

- `shadow/shadow70/` imports NO adapter, NO order manager, NO risk engine,
  NO policy object (enforced by tests TEST-SHADOW-08..12 and the module
  graph probe). There is no code path from a shadow prediction to an order.
- Shadow output never flows into policy, RiskEngine, OrderManager, live
  confidence thresholds, or the Champion.
- Champion output is passed in read-only; observation never mutates it.
- Shadow persistence writes ONLY shadow70_* research tables (INV-018);
  accounting, ledger, broker and outcome rows are never written.

## 6. Runtime states (spec 32 / 34)

`IDLE → (attach) → LOADING → READY ⇄ PAUSED · STOPPED · BLOCKED · FAILED`

- `START/STOP/PAUSE/RESUME` via API; stopping the shadow NEVER stops
  Champion/broker/execution.
- Hot reload: attach/detach/pause/resume are in-process (no engine
  restart). No silent restart of a LIVE engine.

## 7. Configuration (spec 33 — only supported fields)

| Field | Default | Meaning |
| :--- | :--- | :--- |
| `shadow70_enabled` | false | master switch (set by attach API) |
| `shadow_model_id` | '' | the attached candidate |
| `sampling_rate` | 1.0 (100%) | currently fixed 100%; sample_source recorded |
| `max_queue` | 2000 | bounded persistence queue |
| `latency_budget_ms` | 50 | shadow inference budget (exceed → SHADOW_INFERENCE_TIMEOUT) |
| `drift_monitoring_enabled` | true | feature drift monitor |

## 8. Telemetry (spec 14 / 15)

Structured events (never silent):

```
[SHADOW70] event=MODEL_LOADED            model_id= schema=scalp_v3 dimension=70
[SHADOW70] event=MODEL_LOAD_REJECTED     status= failing_gate= reason=
[SHADOW70] event=INFERENCE               model_id= prediction= confidence= latency_ms=
[SHADOW70] event=ERROR                   stage= error_code= reason=
[SHADOW70] event=SHADOW_BACKPRESSURE     dropped_snapshots=
[SHADOW70] event=SHADOW_DRIFT_WARNING    alert_count= severities=
```

Error taxonomy: `SHADOW_SCHEMA_MISMATCH, SHADOW_MODEL_LOAD_FAILED,
SHADOW_FEATURE_INVALID, SHADOW_INFERENCE_TIMEOUT, SHADOW_STALE_FEATURES,
SHADOW_SCALER_MISMATCH, SHADOW_PERSISTENCE_FAILED, SHADOW_BACKPRESSURE,
SHADOW_ARTIFACT_HASH_MISMATCH, SHADOW_MANIFEST_INVALID,
SHADOW_DIMENSION_MISMATCH`. Every error carries component/stage/model_id/
schema/expected+actual dimension/timestamp/correlation_id/error_code/
safe_message in the event row.

## 9. Failure isolation (spec 16 / 17 / 23)

- `observe()` NEVER raises. A shadow fault marks the observation invalid with
  an error_code and is isolated; the Champion path continues.
- Latency: shadow work is off the hot path (bounded queue + async worker);
  no `await` of shadow inference in the tick pipeline.
- MT5 disconnect / news unavailable / liquidity unavailable / shadow model
  failure → shadow marks itself DEGRADED/FAILED, Champion and broker
  interactions unaffected (TEST-SHADOW-23 / TEST-SHADOW-38).

## 10. Backpressure & memory (spec 18 / 39 / 40)

- Bounded queue (`max_queue`); a full queue drops snapshots with
  `SHADOW_BACKPRESSURE dropped_snapshots=N` (never unbounded growth).
- In-memory windows: observations ≤ 2000, latency samples ≤ 500, feature
  health ≤ 1000 vectors, drift ≤ 5000 vectors.
- No synchronous DB on the tick path: every write goes through the
  AuditRepository background queue (INV-001), batched by Shadow70Worker.

## 11. Feature health (spec 20 / 29)

Per Liquidity feature over a bounded window:

```
finite_rate  missing_rate  stale_rate  zero_rate  mean  std  min  max
```

Live distribution is compared against the training distribution when the
reference is provided (`Shadow70DriftMonitor.set_reference`).

## 12. Drift (spec 21 / 22 / 44)

Metrics: PSI (normal-PDF reference from training mean/std), mean shift,
std ratio, missing-rate delta. Severity: `NORMAL → WATCH → WARNING →
CRITICAL` with configurable documented thresholds (health.py). Drift is
OBSERVATIONAL — it never changes trading. Critical drift emits
`SHADOW_DRIFT_WARNING/CRITICAL` (aggregated, throttled — never per-tick
Telegram). Below the sample floor (`min_samples=30`) the status is
`INSUFFICIENT_EVIDENCE`.

## 13. Observations, idempotency, outcomes (spec 12 / 13 / 24 / 25 / 31)

- Deterministic identity: `sha256(snapshot_id | model_id | model_version |
  timestamp)`; persistence uses `INSERT OR IGNORE` on the unique key —
  reconnect/retry cannot duplicate (TEST-SHADOW-13/14).
- Outcome linkage is separate research telemetry: `outcome` defaults to
  `PENDING` and is resolved only by a later research process on the shadow
  tables. It is NEVER an accounting outcome, never a ledger/experience row,
  never real PnL (INV-018; TEST-SHADOW-26..28).
- Sampling: 100% during validation; `sample_source` = LIVE / REPLAY recorded
  on every observation.

## 14. Disagreement analysis (spec 9 / 10 / 26 / 27)

Taxonomy: `AGREEMENT, ACTION_DISAGREEMENT, DIRECTION_DISAGREEMENT,
CONFIDENCE_DIVERGENCE, NO_TRADE_DISAGREEMENT, CHAMPION_BUYS_SHADOW_NO_TRADE,
CHAMPION_SELLS_SHADOW_NO_TRADE, CHAMPION_NO_TRADE_SHADOW_BUYS,
CHAMPION_NO_TRADE_SHADOW_SELLS, BUY_VS_SELL`. Every observation records
regime, session, news state, liquidity state + the 10D liquidity sub-vector
so research can answer "which liquidity signals changed model decisions".

## 15. API & UI (spec 28 / 29 / 30 / 46)

```
GET  /api/models/shadow70/summary         runtime + store + worker
GET  /api/models/shadow70/health          feature health + drift
GET  /api/models/shadow70/disagreements   recent disagreement rows
POST /api/models/shadow70/attach          load-gated candidate attach
POST /api/models/shadow70/start|stop      worker control
POST /api/models/shadow70/detach          stop shadow (Champion unaffected)
```

UI: "70D Shadow Model" section in the Model Governance tab — model/version/
schema/status/last inference/latency/inferences/errors/agreement %/avg
confidence Δ + Liquidity Feature Health grid + Recent Disagreements table.
All values come from the real backend (no fake frontend calculations).

## 16. Persistence

| Table | Purpose |
| :--- | :--- |
| `shadow70_observations` | canonical idempotent observations |
| `shadow70_events` | append-only [SHADOW70] event ledger |
| `shadow70_feature_health` | periodic per-feature health snapshots |
| `shadow70_drift_alerts` | drift alerts (severity-classified) |

Lazy schema on first save (existing audit.db); writes via the queued writer.

## 17. Rollback / disable procedure

1. `POST /api/models/shadow70/detach` (or set `shadow70_enabled=false`).
2. Worker stops flushing; runtime → STOPPED; Champion path untouched.
3. If a candidate misbehaves at the load gate, the runtime never attached —
   no rollback needed.
4. No shadow data is ever deleted automatically; research rows remain
   audit-trail evidence (hygiene/retention governs them).

## 18. Replay parity (spec 48)

The same runtime path on historical snapshots reproduces reference
probabilities byte-for-byte (deterministic inference, TEST-SHADOW-48).
Research-benchmark shadow vs replay shadow are the same model, same schema,
same features, same predictions within 1e-9 tolerance.

## 19. Gaps / known limitations

- 70D candidate availability: NO_VALIDATED_CANDIDATE until the parallel 70D
  series registers a validated candidate (the attach API resolves the
  registry row and load-gates it).
- Liquidity producer bridge (`liq_provider.py`) resolves
  `features.liquidity_engine` lazily; until the parallel series lands it
  returns the neutral 10D + `liquidity_calculation_version="unavailable"`.
- Sampling rates < 100% are not yet exposed as a control (only recorded).

## 20. Quality gate evidence

- tests/unit/test_shadow70_runtime.py (36)
- tests/unit/test_shadow70_safety.py (5)
- tests/unit/test_shadow70_health_drift.py (11)
- scratch/shadow70_live_readonly_smoke.py — real-registry READ-ONLY smoke
  (NO_VALIDATED_CANDIDATE truthfully reported; broker tokens 0; Champion
  unchanged; idempotency; bounded queue)
- scratch/shadow70_ready_path_fixture_smoke.py — SHADOW_READY fixture path
  (30 observations, disagreement classes, persistence 15/15, health/drift)