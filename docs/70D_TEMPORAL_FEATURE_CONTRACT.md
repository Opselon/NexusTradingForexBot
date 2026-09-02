# 70D TEMPORAL LIQUIDITY INTELLIGENCE — TEMPORAL FEATURE CONTRACT (DRAFT)

> Task: 70D Temporal Liquidity Intelligence + Signal Stability
> Author: Hermes-TemporalLiquidity (AGENT-TEMPORAL-01)
> Status: DRAFT for review (STEP-04) — research candidate only, never ACTIVE

## 1. Objective

Reduce micro-flips (BUY/SELL/BUY/SELL within milliseconds) WITHOUT hiding
noise, while preserving genuine reversals. The layer adds TEMPORAL CONTEXT
to the protected 70D Liquidity block (indices 60..69) and, where evidence
justifies, a causal DECISION STABILITY CONTROLLER downstream of the raw
model decision.

## 2. Forensic findings so far (STEP-01..03)

1. Real XAUUSD M1 400-event capture: pool states oscillate (e.g. RECLAIMED ->
   SWEPT -> RECLAIMED -> TOUCHED) around level boundaries; the canonical
   engine recomputes pool state as a pure function of ALL bars each call —
   causal + deterministic, but the per-bar state is NOT monotonic.
2. Determinism: same input x3 -> bit-identical (DETERMINISTIC).
3. Cache/full-rebuild: 8/10 liquidity dims bit-identical; the 2 differing
   (internal/external distance) are bounded-history artifacts (documented),
   not cache bugs.
4. Decision margins from the 50D champion on real audit_signals: pbuy~0.245
   vs psell~0.31 -> decision margin ~0.06, i.e. the model operates near the
   BUY/SELL boundary; small feature deltas flip the argmax.

## 3. Candidate temporal representation (per liquidity feature)

All values are CAUSAL (only bars <= decision_at visible). Missing-value
policy: cold start -> lag unavailable -> documented neutral constants (see
4). Normalization: each feature family keeps its own deterministic
normalization, all clipped to [-3,+3] by the active feature contract.

| # | name | index (candidate) | source_feature | lag_horizon | formula | normalization | clipping | missing_policy | causal_delay | update_frequency |
|---|------|-------------------|----------------|-------------|---------|--------------|----------|----------------|--------------|------------------|
| 1 | bsl_distance_atr_lag1 | 70 | bsl_distance_atr | 1 | value(t-1) | identity | [-3,3] | cold-start -> 3.0 (no BSL = far) | 1 bar | bar-close |
| 2 | bsl_distance_atr_lag2 | 71 | bsl_distance_atr | 2 | value(t-2) | identity | [-3,3] | cold-start -> 3.0 | 2 bars | bar-close |
| 3 | bsl_distance_atr_delta1 | 72 | bsl_distance_atr | 1 | v(t)-v(t-1) | /2 (ATR-normalized) | [-3,3] | 0.0 | 1 bar | bar-close |
| 4 | ssl_distance_atr_lag1 | 73 | ssl_distance_atr | 1 | value(t-1) | identity | [-3,3] | cold-start -> 3.0 | 1 bar | bar-close |
| 5 | ssl_distance_atr_lag2 | 74 | ssl_distance_atr | 2 | value(t-2) | identity | [-3,3] | cold-start -> 3.0 | 2 bars | bar-close |
| 6 | ssl_distance_atr_delta1 | 75 | ssl_distance_atr | 1 | v(t)-v(t-1) | /2 | [-3,3] | 0.0 | 1 bar | bar-close |
| 7 | eqh_strength_lag1 | 76 | eqh_strength | 1 | value(t-1) | identity | [-3,3] | cold-start -> 0.0 | 1 bar | bar-close |
| 8 | eqh_strength_persistence | 77 | eqh_strength | 3 | % bars in last 3 with value>0 | identity | [-3,3] | 0.0 | 3 bars | bar-close |
| 9 | eql_strength_lag1 | 78 | eql_strength | 1 | value(t-1) | identity | [-3,3] | cold-start -> 0.0 | 1 bar | bar-close |
| 10 | eql_strength_persistence | 79 | eql_strength | 3 | % bars in last 3 with value>0 | identity | [-3,3] | 0.0 | 3 bars | bar-close |
| 11 | htf_liquidity_score_lag1 | 80 | htf_liquidity_score | 1 | value(t-1) | identity | [-3,3] | cold-start -> 0.0 | 1 bar | bar-close |
| 12 | htf_liquidity_score_state_duration | 81 | htf_liquidity_score | - | bars since last sign change of htf score | /10 | [-3,3] | 0.0 | 0 (derived from history) | bar-close |
| 13 | internal_liquidity_distance_lag1 | 82 | internal_liquidity_distance | 1 | value(t-1) | identity | [-3,3] | cold-start -> 3.0 | 1 bar | bar-close |
| 14 | internal_liquidity_distance_delta1 | 83 | internal_liquidity_distance | 1 | v(t)-v(t-1) | /2 | [-3,3] | 0.0 | 1 bar | bar-close |
| 15 | external_liquidity_distance_lag1 | 84 | external_liquidity_distance | 1 | value(t-1) | identity | [-3,3] | cold-start -> 3.0 | 1 bar | bar-close |
| 16 | external_liquidity_distance_delta1 | 85 | external_liquidity_distance | 1 | v(t)-v(t-1) | /2 | [-3,3] | 0.0 | 1 bar | bar-close |
| 17 | liquidity_confluence_lag1 | 86 | liquidity_confluence | 1 | value(t-1) | identity | [-3,3] | cold-start -> 0.0 | 1 bar | bar-close |
| 18 | liquidity_confluence_persistence | 87 | liquidity_confluence | 3 | % bars in last 3 with value>0 | identity | [-3,3] | 0.0 | 3 bars | bar-close |
| 19 | liquidity_sweep_state_persistence | 88 | liquidity_sweep_state | 3 | % bars in last 3 with sweep state != 0 | identity | [-3,3] | 0.0 | 3 bars | bar-close |
| 20 | liquidity_sweep_state_time_since_change | 89 | liquidity_sweep_state | - | bars since last sweep-state change | /10 | [-3,3] | 0.0 | 0 (derived) | bar-close |
| 21 | post_sweep_displacement_lag1 | 90 | post_sweep_displacement | 1 | value(t-1) | identity | [-3,3] | cold-start -> 0.0 | 1 bar | bar-close |
| 22 | post_sweep_displacement_delta1 | 91 | post_sweep_displacement | 1 | v(t)-v(t-1) | /2 | [-3,3] | 0.0 | 1 bar | bar-close |

