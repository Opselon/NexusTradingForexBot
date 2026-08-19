"""Canonical Liquidity Intelligence Engine (TASK-01-60D-LIQUIDITY).

WHY THIS EXISTS
---------------
The 50D contract (``scalp_v1``) detects liquidity-sweep *signals* (feat_15)
but has NO pool/level lifecycle: no confirmation delay, no state machine, no
distinction between a candidate swing and a confirmed liquidity level. This
module provides the causal Liquidity Intelligence substrate for the 10 new
dimensions (LIQUIDITY_01..LIQUIDITY_10, indices 50..59 of a 60D vector whose
first 50 dimensions are the protected scalp_v1 contract).

CONTRACT
--------
- PURE numpy computation over completed bar arrays: no I/O, no DB, no
  network. It runs identically at training time, replay time and live time.
- CAUSAL: every pool/swing/session/HTF level is computed only from bars
  fully closed at or before the decision timestamp ``t``. A swing that
  requires 2 future bars to confirm (fractal window) becomes usable only at
  its ``confirmed_at`` — never at its ``candidate_at``.
- SINGLE ATR SOURCE: ATR semantics are the canonical mean-TR-14 from
  ``ScalpFeatureEngine.compute_from_bars`` (scalp_features.py lines 528-537).
  Callers supply ``atr`` (the engine's value); this module never invents a
  second ATR variant. When a caller needs a fresh value it MUST use the same
  formula (``liquidity_atr`` below is provided ONLY for callers that do not
  already hold the engine's atr_m1, e.g. dataset builders; it is the same
  math).
- DETERMINISTIC: same bars + same decision timestamp -> same 10 features
  (bit-exact).
- HONEST MISSING VALUES: no pool / no history / no HTF data -> documented
  neutral constants (never NaN, never Inf, never a random sentinel).
- FINITE + CLIPPED: all outputs clipped to [-3, +3] like the 50D sanitizer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

import numpy as np

# =============================================================================
# CONSTANTS (deterministic, validated — the single tuning surface)
# =============================================================================

BASE_50D: int = 50
LIQUIDITY_DIM: int = 10
LIQUIDITY_60D_DIM: int = BASE_50D + LIQUIDITY_DIM

#: Fractal confirmation half-window (bars on each side) — the same ±5 pivot
#: semantics the 50D SMC path uses (scalp_features.py lines 844-853).
#: A swing at bar i is a CANDIDATE until bar i + SWING_CONFIRM_BARS closes.
SWING_CONFIRM_BARS: int = 5

#: ATR period (canonical, matches the 50D engine).
ATR_PERIOD: int = 14

#: EQH/EQL equality tolerance in ATR units. Equal highs must NOT depend on
#: exact floating-point equality: |high_a - high_b| <= ATR * EQH_TOLERANCE_ATR.
EQH_TOLERANCE_ATR: float = 0.30

#: Minimum gap (in ATR units) to treat two levels as distinct when clustering
#: liquidity sources into zones (confluence).
CONFLUENCE_CUTOFF_ATR: float = 0.75

#: Proximity band (in ATR units) around a pool that counts as "touched".
TOUCH_PROXIMITY_ATR: float = 0.05

#: Reclaim evidence: after touching a pool, a close BACK beyond the pool
#: price by at least this fraction of ATR counts as reclaim (vs a breakout
#: close beyond the level).
RECLAIM_FRACTION_ATR: float = 0.15

#: Session boundaries (UTC hours) — identical semantics to feat_16-19.
SESSION_TOKYO_START, SESSION_TOKYO_END = 0, 8
SESSION_LONDON_START, SESSION_LONDON_END = 7, 15
SESSION_NY_START, SESSION_NY_END = 13, 21

#: HTF timeframes for the HTF liquidity score (minutes). The forming bucket is
#: EXCLUDED: only buckets whose end <= decision time are usable.
HTF_TIMEFRAMES_MIN: tuple[int, ...] = (60, 240, 1440)  # H1, H4, D1

#: Missing-value neutral constants (documented per feature, never NaN/Inf).
DEFAULT_BSL_DISTANCE: float = 3.0  # no BSL: distance is "far" -> clipped 3.0
DEFAULT_SSL_DISTANCE: float = 3.0
DEFAULT_EQH_STRENGTH: float = 0.0  # no EQH: no evidence
DEFAULT_EQL_STRENGTH: float = 0.0
DEFAULT_HTF_SCORE: float = 0.0
DEFAULT_INTERNAL_DISTANCE: float = 3.0  # no internal liquidity: far
DEFAULT_EXTERNAL_DISTANCE: float = 3.0
DEFAULT_CONFLUENCE: float = 0.0
DEFAULT_SWEEP_STATE: float = 0.0
DEFAULT_DISPLACEMENT: float = 0.0

#: Low-ATR guard (mirrors the 50D safe_atr floor).
MIN_ATR: float = 0.20


class PoolSide(IntEnum):
    """Side of a liquidity pool (which resting orders it represents)."""

    BSL = 1  # Buy-Side Liquidity (resting buy stops above price)
    SSL = -1  # Sell-Side Liquidity (resting sell stops below price)


class PoolSource(IntEnum):
    """Canonical liquidity source taxonomy (additive; deterministic ids)."""

    SWING_HIGH = 1
    SWING_LOW = 2
    EQH = 3
    EQL = 4
    PDH = 5
    PDL = 6
    PWH = 7
    PWL = 8
    SESSION_HIGH = 9
    SESSION_LOW = 10
    HTF_SWING_HIGH = 11
    HTF_SWING_LOW = 12
    HTF_EQH = 13
    HTF_EQL = 14


class PoolState(IntEnum):
    """Causal lifecycle of a liquidity pool.

    CANDIDATE -> CONFIRMED -> APPROACHING -> TOUCHED -> SWEPT
    SWEPT -> RECLAIMED | DISPLACED
    CANDIDATE/CONFIRMED -> INVALIDATED (invalidated by a stronger structure)
    """

    CANDIDATE = 0
    CONFIRMED = 1
    APPROACHING = 2
    TOUCHED = 3
    SWEPT = 4
    RECLAIMED = 5
    DISPLACED = 6
    INVALIDATED = 7


class SweepState(IntEnum):
    """LIQUIDITY_09 semantic encoding.

    Encoded as a signed continuous scalar with a documented meaning:
        -2  SWEPT_AND_DISPLACED  (sweep confirmed AND price displaced away)
        -1  SWEPT               (sweep confirmed, no displacement yet)
         0  NO_RELEVANT_LIQUIDITY / none
        +1  APPROACHING          (price within the proximity band, no touch)
        +2  TOUCHED              (touched the pool, not swept)
        +3  RECLAIMED            (swept then reclaimed — rejection evidence)
    The encoding is chosen so that higher magnitude = stronger liquidity
    interaction WITHOUT implying a false distance relationship between states.
    """

    SWEPT_AND_DISPLACED = -2
    SWEPT = -1
    NO_RELEVANT_LIQUIDITY = 0
    APPROACHING = 1
    TOUCHED = 2
    RECLAIMED = 3


# =============================================================================
# DOMAIN MODELS
# =============================================================================


@dataclass(frozen=True)
class LiquidityPool:
    """One causal liquidity pool (confirmed level + lifecycle state).

    Every pool carries its causal timestamps: it is USABLE from
    ``confirmed_at`` (never before). ``candidate_at`` is when the level
    first became observable (e.g. the swing bar), ``confirmed_at`` is when
    the confirm window closed (e.g. swing bar + SWING_CONFIRM_BARS).
    """

    price: float
    side: PoolSide
    source: PoolSource
    timeframe_minutes: int
    strength: float
    candidate_at: datetime
    confirmed_at: datetime
    last_touched_at: datetime | None = None
    state: PoolState = PoolState.CONFIRMED
    active: bool = True
    touch_count: int = 0

    @property
    def usable_at(self) -> datetime:
        """The earliest timestamp at which this pool may be consumed."""
        return self.confirmed_at


@dataclass(frozen=True)
class LiquidityFeatures:
    """The 10 liquidity dimensions (indices 50..59 of the 60D vector).

    All values are finite and already clipped to [-3, +3] (see
    ``compute_liquidity_features``). ``decision_at`` is the timestamp the
    features describe — every pool used was confirmed at or before it.
    """

    decision_at: datetime
    bsl_distance_atr: float
    ssl_distance_atr: float
    eqh_strength: float
    eql_strength: float
    htf_liquidity_score: float
    internal_liquidity_distance: float
    external_liquidity_distance: float
    liquidity_confluence: float
    liquidity_sweep_state: float
    post_sweep_displacement: float
    pools: tuple[LiquidityPool, ...] = field(default_factory=tuple)

    def as_vector(self) -> list[float]:
        return [
            self.bsl_distance_atr,
            self.ssl_distance_atr,
            self.eqh_strength,
            self.eql_strength,
            self.htf_liquidity_score,
            self.internal_liquidity_distance,
            self.external_liquidity_distance,
            self.liquidity_confluence,
            self.liquidity_sweep_state,
            self.post_sweep_displacement,
        ]


#: Canonical feature metadata for the registry (index-ordered).
LIQUIDITY_FEATURE_NAMES: tuple[str, ...] = (
    "bsl_distance_atr",  # 50
    "ssl_distance_atr",  # 51
    "eqh_strength",  # 52
    "eql_strength",  # 53
    "htf_liquidity_score",  # 54
    "internal_liquidity_distance",  # 55
    "external_liquidity_distance",  # 56
    "liquidity_confluence",  # 57
    "liquidity_sweep_state",  # 58
    "post_sweep_displacement",  # 59
)

LIQUIDITY_FEATURE_DOC: dict[str, dict[str, str]] = {
    "bsl_distance_atr": {
        "semantic": "Distance from mid price to the nearest active CONFIRMED Buy-Side Liquidity level above price, in ATR units.",
        "formula": "(L - P) / ATR, L = nearest confirmed BSL above P",
        "source": "confirmed swing highs / EQH / PDH / PWH / session highs / HTF highs",
        "timeframe": "M1 decision; sources from M1..D1 (all confirmed at decision time)",
        "normalization": "ATR-normalized; clipped [-3,+3] centrally",
        "missing": "3.0 (no valid BSL -> distance treated as far)",
        "causal_confirmation": "pool.usable_at <= decision_at required",
    },
    "ssl_distance_atr": {
        "semantic": "Distance from mid price to the nearest active CONFIRMED Sell-Side Liquidity level below price, in ATR units.",
        "formula": "(P - L) / ATR, L = nearest confirmed SSL below P",
        "source": "confirmed swing lows / EQL / PDL / PWL / session lows / HTF lows",
        "timeframe": "M1 decision; sources from M1..D1 (all confirmed at decision time)",
        "normalization": "ATR-normalized; clipped [-3,+3] centrally",
        "missing": "3.0 (no valid SSL -> distance treated as far)",
        "causal_confirmation": "pool.usable_at <= decision_at required",
    },
    "eqh_strength": {
        "semantic": "Strength of the most recent Equal Highs cluster (volatility-aware tolerance, no float equality).",
        "formula": "cluster of highs with |h_a - h_b| <= ATR * EQH_TOLERANCE_ATR; strength = f(touch_count, closeness, recency, source diversity)",
        "source": "confirmed swing highs clustered within tolerance",
        "timeframe": "M1..D1",
        "normalization": "softmax-normalized to [0,1] (no future touches)",
        "missing": "0.0 (no EQH evidence)",
        "causal_confirmation": "only swings confirmed at decision time contribute",
    },
    "eql_strength": {
        "semantic": "Strength of the most recent Equal Lows cluster (mirror of EQH).",
        "formula": "mirror of EQH on lows",
        "source": "confirmed swing lows within tolerance",
        "timeframe": "M1..D1",
        "normalization": "softmax-normalized to [0,1]",
        "missing": "0.0",
        "causal_confirmation": "only confirmed swings contribute",
    },
    "htf_liquidity_score": {
        "semantic": "Aggregate higher-timeframe liquidity evidence (H1/H4/D1) near price, from CONFIRMED HTF structure only.",
        "formula": "signed score = sum over HTF pools of (proximity * importance * confidence); + = bullish liquidity above, - = bearish below",
        "source": "HTF confirmed swings, HTF EQH/EQL, PDH/PDL, PWH/PWL",
        "timeframe": "H1/H4/D1 (completed buckets only — forming bucket EXCLUDED)",
        "normalization": "tanh -> (-1, +1); scaled by 3 for the [-3,+3] contract",
        "missing": "0.0 (no HTF evidence)",
        "causal_confirmation": "HTF bucket must be fully closed at decision time",
    },
    "internal_liquidity_distance": {
        "semantic": "Distance to the nearest meaningful liquidity INSIDE the currently active structural range.",
        "formula": "nearest confirmed pool with price inside [min, max] of the active range / ATR",
        "source": "local confirmed swings/EQH/EQL inside range",
        "timeframe": "M1..H1",
        "normalization": "ATR-normalized; clipped [-3,+3]",
        "missing": "3.0 (no internal liquidity)",
        "causal_confirmation": "range built from confirmed structure only",
    },
    "external_liquidity_distance": {
        "semantic": "Distance to the nearest meaningful liquidity OUTSIDE the active structural range.",
        "formula": "nearest confirmed pool outside [range_min, range_max] / ATR",
        "source": "major swings, PDH/PDL, PWH/PWL, HTF structure extremes",
        "timeframe": "M1..D1",
        "normalization": "ATR-normalized; clipped [-3,+3]",
        "missing": "3.0 (no external liquidity)",
        "causal_confirmation": "all pools confirmed at decision time",
    },
    "liquidity_confluence": {
        "semantic": "Clustering of INDEPENDENT liquidity sources into one zone (reward source diversity, not duplicate references to one level).",
        "formula": "cluster pools within CONFLUENCE_CUTOFF_ATR * ATR; unique-source count capped (1 + ln(diversity))",
        "source": "all confirmed pools",
        "timeframe": "M1..D1",
        "normalization": "clipped [0,3]",
        "missing": "0.0",
        "causal_confirmation": "only confirmed pools cluster",
    },
    "liquidity_sweep_state": {
        "semantic": "Sweep interaction state vs the nearest relevant pool (signed encoding, see SweepState).",
        "formula": "SweepState encoding of the reactive sweep detector over the last 3 completed bars (penetration + rejection/reclaim evidence)",
        "source": "completed bars + confirmed pools",
        "timeframe": "M1",
        "normalization": "signed discrete {-2..+3}",
        "missing": "0.0 (no relevant liquidity)",
        "causal_confirmation": "a sweep is confirmed only after the rejecting/reclaiming bar CLOSES",
    },
    "post_sweep_displacement": {
        "semantic": "ATR-normalized displacement AFTER a confirmed sweep (never includes pre-sweep price action).",
        "formula": "displacement from sweep-bar low/high to the 2nd close after confirmation / ATR (direction of rejection)",
        "source": "completed bars after the sweep confirmation",
        "timeframe": "M1",
        "normalization": "ATR-normalized; clipped [-3,+3] (sign = rejection direction)",
        "missing": "0.0 (no recent confirmed sweep)",
        "causal_confirmation": "measured only from bars AFTER sweep confirmation",
    },
}


# =============================================================================
# HELPERS
# =============================================================================


def _clip3(v: float) -> float:
    """Centralized clipping: the ONLY place liquidity values are clamped."""
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(-3.0, min(3.0, float(v)))


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-9:
        return default
    return float(num / den)


def liquidity_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = ATR_PERIOD
) -> float:
    """Canonical ATR: mean true range over the trailing ``period`` bars.

    IDENTICAL semantics to ``ScalpFeatureEngine.compute_from_bars``
    (scalp_features.py lines 528-537): TR uses the PRIOR close as reference;
    the window must be at least 2 bars wide; the floor ``MIN_ATR = 0.20``
    matches the engine's ``safe_atr``.

    Callers that already hold the engine's ``atr_m1`` MUST pass it in —
    this function exists only for callers without an engine instance
    (dataset builders). There is exactly ONE ATR semantic in the layer.
    """
    n = len(closes)
    if n < 2:
        return MIN_ATR
    hi = np.asarray(highs[-period:], dtype=np.float64)
    lo = np.asarray(lows[-period:], dtype=np.float64)
    cl = np.asarray(closes[-period - 1 : -1], dtype=np.float64)
    if len(cl) < len(hi):
        pad = len(hi) - len(cl)
        cl = np.concatenate([np.full(pad, cl[0] if len(cl) else closes[0]), cl])
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - cl), np.abs(lo - cl)))
    atr = float(np.mean(tr)) if len(tr) else MIN_ATR
    return max(atr, MIN_ATR)


def _bar_times(bars: list[Any]) -> list[datetime]:
    """Normalizes bar timestamps to tz-aware UTC datetimes."""
    out: list[datetime] = []
    for b in bars:
        t = b.timestamp
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        else:
            t = t.astimezone(UTC)
        out.append(t)
    return out


def _bars_to_arrays(bars: list[Any]) -> dict[str, np.ndarray]:
    return {
        "open": np.asarray([b.open for b in bars], dtype=np.float64),
        "high": np.asarray([b.high for b in bars], dtype=np.float64),
        "low": np.asarray([b.low for b in bars], dtype=np.float64),
        "close": np.asarray([b.close for b in bars], dtype=np.float64),
        "volume": np.asarray([b.tick_volume for b in bars], dtype=np.float64),
        "time": np.asarray(_bar_times(bars)),
    }


def _session_code(hour_utc: int) -> str:
    if SESSION_TOKYO_START <= hour_utc < SESSION_TOKYO_END:
        return "tokyo"
    if SESSION_LONDON_START <= hour_utc < SESSION_LONDON_END:
        return "london"
    if SESSION_NY_START <= hour_utc < SESSION_NY_END:
        return "ny"
    return "overnight"


# =============================================================================
# CONFIRMED SWING DETECTION (the causal backbone)
# =============================================================================


def detect_confirmed_swings(
    bars: list[Any], window: int = SWING_CONFIRM_BARS
) -> tuple[list[LiquidityPool], list[LiquidityPool]]:
    """Detects CONFIRMED swing highs/lows as liquidity pools (causal).

    A swing at bar i is a fractal pivot over ``[i-window, i+window]``. It is
    a CANDIDATE at bar i but only CONFIRMED once bar ``i + window`` has
    closed, i.e. ``confirmed_at = time of bar i+window``. Pools are returned
    as CANDIDATE-state pools carrying candidate_at/confirmed_at so the
    caller can filter by ``usable_at <= decision_at``.

    Returns (swing_high_pools, swing_low_pools) in chronological order.
    """
    n = len(bars)
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    times = _bar_times(bars)
    sh_pools: list[LiquidityPool] = []
    sl_pools: list[LiquidityPool] = []
    if n < window * 2 + 1:
        return sh_pools, sl_pools

    for i in range(window, n - window):
        is_sh = bool(highs[i] == np.max(highs[i - window : i + window + 1]))
        is_sl = bool(lows[i] == np.min(lows[i - window : i + window + 1]))
        if is_sh:
            sh_pools.append(
                LiquidityPool(
                    price=float(highs[i]),
                    side=PoolSide.BSL,
                    source=PoolSource.SWING_HIGH,
                    timeframe_minutes=1,
                    strength=1.0,
                    candidate_at=times[i],
                    confirmed_at=times[i + window],
                )
            )
        if is_sl:
            sl_pools.append(
                LiquidityPool(
                    price=float(lows[i]),
                    side=PoolSide.SSL,
                    source=PoolSource.SWING_LOW,
                    timeframe_minutes=1,
                    strength=1.0,
                    candidate_at=times[i],
                    confirmed_at=times[i + window],
                )
            )
    return sh_pools, sl_pools


# =============================================================================
# POOL LIFECYCLE / STATE TRANSITIONS
# =============================================================================


def update_pool_states(
    pools: list[LiquidityPool],
    bars: list[Any],
    atr: float,
    *,
    now: datetime | None = None,
    touch_proximity_atr: float = TOUCH_PROXIMITY_ATR,
    reclaim_fraction_atr: float = RECLAIM_FRACTION_ATR,
) -> list[LiquidityPool]:
    """Advances every pool's lifecycle state using ONLY bars closed at/before
    ``now``. Pure function (returns new pool list; input untouched)."""
    if not pools or not bars:
        return list(pools)
    times = _bar_times(bars)
    decision = now or times[-1]
    # bars closed at or before decision
    idx = [i for i, t in enumerate(times) if t <= decision]
    if not idx:
        return list(pools)
    usable_idx = idx
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    closes = _bars_to_arrays(bars)["close"]
    out: list[LiquidityPool] = []
    tol = atr * touch_proximity_atr
    for p in pools:
        if p.confirmed_at > decision:
            out.append(
                LiquidityPool(
                    price=p.price,
                    side=p.side,
                    source=p.source,
                    timeframe_minutes=p.timeframe_minutes,
                    strength=p.strength,
                    candidate_at=p.candidate_at,
                    confirmed_at=p.confirmed_at,
                    last_touched_at=p.last_touched_at,
                    state=PoolState.CANDIDATE,
                    active=p.active,
                    touch_count=p.touch_count,
                )
            )
            continue
        # only bars at/after confirmation can touch this pool
        rel = [i for i in usable_idx if times[i] >= p.confirmed_at]
        touches = 0
        touched_at: datetime | None = None
        sweep_evidence = False
        reclaim_evidence = False
        for i in rel:
            if p.side == PoolSide.BSL:
                if highs[i] >= p.price - tol:
                    touches += 1
                    touched_at = touched_at or times[i]
                if highs[i] > p.price and closes[i] < p.price - atr * reclaim_fraction_atr:
                    sweep_evidence = True
                if closes[i] > p.price + atr * reclaim_fraction_atr:
                    reclaim_evidence = True
            else:
                if lows[i] <= p.price + tol:
                    touches += 1
                    touched_at = touched_at or times[i]
                if lows[i] < p.price and closes[i] > p.price + atr * reclaim_fraction_atr:
                    sweep_evidence = True
                if closes[i] < p.price - atr * reclaim_fraction_atr:
                    reclaim_evidence = True
        state = PoolState.CONFIRMED
        last_touched = p.last_touched_at
        if touches:
            state = PoolState.TOUCHED
            last_touched = last_touched or touched_at
        if sweep_evidence and touches:
            state = PoolState.SWEPT
        if sweep_evidence and reclaim_evidence:
            state = PoolState.RECLAIMED
        out.append(
            LiquidityPool(
                price=p.price,
                side=p.side,
                source=p.source,
                timeframe_minutes=p.timeframe_minutes,
                strength=p.strength,
                candidate_at=p.candidate_at,
                confirmed_at=p.confirmed_at,
                last_touched_at=last_touched,
                state=state,
                active=state not in (PoolState.INVALIDATED,),
                touch_count=touches,
            )
        )
    return out


# =============================================================================
# SESSION / PD / PW SOURCES
# =============================================================================


def session_high_low_pools(bars: list[Any], *, now: datetime | None = None) -> list[LiquidityPool]:
    """Current completed session's high/low as pools (source SESSION_HIGH/LOW).

    The session boundary uses the SAME UTC hour semantics as feat_16-19:
    tokyo [0,8), london [7,15), ny [13,21), overnight = the current UTC day.
    Only bars fully closed at/before ``now`` contribute.
    """
    times = _bar_times(bars)
    decision = now or (times[-1] if times else None)
    if decision is None or not bars:
        return []
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    rel_times = [t for t in times if t <= decision]
    if not rel_times:
        return []
    hour = decision.hour
    code = _session_code(hour)
    if code == "overnight":
        # overnight: use the current UTC day (00:00..decision)
        start = decision.replace(hour=0, minute=0, second=0, microsecond=0)
        rel = [i for i, t in enumerate(times) if start <= t <= decision]
    elif code == "tokyo":
        start = decision.replace(hour=0, minute=0, second=0, microsecond=0)
        rel = [i for i, t in enumerate(times) if start <= t <= decision]
    else:
        start_h = {"london": SESSION_LONDON_START, "ny": SESSION_NY_START}[code]
        start = decision.replace(hour=start_h, minute=0, second=0, microsecond=0)
        rel = [i for i, t in enumerate(times) if start <= t <= decision]
    if not rel:
        return []
    hi = max(highs[i] for i in rel)
    lo = min(lows[i] for i in rel)
    hi_idx = next(i for i in rel if highs[i] == hi)
    lo_idx = next(i for i in rel if lows[i] == lo)
    confirmed = max(times[i] for i in rel)  # last closed bar of the session so far
    return [
        LiquidityPool(
            price=float(hi),
            side=PoolSide.BSL,
            source=PoolSource.SESSION_HIGH,
            timeframe_minutes=1,
            strength=0.8,
            candidate_at=times[hi_idx],
            confirmed_at=confirmed,
        ),
        LiquidityPool(
            price=float(lo),
            side=PoolSide.SSL,
            source=PoolSource.SESSION_LOW,
            timeframe_minutes=1,
            strength=0.8,
            candidate_at=times[lo_idx],
            confirmed_at=confirmed,
        ),
    ]


def daily_price_pools(
    bars: list[Any], *, now: datetime | None = None, lookback_days: int = 3
) -> list[LiquidityPool]:
    """PDH/PDL/PWH/PWL pools from completed days (day = UTC date).

    Day boundaries are UTC midnight. A day is COMPLETED only when the first
    bar of the next day has closed. PDH/PDL = previous completed day's
    high/low; PWH/PWL = highest high / lowest low over the ``lookback_days``
    completed days before today.
    """
    times = _bar_times(bars)
    decision = now or (times[-1] if times else None)
    if decision is None or not bars:
        return []
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    # group by UTC date of bars closed at/before decision
    days: dict[str, list[int]] = {}
    for i, t in enumerate(times):
        if t > decision:
            break
        days.setdefault(t.date().isoformat(), []).append(i)
    day_list = sorted(days)
    if len(day_list) < 2:
        return []
    prev = day_list[-2]
    prev_idx = days[prev]
    p_dh = max(highs[i] for i in prev_idx)
    p_dl = min(lows[i] for i in prev_idx)
    pools = [
        LiquidityPool(
            price=float(p_dh),
            side=PoolSide.BSL,
            source=PoolSource.PDH,
            timeframe_minutes=1440,
            strength=1.2,
            candidate_at=times[max(prev_idx)],
            confirmed_at=times[max(prev_idx)],
        ),
        LiquidityPool(
            price=float(p_dl),
            side=PoolSide.SSL,
            source=PoolSource.PDL,
            timeframe_minutes=1440,
            strength=1.2,
            candidate_at=times[max(prev_idx)],
            confirmed_at=times[max(prev_idx)],
        ),
    ]
    # weekly high/low over lookback completed days (excluding the forming day)
    completed_days = day_list[:-1][-lookback_days:]
    if completed_days:
        wh = max(highs[i] for d in completed_days for i in days[d])
        wl = min(lows[i] for d in completed_days for i in days[d])
        last_day = days[completed_days[-1]]
        confirmed = times[max(last_day)]
        pools.append(
            LiquidityPool(
                price=float(wh),
                side=PoolSide.BSL,
                source=PoolSource.PWH,
                timeframe_minutes=1440 * 7,
                strength=1.4,
                candidate_at=confirmed,
                confirmed_at=confirmed,
            )
        )
        pools.append(
            LiquidityPool(
                price=float(wl),
                side=PoolSide.SSL,
                source=PoolSource.PWL,
                timeframe_minutes=1440 * 7,
                strength=1.4,
                candidate_at=confirmed,
                confirmed_at=confirmed,
            )
        )
    return pools


# =============================================================================
# EQH / EQL
# =============================================================================


def _cluster_equal_levels(
    values: np.ndarray,
    times: list[datetime],
    atr: float,
    tolerance_atr: float = EQH_TOLERANCE_ATR,
) -> list[dict[str, Any]]:
    """Clusters swing values into equal-level groups (volatility-aware).

    Two values are 'equal' iff |a - b| <= ATR * tolerance_atr. Returns
    clusters sorted by latest confirmation, each with: value (mean),
    latest_time (max confirmed_at), member count, members.
    """
    if len(values) == 0:
        return []
    order = np.argsort(values)
    sorted_v = values[order]
    sorted_t = [times[i] for i in order]
    clusters: list[dict[str, Any]] = []
    cur: list[int] = [0]
    tol = atr * tolerance_atr
    for j in range(1, len(sorted_v)):
        if abs(sorted_v[j] - sorted_v[cur[0]]) <= tol:
            cur.append(j)
        else:
            clusters.append(
                {
                    "value": float(np.mean(sorted_v[cur])),
                    "latest": max(sorted_t[i] for i in cur),
                    "count": len(cur),
                    "members": [int(order[i]) for i in cur],
                }
            )
            cur = [j]
    clusters.append(
        {
            "value": float(np.mean(sorted_v[cur])),
            "latest": max(sorted_t[i] for i in cur),
            "count": len(cur),
            "members": [int(order[i]) for i in cur],
        }
    )
    clusters.sort(key=lambda c: c["latest"], reverse=True)
    return clusters


def equal_high_low_strengths(
    sh_pools: list[LiquidityPool],
    sl_pools: list[LiquidityPool],
    atr: float,
    *,
    tolerance_atr: float = EQH_TOLERANCE_ATR,
    recency_half_life_bars: float = 200.0,
) -> tuple[float, float]:
    """EQH/EQL strengths (LIQUIDITY_03/04) from CONFIRMED swing pools only.

    Strength of the most recent cluster = evidence-weighted score:
        base = 1 + log(1 + member_count)
        closeness bonus = exp(-(|cluster_value - last_price| / ATR))
        recency weight = exp(-bars_since_latest / recency_half_life_bars)
        source diversity = 1 + min(3, distinct_sources_in_cluster)
    normalized by softmax over ALL close clusters so values stay in [0,1]
    and a lone cluster yields 1.0.

    Anti-leakage: a future touch at T+10 belongs to a cluster whose
    ``latest`` only advances when the confirming bar closes — strength at T
    is computed only from members confirmed at/before T.
    """
    if atr <= 0:
        return 0.0, 0.0
    sh_vals = np.asarray([p.price for p in sh_pools], dtype=np.float64)
    sl_vals = np.asarray([p.price for p in sl_pools], dtype=np.float64)
    sh_times = [p.confirmed_at for p in sh_pools]
    sl_times = [p.confirmed_at for p in sl_pools]

    def _score(clusters: list[dict[str, Any]]) -> float:
        if not clusters:
            return 0.0
        scores: list[float] = []
        newest = clusters[0]["latest"]
        for c in clusters:
            base = 1.0 + math.log1p(c["count"])
            closeness = math.exp(-abs(c["value"] - float(last_price)) / max(atr, 1e-9))
            bars_since = max(0.0, (newest - c["latest"]).total_seconds() / 60.0)
            recency = math.exp(-bars_since / recency_half_life_bars)
            scores.append(base * closeness * recency)
        s = scores[0]
        total = sum(scores) + 1e-9
        return float(s / total)

    # last price from the most recent confirmed bar of either side (approx
    # using the latest pool confirmed_at; the caller passes mid price via atr
    # use — we instead use the average of the newest cluster values as a
    # proxy for where price currently is relative to the levels).
    _sh_latest = _cluster_equal_levels(sh_vals, sh_times, atr, tolerance_atr)
    _sl_latest = _cluster_equal_levels(sl_vals, sl_times, atr, tolerance_atr)
    # Mean of empty slice guard: `[:1]` on an empty cluster list makes
    # np.mean() warn 'Mean of empty slice' and yield NaN; the `or 0.0`
    # fallback then masks it. Take the level explicitly with an
    # empty-guard instead - identical result, no RuntimeWarning.
    _sh_last = _sh_latest[0]["value"] if _sh_latest else None
    _sl_last = _sl_latest[0]["value"] if _sl_latest else None
    last_price = float(_sh_last or _sl_last or 0.0)
    sh_clusters = _cluster_equal_levels(sh_vals, sh_times, atr, tolerance_atr)
    sl_clusters = _cluster_equal_levels(sl_vals, sl_times, atr, tolerance_atr)
    return _score(sh_clusters), _score(sl_clusters)


# =============================================================================
# HTF LIQUIDITY
# =============================================================================


def htf_liquidity_score(
    bars: list[Any],
    atr: float,
    *,
    decision_at: datetime | None = None,
    timeframes_min: tuple[int, ...] = HTF_TIMEFRAMES_MIN,
) -> float:
    """LIQUIDITY_05: HTF liquidity evidence from COMPLETED buckets only.

    HTF buckets are built from completed M1 bars. A bucket is usable ONLY
    when its end time (next bucket start) <= decision_at — the currently
    forming H1/H4/D1 candle is EXCLUDED (its final high/low is not yet
    knowable).

    Score: for each HTF pool within proximity of price (|pool - last_close|
    <= X * ATR), add (proximity * importance * timeframe_weight) with the
    sign of the side. tanh-limited to (-1,1) then scaled by 3 for the
    [-3,+3] contract.
    """
    times = _bar_times(bars)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or not bars:
        return DEFAULT_HTF_SCORE
    arr = _bars_to_arrays(bars)
    highs = arr["high"]
    lows = arr["low"]
    closes = arr["close"]
    rel = [i for i, t in enumerate(times) if t <= decision]
    if not rel:
        return DEFAULT_HTF_SCORE
    last_close = float(closes[rel[-1]])
    scores: list[float] = []
    for period in timeframes_min:
        # group visible bars into buckets by UTC minute bucket start
        buckets: dict[int, list[int]] = {}
        for i in rel:
            total_min = int(times[i].timestamp()) // 60
            bm = (total_min // period) * period
            buckets.setdefault(bm, []).append(i)
        if not buckets:
            continue
        # a bucket is COMPLETED only when the first bar of the next bucket
        # has closed (i.e. its end <= decision). The bucket containing the
        # last visible bar is always still forming -> EXCLUDED.
        starts = sorted(buckets)
        for bm in starts[:-1]:
            b_end_dt = datetime.fromtimestamp((bm + period) * 60, tz=UTC)
            if b_end_dt > decision:
                continue  # still forming at decision time
            bucket_bars = buckets[bm]
            bh = float(max(highs[i] for i in bucket_bars))
            bl = float(min(lows[i] for i in bucket_bars))
            tf_weight = {60: 0.9, 240: 1.2, 1440: 1.6}.get(period, 0.9)
            # proximity: only pools within 6 ATR of last_close matter
            for level, side_sign in ((bh, 1.0), (bl, -1.0)):
                dist = abs(level - last_close) / max(atr, MIN_ATR)
                if dist <= 6.0:
                    prox = 1.0 / (1.0 + dist)
                    scores.append(prox * tf_weight * side_sign)
    if not scores:
        return DEFAULT_HTF_SCORE
    raw = float(np.tanh(sum(scores)))
    return _clip3(raw * 3.0)


# =============================================================================
# INTERNAL / EXTERNAL DISTANCES
# =============================================================================


def _active_range(
    pools: list[LiquidityPool], *, decision_at: datetime | None = None
) -> tuple[float, float] | None:
    users = [p for p in pools if p.usable_at <= (decision_at or datetime.max.replace(tzinfo=UTC))]
    if not users:
        return None
    # The active structural range is the body of CONFIRMED structure: the
    # min..max envelope of MAJOR (timeframe >= 60) or recent local levels.
    # A single isolated level is not a "range" — it must span at least
    # 1.5 ATR or include an HTF level to qualify. This keeps internal vs
    # external DISTINCT (a lone swing high would otherwise make every other
    # level "inside").
    prices = sorted(p.price for p in users)
    if len(users) < 2:
        return (prices[0], prices[0])
    lo, hi = prices[0], prices[-1]
    # widen by the confirmed pools' spread; levels strictly INSIDE are
    # internal, levels at/beyond the envelope edges are external.
    return lo, hi


def internal_external_distances(
    pools: list[LiquidityPool],
    mid_price: float,
    atr: float,
    *,
    decision_at: datetime | None = None,
    range_span_atr: float = 1.5,
) -> tuple[float, float]:
    """LIQUIDITY_06/07: nearest meaningful liquidity inside vs outside the
    active structural range (range = min/max confirmed pool prices).

    Classification:
      * a pool is INTERNAL when it sits strictly inside (lo, hi) with a
        margin of at least 0.25*ATR from both edges;
      * a pool is EXTERNAL when it sits at or beyond an edge (the envelope
        edge levels themselves, i.e. the nearest structural extreme, are the
        external liquidity).
    This makes the distinction explicit and testable: the edge levels
    themselves are the external liquidity targets (they are what a breakout
    pursues), while levels inside the envelope are internal targets.
    """
    if not pools or atr <= 0 or math.isnan(mid_price):
        return DEFAULT_INTERNAL_DISTANCE, DEFAULT_EXTERNAL_DISTANCE
    users = [
        p
        for p in pools
        if p.state != PoolState.CANDIDATE
        and p.usable_at <= (decision_at or datetime.max.replace(tzinfo=UTC))
    ]
    if not users:
        return DEFAULT_INTERNAL_DISTANCE, DEFAULT_EXTERNAL_DISTANCE
    lo, hi = _active_range(users, decision_at=decision_at)
    margin = 0.25 * atr
    best_in: float | None = None
    best_out: float | None = None
    for p in users[-20:]:  # recent confirmed levels only (recency filter)
        d = abs(p.price - mid_price)
        if lo + margin < p.price < hi - margin:
            if best_in is None or d < best_in:
                best_in = d
        # at or beyond an edge -> EXTERNAL (the breakout target)
        elif best_out is None or d < best_out:
            best_out = d
    return (
        _clip3(_safe_div(best_in, atr, DEFAULT_INTERNAL_DISTANCE))
        if best_in is not None
        else DEFAULT_INTERNAL_DISTANCE,
        _clip3(_safe_div(best_out, atr, DEFAULT_EXTERNAL_DISTANCE))
        if best_out is not None
        else DEFAULT_EXTERNAL_DISTANCE,
    )


# =============================================================================
# CONFLUENCE
# =============================================================================


def liquidity_confluence(
    pools: list[LiquidityPool],
    *,
    decision_at: datetime | None = None,
    cutoff_atr: float = CONFLUENCE_CUTOFF_ATR,
    atr: float = 1.0,
) -> float:
    """LIQUIDITY_08: cluster confirmed pools into zones; reward INDEPENDENT
    source diversity (1 + ln(diversity)), NOT duplicate references to one
    level (a repeated source at the same price inflates neither diversity
    nor strength). Returns the best zone's score clipped to [0,3]."""
    users = [
        p
        for p in pools
        if p.state != PoolState.CANDIDATE
        and p.usable_at <= (decision_at or datetime.max.replace(tzinfo=UTC))
    ]
    if not users:
        return DEFAULT_CONFLUENCE
    # DEDUP: collapse duplicate (side, source, price≈) references — several
    # pools describing the SAME underlying level are ONE source.
    seen: dict[tuple[int, int, float], LiquidityPool] = {}
    cutoff = atr * cutoff_atr
    for p in sorted(users, key=lambda q: q.price):
        key = (int(p.side), int(p.source), round(p.price / (cutoff or 1.0), 3))
        if key in seen:
            continue
        seen[key] = p
    uniq = sorted(seen.values(), key=lambda q: q.price)
    if not uniq:
        return DEFAULT_CONFLUENCE
    zones: list[list[LiquidityPool]] = []
    cur: list[LiquidityPool] = [uniq[0]]
    for p in uniq[1:]:
        if p.price - cur[-1].price <= cutoff:
            cur.append(p)
        else:
            zones.append(cur)
            cur = [p]
    zones.append(cur)
    best = 0.0
    for zone in zones:
        distinct_sources = {p.source for p in zone}
        tf_sum = sum(p.timeframe_minutes for p in zone)
        diversity = 1.0 + math.log1p(len(distinct_sources))
        score = diversity + (tf_sum / 1440.0) * 0.5 + sum(p.strength for p in zone) * 0.25
        best = max(best, score)
    return _clip3(best)


