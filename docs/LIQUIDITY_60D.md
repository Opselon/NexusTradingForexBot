# LIQUIDITY 60D FEATURE CONTRACT (scalp_liquidity_v1)

> TASK-01-60D-LIQUIDITY · 2026-08-19 · Agent: Hermes-LiquidityFoundation
> Schema id: `scalp_liquidity_v1` (dimension 60, supersedes `scalp_v1`)
> Producer: `src/nexus_scalp/features/liquidity_engine.py::compute_liquidity_features`
> The 60D vector = the protected 50D `scalp_v1` contract (UNCHANGED) + 10
> liquidity dimensions at indices 50..59.

---

## 0. Invariants (every feature)

1. **Causal**: only bars closed at/before the decision timestamp contribute.
   A swing is usable only from its `confirmed_at` (candidate bar + 5 bars).
2. **No future leakage**: adding bars beyond T never changes the features at
   T (TEST-LIQ-28 historical invariance).
3. **One ATR**: values are ATR-normalized with the canonical mean-TR-14
   semantics of `ScalpFeatureEngine.compute_from_bars`
   (features/scalp_features.py lines 528-537; floor `safe_atr = max(atr, 0.20)`).
4. **Finite + clipped**: all outputs finite and within [-3,+3] (the same
   central convention as the 50D sanitizer).
5. **Deterministic**: same bars + same decision_at -> identical floats.
6. **No I/O**: the engine is a pure function of bars (no SQL, no DB, no
   network, no filesystem).

---

## Feature registry (indices 50..59)

| idx | name | category | source | timeframe | units | missing → |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 50 | bsl_distance_atr | BSL distance | confirmed swings/EQH/PDH/PWH/session-H/HTF-highs | M1..D1 | ATR | 3.0 |
| 51 | ssl_distance_atr | SSL distance | confirmed swings/EQL/PDL/PWL/session-L/HTF-lows | M1..D1 | ATR | 3.0 |
| 52 | eqh_strength | structure | confirmed equal highs (ATR tolerance) | M1..D1 | [0,1] | 0.0 |
| 53 | eql_strength | structure | confirmed equal lows (ATR tolerance) | M1..D1 | [0,1] | 0.0 |
| 54 | htf_liquidity_score | HTF evidence | completed H1/H4/D1 buckets only | H1/H4/D1 | [-3,3] | 0.0 |
| 55 | internal_liquidity_distance | range | confirmed pools inside active range | M1..H1 | ATR | 3.0 |
| 56 | external_liquidity_distance | range | confirmed pools at/beyond range edges | M1..D1 | ATR | 3.0 |
| 57 | liquidity_confluence | clustering | confirmed pools in zones (source diversity) | M1..D1 | [0,3] | 0.0 |
| 58 | liquidity_sweep_state | sweep state | reactive sweep detector (penetration+rejection) | M1 | signed int | 0.0 |
| 59 | post_sweep_displacement | sweep outcome | displacement after sweep confirmation | M1 | ATR | 0.0 |

---

## Feature definitions

### 50 — LIQUIDITY_01 bsl_distance_atr
- **Definition**: distance (in ATR) from the mid price P to the nearest
  active CONFIRMED Buy-Side Liquidity level L above P.
- **Formula**: `(L - P) / ATR`, clipped [-3,+3].
- **Sources**: confirmed swing highs, confirmed EQH, PDH, PWH, session
  high, meaningful HTF highs (all pools must have `usable_at <= T`).
- **Causal confirmation**: a swing high is usable only from
  `confirmed_at = time of (swing bar + 5)` — never earlier.
  Session/daily levels are confirmed by the last completed bar of their
  window at T.
- **Missing**: no BSL above P → `3.0` (far; never NaN/Inf).
- **Notes**: raw distance only — NOT a directional prediction. When the
  level is > 3 ATR away the clipped value is 3.0 (the contract's "far"
  saturation), identical to the 50D convention.

