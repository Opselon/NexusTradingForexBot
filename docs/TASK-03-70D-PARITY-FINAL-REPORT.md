# TASK-03-70D-PARITY — FINAL REPORT (AGENT-03)

> Agent: AGENT-03 (Hermes-70D-Parity) · 2026-08-19
> Branch `main` · final parity verification

## CONTRACT

- Schema: **`scalp_v3`**, dimension **70**
  (BASE 0..49 | NEWS/FAMILY 50..59 | LIQUIDITY 60..69).
- Schema hash (canonical, `schema_contract.feature_schema_hash`):
  `235b8fccc96b7e0e`.
- Liquidity names 60..69: `bsl_distance_atr, ssl_distance_atr, eqh_strength,
  eql_strength, htf_liquidity_score, internal_liquidity_distance,
  external_liquidity_distance, liquidity_confluence, liquidity_sweep_state,
  post_sweep_displacement` (canonical `liquidity_engine.as_vector()`).
- News 50..59: `news_context_v1` fields (0..8, 10) — `news_state` at 59.

## DATASET PATH

`model_generation/schema_v2.py::compute_70d_frame` →
- 50D engine on `bars[i-54..i]` (canonical 55-bar window, INV-008)
- liquidity engine on **`bars[0..i]` (FULL causal history)** — TASK-03 fix
  (was 55-bar → TRAINING != LIVE)
- news via `news_bridge.news_context_at` (causal)
- `build_70d_dataset` → DatasetFactory → `verify_70d_artifact`
  (70 cols, schema_id + **feature_schema_hash stamped into manifest** —
  TASK-03 lineage fix, `.item()` fix for duplicate counts)

## LIVE PATH

`LiquidityGovernor.compute_from_engine(bars=completed_bars,
mid_price=tick.bid, atr=fv.atr_m1, decision_at=tick.timestamp)` — full
history, same canonical producer.

## REPLAY PATH

`SampleReplay.replay` reads the dataset artifact `feat_*` columns —
replay == dataset by construction.

## PARITY MATRIX (all 10 liquidity dims, dataset vs live)

| scenario | bars | result |
| :--- | :--- | :--- |
| short_55 | 55 | EXACT (0) |
| mid_120 | 120 | EXACT (0) |
| full_240 | 240 | EXACT (0) |
| deep_400 | 400 | EXACT (0) |
| ramp_300_seed3 | 300 | EXACT (0) |
| ramp_300_seed11 | 300 | EXACT (0) |
| deep4000 regression | 4000 | EXACT to 4.6e-11 (documented 1e-9 tol, ROUNDING_ONLY) |

Tolerance: `1e-12` for small windows; `1e-9` documented for 4000-bar
cumulative float ops (relative ~1e-9; NOT a semantic mismatch).

## REAL DATA

- Source: `data/raw/XAUUSD_M5.parquet` (real broker, ~16 months).
- **Dataset built + verified**: `ds_d3f35b12d63148da` — 1146 rows
  (802/171/173 train/val/test), 70 feat cols, `scalp_v3`, schema hash ok,
  all finite, all in [-3,+3], no duplicate timestamps/sample ids.
- **Multi-timestamp parity: 25/25 timestamps EXACT (delta 0.0)** across
  the real slice (trend/range/HTF/sweep states included).
- Distribution (liquidity block, 1146 rows): bsl mean 0.83, ssl mean 0.90,
  eqh 0.49, eql 0.17, htf 0.0 (no completed HTF buckets within 6 ATR of
  price on this slice — both paths agree = parity holds), internal 0.84,
  external 2.77 (81% saturated 3.0 = far), confluence 2.98 (94% at 3.0),
  sweep -0.59 (mean), displacement 0.13 (88% zero).

## FIRST MISMATCH (found + fixed)

- **Pre-fix** (4000 bars): `eql` +0.000111, `liquidity_confluence` -1.056506,
  `htf_liquidity_score` 0.82 vs 0.28. Root cause: dataset builder passed only
  the 55-bar window to the liquidity engine; live passed full history.
