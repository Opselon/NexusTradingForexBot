# LIQUIDITY 60D FORENSIC BASELINE

> Agent: Hermes-LiquidityFoundation · TASK-01-60D-LIQUIDITY · 2026-08-19
> Scope: FORENSIC ONLY (PHASE A). No production logic was modified.
> Source of truth: executable repository code at HEAD `4001e4c`, branch `main`.
> This report maps WHERE every piece of the 50D feature contract lives and
> HOW it is built, so the 60D Liquidity Intelligence layer can extend it
> without mutating semantics.

---

## 0. Executive summary

- The authoritative 50D contract is the **`FEATURE_NAMES` tuple in
  `src/nexus_scalp/features/scalp_features.py`** (lines 147-198), cross-checked
  at import time against the schema registry dimension
  (`features/schema.py::active_dimension()`).
- The final vector is assembled in
  **`FeatureVector.to_tensor_input()`** which applies the **[-3,+3] clip /
  NaN→0 sanitizer** centrally (lines 349-461).
- Feature-vector computation lives in
  **`ScalpFeatureEngine.compute_from_bars()`** (lines 501-971) — the single
  function used by live engine, dataset builder, and replay.
- There is exactly **one** canonical ATR (14-period, mean true-range over the
  last 14 completed bars, floor `safe_atr = max(atr, 0.20)`) — computed
  inline in `compute_from_bars()` and mirrored by `_atr()` in
  `features/schema_augment.py`.
- **Existing 60D conflict (must be designed around):** TASK-5 already
  registered `scalp_v2` = `scalp_v1` + 10 momentum/regime features
  (`features/schema_augment.py::compute_60d_extras`) occupying feat_50..59,
  wired into governance (`alignment.py`, `load_gate.py`), shadow runtime and
  live_engine. This Liquidity task defines a DIFFERENT 10D at the same
  indices → the new liquidity layer MUST use its own schema id to avoid
  mutating the TASK-5 contract.
- Existing 60D unit-coverage: `tests/unit/test_model_generation_phase13.py`
  covers `compute_60d_extras`; `tests/golden/golden_50d.json` +
  `golden_60d_extras.json` are the golden fixtures; no liquidity features or
  pools exist anywhere in the codebase today.

---

## 1. Where is the authoritative 50D feature contract?

| Answer | Location |
| :--- | :--- |
| Authoritative feature **names + ordering** | `src/nexus_scalp/features/scalp_features.py` — `FEATURE_NAMES` tuple (lines 147-198), 50 entries, feat_0..feat_49 |
| Runtime cross-check against registry | Same file, module import: `if NUM_FEATURES != active_dimension(): raise RuntimeError` (lines 200-209) |
| Schema registry (dimension + schema ids) | `src/nexus_scalp/features/schema.py` — `FeatureSchema` dataclass, `FEATURE_SCHEMAS` registry, `ACTIVE_SCHEMA_ID = "scalp_v1"` (line 95) |
| Contract registry entry | `agents/contracts.md` row `FEATURE_VECTOR_50D v1 (schema-controlled) — features/scalp_features.py` |

The 50D names (indices) verified from source:

```
feat_0  upper_wick_ratio        feat_25 lag_1_clv
feat_1  lower_wick_ratio        feat_26 fvg_sig (fvg_depth)
feat_2  body_to_range_ratio     feat_27 order_block_type (ob_strength)
feat_3  is_doji                 feat_28 choch_sig
feat_4  pinbar_sig              feat_29 breakout_sig
feat_5  engulfing_sig           feat_30 norm_tk_diff
feat_6  close_location_value    feat_31 tk_cross_signal
feat_7  consecutive_momentum    feat_32 kumo_sig
feat_8  norm_displacement       feat_33 norm_kumo_width
feat_9  rapid_reversal_spike    feat_34 norm_rsi
feat_10 dist_to_swing_high_20   feat_35 dist_to_ema_21
feat_11 dist_to_swing_low_20    feat_36 dist_to_ema_50
feat_12 price_compression_ratio feat_37 cross_asset_z_score
feat_13 extreme_sig             feat_38 norm_dist_to_tenkan
feat_14 stop_hunt_depth         feat_39 norm_dist_to_kijun
feat_15 liquidity_sweep_signal  feat_40 htf_h4_trend
feat_16 session_tokyo           feat_41 htf_h1_momentum
feat_17 session_london          feat_42 htf_m30_structure
feat_18 session_ny              feat_43 htf_m15_confirmation
feat_19 session_overlap_ln      feat_44 support_zone_dist
feat_20 lag_1_log_return        feat_45 resistance_zone_dist
feat_21 lag_2_log_return        feat_46 feat_ob_valid_bos
feat_22 lag_3_log_return        feat_47 feat_ob_equilibrium_ratio
feat_23 lag_1_atr_ratio         feat_48 feat_ob_liquidity_swept
feat_24 lag_1_volume_z          feat_49 feat_ob_fib_50_60_alignment
```