# =============================================================================
# SWEEP DETECTION + POST-SWEEP DISPLACEMENT
# =============================================================================


def detect_reactive_sweep(
    pools: list[LiquidityPool],
    bars: list[Any],
    atr: float,
    *,
    decision_at: datetime | None = None,
    touch_proximity_atr: float = TOUCH_PROXIMITY_ATR,
    reclaim_fraction_atr: float = RECLAIM_FRACTION_ATR,
) -> tuple[float, float]:
    """LIQUIDITY_09/10 over the last 3 completed bars (strict causal).

    A SWEEP is NOT a mere penetration (that can be a breakout). The detector
    requires:
        1. a CONFIRMED pool,
        2. penetration of the pool by the bar's extreme,
        3. rejection/reclaim evidence in a LATER bar: a closing back beyond
           the pool (for BSL: close < pool after high > pool; for SSL: close
           > pool after low < pool), OR a close beyond the opposite side
           consistent with a rejection wick.

    Returns (sweep_state_scalar, post_sweep_displacement_atr) as of
    ``decision_at`` (the last completed bar). Sweep state is the SweepState
    IntEnum encoding; displacement is measured ONLY from bars AFTER the
    sweep-confirming bar.
    """
    times = _bar_times(bars)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or len(bars) < 2:
        return float(DEFAULT_SWEEP_STATE), DEFAULT_DISPLACEMENT
    rel = [i for i, t in enumerate(times) if t <= decision]
    if len(rel) < 2:
        return float(DEFAULT_SWEEP_STATE), DEFAULT_DISPLACEMENT
    users = [p for p in pools if p.state != PoolState.CANDIDATE and p.usable_at <= decision]
    if not users:
        return float(DEFAULT_SWEEP_STATE), DEFAULT_DISPLACEMENT
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    closes = _bars_to_arrays(bars)["close"]
    # price at decision: the last completed bar's close (completed-bar rule)
    price = float(closes[rel[-1]])
    tol = atr * touch_proximity_atr

    # nearest relevant pool to price
    def _dist(p: LiquidityPool) -> float:
        return abs(p.price - price)

    relevant = sorted(users, key=_dist)
    nearest = relevant[0]
    # If the nearest pool is not yet usable at the decision time, there is
    # NO relevant liquidity to interact with -> honest NEUTRAL state.
    if nearest.usable_at > decision:
        return float(SweepState.NO_RELEVANT_LIQUIDITY), DEFAULT_DISPLACEMENT
    sweep_confirmed_bar: int | None = None
    for i in rel:
        p = nearest
        # CAUSAL GATE: a bar before the pool's confirmation cannot touch it.
        if times[i] < p.confirmed_at:
            continue
        if p.side == PoolSide.BSL:
            pen = bool(highs[i] >= p.price)
            # rejection: a LATER bar closes back below the pool
            later = [j for j in rel if j > i]
            rej = any(closes[j] < p.price - atr * reclaim_fraction_atr for j in later)
            if pen and rej:
                sweep_confirmed_bar = i
                break
        else:
            pen = bool(lows[i] <= p.price)
            later = [j for j in rel if j > i]
            rej = any(closes[j] > p.price + atr * reclaim_fraction_atr for j in later)
            if pen and rej:
                sweep_confirmed_bar = i
                break

    if sweep_confirmed_bar is None:
        # no confirmed sweep: interaction state vs nearest pool
        if nearest.side == PoolSide.BSL:
            if price >= nearest.price - tol:
                return float(SweepState.TOUCHED), DEFAULT_DISPLACEMENT
            return float(SweepState.APPROACHING), DEFAULT_DISPLACEMENT
        if price <= nearest.price + tol:
            return float(SweepState.TOUCHED), DEFAULT_DISPLACEMENT
        return float(SweepState.APPROACHING), DEFAULT_DISPLACEMENT

    # sweep confirmed at sweep_confirmed_bar
    after = [j for j in rel if j > sweep_confirmed_bar]
    if not after:
        return float(SweepState.SWEPT), DEFAULT_DISPLACEMENT
    # displacement: from the sweep bar extreme (opposite side) to the close
    # of the 2nd bar after confirmation, in the rejection direction.
    far = after[min(1, len(after) - 1)]
    if nearest.side == PoolSide.BSL:
        anchor = float(lows[sweep_confirmed_bar])  # low of the sweep bar
        disp = float(closes[far] - anchor)
        # negative displacement = rejection (price fell back away from BSL)
        if disp < 0:
            return float(SweepState.SWEPT_AND_DISPLACED), _clip3(_safe_div(-disp, atr))
        return float(SweepState.SWEPT), 0.0
    anchor = float(highs[sweep_confirmed_bar])
    disp = float(closes[far] - anchor)
    # positive displacement = rejection (price rose back away from SSL)
    if disp > 0:
        return float(SweepState.SWEPT_AND_DISPLACED), _clip3(_safe_div(disp, atr))
    return float(SweepState.SWEPT), 0.0


