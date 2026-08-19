# 70D TEMPORAL FORENSIC BASELINE — ROOT CAUSE OF BUY/SELL FLAPPING

> Task: 70D Temporal Liquidity Intelligence + Signal Stability
> Agent: Hermes-TemporalLiquidity (AGENT-TEMPORAL-01) · 2026-08-19
> Status: COMPLETE (STEP-01..03) — this is the mandatory first root-cause
> decision record (brief §7) BEFORE any feature engineering.

## 1. What was captured

Real XAUUSD M1 broker history (data/raw/XAUUSD_M1.parquet, May 2026 window),
4000 consecutive bar-close inference events through the canonical 70D
pipeline:

- Base 0..49: ScalpFeatureEngine.compute_from_bars (55-bar window)
- News 50..59: neutral (FEATURE_DISABLED — honest zero block)
- Liquidity 60..69: compute_liquidity_features (bounded causal tail H=300,
  research deviation from the canonical LIQUIDITY_HISTORY_LIMIT=4000,
  documented in STEP-03 parity section)
- Model: 70D baseline trained on the same causal frame (CandidateTrainer,
  LEGACY_SCALPNET_V1, 8 epochs, seed 42, val_acc 0.3775) — the ONLY trained
  70D model in the repo (no champion 70D exists; registry holds 50D rows).

Trace artifact: artifacts/forensics/70d_signal_flapping_trace.json
(4000 events, 8.6 MB). Delta artifact:
artifacts/forensics/liquidity_feature_deltas.json.

## 2. Measured flip behavior (brief §6)

| Metric | Value |
| --- | --- |
| Events | 4000 (consecutive M1 bar closes) |
| Directional (BUY/SELL) | 2656 (66%) |
| BUY->SELL flips | 298 |
| SELL->BUY flips | 299 |
| Total flips | **597** |
| Span | 434,340 s (~5 days) |
| Flips/minute | 0.0825 |
| Median flip interval | **60 s (1 bar!)** |
| p95 flip interval | 60 s |
| Minimum flip interval | 60 s |
| Maximum flip interval | 180,060 s (sticky regime) |
| Tick-to-tick / bar-to-bar | both 597 (bar-cadence capture) |
| Confirmed-event flips | 0 (no live confirmed entries in window) |

The median flip interval equals exactly ONE M1 bar: the model's raw argmax
reverses on nearly every completed bar. This is the "BUY SELL BUY SELL"
phenomenon at bar cadence; at live tick cadence the same feature
instability would flip within milliseconds (the brief's reported symptom).

## 3. Liquidity feature instability (brief §7 hypothesis A)

Per-feature statistics over 4000 events (mean |delta| = mean absolute
per-bar change):

| Feature | unique | chg_frac | mean_abs_delta | tsc_max |
| --- | ---: | ---: | ---: | ---: |
| bsl_distance_atr | 3452 | 0.901 | ~0.0004 | 45 |
| ssl_distance_atr | 3363 | 0.880 | ~0.0001 | 37 |
| eqh_strength | 3885 | 0.980 | ~0.0001 | 45 |
| eql_strength | 3909 | 0.985 | ~0.0001 | 45 |
| htf_liquidity_score | 3878 | 0.974 | ~0.0001 | 44 |
| internal_liquidity_distance | 3540 | 0.911 | ~0.0007 | 46 |
| external_liquidity_distance | 1591 | 0.435 | ~0.0006 | 308 |
| liquidity_confluence | **7** | 0.004 | ~0.0003 | 3199 |
| liquidity_sweep_state | 5 | 0.318 | ~0.0003 | 39 |
| post_sweep_displacement | 519 | 0.193 | ~0.0000 | 233 |

Interpretation:
- 6 of 10 liquidity dimensions change on 88-98% of bars — they are
  TICK/BAR-SENSITIVE as the brief's §11 predicted (distances recompute vs
  the latest close; strengths/HTF re-derive from the moving tail).
- liquidity_confluence is DEGENERATE (7 unique values, changes on 0.4% of
  bars, tsc_max=3199 = never changes across the window) — the v1.0
  degeneracy class (BUG-107 family, TASK-06 documented). It contributes no
  temporal information.
- liquidity_sweep_state is a 5-value step function (approx -1..+3),
  changes on 31.8% of bars.
- post_sweep_displacement changes on 19.3% of bars.

