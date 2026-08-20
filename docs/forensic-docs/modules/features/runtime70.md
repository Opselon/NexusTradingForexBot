# src/nexus_scalp/features/runtime70.py

- **PURPOSE:** The LiveEngine-bound 70D runtime hook (`Runtime70Hook`) — the
  bridge that lets the running engine compute a 70D snapshot (base 50D + news
  10D + liquidity 10D) with the CURRENT model/toggles, and report
  compatibility truthfully (`MODEL CONTRACT INVALID` when the loaded artifact
  is not 70D-capable).
- **ARCHITECTURE LAYER:** Features (runtime bridge for the 70D candidate
  path; observability/state only — zero order authority).
- **RESPONSIBILITY:** (a) hold the runtime config (`Runtime70Config`),
  (b) accept the active model (so it can check input width), (c) accept
  toggle state (liquidity enabled?), (d) `compute_snapshot` — assemble
  base/news/liquidity into one validated 70D snapshot + state dict for
  SSE/debug, (e) `model_compatibility` — truthful model-vs-contract verdict.
- **DEPENDENCIES:** `scalp_features` (50D consumer? no — it takes a 50D
  vector as input), `schema_contract` (geometry/hash), news context
  provider, liquidity runtime, `LatencyTracer` (optional instrumentation),
  `observability.logging`.
- **CONNECTS TO:** LiveEngine (constructed with the engine, model set at
  bundle load), shadow70 runtime (the same hook powers shadow snapshots),
  web/SSE diagnostics, tests (test_70d_runtime_hook_task3).
- **KEY CONCEPTS:**
  - `compute_snapshot` is the assembly point: it takes the 50D vector, news
    context dict, and liquidity 10D (or None) and produces a
    `Runtime70Result` (with per-family availability — a missing liquidity
    block yields NEWS_INCONCLUSIVE/zero-vector semantics per contract, never
    fabricated).
  - `set_model`/`set_toggles` decouple construction from runtime wiring so
    the hook exists before the model loads; `to_state_dict()` feeds the
    observability/debug pipelines.
  - Compatibility is computed, not assumed: `model_compatibility` compares
    the model's actual `num_features` (from the artifact) vs the 70D
    expectation — a 50D champion + 70D request → explicit INCOMPATIBLE
    (the debug console's `MODEL CONTRACT INVALID` flag).
- **HOT PATH / PERFORMANCE:** compute_snapshot is called on demand (debug/
  SSE cadence), NOT per tick; still keeps allocations minimal.
- **EDGE CASES & PITFALLS:** The snapshot may be requested before a model is
  set (model=None during startup) — must degrade to NOT_EXPOSED/INCOMPATIBLE
  rather than crash; news/liquidity providers may be disabled → the hook must
  mark those families unavailable instead of synthesizing values.