### 51 — LIQUIDITY_02 ssl_distance_atr
- **Definition**: distance (in ATR) from P to the nearest active CONFIRMED
  Sell-Side Liquidity level L below P.
- **Formula**: `(P - L) / ATR`, clipped [-3,+3]. Mirror of LIQUIDITY_01.
- **Sources**: confirmed swing lows, confirmed EQL, PDL, PWL, session low,
  HTF lows.
- **Missing**: no SSL below P → `3.0`.

### 52 — LIQUIDITY_03 eqh_strength
- **Definition**: strength of the most recent Equal-Highs cluster.
- **Formula**: cluster of swing-high values with
  `|high_a - high_b| <= ATR * 0.30` (volatility-aware tolerance — NOT float
  equality). Strength =
  `softmax over clusters of base(1+ln(member_count)) * closeness(exp(-|v-P|/ATR)) * recency(exp(-bars_since/200))`.
- **Anti-leakage**: a future touch belongs to a cluster whose
  `latest` advances only when the confirming bar closes — strength at T is
  computed from members confirmed at/before T only.
- **Missing**: no EQH evidence → `0.0`.

### 53 — LIQUIDITY_04 eql_strength
- Mirror of EQH on swing lows. Same tolerance architecture, scoring,
  normalization, clustering (reuses the same `_cluster_equal_levels`).

### 54 — LIQUIDITY_05 htf_liquidity_score
- **Definition**: signed aggregate of HTF liquidity evidence near price
  from COMPLETED H1/H4/D1 buckets (subject to config).
- **Formula**: for each completed bucket, for high/low within 6 ATR of
  last_close: `sign * proximity(1/(1+dist)) * timeframe_weight(H1 0.9, H4 1.2, D1 1.6)`;
  total `= tanh(sum) * 3`.
- **CRITICAL**: a currently-forming H1 candle CANNOT contribute — only
  buckets whose end time <= decision T. Explicitly tested
  (TEST-LIQ-13/25): the future final high of the forming H1 is invisible.
- **Missing**: no HTF evidence → `0.0`.

### 55 — LIQUIDITY_06 internal_liquidity_distance
- **Definition**: distance (in ATR) to the nearest meaningful CONFIRMED
  pool strictly INSIDE the active structural range.
- **Range**: min..max of confirmed pool prices. A pool is INTERNAL when
  `range_min + 0.25*ATR < price < range_max - 0.25*ATR`.
- **Distinction**: internal = levels inside the envelope; external = the
  envelope edge levels themselves (the breakout targets).
- **Missing**: none → `3.0`.

### 56 — LIQUIDITY_07 external_liquidity_distance
- **Definition**: distance (in ATR) to the nearest confirmed pool OUTSIDE
  the active range (at or beyond either edge).
- **Sources**: major swings, PDH/PDL, PWH/PWL, HTF extremes.
- **Distinction is explicit and testable** (TEST-LIQ-14/15).

### 57 — LIQUIDITY_08 liquidity_confluence
- **Definition**: cluster confirmed pools within `0.75 * ATR` into zones;
  score = best zone's `(1 + ln(distinct_sources)) + (tf_sum/1440)*0.5 + sum(strength)*0.25`.
- **Important**: duplicate references to the SAME underlying level (same
  side+source+price≈) collapse to ONE source — 4 references to one pool are
  NOT 4 independent sources (TEST-LIQ-17).
- **Missing**: no pools → `0.0`.

### 58 — LIQUIDITY_09 liquidity_sweep_state
- **Definition**: signed interaction state vs the nearest relevant pool:
  -2 SWEPT_AND_DISPLACED, -1 SWEPT, 0 NO_RELEVANT_LIQUIDITY,
  +1 APPROACHING, +2 TOUCHED, +3 RECLAIMED.
- **Strict causal sweep**: penetration alone is NOT a sweep (breakout).
  The detector requires: a CONFIRMED pool + penetration + a LATER bar
  closing back beyond the pool (rejection/reclaim evidence). The sweep is
  confirmed only when the rejecting bar closes → the event timestamp
  reflects the rejection bar, never the penetration bar.
