# 70D Full-System Forensic Final Report (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · Role: Full-System Data / Worker / Chart Flow
> Auditor · 2026-08-19 · Absorbed HEAD: 2babe15 (origin/main)

## 1. System Architecture (actual)

The NexusScalpEngine runs a single async `LiveEngine.run_loop` that owns the
tick hot path (tick → bars → 50D features → regime → news → Champion
inference → policy → risk → execution → shadow records → SSE) and kicks 9
background workers via `asyncio.to_thread`. Persistence is SQLite WAL
(audit.db / news.db / candle_intel.db) behind an async queue. The web server
(FastAPI) serves one canonical state graph (LiveUiState.2) over REST + SSE.
The 70D series is CANDIDATE-ONLY: the live Champion stays 50D scalp_v1;
scalp_v3 (70D) is the canonical contract; the 70D shadow runtime observes
with the same market state without touching execution.

## 2. Data Flow (full lineage)

tick → BarAggregator (M1, reseed REPLACE+ALIGN) → ScalpFeatureEngine (50D
base) → RegimeClassifier → NewsContextCache (worker-refreshed) →
Champion inference (ScalpNet 4-class) → SignalPolicy (rule matrix + SMC) →
RiskEngine (dynamic lot + clamps) → OrderManager (broker-verified) →
audit_ledger/audit_experience_outcomes (identity chain ticket ==
execution_id; idempotency_key) → accounting → research (causal dataset,
provenance per sample) → registry. The 70D shadow path: base50 + news10
(build_news_10, state at index 59) + liq10 (liquidity_engine) →
build_70d_vector (strict 50+10+10) → Shadow70Runtime.observe (dimension/
finite/bounds/schema-hash/freshness validation) → Shadow70Worker (bounded
queue, INSERT OR IGNORE). Verified end-to-end with a deterministic trace
(artifacts/forensics/feature_vector_trace.json).

## 3. Worker Flow

9 workers (accounting, history-sync, intelligence, research, training,
shadow, news, hygiene, shadow70) — all throttled, cycle_count/last_error
truthful, checkpoints persisted (research/intelligence/news), failures
cycle-isolated, kicked off the tick path. BUG-105 demonstrated the
"RUNNING but doing nothing" class and is fixed with regression tests.

## 4. Chart Flow

Broker-first (MT5 copy_rates_*) with explicit ENGINE_STATE fallback;
OHLC-validated; reseed alignment (BUG-054/058); SSE versioned state/tick/
heartbeat with out-of-order guard; overlays from real engine state
(rectangles/bos/midlines/liq_markers); liquidity section embedded in the
state graph. No synthetic bars.

## 5. 70D Migration Impact (old assumptions discovered)

- BUG-105: the 70D shadow hook was placed inside the 50D-shadow except block
  + conditional-import scoping (dead code) — the clearest post-70D wiring
  defect. FIXED.
- 263 dimension-literal hits audited; all dimension-SENSITIVE ones classified
  (VALID_LEGACY / ACTIVE_60D / ACTIVE_70D / PARAMETERIZED); no remaining
  active 50D/60D assumption feeds the 70D runtime (verified by the assembly
  trace + wrong-dimension rejection probe).
- Schema identity: shadow70 SHADOW70_SCHEMA_ID canonicalized to scalp_v3;
  schema hash 235b8fccc96b7e0e matches the golden parity corpus.
- 2 latent hardening items (attach validation_result forcing;
  governance/evidence 50-width cap) — documented, not exploitable today.

## 6. Bugs Found

| ID | Severity | Status |
| :--- | :--- | :--- |
| BUG-105 (70D hook dead code + UnboundLocalError) | HIGH | FIXED |
| BUG-106 (ledger entry) | MED | FIXED (ledger) |

## 7. Root Causes (evidence)

- 70D hook pasted into the except block of the 50D record (accidental
  placement); `build_70d_vector` import conditional on news context; the 50D
  early-return gate. Proven by scratch/repro_shadow70_hook_dead_code.py:
  happy path → 0 observations; forced 50D failure → UnboundLocalError;
  after fix → 1/2 observations.

