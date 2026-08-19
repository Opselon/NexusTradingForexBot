# LIQUIDITY 70D IMPLEMENTATION MAP

> Agent: Hermes-LiqOptAgent6 · TASK-06-70D-LIQUIDITY-OPTIMIZATION · 2026-08-19
> Evidence from the ACTUAL committed implementation at HEAD `4455874` (branch `main`).
> This map answers TASK-6 §2: WHERE each liquidity feature lives, who produces it,
> who consumes it, and which runtime/dataset paths carry it.

---

## 1. Implementation status (TASK-6 §0 gate)

```text
IMPLEMENTATION_FOUND
```

The 70D series landed as follows:

| Task | Deliverable | Commit / file | Status |
| :--- | :--- | :--- | :--- |
| TASK-01 | 60D liquidity foundation (engine + schema + tests + docs) | `b91b8c9`, `111f16e` — `src/nexus_scalp/features/liquidity_engine.py` (1319 lines) | ✅ committed |
| TASK-02 | 70D integration (`scalp_v4`, LiquidityGovernor, API, UI) | `handoff TASK-02-70D-INTEGRATION.md` — `features/liquidity_runtime.py` | ✅ |
| TASK-03 | 70D parity (contract=scalp_v3 per decision record; builder) | row claimed; handoff NOT yet present at TASK-6 start (parallel in flight) | ⏳ in flight |
| TASK-04 | 70D model validation protocol + tests | `handoff TASK-04-70D-MODEL-VALIDATION.md` — TEST-70D-MODEL-01..25 (18 pass/8 skip) | ✅ protocol |
| TASK-05 | 70D shadow runtime (observability-only) | `handoff TASK-05-70D-SHADOW.md` — `shadow/shadow70/` | ✅ |

TASK-6 waited for this: optimization is performed on the COMMITTED implementation
(`compute_liquidity_features` at b91b8c9+), NOT on the original specification.

---

## 2. Feature → file map (the ten Liquidity dimensions)

All ten features are produced by the SINGLE canonical function
`compute_liquidity_features(bars, decision_at, mid_price, atr)` in
`src/nexus_scalp/features/liquidity_engine.py`.

| # | Name (as_vector idx) | 70D idx (scalp_v4) | Function / class | Source pools | Tests | Consumers |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 01 | `bsl_distance_atr` | 60 | `compute_liquidity_features` → nearest usable BSL above price | SWING_HIGH, EQH, PDH, PWH, SESSION_HIGH, HTF | test_liquidity_engine_contract.py (liq06/07), causality (liq23) | dataset builder (`schema_v2.compute_liquidity_frame`), governor snapshot, shadow70 |
| 02 | `ssl_distance_atr` | 61 | same → nearest usable SSL below | SWING_LOW, EQL, PDL, PWL, SESSION_LOW, HTF | contract liq06/07 | same |
| 03 | `eqh_strength` | 62 | `equal_high_low_strengths` (softmax over EQH clusters) | confirmed swing highs clustered by ATR tolerance | contract liq08/10/11, causality liq24 | same |
| 04 | `eql_strength` | 63 | same (mirror) | swing lows | contract liq09 | same |
| 05 | `htf_liquidity_score` | 64 | `htf_liquidity_score` (H1/H4/D1 completed buckets) | HTF bucket highs/lows | features liq12/13, causality liq25 | same |
| 06 | `internal_liquidity_distance` | 65 | `internal_external_distances` (inside active range) | all usable pools within [min,max] pool price | features liq14/15 | same |
| 07 | `external_liquidity_distance` | 66 | same (outside range) | pools beyond range | features liq14/15 | same |
| 08 | `liquidity_confluence` | 67 | `liquidity_confluence` (dedup + zone clustering) | usable pools, distinct sources | features liq16/17 | same |
| 09 | `liquidity_sweep_state` | 68 | `detect_reactive_sweep` (penetration + rejection) | nearest usable pool | causality liq18-21/26 | same |
| 10 | `post_sweep_displacement` | 69 | same (rejection-direction displacement) | bars after sweep confirmation | causality liq22/27 | same |