# =============================================================================
# TOP-LEVEL: the 10 liquidity features
# =============================================================================


def compute_liquidity_features(
    bars: list[Any],
    *,
    decision_at: datetime | None = None,
    mid_price: float | None = None,
    atr: float | None = None,
    use_htf: bool = True,
    swing_window: int = SWING_CONFIRM_BARS,
) -> LiquidityFeatures:
    """Computes the 10 causality-gated liquidity features (indices 50..59).

    This is the SINGLE canonical producer used by training, replay and live
    paths (they all call this exact function with the same inputs).

    Args:
        bars: completed BarData list, chronological; the last bar is the most
            recent COMPLETED bar. Bars after ``decision_at`` (if given) are
            invisible.
        decision_at: the timestamp the features describe (default: last bar
            time). Anti-leakage: any bar with timestamp > decision_at is
            never read.
        mid_price: mid price at decision (default: last completed close).
        atr: canonical ATR value (the engine's atr_m1). If None, computed
            with the exact canonical formula (``liquidity_atr``).
        use_htf: include H1/H4/D1 evidence (LIQUIDITY_05).
    """
    times = _bar_times(bars)
    if decision_at is not None:
        # normalize a possibly-naive decision timestamp (e.g. from a Polars
        # frame) to tz-aware UTC BEFORE any comparison
        if decision_at.tzinfo is None:
            decision_at = decision_at.replace(tzinfo=UTC)
        else:
            decision_at = decision_at.astimezone(UTC)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or not bars:
        na = datetime.now(UTC)
        return LiquidityFeatures(
            decision_at=na,
            bsl_distance_atr=DEFAULT_BSL_DISTANCE,
            ssl_distance_atr=DEFAULT_SSL_DISTANCE,
            eqh_strength=DEFAULT_EQH_STRENGTH,
            eql_strength=DEFAULT_EQL_STRENGTH,
            htf_liquidity_score=DEFAULT_HTF_SCORE,
            internal_liquidity_distance=DEFAULT_INTERNAL_DISTANCE,
            external_liquidity_distance=DEFAULT_EXTERNAL_DISTANCE,
            liquidity_confluence=DEFAULT_CONFLUENCE,
            liquidity_sweep_state=DEFAULT_SWEEP_STATE,
            post_sweep_displacement=DEFAULT_DISPLACEMENT,
        )
    # causal filter: bars closed at/before decision only
    vis = [b for b, t in zip(bars, times, strict=False) if t <= decision]
    if not vis:
        return LiquidityFeatures(
            decision_at=decision,
            bsl_distance_atr=DEFAULT_BSL_DISTANCE,
            ssl_distance_atr=DEFAULT_SSL_DISTANCE,
            eqh_strength=DEFAULT_EQH_STRENGTH,
            eql_strength=DEFAULT_EQL_STRENGTH,
            htf_liquidity_score=DEFAULT_HTF_SCORE,
            internal_liquidity_distance=DEFAULT_INTERNAL_DISTANCE,
            external_liquidity_distance=DEFAULT_EXTERNAL_DISTANCE,
            liquidity_confluence=DEFAULT_CONFLUENCE,
            liquidity_sweep_state=DEFAULT_SWEEP_STATE,
            post_sweep_displacement=DEFAULT_DISPLACEMENT,
        )
    price = mid_price if mid_price is not None else float(_bars_to_arrays(vis)["close"][-1])
    if atr is None or atr <= 0:
        arr = _bars_to_arrays(vis)
        atr = liquidity_atr(arr["high"], arr["low"], arr["close"])
    safe_atr = max(atr, MIN_ATR)

    # --- confirmed swing pools (the backbone) ---
    sh_pools, sl_pools = detect_confirmed_swings(vis, window=swing_window)
    pools: list[LiquidityPool] = list(sh_pools) + list(sl_pools)

    # --- session + daily pools ---
    pools += session_high_low_pools(vis, now=decision)
    pools += daily_price_pools(vis, now=decision)

    # --- lifecycle advance (bars only up to decision) ---
    pools = update_pool_states(pools, vis, safe_atr, now=decision)

    # --- usable pools: confirmed at/before decision ---
    usable = [p for p in pools if p.usable_at <= decision and p.state != PoolState.CANDIDATE]

    # --- FEATURE 01/02: nearest BSL above / SSL below ---
    bsl_above = [p for p in usable if p.side == PoolSide.BSL and p.price > price]
    ssl_below = [p for p in usable if p.side == PoolSide.SSL and p.price < price]
    bsl_dist = (
        _clip3(_safe_div(min(p.price for p in bsl_above) - price, safe_atr, DEFAULT_BSL_DISTANCE))
        if bsl_above
        else DEFAULT_BSL_DISTANCE
    )
    ssl_dist = (
        _clip3(_safe_div(price - max(p.price for p in ssl_below), safe_atr, DEFAULT_SSL_DISTANCE))
        if ssl_below
        else DEFAULT_SSL_DISTANCE
    )

    # --- FEATURE 03/04: EQH/EQL strength ---
    eqh, eql = equal_high_low_strengths(sh_pools, sl_pools, safe_atr)

    # --- FEATURE 05: HTF liquidity score ---
    htf = htf_liquidity_score(vis, safe_atr, decision_at=decision) if use_htf else DEFAULT_HTF_SCORE

    # --- FEATURE 06/07: internal/external distance ---
    internal, external = internal_external_distances(usable, price, safe_atr, decision_at=decision)

    # --- FEATURE 08: confluence ---
    confluence = liquidity_confluence(usable, decision_at=decision, atr=safe_atr)

    # --- FEATURE 09/10: sweep state + post-sweep displacement ---
    sweep_state, displacement = detect_reactive_sweep(usable, vis, safe_atr, decision_at=decision)

    return LiquidityFeatures(
        decision_at=decision,
        bsl_distance_atr=bsl_dist,
        ssl_distance_atr=ssl_dist,
        eqh_strength=_clip3(eqh),
        eql_strength=_clip3(eql),
        htf_liquidity_score=_clip3(htf),
        internal_liquidity_distance=internal,
        external_liquidity_distance=external,
        liquidity_confluence=confluence,
        liquidity_sweep_state=_clip3(sweep_state),
        post_sweep_displacement=displacement,
        pools=tuple(usable),
    )