- **Missing/no relevant pool**: `0.0`.

### 59 — LIQUIDITY_10 post_sweep_displacement
- **Definition**: ATR-normalized displacement AFTER a confirmed sweep, in
  the rejection direction.
- **Formula**: for a BSL sweep: `(close[rejection+1] - low[sweep_bar]) / ATR` (positive
  when price fell away); SSL mirrored.
- **Anti-leakage**: measured ONLY from bars AFTER the sweep-confirming bar;
  pre-sweep price action never enters (TEST-LIQ-27).
- **Missing**: no recent confirmed sweep → `0.0`.

---

## Normalization contract
- Ten values: all clipped [-3,+3] by ONE central `_clip3()` (never ten
  clip functions); NaN/Inf → 0.0.
- No scaler fitted on future/OOS data; the feature layer exposes raw
  reproducible values (any later train-only scaler is applied downstream by
  the existing architecture).
- Deterministic, causal, training/live identical.

## Missing-value policy (summary)
| condition | value | why |
| :--- | :--- | :--- |
| no active pool (BSL/SSL/internal/external) | 3.0 | "far" saturation, consistent with [-3,3] |
| not enough history / no swings | documented defaults (3.0/0.0) | never NaN/Inf/random |
| ATR unavailable | canonical MIN_ATR 0.20 floor | matches 50D safe_atr |
| HTF data unavailable / forming bucket | 0.0 | no evidence |
| no relevant sweep | 0.0 | neutral |

## Schema/manifest integration
- `features/schema.py` registers `scalp_liquidity_v1` (dimension 60,
  supersedes scalp_v1) — additive; scalp_v1 stays ACTIVE (live contract
  unchanged), scalp_v2 (TASK-5 momentum) untouched.
- Dataset manifest records `feature_schema_id=scalp_liquidity_v1`,
  feature columns feat_0..feat_59. Verification:
  `verify_liquidity_artifact` (60 features, all finite, all in [-3,3]).
- Model manifests: 60D models declare `feature_dimension=60` +
  `build_metadata.input_dimension=60`; the runtime rejects any input whose
  width differs (no silent pad/truncate). Legacy 50D models remain loadable.

## Config
- `model.liquidity_features_enabled` (default **false**): false → existing
  50D behavior; true → the 60D liquidity layer is available to candidate
  pipelines. The switch never silently alters schema expectations: when
  enabled, manifests are explicitly 60D.

## Known limitations
- Session/daily pools are confirmed by "last completed bar of the visible
  window" — they update intra-window (correct for causality, but a
  "session high" is only a pool once the session's bars are seen).
- `equal_high_low_strengths` uses a softmax over close clusters: when only
  ONE cluster exists its strength is 1.0 by construction (no denominator
  competition) — the value is a RELATIVE strength, not an absolute.
- HTF proximity band is 6 ATR; pools farther away contribute 0 to the
  score by design (they are captured by the distance features instead).
- The sweep detector evaluates the single NEAREST pool per decision
  (multi-pool simultaneous sweeps are not aggregated in one tick).
- Performance: the engine re-derives pools from the bar window per call
  (pure function). For high-frequency live use, cache the pool state and
  confirm only newly closed bars (incremental update path).

## Test coverage
- tests/unit/test_liquidity_engine_contract.py (TEST-LIQ-01..11, 32-37, 44)
- tests/unit/test_liquidity_engine_causality.py (TEST-LIQ-12/13, 18-28,
  TEST-60D-BASE-01)
- tests/unit/test_liquidity_engine_features.py (TEST-LIQ-14-17, 29-31,
  38-40, 45)
- tests/helpers/liquidity_fixtures.py (deterministic ramp/spike/sweep
  fixtures — flat bars are avoided because they become fractal pivots
  everywhere)

---

# TASK-02-70D-INTEGRATION APPENDIX — the 70D contract & runtime control plane

