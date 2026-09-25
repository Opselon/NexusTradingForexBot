# src/nexus_scalp/web/debug_snapshot.py

- **PURPOSE:** Builds the canonical debug state snapshot (`build_debug_snapshot`)
  — the 18-section forensic view (runtime/contract/features/model/confidence/
  policy/risk/exposure/execution/positions/exit/liquidity/news/workers/
  database/caches/chart/sse/errors) served by `/api/debug/state` and the
  snapshot ring (`/api/debug/snapshots`, compare). The Debug tab UI is a
  PURE renderer of this — never computes trading intelligence in JS.
- **ARCHITECTURE LAYER:** Web (observability/presentation).
- **RESPONSIBILITY:** (a) assemble the 18 sections from engine state with
  DEFENSIVE hasattr-guards (any missing order-manager attr like
  `_entry_confidences`/`_peak_drawdown_usd` → section degrades, never
  crashes — the fake-engine pattern from tests); (b) feature-matrix
  section registry-driven from `schema_contract` (scalp_v3=70D, hash
  235b8fccc96b7e0e; base 0..49 / news 50..59 / liquidity 60..69) with
  RAW/NORMALIZED/CLIPPED only when the runtime exposes the stage, else
  NOT_EXPOSED (never fake 0); (c) contract validation section —
  `_contract_section` reports `70D CONTRACT BROKEN` on dim mismatch and
  `MODEL CONTRACT INVALID` when actual_classes != 4 (the 128-class
  regression) or live vector width mismatch; (d) snapshot ring:
  `app.state.debug_snapshot_store` (max 64, in-memory) with
  snapshots/{id} and compare (feature T0/T1/Δ diff); (e) SSE diagnostics:
  `app.state.sse_diag` (connection/event_count/serialization_errors +
  last SSE_SERIALIZATION_ERROR with correlation_id).
- **DEPENDENCIES:** engine ref (duck-typed), schema_contract, liquidity
  runtime, LatencyTracer data, logging. NO trading logic.
- **CONNECTS TO:** web/server debug routes, Debug tab UI, tests
  (test_debug_snapshot_phase20 — TEST-DEBUG-01..32), post70d monitoring.
- **KEY CONCEPTS:** Truthfulness: the console shows EXACTLY what the
  runtime exposes — a feature stage never computed renders NOT_EXPOSED;
  the model input tensor shown is the captured post-scaler pre-softmax
  tensor (`_last_model_input_tensor`, sampled); snapshot ring is bounded
  (64) in-memory. Liquidity canonical order 60 bsl / 61 ssl / 62 eqh /
  63 eql per schema_contract (the brief's "63 eqh_strength" example is
  wrong vs the registry — trust LIQUIDITY_10D_NAMES).
- **EDGE CASES & PITFALLS:** new section readers must be hasattr-guarded
  (add defensively); the ring purges oldest on overflow; state_version
  naming (the sse_diag NameError class) fixed — keep stable.