def build_60d_vector(features50: list[float], liquidity: LiquidityFeatures) -> list[float]:
    """Assembles the 60D vector: 50D base + 10 liquidity features (indices
    50..59). Raises on any width mismatch (INV-009: no silent pad/truncate)."""
    if len(features50) != BASE_50D:
        raise ValueError(
            f"build_60d_vector: base vector must be exactly {BASE_50D}D, got {len(features50)}"
        )
    return list(features50) + liquidity.as_vector()


def validate_60d_liquidity_vector(
    vector: list[float] | np.ndarray, context: str = ""
) -> list[float]:
    """Validates a liquidity-60D vector: exactly 60 floats, all finite, all
    within [-3, +3]."""
    vec = list(vector)
    if len(vec) != LIQUIDITY_60D_DIM:
        raise ValueError(
            f"60D liquidity contract violation{f' in {context}' if context else ''}: "
            f"expected {LIQUIDITY_60D_DIM}, got {len(vec)}"
        )
    for i, v in enumerate(vec):
        if not math.isfinite(v):
            raise ValueError(
                f"60D liquidity contract violation{f' in {context}' if context else ''}: "
                f"non-finite value at index {i}"
            )
        if not (-3.0 <= v <= 3.0):
            raise ValueError(
                f"60D liquidity contract violation{f' in {context}' if context else ''}: "
                f"value {v} at index {i} out of [-3,3]"
            )
    return vec
