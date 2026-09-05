"""LIQUIDITY ENGINE v1.1 CANDIDATE — TASK-06-70D-LIQUIDITY-OPTIMIZATION.

Candidate-only optimization of the committed `liquidity_engine.py` (v1).
This module IMPORTS every production function from the frozen v1 engine and
re-parameterizes / fixes only the parts the forensic audit PROVED weak.
The frozen `liquidity_engine.py` stays byte-identical (the golden baseline).

CHANGES vs v1 (each evidence-backed, see docs/LIQUIDITY_70D_OPTIMIZATION_REPORT.md):

1. [BUG FIX / FEATURE_CALCULATION] EQH_EQL_STRENGTH
   v1 bug: `equal_high_low_strengths` computed closeness against the NEWEST
   CLUSTER VALUE as "last_price" (never the real price) → eqh_strength is a
   near-step (median 0.88; unique ~0.85 at 0/1) that does not measure
   strength at all. v1.1 threads `mid_price` through and uses
   `closeness = 1/(1+d/ATR)` (d=0→1, d=10ATR→0.09; preserves the member-count
   signal so the existing contract tests keep their semantics).

2. [BUG FIX / CAUSALITY] SWEEP_NEAREST_POOL_RELEVANCE
   v1 bug: `detect_reactive_sweep` returned APPROACHING(+1)/TOUCHED(+2) for the
   nearest pool EVEN WHEN IT WAS 200 ATR AWAY (no relevance gate) → 40%
   of rows flood +1. v1.1 adds `SWEEP_RELEVANCE_ATR` (default 2.0): pools
   beyond it are NO_RELEVANT_LIQUIDITY(0).

3. [PARAMETER] EQH_TOLERANCE_ATR searchable {0.15,0.30,0.45}.
4. [PARAMETER] CONFLUENCE_CUTOFF_ATR searchable {0.50,0.75,1.00}.
5. [PARAMETER] RECLAIM_FRACTION_ATR searchable {0.10,0.15,0.20}.
6. [PARAMETER] HTF_PROXIMITY_ATR searchable {4,6,8} (v1 hard-coded 6).
7. [PARAMETER] SWEEP_WINDOW_BARS searchable {2,3,5} (v1 hard-coded 3).

ALL outputs remain finite, clipped [-3,+3], causal (the v1 anti-leakage
tests are inherited — the changed functions keep the same bar-visibility
rules). The base 50D + News family are untouched (this module does not even
reference them).

Version constant: LIQUIDITY_ALGORITHM_VERSION = "liquidity-v1.1"
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from nexus_scalp.features.liquidity_engine import (
    MIN_ATR,
    PoolSide,
    PoolState,
    SweepState,
    _bar_times,
    _bars_to_arrays,
    _clip3,
    _safe_div,
    detect_confirmed_swings,
    liquidity_atr,
)

#: Version of THIS candidate algorithm. Models trained on it must record it.
LIQUIDITY_ALGORITHM_VERSION: str = "liquidity-v1.1"

#: Defaults for the searchable surface (v1-compatible where possible).
EQH_TOLERANCE_ATR: float = 0.30
CONFLUENCE_CUTOFF_ATR: float = 0.75
RECLAIM_FRACTION_ATR: float = 0.15
TOUCH_PROXIMITY_ATR: float = 0.05
SWEEP_RELEVANCE_ATR: float = 2.0
HTF_PROXIMITY_ATR: float = 6.0
SWEEP_WINDOW_BARS: int = 3


@dataclass(frozen=True)
class LiquidityParams:
    """The tunable surface (bounded; every field has min/max/step in the report)."""

    eqh_tolerance_atr: float = EQH_TOLERANCE_ATR
    confluence_cutoff_atr: float = CONFLUENCE_CUTOFF_ATR
    reclaim_fraction_atr: float = RECLAIM_FRACTION_ATR
    sweep_relevance_atr: float = SWEEP_RELEVANCE_ATR
    htf_proximity_atr: float = HTF_PROXIMITY_ATR
    sweep_window_bars: int = SWEEP_WINDOW_BARS

    def as_dict(self) -> dict[str, float]:
        return {
            "eqh_tolerance_atr": self.eqh_tolerance_atr,
            "confluence_cutoff_atr": self.confluence_cutoff_atr,
            "reclaim_fraction_atr": self.reclaim_fraction_atr,
            "sweep_relevance_atr": self.sweep_relevance_atr,
            "htf_proximity_atr": self.htf_proximity_atr,
            "sweep_window_bars": float(self.sweep_window_bars),
        }


# ---------------------------------------------------------------------------
# FIX 1: price-aware EQH/EQL strength
# ---------------------------------------------------------------------------


def _cluster_equal_levels(
    values: np.ndarray, times: list[datetime], atr: float, tolerance_atr: float
) -> list[dict[str, Any]]:
    """Clusters swing values into equal-level groups (volatility-aware).

    Identical semantics to v1 (sort + gap-cluster against the first member).
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


