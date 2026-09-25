# src/nexus_scalp/features/liquidity_engine.py

- **PURPOSE:** The canonical pure-causal 10D liquidity producer (TASK-1) —
  computes liquidity-intelligence features (bsl_distance_atr, ssl_distance_atr,
  eqh_strength, eql_strength, htf_liquidity_score, internal/external
  liquidity_distance, liquidity_confluence, liquidity_sweep_state,
  post_sweep_displacement) plus the pool lifecycle that underpins them.
  Pools = candidate liquidity zones (session H/L pools, daily price pools)
  tracked through CANDIDATE → CONFIRMED → ACTIVE → INVALIDATED with a ±5
  fractal confirmation delay (causality: a pool only "exists" once its
  swing is CONFIRMED by later bars).
- **ARCHITECTURE LAYER:** Features. Pure computation + state; no I/O, no
  broker/order authority; safe for research/replay/training.
- **RESPONSIBILITY:** (a) pool detection (`session_high_low_pools`,
  `daily_price_pools`, `detect_confirmed_swings`, `update_pool_states`);
  (b) strength scoring (`equal_high_low_strengths` — clustered-EQH/EQL
  scoring), (c) `htf_liquidity_score` (higher-timeframe liquidity bias),
  (d) `internal_external_distances` (price vs internal/external liquidity),
  (e) the canonical output `LiquidityFeatures.as_vector()` in the exact
  order the 70D contract consumes (LIQUIDITY_10D_NAMES).
- **DEPENDENCIES:** numpy, `domain.models` (bars/tick), internal enum set
  (PoolSide/PoolSource/PoolState/SweepState), `observability.logging`.
- **CONNECTS TO:** `liquidity_runtime.LiquidityGovernor` (the thread-safe
  runtime wrapper that computes these on new-bar cadence live),
  `schema_contract.LIQUIDITY_10D_NAMES` (order contract), shadow70 runtime,
  dataset builders (training/replay parity), the Liquidity Intelligence UI
  tab (pool overlays from `report().pools`), tests
  (test_liquidity_engine_causality / _contract / _features).
- **KEY CONCEPTS:**
  - **Causality is the design center:** a swing becomes "confirmed" only
    after ±5 bars of confirmation (fractal window) — so training, replay,
    and live all see the SAME pool at the SAME bar index; no lookahead.
    `update_pool_states` transitions pools across their lifecycle with
    explicit state guards.
  - **Two family sources:** session H/L pools (per session code derived from
    UTC hour, `_session_code`) and daily price pools (multi-day high/low
    bands) — unified in `LiquidityPool` (side, source, state, observed_at,
    confirmed_at, usable_at, strength).
  - **`_cluster_equal_levels`** — nearly-equal price levels merge into one
    strength cluster (invariant: a pool touched 3× is stronger than one
    touched once); `_score` weights touches × displacement.
  - `as_vector()` returns the exact order the contract demands; features are
    clipped `[-3,+3]`, NaN guarded (never propagate).
- **HOT PATH / PERFORMANCE:** `compute_liquidity_features` is windowed over
  recent bars (O(window)); the LIVE cadence is new-bar (not per-tick) per
  INV-020 — the governor decides cadence; this module stays a pure function
  of `(bars, now)`.
- **EDGE CASES & PITFALLS:** `usable_at` timestamps derive from bar times
  (never host clock); pool invalidation on price breaking through with
  confirmation keeps stale zones from poisoning features; the ±5 confirmation
  delay trades reactivity for causality — the TASK-1 design accepted this
  explicitly (documented in docs/70D_TEMPORAL_FEATURE_CONTRACT.md).