## 2. Where is the FeatureVector / domain object?

| Answer | Location |
| :--- | :--- |
| `FeatureVector` domain model | `src/nexus_scalp/features/scalp_features.py` lines 218-461 — frozen Pydantic `BaseModel` (`ConfigDict(frozen=True)`), one field per feature group |
| Tensor conversion | `FeatureVector.to_tensor_input()` (lines 349-461) |
| Cold-start fallback | `ScalpFeatureEngine._cold_start_vector()` (lines 973-1043) — `< 55` completed bars |

`FeatureVector` is the "domain object" — it holds the raw computed values
(not the sanitized tensor); `to_tensor_input()` derives the 50D tensor.

## 3. Where is the final feature ordering defined?

`FEATURE_NAMES` tuple ordering IS the final ordering. `to_tensor_input()`
builds `raw_features` in that exact order and asserts length 50. The schema
registry's `FeatureSchema.columns` (`feat_0 .. feat_{n-1}`) describes the
TRAINING column names, which map 1:1 to `FEATURE_NAMES` order.

## 4. Where is the final vector assembled?

`FeatureVector.to_tensor_input()` (lines 349-461):
- `raw_features` list of 50 floats
- per-value: `math.isnan/isinf → 0.0`, else `max(-3.0, min(3.0, float(val)))`
- `len(sanitized_features) != 50 → RuntimeError`

## 5. Where does normalization occur?

Per-feature inside `compute_from_bars()` (e.g. `norm_displacement =
live_tick_displacement / safe_atr`), plus the ATR-ratio convention
(`safe_atr = max(atr_m1, 0.20)`). Final clamping to [-3,+3] is centralized in
`to_tensor_input()` (one place, not per feature).

## 6. Where does clipping occur?

`to_tensor_input()` lines 448-453 — `max(-3.0, min(3.0, float(val)))`.
This is the SINGLE clip point for all 50 features (plus the same
[-3,+3] convention applied to the 10 `compute_60d_extras` in
`features/schema_augment.py` line 353).

## 7. Where is ATR calculated?

| Answer | Location |
| :--- | :--- |
| Canonical ATR (hot path) | `compute_from_bars()` lines 528-537: `tr = max(h-l, |h-cprev|, |l-cprev|)` over the last 14 bars, `atr_m1 = mean(tr)`, floor `safe_atr = max(atr_m1, 0.20)` |
| ATR mirror (offline extras) | `features/schema_augment.py::_atr()` lines 189-208 — identical mean-TR-14 semantics over arrays |
| ATR consumption (50D features) | feat_8, 10, 11, 14, 22, 23, 35, 36, 38, 39, 40-43 (ratios) |

There is no separate `atr_m1` module: ATR is computed inline where the
engine runs. `FeatureVector.atr_m1` carries it into the domain object.

## 8. Where are swings detected?

| Answer | Location |
| :--- | :--- |
| Swing high/low (feat_46-49 SMC path) | `compute_from_bars()` lines 844-853: fractal pivot over ±5 bars window (`for i in range(5, len-5): highest high / lowest low`) |
| Swing fallback | `swing_highs = [(len-25, max(highs))]` when none found |
| S/R levels (feat_44-45) | `find_support_resistance_levels()` lines 102-141 — fractal ±window(3) + 0.05% level cleanup |
| Coarse 20-bar swing (feat_10-11) | inline `max(highs[-20:-1])` / `min(lows[-20:-1])` (lines 587-591) |