> TASK-02-70D-INTEGRATION · 2026-08-19 · Agent: Hermes-70D-Integration (TASK-2)
> The 60D `scalp_liquidity_v1` contract above is TASK-1's deliverable and is
> UNCHANGED by this appendix. This section documents the 70D integration
> layer built on top of it.

## 1. The 70D contract — `scalp_v4`

```
schema_id  = scalp_v4      (registered in src/nexus_scalp/features/schema.py)
dimension  = 70
BASE       = feat_0  .. feat_49    (scalp_v1 50D, protected, byte-identical)
FAMILY     = feat_50 .. feat_59    (slot-50..59 family: TASK-5 scalp_v2
                                    momentum extras under their own schema id;
                                    TASK-1 liquidity 10D under scalp_liquidity_v1)
LIQUIDITY  = feat_60 .. feat_69    (TASK-1 liquidity features, canonical
                                    as_vector() order)
```

Rationale for the schema id: the brief's `scalp_v3` name was already taken by
the repo's forward-declared 350D research contract (registered + asserted in
existing tests). Per brief TEST-29 ("Adapt names to the actual repository
contract") TASK-2 registers `scalp_v4`; `scalp_v3`/350D stays untouched.

## 2. Liquidity runtime governor — `features/liquidity_runtime.py`

- `LiquidityGovernor` — thread-safe runtime holder of the real snapshot,
  explicit status (ENABLED/DISABLED/DEGRADED/UNAVAILABLE), causal state
  (VALID/STALE/INVALID), latency, source, model compatibility.
- `build_70d_vector(features50, family_10, liquidity_10)` — strict assembly,
  raises on width mismatch (INV-009), never pads/truncates.
- `resolve_model_compatibility(...)` — explicit matrix:
  scalp_v2/60D + 60D runtime PASS · scalp_v4/70D + 70D runtime PASS ·
  60D model + 70D runtime BLOCK (LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) ·
  unknown -> UNKNOWN (never guessed).
- Enabled flag persisted through `SettingsService` key
  `model.liquidity_features_enabled` (HOT_RESTRICTED) — the UI toggle routes
  through POST /api/liquidity/toggle; never a UI-only flag, never live.yaml
  direct writes (INV-010).
- The governor computes on the engine's new-bar cadence (pure numpy, no DB,
  information-only — never touches orders/SL/TP/risk/execution).

## 3. API surface

| Endpoint | Purpose |
| :--- | :--- |
| GET /api/liquidity/state | status + 10 real values + latency + schema + model compatibility |
| GET /api/liquidity/features | ten values with index 60..69, source, timestamp, status |
| POST /api/liquidity/toggle | persist + hot-apply (body `{"enabled": bool}`) |
| /api/live/state + /api/status | canonical graph embeds `liquidity` section (SSE carries it too) |

## 4. UI

- New "Liquidity Intelligence" tab (Web/index.html): status badge, schema,
  dimension, feature count, source, causal state, last update, latency, model
  compatibility, enable/disable toggle, ten per-value cards (index 60..69).
- Feature matrix groups rendered from the canonical /api/status features
  payload; liquidity entries are the LIVE ENGINE values only.
- Chart overlays: liquidity pool lines drawn ONLY from the real snapshot
  pools (window.__liquidityPools). No liquidity -> no lines, chart intact.
- Console traces: [LIQUIDITY_UI] state_loaded / request_failed / toggle_*
  with correlation ids.

## 5. Model compatibility

- No padding, no truncation, no silent upgrade/downgrade.
- A 70D `scalp_v4` artifact is a SEPARATE candidate architecture; existing
  60D `scalp_v2` models remain loadable and never auto-migrate.
- No auto-promotion: nothing in this task promotes 70D to Champion.

## 6. News/Liquidity independence

News (12D news_context_v1 stream) and Liquidity (10D at 60..69) are
independent families: either can be enabled/disabled/unavailable without
affecting the other (TEST-70D-06..09/28). The system never substitutes one
for the other.