Supporting domain objects (all in `liquidity_engine.py`): `LiquidityPool`
(frozen dataclass), `PoolSide/ PoolSource/ PoolState/ SweepState` (IntEnums),
`LiquidityFeatures` (frozen dataclass carrying pools + `.as_vector()`),
`LIQUIDITY_FEATURE_NAMES` (index-ordered tuple), `LIQUIDITY_FEATURE_DOC`
(per-feature semantics), `detect_confirmed_swings`, `update_pool_states`,
`session_high_low_pools`, `daily_price_pools`, `liquidity_confluence`,
`internal_external_distances`, `htf_liquidity_score`, `detect_reactive_sweep`,
`liquidity_atr`, `build_60d_vector`, `validate_60d_liquidity_vector`.

There is NO separate `LiquidityEngine` / `LiquidityDetector` class — the
"state machine" is `PoolState` + `update_pool_states`; the "detector" is
`detect_reactive_sweep`.

---

## 3. Feature registry (schema) map

| Schema id | Dimension | Role | Registry location |
| :--- | :--- | :--- | :--- |
| `scalp_v1` | 50 | ACTIVE production contract (Base 0..49) | `features/schema.py` |
| `scalp_v2` | 60 | TASK-5 momentum extras 50..59 (candidate) | `features/schema.py` |
| `scalp_liquidity_v1` | 60 | TASK-1 liquidity at 50..59 (candidate) | `features/schema.py` |
| `scalp_v4` | 70 | TASK-2 70D integration contract (Base 0..49 \| family 50..59 \| liquidity 60..69) | `features/schema.py` |
| `scalp_v3` | 350 | TASK-3 70D canonical per its decision record (Base \| News 10D 50..59 \| Liquidity 60..69) — NOTE: 350 was pre-existing; TASK-3 decision re-purposes the id semantics | `features/schema.py` |

Manifest support: `governance/verify.py` (liquidity_algorithm_version,
liquidity_contract checks), `model_generation/models.py` manifest fields.

---

## 4. Dataset path

- Producer: `model_generation/schema_v2.py::compute_liquidity_frame` (per-row
  causal 55-bar window, synthetic tick @ close, `spread=0.20`) →
  `build_liquidity_dataset` → `verify_liquidity_artifact`.
- Real raw source used for measurement: `data/raw/XAUUSD_M5.parquet` (100,000
  M5 bars, 2025-03-12 → 2026-08-17).
- Existing 50D/60D dataset artifacts: `ds_cb30f87520e9e6a4` (scalp_v1),
  `ds_b64513f79687824a` (scalp_v2), 99,946 rows each (70/15/15 split).
- No 70D artifact exists yet (TASK-3/4 pending).

## 5. Runtime path

- Live: `application/live_engine.py` → governor (`features/liquidity_runtime.py`)
  snapshot on new bar (pure numpy, info-only, failure-isolated; INV-020).
- Shadow: `shadow/shadow70/` (observability-only; no policy/risk/order path).
- Replay: `model_generation/replay.py::SampleReplay` reads `feat_*` columns
  from any dataset frame (schema-agnostic — works for 60D/70D).

## 6. Tests (all green at TASK-6 start)

| Suite | Count | Result |
| :--- | :--- | :--- |
| test_liquidity_engine_contract.py | 27 | ✅ |
| test_liquidity_engine_causality.py | 17 | ✅ |
| test_liquidity_engine_features.py | 16 | ✅ |
| test_liquidity_runtime_integration_phase18.py | 30 | ✅ |
| test_shadow70_runtime.py / _safety.py / _health_drift.py | 52 | ✅ (after tests/conftest.py fix, commit 2a30b14) |
| test_70d_model_validation_task4.py | 26 (18 pass / 8 skip) | ✅ skips truthful |
| integration/test_liquidity_api.py | 9 | ✅ (TASK-2, transient) |

## 7. Runtime telemetry hooks

- `/api/liquidity/state|features`, `POST /api/liquidity/toggle`, Web panel
  (TASK-2). Shadow drift/health monitors (TASK-5).

---

## 8. Baseline evidence (TASK-6 §4) — REAL data

Source: `data/raw/XAUUSD_M5.parquet` first 30k bars → 29,946 feature rows.
Latency (liquidity call only): p50 1.70 ms, p95 3.02 ms, max 12.93 ms.

Per-feature distribution summary (full JSON: `docs/LIQUIDITY_70D_GOLDEN_BASELINE.json`):

