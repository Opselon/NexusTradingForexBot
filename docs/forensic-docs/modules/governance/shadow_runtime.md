# src/nexus_scalp/governance/shadow_runtime.py

- PURPOSE: Governance Shadow Runtime — thin failure-isolated wrapper over
  the PHASE 11 ChallengerRuntime adding TASK-6 governance guarantees:
  same-input alignment, per-comparison latency governance, failure /
  execution isolation, deterministic inference.
- ARCHITECTURE LAYER: Domain (governance) wrapping Features→shadow
  runtime; NO adapter/order-manager/risk imports (spec 10 / property 8).
- RESPONSIBILITY: GovernanceShadowRuntime.compare — one parallel
  comparison (the only entry the engine calls); telemetry counters
  (comparisons/errors/dropped/timeouts/invalid_probability/
  schema_mismatches, bounded latency window).
- DEPENDENCIES: governance.alignment (challenger_input_for,
  feature_parity, news_context_hash), governance.models, governance.store,
  shadow.challenger.ChallengerRuntime; torch via the runtime.
- CONNECTS TO: LiveEngine governance wiring, GovernanceStore (comparison
  rows + FAILURE_ISOLATED events), Telegram reporting summary.
- KEY CONCEPTS:
  - compare() ALWAYS returns a dict, NEVER raises. Fault sequence:
    1) SAME-INPUT ALIGNMENT via challenger_input_for — failure sets
       alignment="NONE", schema_mismatches++, records
       SCHEMA_MISMATCH event; 2) challenger inference under a stopwatch;
       out-of-range confidence (not 0..1) → PREDICTION_INVALID; raised
       inference → SHADOW_INFERENCE_FAILED; 3) LATENCY GOVERNANCE:
       total_ms bounded; challenger latency > SHADOW_LATENCY_BUDGET_MS
       (50ms) → SHADOW_TIMEOUT (comparison invalid — never blocks the
       Champion path); 4) FEATURE PARITY vs the offline/replay reference:
       parity state MISMATCH (not UNKNOWN) → FEATURE_PARITY_FAILURE and
       the comparison is INVALID — flagged, never used in promotion
       statistics.
  - agreement = valid AND champion_action == challenger_action.
  - Every failure goes through _record_failure → [MODEL_SHADOW]
    event=FAILURE_ISOLATED + a GovernanceEvent (stage SHADOW,
    error_type="SHADOW_ISOLATED"), best-effort store write.
  - Bounded windows: _recent capped at MAX_INMEMORY_DECISIONS=2000,
    latency_ms capped at 500 samples; comparisons persisted only when
    valid (spec 14: canonical row, no raw ticks); simulated=True always.
  - Determinism (spec 15): inference torch.inference_mode inside the
    ChallengerRuntime.
  - summary(): state SHADOW, avg/max/p95 latency (p95 from the sorted
    500-window when >= 20 samples else max), last_update/last_error.
- HOT PATH / PERFORMANCE: compare() runs per decision on the live path
  but only the challenger inference is heavyweight (~ms, CPU);
  store.save_shadow_comparison is a queue put (non-blocking);
  latency append is amortized O(1).
- EDGE CASES & PITFALLS: p95 index math
  sorted(latency_ms)[int(len*0.95)-1] — off-by-one at exactly lengths
  where int(len*0.95)-1 == len-1 is fine, but for len=20 the 19th index
  (18) is used — genuine p95 would be index 18/19 boundary; UNKNOWN
  parity (no reference) does NOT invalidate the comparison (spec'd); a
  challenger with no ref (self.ref None) → challenger_input_for raises →
  SCHEMA_MISMATCH path (align handling depends on ref fields); dropped
  counter increments for ANY invalid comparison.