## 8. Fixes (commits)

- Fix absorbed into swarm commit 14fff5a (Hermes-Parity) — verified
  byte-for-byte before push (standalone `_record_shadow70_observation`,
  hoisted imports, canonical schema hash).
- Docs commit 066a7ba (Hermes-Forensic-70D) — forensic map + probes, pushed.
- Pushed to origin: 6086d4e..2babe15 (includes my 066a7ba).

## 9. Tests (exact counts)

- 75 shadow70+parity unit tests (test_shadow70_* + test_70d_contract_parity)
- 34 inference-validator + replay-parity tests
- 31 parity + dataset-parity tests (slow, run separately)
- 4 BUG-105 regressions (TEST-SHADOW-36..39)
- Combined run at absorbed HEAD: 105 passed (shadow suites + parity +
  liquidity runtime integration) — pre-existing WIP failures resolved by the
  swarm (TASK-01/TASK-02 landed).

## 10. Performance

- 70D assembly: pure numpy, deterministic; shadow inference latency fixture
  avg 0.069 ms (budget 50 ms); no DB on hot path (INV-001) — verified by
  design + tests. No new bottlenecks introduced. (p50/p95/p99 for the full
  engine were NOT measured live — no real runtime; see risks.)

## 11. Database

- audit.db: canonical ledger + shadow70_* tables + governance + incidents;
  schema migration gate (TASK-10) at startup; hygiene worker AUDIT_ONLY
  default; no duplicate storage detected for economic facts (idempotency_key
  dedup proven; split-fill protection BUG-097).
- news.db / candle_intel.db isolated; no cross-DB truth duplication.

## 12. UI/API

- One canonical state graph; every endpoint proven honest (health endpoints
  explain empty states; 200-but-wrong checks all pass); bundle identity
  headers; 70D shadow + liquidity panels wired; notifications via Telegram
  with real worker verdicts.

## 13. Runtime (model/feature state)

- Live active schema scalp_v1/50D (protected). 70D candidate-only; load gate
  (Shadow70LoadValidator 7 gates) rejects wrong dimension/hash/schema;
  InferenceValidator adds 10 rejection codes; scaler contract validates
  dimension + hash. No 70D model artifact exists yet in the registry
  (NO_VALIDATED_CANDIDATE is the truthful current state).

## 14. Remaining Risks

| Risk | Class |
| :--- | :--- |
| No validated 70D candidate in registry → shadow stays IDLE until TASK-04 series lands | PROVEN |
| Shadow70 live inference used a stub fn in fixture; real torch latency unmeasured | NOT PROVEN |
| Performance (p50/p95/p99) of the full live engine not measured (no live MT5 session this task) | NOT PROVEN |
| attach_shadow70 validation_result forcing (latent hardening) | NOT PROVEN (documented) |
| governance/evidence 50-width cap (latent hardening) | NOT PROVEN (documented) |
| shadow70 models.py docstring stale (scalp_v4 in prose) | PROVEN (cosmetic) |

## 15. Final Status

**RELEASE_HEALTHY_WITH_WARNINGS**

- 70D integration code path proven correct (assembly + validation + golden
  parity + regression tests).
- BUG-105 (the one proven post-70D wiring bug) fixed with regression tests.
- Warnings: no real 70D candidate artifact/`live` performance envelope yet —
  the 70D shadow cannot produce real observations until TASK-04 promotes a
  validated candidate; two latent hardening items are open (documented).

## 16. References

- docs/70D_SYSTEM_FLOW_FORENSIC_MAP.md
- docs/70D_WORKER_FLOW_FORENSICS.md
- docs/70D_CHART_FLOW.md
- docs/70D_DATA_FLOW.md
- docs/70D_API_UI_FLOW.md
- docs/70D_FULL_APPLICATION_FLOW.md
- docs/70D_POST_MIGRATION_BUG_MATRIX.md
- scratch/repro_shadow70_hook_dead_code.py · scratch/trace_70d_vector_assembly.py
- artifacts/forensics/feature_vector_trace.json
- agents/bugs.md (BUG-105/106)