IMPORTANT for liquidity: the ±5 fractal requires 5 future bars to confirm a
swing. The 50D pipeline computes swings with the FULL window (completed bars
only), so a swing at bar i is only visible after bar i+5 closes. The
liquidity layer MUST encode this as `candidate_at` vs `confirmed_at`.

## 9. Where are BOS / CHOCH / structure states detected?

| Answer | Location |
| :--- | :--- |
| CHOCH | `compute_from_bars()` lines 668-672: `choch_bullish = is_downtrend and mid_price > swing_high_20_choch` (and mirror) |
| BOS | lines 873-883: order-block + close > prior swing high / < prior swing low → `feat_ob_valid_bos` |
| Breakout | lines 674-675: `broke_prev_high/low` vs last completed high/low |
| Order block | lines 654-659: engulfing rule → `order_block_type` |

## 10. Where is session state calculated?

| Answer | Location |
| :--- | :--- |
| Session flags (feat_16-19) | `compute_from_bars()` lines 617-628 — UTC hour of the tick: tokyo 0-8, london 7-15, ny 13-21, overlap 13-15 |
| Session encoding (60D extra) | `schema_augment.py::session_phase_encoding()` lines 174-186 |

## 11. Where are M15/M30/H1/H4 values generated?

| Answer | Location |
| :--- | :--- |
| MTF aggregation | `aggregate_bars(m1_bars, period_minutes)` — `scalp_features.py` lines 40-99 — intraday timeframe bucket alignment with UTC minute buckets; frames M15/M30/H1/H4 from completed M1 bars |
| HTF feature consumption | lines 727-841: htf_h4_trend / htf_h1_momentum / htf_m30_structure / htf_m15_confirmation / htf_h1_atr_ratio / htf_h4_atr_ratio |

CRITICAL FOR LIQUIDITY: `aggregate_bars` marks the LAST aggregated bucket
`is_complete=True` even if it is still forming when M1 history ends (the
bucket closes only when the next bucket starts). The liquidity layer must
EXCLUDE the currently-forming HTF bucket when computing HTF liquidity
(only buckets whose end <= decision T are usable).

## 12. Where are support/resistance levels generated?

`find_support_resistance_levels()` (lines 102-141) — ±3 fractal over the
last 50 bars, 0.05%-cleanup, consumed by feat_44/45.
NOTE: these are static price levels, NOT confirmed-liquidity pools.

## 13. Where is SMC represented?

| Answer | Location |
| :--- | :--- |
| Order blocks | feat_27 (order_block_type ±1), ob_strength = order_block_type × (volume/avg) |
| BOS flag | feat_46 (feat_ob_valid_bos) |
| Equilibrium | feat_47 (ob_equilibrium_ratio, 50% impulse) |
| Liquidity swept | feat_48 (feat_ob_liquidity_swept) — 0/1 |
| Fib 50-60 alignment | feat_49 (feat_ob_fib_50_60_alignment) |
| Stop-hunt depth | feat_14 (stop_hunt_depth — sweep depth when liquidity_sweep_signal ±1) |
| Sweep signal | feat_15 (liquidity_sweep_signal ±1) |

SMC exists as SIGNALS inside the 50D, but there is NO pool/level lifecycle
(no confirmed vs candidate, no state machine). This is the gap the
Liquidity layer fills.

## 14. Where is the training-time feature vector created?

| Answer | Location |
| :--- | :--- |
| 50D training frame | `model_generation/schema_v2.py::compute_60d_frame` (lines 79-181) — for each row i ≥ 55, runs `ScalpFeatureEngine.compute_from_bars(bars[i-54..i], synthetic tick@close)` |
| Sample construction | `model_generation/sample_factory.py::SampleFactory.build_samples` (lines 174-285) — `feature_vector` from `feat_*` columns per `feature_schema.dimension` |
| 50D dataset (Data Gate) | `ds_cb30f87520e9e6a4` (seed 42, chronological 70/15/15 split) — documented in TASK-5 handoff |

## 15. Where is the live/inference feature vector created?

| Answer | Location |
| :--- | :--- |
| Live engine | `application/live_engine.py` — `self.feature_engine = ScalpFeatureEngine(symbol)` (line 240); `FEATURE_DIM/FEATURE_COLS/FEATURE_SCHEMA_ID` resolved from `features/schema` (lines 169-172); inference call `compute_from_bars(completed_bars, last_tick)` (line 1510) |
| Shadow/challenger | `governance/alignment.py::challenger_input_for()` — champion 50D + optional 10 60D extras + news |

