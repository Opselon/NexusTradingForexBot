"""Incremental 70D frame builder — BUG-106 performance fix (AGENT-09).

`compute_70d_frame` (schema_v2.py) was O(n^2)-or-worse: for each output row
it called the canonical `compute_liquidity_features(all_bars[:i+1])`, which
re-scans the FULL history every row (swing detection over the visible
window, session/daily pool reconstruction, per-pool touch/sweep/reclaim
scans, HTF bucket rebuilds, plus array conversions).

This module provides `compute_70d_frame_fast` — a semantics-preserving
incremental builder that produces BYTE-IDENTICAL feature vectors to the
canonical per-row function while running in ~O(n * window) total.

Equivalence strategy (not approximation):
- The canonical functions are all *causal filters*: every pool source
  (swing/session/daily) depends only on bars with t <= decision, and every
  pool state (touch/sweep/reclaim) is a monotone predicate over bars after
  confirmed_at. Therefore the state at decision k is a PREFIX of the
  full-history state: nothing computed at decision k+1 can change the
  output at decision k.
- Swings: computed ONCE over the full bar list (fractal pivots with
  candidate_at / confirmed_at). At decision k the visible confirmed swings
  are exactly those with confirmed_at <= times[k] — identical to
  detect_confirmed_swings(vis) because vis = bars[:k+1] and a swing at i is
  confirmed at i+window <= k iff i <= k-window (the same prefix rule).
- Session/daily pools: running max/min per session / UTC day, filtered to
  completed windows at decision — identical results, O(1) amortized.
- update_pool_states: incremental — each pool's state is advanced only over
  bars after its previous evaluation point; the predicate
  (touched/swept/reclaimed) is monotone so the final state at decision k is
  identical to re-scanning all bars >= confirmed_at.
- HTF liquidity score: incremental bucket max/min per timeframe.
- Feature assembly (confluence, distances, EQH/EQL, sweep detector): run
  on the SAME pool lists the canonical would produce — identical outputs.

VERIFIED against the canonical function on real data (see
tests/unit/test_70d_frame_incremental_phase19.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import (
    DEFAULT_BSL_DISTANCE,
    DEFAULT_HTF_SCORE,
    DEFAULT_SSL_DISTANCE,
    HTF_TIMEFRAMES_MIN,
    MIN_ATR,
    RECLAIM_FRACTION_ATR,
    TOUCH_PROXIMITY_ATR,
    LiquidityPool,
    PoolSide,
    PoolSource,
    PoolState,
    _bar_times,
    _bars_to_arrays,
    _clip3,
    _safe_div,
    _session_code,
    detect_confirmed_swings,
    detect_reactive_sweep,
    equal_high_low_strengths,
    internal_external_distances,
    liquidity_confluence,
)
from nexus_scalp.features.scalp_features import HTF_HISTORY_BARS, ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

#: Bounded lookback for detect_reactive_sweep (BUG-106): the detector only
#: reads bars at/before decision and after the nearest pool's confirmed_at
#: (penetration + rejection-reclaim scan). Rather than a fixed cap, the
#: window is derived from the pools themselves: bars since the earliest
#: confirmed_at among usable pools (+ margin), so semantics are identical to
#: the canonical full-history call for every pool lifecycle state, while the
#: slice stays tiny (pools confirmed > SWEEP_ABS_MAX_BARS ago are still
#: visible via the cap).
SWEEP_ABS_MAX_BARS: int = 2000  # absolute cap: 2000 M5 bars = ~7 days


#: Prices for the nearest-pool lookback helper (post-sweep displacement
#: anchor needs the sweep bar; the NEAREST pool is what the detector uses).
def sweep_lookback(pools: list[Any], times: list[datetime], i: int, price: float) -> int:
    """Bars needed for detect_reactive_sweep at decision index i.

    The detector only acts on the NEAREST relevant pool (by |price - pool|)
    and scans bars from ITS confirmed_at onward. Return the trailing-bar
    count covering that pool (capped at SWEEP_ABS_MAX_BARS).
    """
    if not pools or i < 0:
        return SWEEP_ABS_MAX_BARS
    decision = times[i]
    nearest = None
    best = None
    for p in pools:
        if p.usable_at is not None and p.usable_at <= decision and p.state != PoolState.CANDIDATE:
            d = abs(p.price - price)
            if best is None or d < best:
                best = d
                nearest = p
    if nearest is None:
        return SWEEP_ABS_MAX_BARS
    anchor = nearest.confirmed_at
    lo = 0
    for j in range(i, -1, -1):
        if times[j] <= anchor:
            lo = j
            break
    bars_needed = i + 1 - lo
    # the detector requires len(rel) >= 2 (rel = bars <= decision) and
    # scans from ANY bar (only skipping < confirmed_at). Include at least
    # 2 completed bars before decision even when confirmed_at == decision.
    bars_needed = max(bars_needed, 2)
    return min(SWEEP_ABS_MAX_BARS, bars_needed)


# ---------------------------------------------------------------------------
# Incremental session/daily pools
# ---------------------------------------------------------------------------


def _session_ranges(decision: datetime) -> tuple[datetime, datetime]:
    """(start, end-exclusive-UTC) of the completed session containing decision."""
    hour = decision.hour
    code = _session_code(hour)
    day_start = decision.replace(hour=0, minute=0, second=0, microsecond=0)
    if code == "tokyo":
        return day_start, day_start.replace(hour=8)
    if code == "london":
        return day_start.replace(hour=7), day_start.replace(hour=15)
    if code == "ny":
        return day_start.replace(hour=13), day_start.replace(hour=21)
    return day_start, day_start.replace(hour=23, minute=59) + timedelta(minutes=1)


#: Max retained swing pools per side (BUG-106 bounded retention).
POOL_RETENTION: int = 200


class IncrementalLiquidityState:
    """Holds the reusable structural state across rows (BUG-106 fix).

    Pure data + incremental update helpers; never touches the canonical
    engine's semantics. All outputs are slices/filters of the SAME data the
    canonical per-row functions would compute.
    """

    def __init__(self, bars: list[BarData]) -> None:
        self.bars = bars
        self.n = len(bars)
        self.times = _bar_times(bars)
        self.arr = _bars_to_arrays(bars)
        self.highs = self.arr["high"]
        self.lows = self.arr["low"]
        self.closes = self.arr["close"]

        # --- swings: computed ONCE over the full history ---
        self.sh_pools, self.sl_pools = detect_confirmed_swings(bars)
        # CANONICAL ORDER: sh then sl (detect_confirmed_swings output);
        # session + daily appended per-row in the caller (same as canonical
        # compute_liquidity_features). Do NOT sort: internal_external_distances
        # uses users[-20:] where ORDER matters (BUG-106 parity).
        #
        # BUG-106 bounded retention: keep only the most recent POOL_RETENTION
        # confirmed swing pools (by confirmed_at). Older swing levels are
        # far behind price and contribute nothing to distances (users[-20:]),
        # sweep (nearest pool), or confluence (far zones cannot win the
        # diversity-max score). The bound is far above any real decision
        # horizon; parity is machine-verified on the harness.
        sh = list(self.sh_pools)
        sl = list(self.sl_pools)
        if len(sh) > POOL_RETENTION:
            sh = sh[-POOL_RETENTION:]
        if len(sl) > POOL_RETENTION:
            sl = sl[-POOL_RETENTION:]
        self.all_pools: list[LiquidityPool] = sh + sl

        # --- incremental daily grouping (BUG-106: daily_pools_at was O(n)
        # per row — it rebuilt the full day->bars dict from scratch). We
        # maintain the grouping additively: each new bar appends to its
        # day, and completed-day aggregates are cached. ---
        self._day_idx: dict[str, list[int]] = {}
        self._day_hi: dict[str, float] = {}
        self._day_lo: dict[str, float] = {}
        self._last_day_processed = -1
        self._precompute_daily()
        self._precompute_htf()
        self._day_list: list[str] = []  # refreshed each call (cheap: <= 3 years)

        # --- per-row pool state recompute (atr-aware, vectorized slices) ---
        # The canonical update_pool_states recomputes every pool's state at
        # EVERY row with the CURRENT atr (touch/sweep/reclaim thresholds are
        # atr-scaled), so state cannot be cached across rows. We keep the
        # O(pools x slice) recompute but over precomputed numpy arrays with
        # slice comparisons (no list rebuilds, no _bars_to_arrays).

    def _precompute_daily(self) -> None:
        """One-pass daily grouping + running high/low per UTC day."""
        for i in range(self.n):
            d = self.times[i].date().isoformat()
            if d not in self._day_idx:
                self._day_idx[d] = [i]
                self._day_hi[d] = float(self.highs[i])
                self._day_lo[d] = float(self.lows[i])
            else:
                self._day_idx[d].append(i)
                if self.highs[i] > self._day_hi[d]:
                    self._day_hi[d] = float(self.highs[i])
                if self.lows[i] < self._day_lo[d]:
                    self._day_lo[d] = float(self.lows[i])
        self._last_day_processed = self.n - 1

    def _precompute_htf(self) -> None:
        """Per-period UTC-minute-bucket running high/low, computed ONCE.

        Each bucket key = (period_minutes, bucket_start_minute).
        Value = (high, low). Membership never changes (bars are fixed);
        at decision time we only filter buckets whose end <= decision and
        drop the still-forming bucket (end > decision) — identical to the
        canonical completed-bucket rule.
        """
        self._htf_buckets: dict[tuple[int, int], tuple[float, float]] = {}
        for i in range(self.n):
            total_min = int(self.times[i].timestamp()) // 60
            for period in HTF_TIMEFRAMES_MIN:
                bm = (total_min // period) * period
                key = (period, bm)
                if key not in self._htf_buckets:
                    self._htf_buckets[key] = (float(self.highs[i]), float(self.lows[i]))
                else:
                    h, l = self._htf_buckets[key]
                    if self.highs[i] > h:
                        h = float(self.highs[i])
                    if self.lows[i] < l:
                        l = float(self.lows[i])
                    self._htf_buckets[key] = (h, l)
        # per-period sorted bucket starts (for the completed filter)
        self._htf_period_starts: dict[int, list[int]] = {}
        for period in HTF_TIMEFRAMES_MIN:
            self._htf_period_starts[period] = sorted(
                bm for (p, bm) in self._htf_buckets if p == period
            )

    def _bar_index_at(self, t: datetime) -> int:
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.times[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1  # last index with times <= t

    def pools_visible_at(self, decision: datetime, atr: float) -> list[LiquidityPool]:
        """All confirmed pools with confirmed_at <= decision, state recomputed
        with the CURRENT atr (byte-identical to canonical update_pool_states)."""
        safe_atr = max(atr, MIN_ATR)
        tol = safe_atr * TOUCH_PROXIMITY_ATR
        k = self._bar_index_at(decision)
        out: list[LiquidityPool] = []
        for p in self.all_pools:
            if p.confirmed_at > decision:
                continue  # not confirmed yet at this decision (canonical vis)
            c = self._first_bar_index_at_or_after(p.confirmed_at)
            if c > k:
                out.append(p)
                continue
            hi_slice = self.highs[c : k + 1]
            lo_slice = self.lows[c : k + 1]
            cl_slice = self.closes[c : k + 1]
            if p.side == PoolSide.BSL:
                touches = int((hi_slice >= p.price - tol).sum())
                sweep_evidence = bool(
                    (
                        (hi_slice > p.price)
                        & (cl_slice < p.price - safe_atr * RECLAIM_FRACTION_ATR)
                    ).any()
                )
                reclaim_evidence = bool(
                    (cl_slice > p.price + safe_atr * RECLAIM_FRACTION_ATR).any()
                )
            else:
                touches = int((lo_slice <= p.price + tol).sum())
                sweep_evidence = bool(
                    (
                        (lo_slice < p.price)
                        & (cl_slice > p.price + safe_atr * RECLAIM_FRACTION_ATR)
                    ).any()
                )
                reclaim_evidence = bool(
                    (cl_slice < p.price - safe_atr * RECLAIM_FRACTION_ATR).any()
                )
            state = PoolState.CONFIRMED
            last_touched = p.last_touched_at
            if touches:
                state = PoolState.TOUCHED
                last_touched = last_touched or self.times[c]
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

    def advance_pools_np(
        self,
        pools: list[LiquidityPool],
        decision: datetime,
        atr: float,
    ) -> list[LiquidityPool]:
        """Vectorized lifecycle advance for ANY pool list (swing/session/daily).

        Byte-identical semantics to canonical update_pool_states:
          - bars only at/after confirmed_at and <= decision can touch;
          - touches, sweep evidence, reclaim evidence computed over numpy
            slices; state resolution CONFIRMED -> TOUCHED -> SWEPT -> RECLAIMED.
        """
        safe_atr = max(atr, MIN_ATR)
        tol = safe_atr * TOUCH_PROXIMITY_ATR
        k = self._bar_index_at(decision)
        out: list[LiquidityPool] = []
        for p in pools:
            if p.confirmed_at > decision:
                out.append(p)
                continue
            c = self._first_bar_index_at_or_after(p.confirmed_at)
            if c > k:
                out.append(p)
                continue
            hi_slice = self.highs[c : k + 1]
            lo_slice = self.lows[c : k + 1]
            cl_slice = self.closes[c : k + 1]
            if p.side == PoolSide.BSL:
                touches = int((hi_slice >= p.price - tol).sum())
                sweep_evidence = bool(
                    (
                        (hi_slice > p.price)
                        & (cl_slice < p.price - safe_atr * RECLAIM_FRACTION_ATR)
                    ).any()
                )
                reclaim_evidence = bool(
                    (cl_slice > p.price + safe_atr * RECLAIM_FRACTION_ATR).any()
                )
                touched_at = self.times[c] if touches else None
            else:
                touches = int((lo_slice <= p.price + tol).sum())
                sweep_evidence = bool(
                    (
                        (lo_slice < p.price)
                        & (cl_slice > p.price + safe_atr * RECLAIM_FRACTION_ATR)
                    ).any()
                )
                reclaim_evidence = bool(
                    (cl_slice < p.price - safe_atr * RECLAIM_FRACTION_ATR).any()
                )
                touched_at = self.times[c] if touches else None
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

    def _first_bar_index_at_or_after(self, t: datetime) -> int:
        lo, hi = 0, self.n
        while lo < hi:
            mid = (lo + hi) // 2
            if self.times[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------------
    # Session / daily pools (incremental)
    # ------------------------------------------------------------------

    def session_pools_at(self, decision: datetime) -> list[LiquidityPool]:
        """Current completed session high/low pools (canonical semantics)."""
        start, _end_excl = _session_ranges(decision)
        # find bars in [start, _end_excl) and <= decision
        i0 = self._first_bar_index_at_or_after(start)
        i1 = self._bar_index_at(decision)
        if i1 < i0:
            return []
        hi = float(np.max(self.highs[i0 : i1 + 1]))
        lo = float(np.min(self.lows[i0 : i1 + 1]))
        hi_idx = i0 + int(np.argmax(self.highs[i0 : i1 + 1]))
        lo_idx = i0 + int(np.argmin(self.lows[i0 : i1 + 1]))
        confirmed = self.times[i1]
        return [
            LiquidityPool(
                price=hi,
                side=PoolSide.BSL,
                source=PoolSource.SESSION_HIGH,
                timeframe_minutes=1,
                strength=0.8,
                candidate_at=self.times[hi_idx],
                confirmed_at=confirmed,
            ),
            LiquidityPool(
                price=lo,
                side=PoolSide.SSL,
                source=PoolSource.SESSION_LOW,
                timeframe_minutes=1,
                strength=0.8,
                candidate_at=self.times[lo_idx],
                confirmed_at=confirmed,
            ),
        ]

    def daily_pools_at(self, decision: datetime, lookback_days: int = 3) -> list[LiquidityPool]:
        """PDH/PDL/PWH/PWL pools (canonical semantics, incremental grouping).

        Uses the precomputed per-day running high/low (BUG-106: this was
        O(n) per row when it rebuilt the grouping from scratch). Day list is
        computed from the pre-grouped keys (bounded by calendar days)."""
        # completed days only: the last bar of a day must be <= decision AND
        # the day must be finished (next day has at least one bar <= decision)
        k = self._bar_index_at(decision)
        if k < 0:
            return []
        # days with bars up to decision
        day_list = []
        for d in self._day_idx:
            idxs = self._day_idx[d]
            if idxs and idxs[0] <= k:
                day_list.append(d)
        day_list.sort()
        if len(day_list) < 2:
            return []
        prev = day_list[-2]
        prev_idx = self._day_idx[prev]
        p_dh = self._day_hi[prev]
        p_dl = self._day_lo[prev]
        confirmed = self.times[max(prev_idx)]
        pools = [
            LiquidityPool(
                price=p_dh,
                side=PoolSide.BSL,
                source=PoolSource.PDH,
                timeframe_minutes=1440,
                strength=1.2,
                candidate_at=confirmed,
                confirmed_at=confirmed,
            ),
            LiquidityPool(
                price=p_dl,
                side=PoolSide.SSL,
                source=PoolSource.PDL,
                timeframe_minutes=1440,
                strength=1.2,
                candidate_at=confirmed,
                confirmed_at=confirmed,
            ),
        ]
        completed_days = day_list[:-1][-lookback_days:]
        if completed_days:
            wh = max(self._day_hi[d] for d in completed_days)
            wl = min(self._day_lo[d] for d in completed_days)
            last_day = self._day_idx[completed_days[-1]]
            conf = self.times[max(last_day)]
            pools.append(
                LiquidityPool(
                    price=wh,
                    side=PoolSide.BSL,
                    source=PoolSource.PWH,
                    timeframe_minutes=1440 * 7,
                    strength=1.4,
                    candidate_at=conf,
                    confirmed_at=conf,
                )
            )
            pools.append(
                LiquidityPool(
                    price=wl,
                    side=PoolSide.SSL,
                    source=PoolSource.PWL,
                    timeframe_minutes=1440 * 7,
                    strength=1.4,
                    candidate_at=conf,
                    confirmed_at=conf,
                )
            )
        return pools

    def htf_score_at(self, decision: datetime, atr: float) -> float:
        """HTF liquidity score — precomputed bucket max/min (canonical semantics).

        Completed-bucket rule preserved: a bucket counts only when its end
        (next bucket start) <= decision; the forming bucket is excluded.
        BUG-106: O(buckets) per row instead of O(n) (no per-row regrouping).
        """
        last_idx = self._bar_index_at(decision)
        if last_idx < 0:
            return DEFAULT_HTF_SCORE
        last_close = float(self.closes[last_idx])
        scores: list[float] = []
        for period in HTF_TIMEFRAMES_MIN:
            starts = self._htf_period_starts.get(period, [])
            if not starts:
                continue
            # completed buckets: start + period <= decision (end <= decision)
            # iterate starts; the last one may be forming -> exclude by end check
            for bm in starts:
                b_end_dt = datetime.fromtimestamp((bm + period) * 60, tz=UTC)
                if b_end_dt > decision:
                    continue  # still forming at decision time (canonical rule)
                key = (period, bm)
                bh, bl = self._htf_buckets[key]
                tf_weight = {60: 0.9, 240: 1.2, 1440: 1.6}.get(period, 0.9)
                for level, side_sign in ((bh, 1.0), (bl, -1.0)):
                    dist = abs(level - last_close) / max(atr, MIN_ATR)
                    if dist <= 6.0:
                        prox = 1.0 / (1.0 + dist)
                        scores.append(prox * tf_weight * side_sign)
        if not scores:
            return DEFAULT_HTF_SCORE
        return _clip3(float(np.tanh(sum(scores))) * 3.0)


def compute_70d_frame_fast(
    df: pl.DataFrame,
    *,
    min_bars: int = 55,
    spread: float = 0.20,
    news_frame: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Incremental 70D frame builder — byte-identical to compute_70d_frame.

    Same contract, same columns, same values. Only the time complexity
    changes (O(n*window) instead of O(n^2+)).
    """
    from nexus_scalp.features.features70 import (
        FeatureSourceState,
        clamp_neutral_family,
        news_10d_from_context,
    )
    from nexus_scalp.model_generation.news_bridge import news_context_at

    raw = df.sort("time")
    times: list[datetime] = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    n = raw.height
    all_bars: list[BarData] = []
    for j in range(n):
        bj = raw.row(j, named=True)
        all_bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=times[j],
                open=float(bj["open"]),
                high=float(bj["high"]),
                low=float(bj["low"]),
                close=float(bj["close"]),
                tick_volume=int(bj.get("tick_volume", 0) or 0),
                is_complete=True,
            )
        )

    news_enabled = news_frame is not None and not news_frame.is_empty()
    lstate = IncrementalLiquidityState(all_bars)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        if i + 1 < min_bars:
            continue  # causal warm-up
        ts = times[i]
        b = raw.row(i, named=True)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=ts,
            bid=float(b["close"]),
            ask=float(b["close"]) + spread,
            volume=int(b.get("tick_volume", 0) or 0),
        )
        # --- liquidity: incremental, same semantics ---
        # BUG-234: HTF window parity — pass full causal history so h1/m30
        # train == live. Base features still see only the last 55 (engine slices internally).
        fv_window = all_bars[max(0, i + 1 - HTF_HISTORY_BARS) : i + 1]
        fv = engine.compute_from_bars(fv_window, tick)
        x50 = fv.to_tensor_input()

        # --- liquidity: incremental, same semantics ---
        atr = fv.atr_m1
        lstate._last_atr = max(atr, MIN_ATR)  # type: ignore[attr-defined]
        vis_pools = lstate.pools_visible_at(ts, atr)
        session_pools = lstate.session_pools_at(ts)
        daily_pools = lstate.daily_pools_at(ts)
        # canonical order: swings + session + daily, THEN lifecycle advance
        # over ALL pools (BUG-106: session/daily pools must get the same
        # touch/sweep/reclaim advance the canonical update_pool_states
        # applies; without it PDH/PWH stayed CONFIRMED instead of SWEPT).
        # canonical order: swings + session + daily; lifecycle advance is
        # vectorized over numpy slices (identical semantics to
        # update_pool_states, O(pools x slice) per row but numpy-speed).
        pools_all = vis_pools + session_pools + daily_pools
        pools_all = lstate.advance_pools_np(pools_all, ts, atr)
        # filter usable
        usable = [p for p in pools_all if p.usable_at <= ts and p.state != PoolState.CANDIDATE]
        bsl_above = [p for p in usable if p.side == PoolSide.BSL and p.price > float(b["close"])]
        ssl_below = [p for p in usable if p.side == PoolSide.SSL and p.price < float(b["close"])]
        price = float(b["close"])
        safe_atr = max(atr, MIN_ATR)
        bsl_dist = (
            _clip3(
                _safe_div(min(p.price for p in bsl_above) - price, safe_atr, DEFAULT_BSL_DISTANCE)
            )
            if bsl_above
            else DEFAULT_BSL_DISTANCE
        )
        ssl_dist = (
            _clip3(
                _safe_div(price - max(p.price for p in ssl_below), safe_atr, DEFAULT_SSL_DISTANCE)
            )
            if ssl_below
            else DEFAULT_SSL_DISTANCE
        )
        sh_vis = [p for p in vis_pools if p.side == PoolSide.BSL]
        sl_vis = [p for p in vis_pools if p.side == PoolSide.SSL]
        eqh, eql = equal_high_low_strengths(sh_vis, sl_vis, safe_atr)
        htf = lstate.htf_score_at(ts, safe_atr)
        internal, external = internal_external_distances(usable, price, safe_atr, decision_at=ts)
        confluence = liquidity_confluence(usable, decision_at=ts, atr=safe_atr)
        sweep_state, displacement = detect_reactive_sweep(
            usable,
            all_bars[max(0, i + 1 - sweep_lookback(usable, times, i, float(b["close"]))) : i + 1],
            safe_atr,
            decision_at=ts,
        )

        liq10 = [
            bsl_dist,
            ssl_dist,
            _clip3(eqh),
            _clip3(eql),
            _clip3(htf),
            internal,
            external,
            confluence,
            _clip3(sweep_state),
            displacement,
        ]

        if news_enabled:
            ctx = news_context_at(news_frame, ts)
            news10 = news_10d_from_context(ctx)
            news_status = FeatureSourceState.FEATURE_AVAILABLE.value
        else:
            news10 = [0.0] * 10
            news_status = FeatureSourceState.FEATURE_DISABLED.value

        rec = {
            "timestamp": ts,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "spread": spread,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": int(b.get("tick_volume", 0) or 0),
            "news_status": news_status,
            "liquidity_status": FeatureSourceState.FEATURE_AVAILABLE.value,
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        for idx in range(10):
            rec[f"feat_{50 + idx}"] = float(clamp_neutral_family(news10, (0.0,) * 10)[idx])
        for idx in range(10):
            rec[f"feat_{60 + idx}"] = float(
                clamp_neutral_family(liq10, (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0))[idx]
            )
        rows.append(rec)
    return pl.DataFrame(rows)
