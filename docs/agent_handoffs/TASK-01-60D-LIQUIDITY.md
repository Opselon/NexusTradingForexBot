# TASK-01-60D-LIQUIDITY — Handoff (Liquidity Intelligence Foundation)

> Agent: Hermes-LiquidityFoundation · Role: Liquidity Intelligence Foundation /
> Feature Contract Engineer · Date: 2026-08-19 · Branch: main
> Commit: `b91b8c9` (16 files, +3588) · Status: READY_FOR_REVIEW
> Schema: `scalp_liquidity_v1` (dimension 60, supersedes `scalp_v1`)

## What was implemented

1. **Canonical causal liquidity engine** — `src/nexus_scalp/features/liquidity_engine.py`
   (NEW, ~1300 lines, pure numpy, zero I/O):
   - `LiquidityPool` domain model: price, side (BSL>0/SSL<0), source, timeframe,
     strength, candidate_at, confirmed_at, last_touched_at, state, active, touch_count.
   - Lifecycle: CANDIDATE → CONFIRMED → APPROACHING → TOUCHED → SWEPT →
     RECLAIMED/DISPLACED/INVALIDATED (`PoolState` IntEnum).
   - `detect_confirmed_swings`: ±5 fractal pivots; a swing at bar i is usable
     only from bar i+5 (`confirmed_at`); candidate_at ≠ confirmed_at.
   - Session (UTC-hour semantics identical to feat_16-19), PDH/PDL, PWH/PWL pools.
   - `equal_high_low_strengths`: EQH/EQL with ATR tolerance
     (`|h_a-h_b| <= ATR*0.30`, never float equality), softmax over clusters
     (member count × closeness × recency), future touches invisible.
   - `htf_liquidity_score`: COMPLETED H1/H4/D1 buckets ONLY — the forming
     candle is excluded (its final high/low is unknowable); tanh-scaled ×3.
   - `internal_external_distances`: internal = pools strictly inside
     (range_min+0.25ATR, range_max−0.25ATR); external = edge levels themselves.
   - `liquidity_confluence`: zones within 0.75·ATR; duplicate references to the
     same underlying level collapse to ONE source (4 refs ≠ 4 sources).
   - `detect_reactive_sweep`: penetration + LATER bar closing back beyond the
     pool (rejection/reclaim) — pure penetration is a BREAKOUT, never a sweep.
   - `post_sweep_displacement`: measured ONLY from bars AFTER the
     sweep-confirming bar.
   - Central `_clip3` ([-3,+3]) + documented missing defaults (3.0 far / 0.0
     neutral; never NaN/Inf/random).
2. **Schema** — `features/schema.py` registers `scalp_liquidity_v1` (60D,
   supersedes scalp_v1). scalp_v1 remains ACTIVE (50D live contract unchanged);
   scalp_v2 (TASK-5 momentum) untouched.
3. **Dataset builder** — `model_generation/schema_v2.py`:
   `compute_liquidity_frame` / `build_liquidity_dataset` /
   `verify_liquidity_artifact` (60 feat columns, all finite, all in [-3,3]).
   TRAINING/LIVE/REPLAY parity is structural: all call the same
   `compute_liquidity_features` with identical inputs.
4. **Config switch** — `model.liquidity_features_enabled` (default **false**):
   false → exactly the existing 50D behavior; true → 60D layer available to
   candidate pipelines. The switch never silently alters schema expectations.
5. **Docs** — `docs/LIQUIDITY_60D_FORENSIC_BASELINE.md`,
   `docs/LIQUIDITY_60D_50D_CONTRACT_SNAPSHOT.json` (50D contract capture),
   `docs/LIQUIDITY_60D.md` (per-feature contract).
6. **Registries (additive)** — taskboard `TASK-01-60D-LIQUIDITY`,
   contracts `LIQUIDITY_60D v1`, runtime_invariants `INV-019`.

## Exact formulas / indices (scalp_liquidity_v1, feat_50..59)

| idx | name | formula (all clipped [-3,3]) |
| :--- | :--- | :--- |
| 50 | bsl_distance_atr | (nearest confirmed BSL above − P) / ATR; missing → 3.0 |
| 51 | ssl_distance_atr | (P − nearest confirmed SSL below) / ATR; missing → 3.0 |
| 52 | eqh_strength | softmax(clusters of confirmed highs, tol 0.30·ATR); missing → 0.0 |
| 53 | eql_strength | mirror on lows |
| 54 | htf_liquidity_score | tanh(Σ sign·proximity·tf_weight over completed H1/H4/D1 buckets) × 3; missing → 0.0 |
| 55 | internal_liquidity_distance | nearest confirmed pool inside range / ATR; missing → 3.0 |
| 56 | external_liquidity_distance | nearest confirmed pool at/beyond range edges / ATR; missing → 3.0 |
| 57 | liquidity_confluence | best zone: (1+ln(distinct_sources)) + tf_sum/1440·0.5 + Σstrength·0.25; missing → 0.0 |
| 58 | liquidity_sweep_state | signed: −2 SWEPT_AND_DISPLACED, −1 SWEPT, 0 NONE, +1 APPROACHING, +2 TOUCHED, +3 RECLAIMED |
| 59 | post_sweep_displacement | (close[rejection+1] − sweep-bar opposite extreme)/ATR in rejection direction; missing → 0.0 |