def equal_high_low_strengths_v1_1(
    sh_pools: list[Any],
    sl_pools: list[Any],
    atr: float,
    mid_price: float,
    *,
    tolerance_atr: float = EQH_TOLERANCE_ATR,
    recency_half_life_bars: float = 200.0,
) -> tuple[float, float]:
    """Price-aware EQH/EQL strength (FIX 1).

    score(cluster) = base * closeness * recency, softmax-normalized:
      base     = 1 + log1p(count)          (evidence: member count)
      closeness= 1 / (1 + d/ATR)           (d = |cluster_value - mid_price|)
      recency  = exp(-bars_since/200)
    The newest cluster near price wins; a cluster far from price scores ~0
    even with many members; a lone cluster AT price = 1.0.
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
            d = abs(c["value"] - mid_price)
            closeness = 1.0 / (1.0 + d / max(atr, 1e-9))
            bars_since = max(0.0, (newest - c["latest"]).total_seconds() / 60.0)
            recency = math.exp(-bars_since / recency_half_life_bars)
            scores.append(base * closeness * recency)
        s = scores[0]
        total = sum(scores) + 1e-9
        return float(s / total)

    sh_clusters = _cluster_equal_levels(sh_vals, sh_times, atr, tolerance_atr)
    sl_clusters = _cluster_equal_levels(sl_vals, sl_times, atr, tolerance_atr)
    return _score(sh_clusters), _score(sl_clusters)


# ---------------------------------------------------------------------------
# FIX 2: sweep relevance gate + parameterized sweep window
# ---------------------------------------------------------------------------


def detect_reactive_sweep_v1_1(
    pools: list[Any],
    bars: list[Any],
    atr: float,
    *,
    decision_at: datetime | None = None,
    touch_proximity_atr: float = TOUCH_PROXIMITY_ATR,
    reclaim_fraction_atr: float = RECLAIM_FRACTION_ATR,
    relevance_atr: float = SWEEP_RELEVANCE_ATR,
    window_bars: int = SWEEP_WINDOW_BARS,
) -> tuple[float, float]:
    """v1.1 sweep detector: relevance-gated + bounded confirmation window.

    Same causal skeleton as v1 (penetration then rejection on later bars)
    plus:
      - pools farther than relevance_atr * ATR from price are
        NO_RELEVANT_LIQUIDITY (0.0) — they are NOT 'approaching'.
      - confirmation search is bounded to the last ``window_bars`` completed
        bars (the reactive window), so a sweep 100 bars ago is not re-asserted
        forever.
    Returns (sweep_state_scalar, post_sweep_displacement_atr).
    """
    times = _bar_times(bars)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or len(bars) < 2:
        return float(SweepState.NO_RELEVANT_LIQUIDITY), 0.0
    rel = [i for i, t in enumerate(times) if t <= decision]
    if len(rel) < 2:
        return float(SweepState.NO_RELEVANT_LIQUIDITY), 0.0
    users = [p for p in pools if p.state != PoolState.CANDIDATE and p.usable_at <= decision]
    if not users:
        return float(SweepState.NO_RELEVANT_LIQUIDITY), 0.0
    highs = _bars_to_arrays(bars)["high"]
    lows = _bars_to_arrays(bars)["low"]
    closes = _bars_to_arrays(bars)["close"]
    price = float(closes[rel[-1]])
    tol = atr * touch_proximity_atr

    # relevance gate (FIX 2)
    relevant = sorted(users, key=lambda p: abs(p.price - price))
    nearest = relevant[0]
    if abs(nearest.price - price) > atr * relevance_atr:
        return float(SweepState.NO_RELEVANT_LIQUIDITY), 0.0

    # bounded reactive window (param)
    tail = rel[-int(window_bars) :] if int(window_bars) > 0 else rel
    sweep_confirmed_bar: int | None = None
    for i in tail:
        p = nearest
        if times[i] < p.confirmed_at:
            continue
        if p.side == PoolSide.BSL:
            pen = bool(highs[i] >= p.price)
            later = [j for j in tail if j > i]
            rej = any(closes[j] < p.price - atr * reclaim_fraction_atr for j in later)
            if pen and rej:
                sweep_confirmed_bar = i
                break
        else:
            pen = bool(lows[i] <= p.price)
            later = [j for j in tail if j > i]
            rej = any(closes[j] > p.price + atr * reclaim_fraction_atr for j in later)
            if pen and rej:
                sweep_confirmed_bar = i
                break

    if sweep_confirmed_bar is None:
        # interaction state (only for relevant pools)
        if nearest.side == PoolSide.BSL:
            if price >= nearest.price - tol:
                return float(SweepState.TOUCHED), 0.0
            return float(SweepState.APPROACHING), 0.0
        if price <= nearest.price + tol:
            return float(SweepState.TOUCHED), 0.0
        return float(SweepState.APPROACHING), 0.0

    after = [j for j in rel if j > sweep_confirmed_bar]
    if not after:
        return float(SweepState.SWEPT), 0.0
    after[0]
    far = after[min(1, len(after) - 1)]
    if nearest.side == PoolSide.BSL:
        anchor = float(lows[sweep_confirmed_bar])
        disp = float(closes[far] - anchor)
        if disp < 0:
            return float(SweepState.SWEPT_AND_DISPLACED), _clip3(_safe_div(-disp, atr))
        return float(SweepState.SWEPT), 0.0
    anchor = float(highs[sweep_confirmed_bar])
    disp = float(closes[far] - anchor)
    if disp > 0:
        return float(SweepState.SWEPT_AND_DISPLACED), _clip3(_safe_div(disp, atr))
    return float(SweepState.SWEPT), 0.0


# ---------------------------------------------------------------------------
# FIX 3: parameterized HTF proximity
# ---------------------------------------------------------------------------


def htf_liquidity_score_v1_1(
    bars: list[Any],
    atr: float,
    *,
    decision_at: datetime | None = None,
    timeframes_min: tuple[int, ...] = (60, 240, 1440),
    proximity_atr: float = HTF_PROXIMITY_ATR,
) -> float:
    """v1.1 HTF liquidity score (parameterized proximity band).

    Identical bucket logic (completed buckets only, forming excluded).
    """
    times = _bar_times(bars)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or not bars:
        return 0.0
    arr = _bars_to_arrays(bars)
    highs = arr["high"]
    lows = arr["low"]
    closes = arr["close"]
    rel = [i for i, t in enumerate(times) if t <= decision]
    if not rel:
        return 0.0
    last_close = float(closes[rel[-1]])
    scores: list[float] = []
    for period in timeframes_min:
        buckets: dict[int, list[int]] = {}
        for i in rel:
            total_min = int(times[i].timestamp()) // 60
            bm = (total_min // period) * period
            buckets.setdefault(bm, []).append(i)
        if not buckets:
            continue
        starts = sorted(buckets)
        for bm in starts[:-1]:
            b_end_dt = datetime.fromtimestamp((bm + period) * 60, tz=UTC)
            if b_end_dt > decision:
                continue
            bucket_bars = buckets[bm]
            bh = float(max(highs[i] for i in bucket_bars))
            bl = float(min(lows[i] for i in bucket_bars))
            tf_weight = {60: 0.9, 240: 1.2, 1440: 1.6}.get(period, 0.9)
            for level, side_sign in ((bh, 1.0), (bl, -1.0)):
                dist = abs(level - last_close) / max(atr, MIN_ATR)
                if dist <= proximity_atr:
                    prox = 1.0 / (1.0 + dist)
                    scores.append(prox * tf_weight * side_sign)
    if not scores:
        return 0.0
    raw = float(np.tanh(sum(scores)))
    return _clip3(raw * 3.0)


# ---------------------------------------------------------------------------
# FIX 4: parameterized confluence (cutoff) — thin wrapper around v1 logic
# ---------------------------------------------------------------------------


def _dedup_pools(pools: list[Any], cutoff: float) -> list[Any]:
    """Collapse duplicate (side, source, price≈) references — same as v1."""
    seen: dict[tuple[int, int, float], Any] = {}
    for p in sorted(pools, key=lambda q: q.price):
        key = (int(p.side), int(p.source), round(p.price / (cutoff or 1.0), 3))
        if key in seen:
            continue
        seen[key] = p
    return sorted(seen.values(), key=lambda q: q.price)


def liquidity_confluence_v1_1(
    pools: list[Any],
    *,
    decision_at: datetime | None = None,
    cutoff_atr: float = CONFLUENCE_CUTOFF_ATR,
    atr: float = 1.0,
    mid_price: float | None = None,
) -> float:
    """v1.1 confluence: zones reward independent SOURCES + TIMEFRAME diversity +
    proximity to price; the result uses the full [0,3] range instead of the
    v1 quantized step (was: 6 discrete levels, 34% saturate at 3.0).

    score = (1 + ln(1 + distinct_sources)) * (1 + 0.5 * tf_diversity)
            * proximity_factor
    where tf_diversity = (# distinct timeframes in zone - 1) capped at 3
    and   proximity_factor = 1 / (1 + d_zone/ATR)   (d_zone = distance of the
           zone's nearest pool to price; a zone far from price is less
           actionable, so it scores lower; a zone AT price = 1.0)
    Clipped [0, 3].
    """
    from nexus_scalp.features.liquidity_engine import (
        PoolState,
    )

    users = [
        p
        for p in pools
        if p.state != PoolState.CANDIDATE
        and p.usable_at <= (decision_at or datetime.max.replace(tzinfo=UTC))
    ]
    if not users:
        return 0.0
    cutoff = atr * cutoff_atr
    uniq = _dedup_pools(users, cutoff)
    if not uniq:
        return 0.0
    # cluster into zones (gap-based, same as v1)
    zones: list[list[Any]] = []
    cur: list[Any] = [uniq[0]]
    for p in uniq[1:]:
        if p.price - cur[-1].price <= cutoff:
            cur.append(p)
        else:
            zones.append(cur)
            cur = [p]
    zones.append(cur)

    price = mid_price if mid_price is not None else float(np.mean([p.price for p in uniq]))
    best = 0.0
    for zone in zones:
        distinct_sources = {p.source for p in zone}
        tfs = {p.timeframe_minutes for p in zone}
        tf_div = min(len(tfs) - 1, 3)
        d_zone = min(abs(p.price - price) for p in zone)
        prox = 1.0 / (1.0 + d_zone / max(atr, 1e-9))
        score = (1.0 + math.log1p(max(0, len(distinct_sources) - 1))) * (1.0 + 0.5 * tf_div) * prox
        best = max(best, score)
    return _clip3(best)


# ---------------------------------------------------------------------------
# TOP-LEVEL: canonical v1.1 producer
# ---------------------------------------------------------------------------


def compute_liquidity_features_v1_1(
    bars: list[Any],
    *,
    decision_at: datetime | None = None,
    mid_price: float | None = None,
    atr: float | None = None,
    use_htf: bool = True,
    params: LiquidityParams | None = None,
) -> Any:
    """The canonical v1.1 producer (mirror of v1's compute_liquidity_features).

    Reuses v1's pool construction (detect_confirmed_swings, session/daily
    pools, update_pool_states) and swaps in the fixed/parameterized pieces.
    """
    from nexus_scalp.features.liquidity_engine import (
        DEFAULT_BSL_DISTANCE,
        DEFAULT_CONFLUENCE,
        DEFAULT_DISPLACEMENT,
        DEFAULT_EQH_STRENGTH,
        DEFAULT_EQL_STRENGTH,
        DEFAULT_EXTERNAL_DISTANCE,
        DEFAULT_HTF_SCORE,
        DEFAULT_INTERNAL_DISTANCE,
        DEFAULT_SSL_DISTANCE,
        DEFAULT_SWEEP_STATE,
        LiquidityFeatures,
        daily_price_pools,
        internal_external_distances,
        session_high_low_pools,
        update_pool_states,
    )

    p = params or LiquidityParams()
    times = _bar_times(bars)
    decision = decision_at or (times[-1] if times else None)
    if decision is None or not bars:
        return LiquidityFeatures(
            decision_at=times[-1] if times else datetime.now(UTC),
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

    sh_pools, sl_pools = detect_confirmed_swings(vis)
    pools: list[Any] = list(sh_pools) + list(sl_pools)
    pools += session_high_low_pools(vis, now=decision)
    pools += daily_price_pools(vis, now=decision)
    pools = update_pool_states(pools, vis, safe_atr, now=decision)
    usable = [p for p in pools if p.usable_at <= decision and p.state != PoolState.CANDIDATE]

    # 01/02 BSL/SSL distances (unchanged from v1)
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

    # 03/04 EQH/EQL (FIX 1: price-aware)
    eqh, eql = equal_high_low_strengths_v1_1(
        sh_pools, sl_pools, safe_atr, price, tolerance_atr=p.eqh_tolerance_atr
    )

    # 05 HTF (FIX 3: parameterized proximity)
    htf = (
        htf_liquidity_score_v1_1(
            vis, safe_atr, decision_at=decision, proximity_atr=p.htf_proximity_atr
        )
        if use_htf
        else DEFAULT_HTF_SCORE
    )

    # 06/07 internal/external (unchanged)
    internal, external = internal_external_distances(usable, price, safe_atr, decision_at=decision)

    # 08 confluence (FIX 4: parameterized cutoff)
    confluence = liquidity_confluence_v1_1(
        usable,
        decision_at=decision,
        cutoff_atr=p.confluence_cutoff_atr,
        atr=safe_atr,
        mid_price=price,
    )

    # 09/10 sweep (FIX 2: relevance gate + window)
    sweep_state, displacement = detect_reactive_sweep_v1_1(
        usable,
        vis,
        safe_atr,
        decision_at=decision,
        reclaim_fraction_atr=p.reclaim_fraction_atr,
        relevance_atr=p.sweep_relevance_atr,
        window_bars=p.sweep_window_bars,
    )

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
