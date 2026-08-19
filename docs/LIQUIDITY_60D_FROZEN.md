# LIQUIDITY 60D FROZEN CONTRACT (TASK-02 STEP 16-17)

> Agent: AGENT-02 (Hermes-LiquidityIntegration) · 2026-08-19
> This document FREEZES the liquidity algorithm after real-data validation.

## Algorithm version
- `LIQUIDITY_ALGORITHM_VERSION = "scalp_liquidity_v1.0.0"`
- Constant source: `src/nexus_scalp/features/liquidity_runtime.py`

## Schema
- schema id: `scalp_liquidity_v1` (registered in `features/schema.py`)
- dimension: 60 (indices 0..49 = protected scalp_v1; 50..59 = liquidity)
- supersedes: `scalp_v1`; ACTIVE live schema remains `scalp_v1`
- schema hash: see the golden snapshot file (tests/golden/liquidity_70d_reference.json)

## Frozen constants (TASK-01 handoff; validated on real data)
| constant | value |
| :--- | :--- |
| SWING_CONFIRM_BARS | 5 |
| EQH_TOLERANCE_ATR | 0.30 |
| CONFLUENCE_CUTOFF_ATR | 0.75 |
| TOUCH_PROXIMITY_ATR | 0.05 |
| RECLAIM_FRACTION_ATR | 0.15 |
| HTF weight H1 | 0.9 |
| HTF weight H4 | 1.2 |
| HTF weight D1 | 1.6 |
| ATR period | 14 (mean TR, floor 0.20) |
| HTF proximity band | 6 ATR |

## Indices 50..59
50 bsl_distance_atr · 51 ssl_distance_atr · 52 eqh_strength · 53 eql_strength ·
54 htf_liquidity_score · 55 internal_liquidity_distance · 56 external_liquidity_distance ·
57 liquidity_confluence · 58 liquidity_sweep_state · 59 post_sweep_displacement

## Normalization / clipping / missing policy
- All values: finite, clipped [-3,+3] via ONE central `_clip3()`; NaN/Inf -> 0.0
- Missing: no pool -> 3.0 (far); no evidence -> 0.0 (neutral)
- Never NaN, never Inf, never random

## Causal delays
- Swing: candidate_at (swing bar) -> confirmed_at (bar + SWING_CONFIRM_BARS)
- HTF: forming bucket excluded; only buckets fully closed at decision time
- Sweep: penetration + LATER closed rejection bar; event at rejection close
- Displacement: measured only from bars AFTER the sweep-confirming bar

## Real-data evidence (2026-08-19, XAUUSD M1, 100k bars)
- swing confirm-delay exact +5: 500/500
- HTF forming-exclusion: 25/25
- sweep future-invariant: 25/25
- historical invariance bit-exact: 25/25
- feature latency p50=39.6ms p95=49.7ms max=54.4ms (501-bar window)
- frame: 99,946 rows, 60 feat cols, all finite, all in [-3,3]
  (artifacts/model_generation/liquidity_task02/)

## Parity evidence
- dataset/replay/runtime: all call the SAME canonical
  `compute_liquidity_features` (structural parity; TEST-LIQ-29/30 + task02 tests)
- 50D protected: TEST-60D-BASE-01 + TEST-TASK02-08

## Golden snapshot
- tests/golden/liquidity_70d_reference.json (see STEP 10 commit)

## Tests
- tests/unit/test_liquidity_engine_{contract,causality,features}.py (60)
- tests/unit/test_liquidity_task02_integration.py (25)
- tests/unit/test_liquidity_runtime_integration_phase18.py (30)
- tests/integration/test_liquidity_api.py (14)

## Commit SHAs (TASK-02 steps)
- STEP-0 baseline: cf84f79
- STEP-1/2 flag contract + persistence fix: 9eab99e
- STEP-3 API state: a703b8e
- STEP-4/5 UI toggle: 5bca9f3
- STEP-6/7 hot reload + runtime 60D vector: 8d8270e
- STEP-9 real-data causality: 4de3ce7

## Change policy
ANY change to the frozen constants/formulas requires:
schema/version review -> new tests -> new experiment -> new commit -> new
handoff (per TASK-02 §3). A weak model result is NOT a reason to change.

## STEP 21/22 PERFORMANCE MEASUREMENT (real data, 2026-08-19)

Measured (cProfile, 200-row real window on XAUUSD M1):
- per-row cost: ~11.6 ms/row (compute_liquidity_frame, 1000 rows = 70s on a
  contended host; 100k rows = 499s standalone)
- dominant cost: _bars_to_arrays/_bar_times re-derivation (~40% of time:
  each sub-function re-builds numpy arrays + timestamps from the BarData
  list on every call)

PROVEN BOTTLENECK: the canonical engine re-derives bar arrays per sub-call.
DESIGNED FIX (NOT applied — engine frozen): thread a single pre-computed
(bars_to_arrays, bar_times) pair through compute_liquidity_features and its
sub-functions; identical outputs, ~2-3x throughput. Apply in a FUTURE schema
version with its own golden re-baseline, per the change policy.

Live hot-path impact: the liquidity snapshot runs on the bar-close cadence
(not per-tick); 11.6ms << 60s M1 cadence -> NO live hot-path regression.
Dataset-build impact: 100k-row frame ≈ 8-19 min; bounded slices recommended
for iteration.
