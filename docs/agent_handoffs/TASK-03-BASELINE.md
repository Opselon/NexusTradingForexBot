# TASK-03-BASELINE — 70D Parity Forensic Map

> Agent: AGENT-03 (Hermes-70D-Parity) · 2026-08-19
> Base: HEAD `75843d4` (before TASK-03 edits) · Branch `main`
> Purpose: document the ACTUAL current paths — dataset producer, live
> runtime, replay — layer by layer, before/alongside the parity harness.

---

## 0. Canonical 70D contract (source of truth)

- Schema id: `scalp_v3` (dimension `70`) — registered in
  `src/nexus_scalp/features/schema.py` (TASK-03 Parity commit `09dd0bc`
  superseded the old 350D forward-declaration).
- Authoritative names/hash: `src/nexus_scalp/features/schema_contract.py`
  (`canonical_feature_names()`, `feature_schema_hash()`,
  `validate_70d_vector()`). Hash at TASK-03 start: `235b8fccc96b7e0e`.
- Layout: base 0..49 | news/family 50..59 | liquidity 60..69.
  News 10D = `news_context_v1` fields `(0..8, 10)` — i.e. `news_state` at
  index 59 (AGENT-10 decision), NOT the blind first-10 slice.
  Liquidity 10D = `liquidity_engine.as_vector()` order
  (`bsl_distance_atr … post_sweep_displacement`).
- Assembly: `src/nexus_scalp/features/features70.py::assemble_70d` — the
  single snapshot constructor (strict: never pads/truncates; neutral blocks
  only with explicit FEATURE_* status).

## 1. Dataset producer path

```
Historical OHLCV (raw polars frame, time/OHLC/tick_volume)
  -> model_generation/schema_v2.py::compute_70d_frame
       per row i >= min_bars-1 (55):
         window50 = bars[i-54 .. i]          (50D engine canonical window)
         liq_window = bars[0 .. i]           (FULL causal history <= T)
               ^^^ TASK-03 FIX (was: 55-bar window -> TRAINING != LIVE)
       engine.compute_from_bars(window50, synthetic tick@close)
         -> x50 = fv.to_tensor_input()       (feat_0..49)
       compute_liquidity_features(liq_window, decision_at=T,
                                  mid_price=close, atr=fv.atr_m1)
         -> liq10 (feat_60..69)
       news bridge (optional) -> news10 (feat_50..59)
  -> build_70d_dataset (DatasetFactory, SampleFactory scalp_v3)
  -> verify_70d_artifact (70 cols, schema id, hash, finite, [-3,+3])
```

Per-layer record:

| layer | file/function | input | output | timestamp semantics |
| :--- | :--- | :--- | :--- | :--- |
| OHLCV | raw frame | broker bars | polars frame | bar timestamps UTC |
| 50D engine | `scalp_features.ScalpFeatureEngine.compute_from_bars` | 55-bar window + tick | FeatureVector → x50 | completed bars only |
| liquidity | `liquidity_engine.compute_liquidity_features` | full causal history + decision_at + mid + atr | LiquidityFeatures.as_vector() | bars <= decision_at; swing confirmed at +5 |
| news | `news_bridge.news_context_at` + `features70.news_10d_from_context` | news frame + ts | 10D block | events <= ts |
| assembly | `features70.assemble_70d` | base+news+liquidity | Feature70Snapshot (70) | snapshot timestamp |

Normalization/clipping/missing for the liquidity block: values are the
canonical `as_vector()` (already normalized + clipped [-3,+3] centrally by
the engine; missing defaults `3.0` far / `0.0` neutral).

## 2. Live runtime path

```
Completed bars (aggregator, up to 4000)
  -> LiveEngine._process_tick_pipeline (new-bar cadence)
       fv = feature_engine.compute_from_bars(completed_bars, tick)
       liquidity_governor.compute_from_engine(
           bars=completed_bars,            (FULL history)
           mid_price=tick.bid,
           atr=fv.atr_m1,
           decision_at=tick.timestamp)
  -> LiquidityGovernor._last_snapshot (LiquiditySnapshot, 10D)
  -> report()/snapshot_payload() -> /api/liquidity/* + /api/status
```

Pre-TASK-03 parity finding: live passed FULL completed history; dataset
passed only 55 bars → HTF/session/confluence diverged (htf 0.82 vs 0.28,
confluence 3.0 vs 1.94). FIXED in schema_v2 (both 60D & 70D builders now
pass `bars[0..i]`).

## 3. Replay path

- `model_generation/replay.py::SampleReplay.replay(dataset_id, sample_id)`
- Reads the dataset artifact rows (`feat_*` columns) directly → the replay
  vector IS the dataset vector by construction.
- `sample_factory.SampleFactory.build_samples` also reads `feat_*` columns
  per `feature_schema.dimension`.

## 4. Parity harness (TASK-03)

- `tests/unit/test_70d_parity_task3.py` — TEST-03-01..20 (exact 10D parity,
  ordering, hash, HTF/swing/EQH/confluence/sweep/displacement/missing/
  clipping, 50D + 50..59 regressions, cache ON/OFF, golden agreement,
  vector-hash agreement).
- `tests/golden/70d_liquidity_parity/parity_golden.json` — deterministic
  golden (240 bars, seed 7): dataset + live vectors, schema hash,
  exact_match.
- `scripts/gen_70d_parity_report.py` →
  `artifacts/validation/70d_liquidity_parity.json` +
  `docs/70D_LIQUIDITY_PARITY_REPORT.md`.

## 5. Result so far

- All 6 scenarios in the report: EXACT MATCH (tolerance 1e-12).
- 84 tests across parity + liquidity + API suites pass.
- Golden: `exact_match: true`, schema hash `235b8fccc96b7e0e`.