## 16. Are training and live implementations actually the same?

**YES for the 50D base.** Both call `ScalpFeatureEngine.compute_from_bars()`
(train: schema_v2 `compute_60d_frame`; live: live_engine line 1510). The only
difference is the synthetic tick (bid=ask=close, spread 0.20) in training vs
the real live tick. Ordering is identical (FEATURE_NAMES contract).

**NO for the 60D extras.** TASK-5 live path (`live_engine.py` ~2879-2923)
calls `compute_60d_extras` from the shadow path only; the 60D training path
(`schema_v2.compute_60d_frame`) computes the same function — so the SAME
function is reused, but the plumbing differs (shadow-only live vs full-frame
train). The liquidity layer must follow the schema_v2 pattern for training
and expose the same canonical function for live.

## 17. Where does the schema/version get persisted?

| Answer | Location |
| :--- | :--- |
| Schema registry | `features/schema.py::FEATURE_SCHEMAS` — `FeatureSchema(schema_id, dimension, description, is_active, supersedes)` |
| Dataset manifest | `model_generation/models.py::DatasetManifest` — `feature_schema_id` (line ~200) |
| Model manifest | `ModelManifest` — `feature_schema_id`, `feature_dimension`, `feature_schema_version` |
| Live wiring | `LiveEngine.FEATURE_SCHEMA_ID / FEATURE_DIM` resolved from the registry |

## 18. Which tests prove 50D parity?

| Test | Covers |
| :--- | :--- |
| `tests/unit/test_scalp_features.py` | cold-start 50D, dynamic z-score, tensor length 50, feature values at known indices |
| `tests/unit/test_scalp_features_forensic_bug082.py` | BUG-082 feature checks |
| `tests/unit/test_model_generation_phase13.py` | schema/dataset/model manifest contracts; `augment_50d_to_60d` strictness |
| `tests/unit/test_model_governance_phase16.py` | load gate, golden 50D/60D parity, schema mismatch rejection |
| `tests/golden/golden_50d.json` + `golden_60d_extras.json` | canonical vectors (content-hashed) |

No test currently asserts "first 50 dims of a 60D vector == the 50D vector
from the same input" — that is exactly TEST-60D-BASE-01 the Liquidity task
creates.

## 19. Which functions are reusable for liquidity?

- `aggregate_bars()` — HTF bar construction (M15/M30/H1/H4) from completed M1 bars
- `find_support_resistance_levels()` — static S/R levels (for EXTERNAL candidates, use with caution: unconfirmed)
- `ScalpFeatureEngine.compute_from_bars()` — produces atr_m1 + 50D base; reusable for the 60D composite
- `schema_augment._safe_div()` — safe division helper (small, private)
- `schema.py::FEATURE_SCHEMAS.register()` — schema registration path

## 20. Which functions currently duplicate logic?

| Duplication | Where |
| :--- | :--- |
| ATR: mean-TR-14 | `compute_from_bars()` inline (lines 528-537) AND `schema_augment._atr()` (lines 189-208) — near-identical, independent implementations |
| Swing detection | ±5 fractal in `compute_from_bars()` (lines 844-853) AND ±3 fractal in `find_support_resistance_levels()` (lines 102-141) — different windows, both "swing"-named |
| Session flags vs phase encoding | `compute_from_bars()` (hour→4 bools) vs `schema_augment.session_phase_encoding()` (hour→1 float) — same hour logic twice |

The Liquidity layer must NOT add a third duplication. It will reuse
`aggregate_bars()` + a confirmed-swing detector built on the ±5 fractal
semantics, and use the 50D engine's ATR (passed in or recomputed with the
exact same formula).

## 21. Missing-value conventions (existing)

- NaN/Inf → 0.0 (50D sanitizer), then clip [-3,+3]
- cold start (< 55 bars) → FeatureVector with neutral defaults
  (`atr_m1=1.50`, tenkan=kijun=span=mid_price, rsi=50, dist=0/3.0, etc.)