ATR = canonical mean-TR-14 (identical to `ScalpFeatureEngine.compute_from_bars`
lines 528-537; floor 0.20). Confirmation delay = SWING_CONFIRM_BARS=5.

## Tests (60, all pass)

- `tests/unit/test_liquidity_engine_contract.py` — TEST-LIQ-01..11, 32-37, 44
  (registry 0..59, dimension 60, finite/clip, BSL/SSL, EQH/EQL tolerance,
  missing defaults, no-DB/no-network, determinism, edge cases).
- `tests/unit/test_liquidity_engine_causality.py` — TEST-LIQ-12/13, 18-28,
  TEST-60D-BASE-01 (future swing/EQH/HTF/sweep/displacement cannot leak,
  historical invariance at T, breakout≠sweep, reclaim, 50D first-half).
- `tests/unit/test_liquidity_engine_features.py` — TEST-LIQ-14-17, 29-31,
  38-40, 45 (HTF, internal/external, confluence + duplicate suppression,
  training/live/replay parity, 50D↔60D model rejection, legacy loadable,
  config switch, dataset smoke).
- `tests/helpers/liquidity_fixtures.py` — deterministic ramp/spike/sweep
  fixtures (flat bars are fractal pivots everywhere — avoid).

## Verification run

- Focused liquidity suites: 60 passed.
- Touched-surface regression (scalp_features, phase13, liquidity): 177 passed.
- ruff check + ruff format --check: clean on all touched files.
- mypy: clean on new/changed files. NOTE repo-wide mypy is blocked by a
  PRE-EXISTING parse issue in `src/nexus_scalp/web/server.py:3979` (present at
  HEAD, unrelated to this task — 39 errors at HEAD).
- Full tests/unit: the only failures are in parallel-agent WIP files
  (test_forensic_monitoring_task11.py untracked, TestGovernance70 in
  test_model_governance_phase16.py modified by the 70D agent, test_web_security
  pre-existing). NOT caused by this task.

## Performance (hot path)

The engine is a pure function re-deriving pools per call. Flag is OFF by
default → zero live-impact. When enabled for candidate ingestion, pool state
can be cached incrementally (only newly closed bars confirm swings) — see
"Known limitations" in docs/LIQUIDITY_60D.md. No per-tick DB/network.

## Known limitations / risks

- Session/daily pools confirm at the last completed bar of their window.
- EQH/EQL strength is a RELATIVE softmax share: a lone cluster = 1.0.
- HTF proximity band = 6 ATR (farther pools → 0 contribution by design).
- Sweep detector evaluates the single nearest pool per decision.
- NOT PROVEN: real-broker M1 causality (validated on engineered + synthetic
  M5 corridor only — genuine MT5 bars recommended for the 70D training task).

## What TASK-2 should do next

1. Read `docs/LIQUIDITY_60D.md` (feature contract) + this handoff + the 50D
   snapshot JSON before touching anything.
2. Wire the 60D liquidity layer into the candidate pipeline: build a
   `scalp_liquidity_v1` dataset from a real broker parquet (use
   `build_liquidity_dataset`), train an A/B candidate (50D base vs 60D
   liquidity) via the existing CandidateTrainer — NO promotion.
3. Freeze the liquidity algorithm version (this commit's constants:
   SWING_CONFIRM_BARS=5, EQH_TOLERANCE_ATR=0.30, CONFLUENCE_CUTOFF_ATR=0.75,
   TOUCH_PROXIMITY_ATR=0.05, RECLAIM_FRACTION_ATR=0.15, HTF weights H1 0.9 /
   H4 1.2 / D1 1.6) — the 70D series (TASK-02..07) consumes
   `compute_liquidity_features` as the family producer.
4. Add the golden feature snapshot (docs requested in TASK-1 §47) using the
   existing `tests/golden/` harness once the first real dataset is built.
5. Do NOT modify indices 50..59 semantics without a schema bump
   (INV-009/INV-019); if a formula changes, bump the schema id + golden hash.
6. The parallel 70D swarm (TASK-05-70D-SHADOW, TASK-02-70D-INTEGRATION)
   already treats `scalp_liquidity_v1` as a family producer — coordinate
   schema changes with them via agents/taskboard.md.