| feature | min | max | mean | median | std | p01 | p95 | p99 | zero% | sat% | unique |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| bsl_distance_atr | 0.001 | 3.000 | 1.563 | 1.423 | 1.035 | 0.025 | 3.000 | 3.000 | 0.0 | 20.3 | 23861 |
| ssl_distance_atr | 0.002 | 3.000 | 1.742 | 1.719 | 1.050 | 0.029 | 3.000 | 3.000 | 0.0 | 27.5 | 21700 |
| eqh_strength | 0.000 | 1.000 | 0.851 | 0.879 | 0.145 | 0.516 | 1.000 | 1.000 | 0.5 | 0.0 | 26005 |
| eql_strength | 0.000 | 1.000 | 0.585 | 0.680 | 0.368 | 0.000 | 1.000 | 1.000 | 0.9 | 0.0 | 29579 |
| htf_liquidity_score | -3.000 | 3.000 | 0.262 | 0.953 | 2.321 | -2.984 | 2.949 | 2.990 | 0.0 | ~0 | 29938 |
| internal_liquidity_distance | 0.000 | 3.000 | 1.019 | 0.696 | 0.931 | 0.009 | 3.000 | 3.000 | 0.3 | 9.3 | 27034 |
| external_liquidity_distance | 0.000 | 3.000 | 1.727 | 1.724 | 0.974 | 0.019 | 3.000 | 3.000 | 0.5 | 20.2 | 23757 |
| liquidity_confluence | 1.893 | 3.000 | 2.750 | 2.800 | 0.225 | 1.943 | 3.000 | 3.000 | 0.0 | 34.3 | **11** |
| liquidity_sweep_state | -2.000 | 2.000 | 0.228 | 1.000 | 1.296 | -2.000 | 2.000 | 2.000 | 0.0 | 0.0 | **4** |
| post_sweep_displacement | 0.000 | 3.000 | 0.041 | 0.000 | 0.200 | 0.000 | 0.262 | 1.035 | 92.6 | 0.0 | 2213 |

Redundancy vs Base 50D (max |Pearson|; 11,946 rows):
`bsl≈base_45 (+0.72)`, `ssl≈base_44 (+0.73)`, `htf≈base_36 (+0.84)`,
`eql≈base_33 (+0.43)`, `internal≈base_12 (-0.22)`, others < |0.36|.

Within-family: `bsl↔htf (-0.57)`, `ssl↔htf (+0.55)`, `sweep_state↔displacement (-0.34)`.

---

## 9. What TASK-6 must NOT touch (contract)

- Base 50D (feat_0..49) — byte-identical (INV-70D-001, TEST-60D-BASE-01).
- News family / `news_context_v1` — independent 12D stream, untouched.
- `scalp_v2` TASK-5 momentum extras — untouched.
- Execution / risk / policy / labels / Triple-Barrier — untouched.
- The ACTIVE live schema stays `scalp_v1`; optimization is candidate-only.


---

## 10. Optimization candidate (TASK-6, ADDENDUM)

| Item | Value |
| :--- | :--- |
| Candidate module | `src/nexus_scalp/features/liquidity_engine_opt.py` |
| Version | `liquidity-v1.1` (`LIQUIDITY_ALGORITHM_VERSION`) |
| Frozen baseline | committed `liquidity_engine.py` v1 — UNTOUCHED |
| Fixes | BUG-106 (EQH/EQL price-aware closeness), BUG-107 (sweep relevance gate) |
| Improvements | confluence range usage (34%→5% saturation, 11→2,751 unique), EQH info content |
| Parameterized (bounded) | eqh_tolerance_atr, confluence_cutoff_atr, reclaim_fraction_atr, sweep_relevance_atr, htf_proximity_atr, sweep_window_bars |
| Tests | `tests/unit/test_liquidity_optimization_phase19.py` (TEST-LIQ-OPT-01..28) |
| Golden | `docs/LIQUIDITY_70D_GOLDEN_BASELINE.json` |
| Report | `docs/LIQUIDITY_70D_OPTIMIZATION_REPORT.md` |
| Handoff | `docs/agent_handoffs/TASK-06-70D-LIQUIDITY-OPTIMIZATION.md` |

The candidate is NOT wired into live_engine/governance/shadow (TEST-LIQ-OPT-22).
Promotion requires the TASK-4 fair benchmark on a real 70D artifact.
