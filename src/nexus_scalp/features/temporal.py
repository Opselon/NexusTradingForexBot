"""Temporal Liquidity Intelligence — causal temporal feature extractor (TASK-TEMPORAL-01).

RESEARCH CANDIDATE ONLY — never ACTIVE/CHAMPION.

WHY THIS EXISTS
---------------
The STEP-01..03 forensics proved the protected 70D Liquidity block (indices
60..69) is recomputed fresh at every decision: 6 of 10 dimensions change on
88-98% of bars, pool states oscillate around level boundaries, and the model
flips its raw argmax on nearly every M1 bar (median flip interval = 60 s,
597 flips / 4000 events, max decision margin 0.27). The model therefore sees
ZERO history of liquidity evolution.

This module adds a CAUSAL TEMPORAL CONTEXT layer on top of the canonical
liquidity 10D:

    CURRENT  (protected 70D, untouched)
    + LAG-1 / LAG-2          (previous decisions' values)
    + DELTA-1                (v(t) - v(t-1))
    + PERSISTENCE            (fraction of recent bars with active state)
    + TIME-SINCE-CHANGE      (bars since the value/state last changed)

CONTRACT
--------
- PURE: only the causal history of canonical liquidity vectors (each vector
  already computed by liquidity_engine.compute_liquidity_features at its own
  decision_at) -> 22 deterministic floats clipped [-3,+3].
- CAUSAL: a temporal value at decision T uses only vectors with
  decision_at <= T. lag_k uses the k-th earlier vector. NO future bars.
- COLD START: at the beginning of a sequence the missing lags use the same
  documented NEUTRAL constants as the liquidity engine (3.0 for distances,
  0.0 for strength/score/confluence/sweep/displacement) — NEVER arbitrary
  zeros for distance dims (0.0 would mean "price AT liquidity").
- O(1) incremental: the extractor keeps a bounded rolling buffer of the last
  TEMPORAL_HISTORY (=8) liquidity vectors; each update appends one vector
  and pops the oldest. No DB, no network, no rebuild.
- DETERMINISTIC: same causal sequence -> same 22 dims (bit-exact).
- TRAIN/LIVE PARITY: the same pure function is used in training frames,
  replay, live runtime and shadow. There is exactly ONE implementation.

SCHEMA: the 22 dims extend the canonical 70D to 92D under a NEW candidate
schema id "scalp_v4_temporal_candidate" (registered in features/schema.py
by the task; the ACTIVE contract remains scalp_v1 / 70D untouched).

FEATURE FAMILIES (temporal representations chosen per source feature per the
STEP-04 contract — see docs/70D_TEMPORAL_FEATURE_CONTRACT.md):
  distances          current + lag1 + lag2 + delta1
  strengths          current + lag1 + persistence
  htf                current + lag1 + state_duration
  internal/external  current + lag1 + delta1
  confluence         current + lag1 + persistence   (degenerate v1.0: no-op)
  sweep_state        current + persistence + time_since_change (NOT a raw
                     continuous series — event semantics)
  displacement       current + lag1 + delta1
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Neutral constants mirrored from liquidity_engine (documented).
NEUTRAL_DISTANCE: float = 3.0
NEUTRAL_ZERO: float = 0.0

#: Bounded rolling history depth (8 decisions is enough for lag2 +
#: 3-bar persistence + time-since-change up to 8; larger adds nothing).
TEMPORAL_HISTORY: int = 8

#: Delta normalization divisor (ATR-unit features move ~O(1); /2 keeps the
#: delta in [-3,+3] for swings up to 6 ATR; clipped after).
DELTA_NORM: float = 2.0

#: Persistence window (bars).
PERSISTENCE_WINDOW: int = 3

#: Time-since-change normalization divisor (bars -> [-3,+3] for up to 30).
TSC_NORM: float = 10.0


@dataclass(frozen=True)
class TemporalLiquiditySnapshot:
    """One immutable temporal snapshot (22 floats + provenance)."""

    timestamp_utc: str
    schema_id: str = "scalp_v4_temporal_candidate"
    dimension: int = 22
    values: tuple[float, ...] = field(default_factory=tuple)
    names: tuple[str, ...] = field(default_factory=tuple)

    def as_vector(self) -> list[float]:
        return list(self.values)

    def validate(self) -> None:
        if len(self.values) != self.dimension:
            raise ValueError(
                f"temporal contract violation: expected {self.dimension}, got {len(self.values)}"
            )
        for v in self.values:
            if math.isnan(v) or math.isinf(v):
                raise ValueError(f"non-finite temporal value {v}")
            if not (-3.0 <= v <= 3.0):
                raise ValueError(f"temporal value {v} out of [-3,+3]")


#: Canonical temporal dimension names (order = vector position).
TEMPORAL_FEATURE_NAMES: tuple[str, ...] = (
    "bsl_distance_atr_lag1",
    "bsl_distance_atr_lag2",
    "bsl_distance_atr_delta1",
    "ssl_distance_atr_lag1",
    "ssl_distance_atr_lag2",
    "ssl_distance_atr_delta1",
    "eqh_strength_lag1",
    "eqh_strength_persistence",
    "eql_strength_lag1",
    "eql_strength_persistence",
    "htf_liquidity_score_lag1",
    "htf_liquidity_score_state_duration",
    "internal_liquidity_distance_lag1",
    "internal_liquidity_distance_delta1",
    "external_liquidity_distance_lag1",
    "external_liquidity_distance_delta1",
    "liquidity_confluence_lag1",
    "liquidity_confluence_persistence",
    "liquidity_sweep_state_persistence",
    "liquidity_sweep_state_time_since_change",
    "post_sweep_displacement_lag1",
    "post_sweep_displacement_delta1",
)


def _clip3(v: float) -> float:
    return max(-3.0, min(3.0, v))


def _neutral_for(idx10: int) -> float:
    """Documented cold-start neutral for a canonical liquidity index 0..9."""
    if idx10 in (0, 1, 5, 6):  # distances
        return NEUTRAL_DISTANCE
    return NEUTRAL_ZERO


def temporal_features_from_history(
    history: list[list[float]],
) -> tuple[float, ...]:
    """Pure temporal extraction from an ordered list of canonical liquidity
    10-vectors (oldest -> newest, each already clipped [-3,+3]).

    ``history`` must contain only vectors whose decision_at <= the current
    decision. The LAST element is the current vector; earlier elements are
    the causal lags. Cold start: when a lag is missing the documented
    neutral is used.

    Returns the 22 clipped values in TEMPORAL_FEATURE_NAMES order.
    """
    if not history:
        raise ValueError("temporal extraction requires >=1 liquidity vector")
    cur = history[-1]
    lag1 = history[-2] if len(history) >= 2 else None
    lag2 = history[-3] if len(history) >= 3 else None
    last3 = history[-PERSISTENCE_WINDOW:] if len(history) >= PERSISTENCE_WINDOW else history

    def v_or_neutral(vec: list[float] | None, idx10: int) -> float:
        if vec is None:
            return _neutral_for(idx10)
        return float(vec[idx10])

    def delta(cur_v: float, prev: list[float] | None, idx10: int) -> float:
        if prev is None:
            return 0.0  # cold start: no change evidence, NEVER neutral-derived
        prev_v = v_or_neutral(prev, idx10)
        return _clip3((cur_v - prev_v) / DELTA_NORM)

    def persistence(idx10: int, active_abs: float = 1e-9) -> float:
        vals = [v_or_neutral(v, idx10) for v in last3]
        active = sum(1 for x in vals if abs(x) > active_abs)
        return _clip3(active / len(vals) * 3.0)

    # time-since-change for sweep state: bars since the sweep-state value
    # (index 8) last differed from the current one
    tsc_sweep = 0.0
    if len(history) >= 2:
        cur_sweep = cur[8]
        tsc = 0
        for v in reversed(history[:-1]):
            if abs(float(v[8]) - cur_sweep) > 1e-12:
                break
            tsc += 1
        tsc_sweep = _clip3(tsc / TSC_NORM)

    # state-duration for htf score: bars since sign of the htf score changed
    htf_dur = 0.0
    cur_htf = cur[4]
    if len(history) >= 2:
        dur = 0
        for v in reversed(history[:-1]):
            if (float(v[4]) >= 0) != (cur_htf >= 0) and abs(float(v[4]) - cur_htf) > 1e-9:
                break
            dur += 1
        htf_dur = _clip3(dur / TSC_NORM)

    out = [
        v_or_neutral(lag1, 0),  # bsl lag1
        v_or_neutral(lag2, 0),  # bsl lag2
        delta(cur[0], lag1, 0),  # bsl delta1
        v_or_neutral(lag1, 1),  # ssl lag1
        v_or_neutral(lag2, 1),  # ssl lag2
        delta(cur[1], lag1, 1),  # ssl delta1
        v_or_neutral(lag1, 2),  # eqh lag1
        persistence(2),  # eqh persistence
        v_or_neutral(lag1, 3),  # eql lag1
        persistence(3),  # eql persistence
        v_or_neutral(lag1, 4),  # htf lag1
        htf_dur,  # htf state duration
        v_or_neutral(lag1, 5),  # internal lag1
        delta(cur[5], lag1, 5),  # internal delta1
        v_or_neutral(lag1, 6),  # external lag1
        delta(cur[6], lag1, 6),  # external delta1
        v_or_neutral(lag1, 7),  # confluence lag1
        persistence(7),  # confluence persistence
        persistence(8, active_abs=1e-9),  # sweep persistence
        tsc_sweep,  # sweep time-since-change
        v_or_neutral(lag1, 9),  # displacement lag1
        delta(cur[9], lag1, 9),  # displacement delta1
    ]
    clipped = [_clip3(v) for v in out]
    return tuple(clipped)


class TemporalLiquidityTracker:
    """O(1) incremental temporal tracker (stateful, deterministic, bounded).

    Usage (live / replay / training identical):
        tracker = TemporalLiquidityTracker()
        for liq10 in causal_liquidity_vectors:
            snap = tracker.update(liq10, ts)
            # snap.values -> 22 temporal dims (or tracker.vector70plus())

    Deterministic: same causal sequence -> same snapshots. Bounded: holds at
    most TEMPORAL_HISTORY vectors. Thread-safe by construction (no shared
    mutable state outside the instance; callers own the instance).
    """

    def __init__(self, history: list[list[float]] | None = None) -> None:
        self._history: list[list[float]] = list(history or [])[-TEMPORAL_HISTORY:]
        self._last_ts: str = ""

    def update(self, liquidity10: list[float], timestamp_utc: str) -> TemporalLiquiditySnapshot:
        """Appends one causal liquidity vector; returns the temporal snapshot."""
        if len(liquidity10) != 10:
            raise ValueError(f"liquidity vector must be 10D, got {len(liquidity10)}")
        self._history.append([float(v) for v in liquidity10])
        self._history = self._history[-TEMPORAL_HISTORY:]
        self._last_ts = timestamp_utc
        vals = temporal_features_from_history(self._history)
        snap = TemporalLiquiditySnapshot(
            timestamp_utc=timestamp_utc,
            values=vals,
            names=TEMPORAL_FEATURE_NAMES,
        )
        snap.validate()
        return snap

    @property
    def history_depth(self) -> int:
        return len(self._history)

    def reset(self) -> None:
        """Clears all state (symbol/model/schema/timeframe change, restart)."""
        self._history = []
        self._last_ts = ""

    def extend_70d(
        self, vector70: list[float], liquidity10: list[float], timestamp_utc: str
    ) -> list[float]:
        """Returns the 92D candidate vector (70 canonical + 22 temporal).

        The canonical 70D block is passed through UNTOUCHED (protected
        baseline); the temporal block is appended.
        """
        if len(vector70) != 70:
            raise ValueError(f"70D vector must be 70D, got {len(vector70)}")
        snap = self.update(liquidity10, timestamp_utc)
        return [float(v) for v in vector70] + list(snap.values)
