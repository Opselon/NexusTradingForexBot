# TASK-03-70D-PARITY — Handoff (AGENT-03)

> Agent: AGENT-03 (Hermes-70D-Parity) · 2026-08-19
> Role: 70D Parity & Dataset Integrity Engineer

## 1. Contract

- Canonical schema: **`scalp_v3`**, dimension **70**
  (BASE 0..49 | NEWS/FAMILY 50..59 | LIQUIDITY 60..69).
- Schema hash (authoritative): `235b8fccc96b7e0e`
  (`features/schema_contract.py::feature_schema_hash`).
- Liquidity names 60..69: `bsl_distance_atr, ssl_distance_atr, eqh_strength,
  eql_strength, htf_liquidity_score, internal_liquidity_distance,
  external_liquidity_distance, liquidity_confluence, liquidity_sweep_state,
  post_sweep_displacement` (canonical `liquidity_engine.as_vector()` order).
- News 10D (50..59): `news_context_v1` fields (0..8, 10) — `news_state`
  at index 59 (AGENT-10 decision).

## 2. Dataset producer path

`model_generation/schema_v2.py::compute_70d_frame`:
- Per row `i` (>= 54): 50D engine on `bars[i-54..i]` (canonical 55-bar
  window, INV-008), liquidity engine on **`bars[0..i]` (FULL causal
  history)** — TASK-03 parity fix, decision_at = bar timestamp,
  mid_price = close, atr = fv.atr_m1; news via `news_bridge.news_context_at`.
- `build_70d_dataset` → DatasetFactory (SampleFactory scalp_v3) →
  `verify_70d_artifact` (70 cols, schema id + hash, finite, [-3,+3],
  no duplicate timestamps/sample ids).

## 3. Live producer path

`LiquidityGovernor.compute_from_engine(bars=completed_bars,
mid_price=tick.bid, atr=fv.atr_m1, decision_at=tick.timestamp)` — full
completed history; same canonical `compute_liquidity_features`.

## 4. Replay producer path

`SampleReplay.replay(dataset_id, sample_id)` reads the dataset artifact
`feat_*` columns → replay vector == dataset vector (by construction).

## 5. Parity result

- **EXACT MATCH** across all scenarios (tolerance 1e-12): short_55, mid_120,
  full_240, deep_400, ramp_300_seed3, ramp_300_seed11 + regression
  4000-bar case.
- Proven bug + fix: dataset builder previously passed ONLY the 55-bar
  window to the liquidity engine while live passed the full history →
  TRAINING != LIVE (measured: eql +0.000111, liquidity_confluence
  -1.056506 at 4000 bars; htf 0.823 vs 0.279 at 4000). Fix: pass
  `all_bars[:i+1]` (full causal history) in BOTH `compute_70d_frame` and
  `compute_liquidity_frame`; 50D window untouched.
- Regression test: `test_03_01b_deep_history_parity_regression` (4000 bars).

## 6. Tests

- `tests/unit/test_70d_parity_task3.py` — TEST-03-01..20 + deep-history
  regression (21 tests).
- Existing parity suites: `test_70d_contract_parity_task3.py`,
  `test_70d_dataset_parity_task3.py`, `test_70d_replay_parity_task3.py` (C4).
- Golden: `tests/golden/70d_liquidity_parity/parity_golden.json` (240 bars,
  seed 7; dataset + live vectors; schema hash; exact_match).

## 7. Performance

- Liquidity calc: ~8-10 ms at 240 bars, ~262 ms at 4000 bars (single
  governor call, per new-bar cadence in live).
- Dataset: 4000 rows × (50D + liquidity + news) bounded by O(n·window).

## 8. Remaining risks

- The TASK-03 Parity agent (C4) and this task both touch schema_v2.py —
  coordinate before merging; do NOT re-apply a 55-bar window (would regress
  parity). PROVEN regression risk documented in the test suite.
- 70D model training is out of scope (TASK-04 benchmark). No auto-promotion.
- `artifacts/validation/*.json` is gitignored by design (runtime artifact).

## 9. EXACT NEXT-AGENT INSTRUCTIONS (TASK-4)

1. Read this handoff + `docs/70D_LIQUIDITY_PARITY_REPORT.md` +
   `docs/agent_handoffs/TASK-03-BASELINE.md`.
2. Do NOT rebuild the liquidity algorithm or the 70D layout — consume
   `features70.assemble_70d` + `schema_contract` + `compute_70d_frame`.
3. Build the 70D candidate dataset via `build_70d_dataset` on real broker
   parquet; run `verify_70d_artifact` before any training.
4. Train the candidate with CandidateTrainer (seed-before-model, BUG-101
   fix), fair A/B/C benchmark equal budgets/seeds/splits (TASK-04 protocol).
5. Keep `resolve_model_compatibility` as the ONLY model-vector gate (no
   padding/truncation).
6. Quality gates + beforePush; report parallel-agent failures separately.
7. Commit agent-labelled in coherent steps; push + verify each.