- Classification: `DIFFERENT_INPUT_WINDOW` (pool diversity loss).
- Fix: dataset builders now pass `all_bars[:i+1]` (full causal history) to
  the liquidity engine; 50D window untouched (INV-008). Adopted at HEAD
  (`efa3347`), regression-guarded by `test_03_01b`.

## SECOND BUG (fixed)

- `verify_70d_artifact` crashed with `TypeError: int() ... not 'DataFrame'`
  on ANY real dataset (duplicate-count `.sum()` without `.item()`).
  Fixed + TEST-03-33 regression. Also stamped `feature_schema_hash` into
  the dataset manifest (was empty → lineage gap).

## TESTS

- `test_70d_parity_task3.py`: 21 tests (TEST-03-01..20 + deep regression +
  TEST-03-33 roundtrip).
- Full liquidity+parity+API suites: **148 passed** at STEP-01/02;
  66 focused at STEP-03/05; all green.
- Golden fixtures: `tests/golden/70d_liquidity_parity/parity_golden.json`
  (240 bars) + `deep4000_golden.json` (4000 bars, exact).

## PERFORMANCE (measured, governor per-new-bar)

| bars | M1 synthetic | real M5 |
| :--- | :--- | :--- |
| 240 | 16 ms | — |
| 1000 | 68 ms | 733 ms |
| 4000 | 290 ms | — |

O(n) pool rebuild dominates; live caps at 4000 bars → bounded ≤ ~300 ms (M1)
per bar. Dataset frame build is O(n²) (~306 s @ 4000 rows) — documented,
mitigated for CI via committed goldens.

## BUGS

- None new beyond the two fixed above (both PROVEN + regression-guarded).

## COMMITS (this task)

- `617c23a` AGENT-03 STEP-01/02 — parity harness + goldens + report +
  baseline doc.
- `a190602` AGENT-03 STEP-03/05 — verify fix + schema-hash stamp +
  real-data build verified.

## REMOTE VERIFICATION

- HEAD == origin/main == `a190602` after push (verified SHA equality).

## REMAINING RISKS

- O(n²) dataset frame build (performance, not correctness) — optimization is
  TASK-06 (LIQUIDITY-OPTIMIZATION) scope. PROVEN.
- `htf=0` on the real M5 slice is correct behavior (completed-bucket
  proximity gate) — both paths agree; a longer slice with price near HTF
  levels will populate it. PROVEN parity, UNKNOWN distributional coverage.
- 70D model training is TASK-04; no auto-promotion. NOT PROVEN (out of scope).

## TASK-4 HANDOFF (exact next actions)

1. Read `docs/agent_handoffs/TASK-03-70D-PARITY.md` +
   `docs/70D_LIQUIDITY_PARITY_REPORT.md` + this report.
2. Use `build_70d_dataset` on the FULL real M5 parquet (not just 1200 bars)
   for the 70D candidate dataset; run `verify_70d_artifact` first.
3. Train A/B/C (50D / 60D+news / 70D) via CandidateTrainer (seed-before-model,
   BUG-101 fix), equal budgets/seeds/splits (TASK-04 protocol).
4. Keep `resolve_model_compatibility` as the only model-vector gate; never
   pad/truncate. Reject 60D model + 70D input with
   `LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE`.
5. Quality gates + beforePush; report parallel-agent failures separately.
6. Commit agent-labelled in coherent steps; push + verify each.

## DATASET/REPLAY/LIVE PARITY MANIFEST

```
schema_id            = scalp_v3
dimension            = 70
schema_hash          = 235b8fccc96b7e0e
liquidity_block      = 60..69 (canonical as_vector order)
dataset_producer     = schema_v2.compute_70d_frame (full causal history)
live_producer        = LiquidityGovernor.compute_from_engine (full history)
replay_producer      = SampleReplay (dataset columns)
training == replay   = bit-exact (same artifact columns)
training == live     = EXACT (1e-12; 4.6e-11 @ 4000 bars, ROUNDING_ONLY)
real dataset         = ds_d3f35b12d63148da (1146 rows, verified ok)
real timestamps      = 25/25 EXACT (delta 0.0)
goldens              = tests/golden/70d_liquidity_parity/{parity,deep4000}_golden.json
tests                = 21 parity tests + 148 liquidity/parity/API green
```