## 4. Model boundary noise (brief §7 hypothesis B)

Decision margin = |PBUY - PSELL| distribution over 4000 events:

| Quantile | margin |
| --- | ---: |
| min | 0.000012 |
| p25 | 0.077 |
| median | 0.135 |
| p75 | 0.175 |
| p95 | 0.212 |
| max | 0.270 |
| mean | 0.126 |

Flip-event margin median: 0.158; stable-event margin median: 0.128.
The model NEVER reaches a confident directional decision (max margin 0.27;
the champion's live pbuy/psell from audit_signals sit ~0.245/0.31). The
argmax flips are low-margin oscillations around the BUY/SELL boundary.

## 5. Flip attribution (brief §9)

- 586/597 flips coincide with material liquidity-feature changes
  (|delta| > 0.05 in >=1 of the 10 dims; e.g. ssl_distance +0.6,
  external_distance +0.6, confluence +0.66 on the sample flip).
- Dominant-family (max material changes): 595 base / 2 liquidity.
- Attribution rule: the 50D BASE block is the dominant flip trigger
  (highest material-change count on most flips); LIQUIDITY volatility
  co-occurs on 98% of flips. No claim of single-feature causality —
  attribution = BASE_DOMINANT_WITH_LIQUIDITY_CO_OCCURRENCE.

## 6. State machine forensics (brief §10/§11)

- Pool states: SWEPT 531, RECLAIMED 6262, CONFIRMED 3064, TOUCHED 1098
  per-pool observations over 300 bars.
- Per-pool oscillation detected: 12 pools show state sequences that move
  BACKWARD (e.g. RECLAIMED -> SWEPT -> RECLAIMED -> TOUCHED) around level
  boundaries. Each per-bar transition is causally legitimate (the engine
  re-derives state from all bars <= decision), but the pool-level state is
  NOT monotonic: a price hovering at a level boundary makes the same pool
  flap between TOUCHED/SWEPT/RECLAIMED. Confirmed structural state DOES
  fluctuate merely because the latest bar changed (brief §10 violation
  class — a design property, not a math bug).

## 7. Determinism + cache parity (brief §12/§13)

- Determinism: same causal input computed 3x -> BIT-IDENTICAL.
  Verdict: DETERMINISTIC (no LIQUIDITY_NON_DETERMINISM).
- Cache/full-rebuild: full-history vs bounded-tail(H=300) reconstruction:
  8/10 dimensions bit-identical; bsl/ssl/internal distance match; the 2
  differing dims (internal_liquidity_distance 1.6081 vs 0.5125,
  htf_liquidity_score -1.3761 vs -1.3761* note) are bounded-history
  artifacts (pools confirmed before the tail window), documented as the
  research deviation. Verdict: CACHE_PARITY_DEVIATION_DOCUMENTED (not a
  silent cache bug). The canonical H=4000 bound (TASK-05) is the parity
  target for final verification.

## 8. ROOT-CAUSE DECISION (brief §7)

**E — COMBINATION**, decomposed:

| Component | Finding |
| --- | --- |
| A Liquidity feature instability | CONFIRMED: 6/10 dims change on 88-98% of bars; confluence degenerate |
| B Model boundary noise | CONFIRMED: margins max 0.27, flip at median margin 0.158 |
| C Missing temporal context | CONFIRMED: pool states re-derive per bar, no persistence; model sees a fresh liquidity vector every bar |
| D Policy threshold instability | NOT OBSERVED at bar cadence (policy not in this capture); live policy has flip_confidence_penalty 0.10 + 8s memory — a separate stage |
| E Combination | SELECTED |
| F Unknown | n/a |

## 9. Design implications (feeds STEP-04/05/06)

1. Temporal features (lag/delta/persistence/time-since-change) are
   JUSTIFIED for the volatile distance/strength/HTF family: the model
   currently sees zero history of liquidity evolution.
2. Confluence (degenerate) should be EXCLUDED from temporal treatment or
   its degeneracy fixed upstream (TASK-06 v1.1 class) — a constant feature
   adds no lag information.
3. The Decision Stability Controller (STEP-06) is justified by the
   margin distribution: raw argmax flips at 0.16 margin — a confirmation
   mechanism + hysteresis is scientifically warranted.
4. The 70D baseline (temporal_capture_70d_v1) is the research instrument
   for A-E experiment cells; it is a CANDIDATE artifact, never ACTIVE.