- `compute_60d_extras` defaults: `DEFAULTS_60D = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)` — documented per feature (regime_compression→1.0 neutral, etc.)

## 22. Config surface

- `AppConfig` (`configuration/config.py`) — `ModelConfig` holds
  `feature_schema_version`; `AlgoConfig` holds algo knobs; no
  liquidity switch exists today. The task adds `liquidity_features_enabled`
  as a new explicit switch (default False; when False the engine behaves
  exactly as today).

## 23. Existing 60D (TASK-5) plumbing that must NOT be broken

- `features/schema.py::scalp_v2` (dimension=60, supersedes scalp_v1) — ACTIVE registry entry (not the ACTIVE live schema)
- `features/schema_augment.py::compute_60d_extras` — momentum 10D producer
- `governance/alignment.py::challenger_input_for` — expects `scalp_v2` extras length 10
- `governance/load_gate.py::_REGISTERED_SCHEMA_IDS = ("scalp_v1", "scalp_v2", "scalp_v3")`
- `application/live_engine.py` shadow path (~2879-2923) — computes extras only for scalp_v2 challengers
- `tests/golden/golden_60d_extras.json` — canonical momentum extras

The Liquidity 60D layer registers a NEW schema id (`scalp_liquidity_v1`)
with its own producer; `scalp_v2` + all of the above stay byte-identical.

---

## A. EXISTING 50D (contract capture for the new layer)

```text
authoritative source:  src/nexus_scalp/features/scalp_features.py (FEATURE_NAMES tuple, lines 147-198)
feature count:         50 (indices 0..49)
schema:                scalp_v1 (features/schema.py ACTIVE_SCHEMA_ID; FeatureSchema dimension=50)
normalization:         per-feature in compute_from_bars(); ATR-ratio convention (safe_atr= max(atr, 0.20))
clipping:              to_tensor_input() lines 448-453: NaN/Inf→0.0 then clamp [-3,+3]  (SINGLE central point)
missing-value rule:    NaN/Inf → 0.0; cold start (<55 bars) → neutral FeatureVector defaults
training source:       model_generation/schema_v2.py::compute_60d_frame (per-row causal window, synthetic tick@close)
runtime source:        application/live_engine.py line 1510: compute_from_bars(completed_bars, last_tick)
replay source:         model_generation/replay.py::SampleReplay (feature vector from dataset artifact feat_* columns)
serialization:         DatasetManifest.feature_schema_id / ModelManifest.feature_schema_id+feature_dimension
schema version:        scalp_v1 / v1.0.0 (feature_schema_version in ModelManifest)
```

## B. (Liquidity engine design constraints — NOT YET IMPLEMENTED)

```text
ATR source:            canonical mean-TR-14 formula from compute_from_bars() (lines 528-537) — 
                       liquidity layer must reuse equivalent semantics (no third ATR variant)
swing confirmation:    ±5-bar fractal ⇒ swing at bar i confirmed at bar i+5 (candidate_at ≠ confirmed_at)
HTF confirmed only:    aggregate_bars() marks the forming bucket is_complete=True → liquidity MUST exclude
                       the still-forming bucket (only buckets fully closed at decision T)
session handling:      UTC hour semantics identical to feat_16-19
missing policy:        follow existing convention: documented constants, never NaN/Inf/random
```

---

## D. RISKS / DESIGN NOTES (forensic findings)

- `aggregate_bars()` forming-bucket hazard is real: H1 bucket for the
  current hour is marked complete even when M1 history ends mid-hour. The
  anti-leakage test (future HTF close cannot leak) must guard this exact path.
- The `±5` fractal confirmation delay is NOT encoded anywhere in the repo
  today (50D uses completed bars but does not distinguish candidate vs
  confirmed). The liquidity layer introduces that distinction — this is an
  ADDITIVE causal refinement, not a change to feat_0-49 values.
- No liquidity pool lifecycle exists anywhere. `LiquidityPool` + states are
  new domain objects; no duplicate domain model exists to reuse.
- `liquidity_sweep_signal` (feat_15) uses a one-bar-close rule (low <
  prev-10-low AND close > prev-10-low). The new sweep detector must define
  BREAKOUT vs SWEEP per the task section 19 (penetration + rejection/reclaim
  evidence), independent of feat_15.