22 candidate temporal dims -> candidate schema `scalp_v4_temporal_candidate`
(70 + 22 = 92D).

## 4. Missing-value policy (cold start)

- lag1/lag2: at the FIRST decision of a session the previous value does not
  exist. Use the feature's documented NEUTRAL constant (the same constants
  the engine uses when the datum is absent: 3.0 for distances, 0.0 for
  strength/score/confluence/sweep/displacement). NEVER zero-fill distances
  (0.0 would mean "price AT liquidity" — a false signal).
- delta1: 0.0 (no change) at cold start.
- persistence: 0.0 (no evidence).
- time-since-change / state-duration: 0.0 at cold start.
- The cold-start policy is deterministic and documented; anti-leakage tests
  assert no future bar influences any temporal value.

## 5. No-future-data guarantee

For a decision at timestamp T, only bars with timestamp <= T contribute.
lag_k uses value at T-k (bar timestamps are strictly increasing M1 -> T-k
is causally earlier). All 22 temporal features are computed from the same
causal vector sequence; the anti-leakage test (TEST-TEMPORAL-08) recomputes
T after appending future bars and asserts bit-identical results.

## 6. Determinism / parity

- Determinism: same input sequence -> same temporal features (pure function
  of the causal history).
- Parity: the temporal feature extractor is a PURE function used identically
  in training, replay, live runtime and shadow (single code path).
- Cache/full-rebuild: the temporal extractor maintains a bounded rolling
  buffer; a full rebuild from the same causal history produces identical
  values (tested).

## 7. Decision Stability Controller (design proposal, STEP-06)

State machine (minimum): NONE -> BUY_CANDIDATE -> BUY_CONFIRMED (and mirror
for SELL). Transitions require:

- CANDIDATE entry: raw model direction D and |PBUY - PSELL| >= MIN_MARGIN.
- CONFIRMED: N consecutive candidate observations of the SAME direction
  (N=2..3, chosen by replay evidence, NOT by "looks smoother"), OR a
  structural confirmation (liquidity sweep / level break) in the SAME
  direction.
- Exit stability SEPARATE from entry stability: an open losing position may
  exit on a faster confirmation than a new entry needs.
- HARD_REVERSAL: strong margin + structural confirmation + confirmed
  liquidity event -> immediate reversal.
- max_candidate_age: candidate expires after MAX_CANDIDATE_AGE bars unless
  evidence strengthens.
- Reset: symbol/model/schema/timeframe change or runtime restart clears all
  temporal/stability state (TEST-TEMPORAL-20/21).

## 8. Telemetry

- [TEMPORAL_LIQUIDITY] events on feature updates (bounded).
- [SIGNAL_STABILITY] events ONLY on confirmed direction changes (never per
  micro-flip): timestamp, previous, candidate, new_direction, PBUY, PSELL,
  margin, candidate_age, confirmation_reason.
