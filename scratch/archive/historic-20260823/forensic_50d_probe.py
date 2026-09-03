"""
Forensic 50D Feature Verification Probe — Phase 1/2/3 harness
==============================================================
Independent mathematical recomputation of every feature in the canonical
50D contract (src/nexus_scalp/features/scalp_features.py, FEATURE_NAMES),
compared against the live ScalpFeatureEngine on controlled deterministic
fixtures. The independent implementation uses ONLY numpy + math — it never
imports or calls any nexus_scalp feature helper, so an implementation bug
cannot be masked by reusing the same code path.

Contract resolved from executable code (NOT from docs):
    feat_0  upper_wick_ratio
    feat_1  lower_wick_ratio
    feat_2  body_to_range_ratio
    feat_3  is_doji
    feat_4  pinbar_sig
    feat_5  engulfing_sig
    feat_6  close_location_value
    feat_7  consecutive_momentum_count
    feat_8  norm_displacement
    feat_9  rapid_reversal_spike_val
    feat_10 dist_to_swing_high_20
    feat_11 dist_to_swing_low_20
    feat_12 price_compression_flag_ratio
    feat_13 extreme_sig
    feat_14 stop_hunt_depth
    feat_15 liquidity_sweep_signal
    feat_16 session_tokyo
    feat_17 session_london
    feat_18 session_ny
    feat_19 session_overlap_london_ny
    feat_20 lag_1_log_return   (x100)
    feat_21 lag_2_log_return   (x100)
    feat_22 lag_3_log_return   (x100)
    feat_23 lag_1_atr_ratio
    feat_24 lag_1_volume_z
    feat_25 lag_1_clv
    feat_26 fvg_sig
    feat_27 order_block_type
    feat_28 choch_sig
    feat_29 breakout_sig
    feat_30 norm_tk_diff
    feat_31 tk_cross_signal
    feat_32 kumo_sig
    feat_33 norm_kumo_width
    feat_34 norm_rsi
    feat_35 dist_to_ema_21
    feat_36 dist_to_ema_50
    feat_37 cross_asset_z_score
    feat_38 norm_dist_to_tenkan
    feat_39 norm_dist_to_kijun
    feat_40 htf_h4_trend
    feat_41 htf_h1_momentum
    feat_42 htf_m30_structure
    feat_43 htf_m15_confirmation
    feat_44 support_zone_dist
    feat_45 resistance_zone_dist
    feat_46 feat_ob_valid_bos
    feat_47 feat_ob_equilibrium_ratio
    feat_48 feat_ob_liquidity_swept
    feat_49 feat_ob_fib_50_60_alignment

Output: JSON matrix rows to stdout (index, name, runtime, expected,
abs_err, rel_err, status).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime, timedelta

import numpy as np

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FEATURE_NAMES, ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData

# =============================================================================
# Deterministic fixture builders
# =============================================================================


def make_bars(
    closes: list[float],
    *,
    symbol: str = "XAUUSD",
    start: datetime | None = None,
    ohlc: list[tuple[float, float, float, float]] | None = None,
    volumes: list[int] | None = None,
) -> list[BarData]:
    """Builds completed M1 bars from a close series (or explicit OHLC)."""
    start = start or datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    bars: list[BarData] = []
    for i, c in enumerate(closes):
        if ohlc is not None and i < len(ohlc):
            o, h, l, cl = ohlc[i]
        else:
            o = c
            h = max(c, c + 0.8)
            l = min(c, c - 0.7)
            cl = c
        vol = int(volumes[i]) if volumes else 100
        bars.append(
            BarData(
                symbol=symbol,
                timeframe="M1",
                timestamp=start + timedelta(minutes=i),
                open=o,
                high=h,
                low=l,
                close=cl,
                tick_volume=vol,
                is_complete=True,
            )
        )
    return bars


def trend_bars(n: int = 60, step: float = 0.1, start_price: float = 2000.0) -> list[BarData]:
    """Strong deterministic uptrend."""
    closes = [start_price + i * step for i in range(n)]
    return make_bars(closes)


def flat_bars(n: int = 60, price: float = 2000.0, vol: int = 100) -> list[BarData]:
    """Perfectly flat market (zero movement)."""
    closes = [price] * n
    ohlc = [(price, price, price, price) for _ in range(n)]
    return make_bars(closes, ohlc=ohlc, volumes=[vol] * n)


def doji_bars(n: int = 60, price: float = 2000.0) -> list[BarData]:
    """Doji candles: open == close, tiny range."""
    ohlc = [(price - 0.02, price + 0.05, price - 0.05, price - 0.02) for _ in range(n)]
    return make_bars([price] * n, ohlc=ohlc)


def wick_dominant_bars(n: int = 60, price: float = 2000.0) -> list[BarData]:
    """Wick-dominant candles: long upper wick, tiny body."""
    ohlc = [(price, price + 2.0, price - 0.1, price + 0.05) for _ in range(n)]
    return make_bars([price + 0.05] * n, ohlc=ohlc)


def volume_spike_bars(n: int = 60) -> list[BarData]:
    """Trend with a single massive volume spike."""
    bars = trend_bars(n)
    vols = [100] * n
    vols[-2] = 5000
    return make_bars(
        [b.close for b in bars],
        ohlc=[(b.open, b.high, b.low, b.close) for b in bars],
        volumes=vols,
    )


def reversal_bars(n: int = 60) -> list[BarData]:
    """Strong trend then sharp reversal."""
    closes = [2000.0 + i * 0.1 for i in range(40)] + [2004.0 - i * 0.25 for i in range(20)]
    return make_bars(closes)


def high_vol_bars(n: int = 60) -> list[BarData]:
    """High-volatility random-walk-ish deterministic series (seeded)."""
    rng = np.random.default_rng(42)
    closes = [2000.0]
    for _ in range(n - 1):
        closes.append(closes[-1] + float(rng.normal(0, 2.0)))
    ohlc = [
        (
            c - abs(float(rng.normal(0, 1.0))),
            c + abs(float(rng.normal(0, 2.0))),
            c - abs(float(rng.normal(0, 2.0))),
            c,
        )
        for c in closes
    ]
    return make_bars(closes, ohlc=ohlc)


def lagging_tick(
    engine: ScalpFeatureEngine, bars: list[BarData], mid_delta: float = 0.0
) -> TickData:
    """Tick at the last bar's close (plus optional mid delta)."""
    last = bars[-1]
    mid = last.close + mid_delta
    return TickData(
        symbol=engine.symbol,
        timestamp=last.timestamp + timedelta(seconds=30),
        bid=mid - 0.1,
        ask=mid + 0.1,
        volume=1,
    )


# =============================================================================
# INDEPENDENT RECOMPUTATION (numpy only — no repo helpers)
# =============================================================================


def independent_features(
    bars: list[BarData],
    tick: TickData,
    symbol: str,
) -> list[float]:
    """Recomputes the canonical 50D vector from raw bars+tick, independently."""
    n = len(bars)
    closes = np.array([b.close for b in bars], dtype=np.float64)
    highs = np.array([b.high for b in bars], dtype=np.float64)
    lows = np.array([b.low for b in bars], dtype=np.float64)
    opens = np.array([b.open for b in bars], dtype=np.float64)
    volumes = np.array([b.tick_volume for b in bars], dtype=np.float64)
    mid_price = (tick.bid + tick.ask) / 2.0

    tail = slice(max(0, n - 55), n)
    C, H, L, O, V = closes[tail], highs[tail], lows[tail], opens[tail], volumes[tail]
    last_close = C[-1]

    # --- feat_8: norm_displacement = (mid - last_close) / max(atr, 0.20)
    tr_all = np.maximum(
        H - L,
        np.maximum(np.abs(H - np.roll(C, 1)), np.abs(L - np.roll(C, 1))),
    )
    # roll wraps: fix first element (no prev close) -> use H-L
    tr_all[0] = H[0] - L[0]
    atr = float(np.mean(tr_all[-14:]))
    safe_atr = max(atr, 0.20)
    live_displacement = mid_price - last_close
    norm_displacement = live_displacement / safe_atr

    # --- feat_0..2 wick anatomy on last bar
    bar_range = max(H[-1] - L[-1], 0.01)
    body_top = max(O[-1], C[-1])
    body_bottom = min(O[-1], C[-1])
    body_size = body_top - body_bottom
    upper_wick = H[-1] - body_top
    lower_wick = body_bottom - L[-1]
    upper_wick_ratio = upper_wick / bar_range
    lower_wick_ratio = lower_wick / bar_range
    body_to_range_ratio = body_size / bar_range

    # --- feat_3 is_doji
    is_doji = 1.0 if body_to_range_ratio <= 0.12 else 0.0

    # --- feat_4 pinbar_sig
    is_hammer = lower_wick_ratio >= 0.55 and body_top >= (H[-1] - bar_range * 0.35)
    is_shooting = upper_wick_ratio >= 0.55 and body_bottom <= (L[-1] + bar_range * 0.35)
    if is_hammer:
        pinbar_sig = float(min(2.0, lower_wick_ratio * 2.0))
    elif is_shooting:
        pinbar_sig = float(max(-2.0, -upper_wick_ratio * 2.0))
    else:
        pinbar_sig = 0.0

    # --- feat_5 engulfing_sig
    eng_bull = C[-2] < O[-2] and C[-1] > O[-1] and O[-1] <= C[-2] and C[-1] >= O[-2]
    eng_bear = C[-2] > O[-2] and C[-1] < O[-1] and O[-1] >= C[-2] and C[-1] <= O[-2]
    if eng_bull:
        engulfing_sig = float(min(2.0, 1.0 + body_to_range_ratio))
    elif eng_bear:
        engulfing_sig = float(max(-2.0, -(1.0 + body_to_range_ratio)))
    else:
        engulfing_sig = 0.0

    # --- feat_6 clv
    clv = float(((C[-1] - L[-1]) - (H[-1] - C[-1])) / bar_range)

    # --- feat_7 consecutive_momentum_count
    color_dir = np.sign(C[-10:] - O[-10:])
    count = 0.0
    cur = color_dir[-1]
    if cur != 0:
        for d in reversed(color_dir):
            if d == cur:
                count += 1.0
            else:
                break
    consec = float(np.clip((count * cur) / 5.0, -1.0, 1.0))

    # --- feat_9 rapid_reversal_spike_val
    log_ret_m1 = math.log(C[-1] / C[-2]) if C[-2] > 0 else 0.0
    spike = (
        1.0
        if (abs(live_displacement) > safe_atr * 0.6 and (live_displacement * log_ret_m1) < 0)
        else 0.0
    )

    # --- feat_10/11 swing distances (window [-20:-1], i.e. excludes last bar)
    swing_high_20 = float(np.max(H[-20:-1]))
    swing_low_20 = float(np.min(L[-20:-1]))
    dist_sh = (swing_high_20 - mid_price) / safe_atr
    dist_sl = (mid_price - swing_low_20) / safe_atr

    # --- feat_12 price_compression_flag_ratio
    range_5 = float(np.max(H[-5:]) - np.min(L[-5:]) + 1e-8)
    range_20 = float(np.max(H[-20:]) - np.min(L[-20:]) + 1e-8)
    compression = float(np.clip(range_5 / range_20, 0.0, 2.0))

    # --- feat_13 extreme_sig
    recent_max_50 = float(np.max(H[-50:]))
    recent_min_50 = float(np.min(L[-50:]))
    total_range = (recent_max_50 - recent_min_50) + 1e-8
    range_pos = (mid_price - recent_min_50) / total_range
    extreme_sig = 1.0 if range_pos >= 0.95 else (-1.0 if range_pos <= 0.05 else 0.0)

    # --- feat_14/15 stop_hunt_depth / liquidity_sweep_signal
    recent_high_10 = float(np.max(H[-11:-1]))
    recent_low_10 = float(np.min(L[-11:-1]))
    sweep = 0.0
    stop_depth = 0.0
    if L[-1] < recent_low_10 and C[-1] > recent_low_10:
        sweep = 1.0
        stop_depth = (recent_low_10 - L[-1]) / safe_atr
    elif H[-1] > recent_high_10 and C[-1] < recent_high_10:
        sweep = -1.0
        stop_depth = (H[-1] - recent_high_10) / safe_atr

    # --- feat_16..19 sessions
    dt = tick.timestamp
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    hour = dt.hour
    s_tokyo = 1.0 if 0 <= hour < 8 else 0.0
    s_london = 1.0 if 7 <= hour < 15 else 0.0
    s_ny = 1.0 if 13 <= hour < 21 else 0.0
    s_overlap = 1.0 if 13 <= hour < 15 else 0.0

    # --- feat_20..22 lag log returns (x100)
    lag1 = math.log(C[-2] / C[-3]) if C[-3] > 0 else 0.0
    lag2 = math.log(C[-3] / C[-4]) if C[-4] > 0 else 0.0
    lag3 = math.log(C[-4] / C[-5]) if C[-5] > 0 else 0.0

    # --- feat_23 lag_1_atr_ratio
    tr_lag1 = max(H[-2] - L[-2], abs(H[-2] - C[-3]), abs(L[-2] - C[-3]))
    lag1_atr = tr_lag1 / safe_atr

    # --- feat_24 lag_1_volume_z
    vol_mean_20 = float(np.mean(V[-21:-1])) + 1e-8
    vol_std_20 = float(np.std(V[-21:-1])) + 1e-8
    vol_z = (V[-2] - vol_mean_20) / vol_std_20

    # --- feat_25 lag_1_clv
    lag1_range = max(H[-2] - L[-2], 0.01)
    lag1_clv = ((C[-2] - L[-2]) - (H[-2] - C[-2])) / lag1_range

    # --- feat_26 fvg_sig
    fvg_bull = (L[-1] - H[-3]) > safe_atr * 0.20
    fvg_bear = (L[-3] - H[-1]) > safe_atr * 0.20
    if fvg_bull:
        fvg_sig = (L[-1] - H[-3]) / safe_atr
    elif fvg_bear:
        fvg_sig = -(L[-3] - H[-1]) / safe_atr
    else:
        fvg_sig = 0.0

    # --- feat_27 order_block_type (int, then float)
    ob_type = 0.0
    if C[-1] > H[-2] and C[-2] < O[-2]:
        ob_type = 1.0
    elif C[-1] < L[-2] and C[-2] > O[-2]:
        ob_type = -1.0
    ob_strength = ob_type * (V[-1] / vol_mean_20)

    # --- EMA (repo: seed=first value, alpha=2/(p+1), sequential)
    def ema(arr: np.ndarray, period: int) -> float:
        if len(arr) == 0:
            return 0.0
        alpha = 2.0 / (period + 1.0)
        e = float(arr[0])
        for p in arr[1:]:
            e = p * alpha + e * (1.0 - alpha)
        return e

    ema20 = ema(C[-20:], 20)
    ema50 = ema(C[-50:], 50)
    ema21 = ema(C[-21:], 21)
    is_downtrend = ema20 < ema50
    is_uptrend = ema20 > ema50

    # --- feat_28 choch_sig
    sh20_choch = float(np.max(H[-20:-5]))
    sl20_choch = float(np.min(L[-20:-5]))
    choch_bull = is_downtrend and mid_price > sh20_choch
    choch_bear = is_uptrend and mid_price < sl20_choch
    choch_sig = 1.0 if choch_bull else (-1.0 if choch_bear else 0.0)

    # --- feat_29 breakout_sig
    broke_prev_high = mid_price > H[-1]
    broke_prev_low = mid_price < L[-1]
    breakout_sig = 1.0 if broke_prev_high else (-1.0 if broke_prev_low else 0.0)

    # --- Ichimoku (repo: last 9/26/52)
    tenkan = (float(np.max(H[-9:])) + float(np.min(L[-9:]))) / 2.0
    kijun = (float(np.max(H[-26:])) + float(np.min(L[-26:]))) / 2.0
    span_a = (tenkan + kijun) / 2.0
    span_b = (float(np.max(H[-52:])) + float(np.min(L[-52:]))) / 2.0
    is_above_kumo = mid_price > max(span_a, span_b)
    is_below_kumo = mid_price < min(span_a, span_b)

    # --- feat_30 norm_tk_diff
    norm_tk_diff = (tenkan - kijun) / safe_atr

    # --- feat_31 tk_cross_signal
    prev_tenkan = (float(np.max(H[-10:-1])) + float(np.min(L[-10:-1]))) / 2.0
    prev_kijun = (float(np.max(H[-27:-1])) + float(np.min(L[-27:-1]))) / 2.0
    tk_cross = 0.0
    if prev_tenkan <= prev_kijun and tenkan > kijun:
        tk_cross = 1.0
    elif prev_tenkan >= prev_kijun and tenkan < kijun:
        tk_cross = -1.0

    # --- feat_32 kumo_sig
    kumo_sig = 1.0 if is_above_kumo else (-1.0 if is_below_kumo else 0.0)

    # --- feat_33 norm_kumo_width
    norm_kumo_width = (span_a - span_b) / safe_atr

    # --- feat_34 norm_rsi
    diffs = np.diff(C[-15:])
    gains = np.maximum(diffs, 0.0)
    losses = np.maximum(-diffs, 0.0)
    avg_gain = float(np.mean(gains)) + 1e-8
    avg_loss = float(np.mean(losses)) + 1e-8
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    norm_rsi = (rsi - 50.0) / 16.66

    # --- feat_35/36 dist_to_ema
    dist_ema21 = (mid_price - ema21) / safe_atr
    dist_ema50 = (mid_price - ema50) / safe_atr

    # --- feat_37 cross_asset_z_score
    closes_20 = C[-19:] if len(C) >= 19 else C
    window_20 = np.append(closes_20, mid_price)
    mu = float(np.mean(window_20))
    sigma = float(np.std(window_20))
    z = (mid_price - mu) / (sigma + 1e-8)

    # --- feat_38/39 norm_dist_to_tenkan/kijun
    ndt = (tenkan - kijun) / (safe_atr * 2.0)
    ndk = (kijun - tenkan) / (safe_atr * 2.0)

    # =========================================================================
    # MTF features (aggregate from the SAME full completed_bars list)
    # =========================================================================

    def agg(
        bars_: list[BarData], period_minutes: int
    ) -> list[tuple[float, float, float, float, float]]:
        """(o,h,l,c,v) tuples per completed bucket, UTC-aligned (repo semantics)."""
        out: list[tuple[float, float, float, float, float]] = []
        cur = None
        o = h = l = c = 0.0
        v = 0
        for b in bars_:
            total_minutes = int(b.timestamp.timestamp()) // 60
            bucket_minute = (total_minutes // period_minutes) * period_minutes
            bucket_ts = datetime.fromtimestamp(bucket_minute * 60, tz=UTC)
            if cur is None:
                cur = bucket_ts
                o, h, l, c, v = b.open, b.high, b.low, b.close, b.tick_volume
            elif bucket_ts == cur:
                h = max(h, b.high)
                l = min(l, b.low)
                c = b.close
                v += b.tick_volume
            else:
                out.append((o, h, l, c, v))
                cur = bucket_ts
                o, h, l, c, v = b.open, b.high, b.low, b.close, b.tick_volume
        if cur is not None:
            out.append((o, h, l, c, v))
        return out

    m15 = agg(bars, 15)
    m30 = agg(bars, 30)
    h1 = agg(bars, 60)
    h4 = agg(bars, 240)

    # --- feat_40 htf_h4_trend
    if len(h4) >= 3:
        h4c = np.array([x[4] for x in h4], dtype=np.float64)
        h4e = ema(h4c, 3)
        htf_h4 = 1.0 if h4c[-1] > h4e else -1.0
    elif len(h4) >= 1:
        htf_h4 = 1.0 if h4[-1][4] > h4[0][4] else -1.0
    else:
        htf_h4 = 0.0

    # --- feat_41 htf_h1_momentum
    if len(h1) >= 2:
        htf_h1 = (h1[-1][4] - h1[-2][4]) / safe_atr
    else:
        htf_h1 = 0.0

    # --- feat_42 htf_m30_structure
    if len(m30) >= 5:
        m30c = np.array([x[4] for x in m30], dtype=np.float64)
        m30e = ema(m30c, 5)
        htf_m30 = 1.0 if m30c[-1] > m30e else -1.0
    else:
        htf_m30 = 0.0

    # --- feat_43 htf_m15_confirmation
    if len(m15) >= 2:
        ml, mp = m15[-1], m15[-2]
        m15_bull = ml[3] > ml[0] and mp[3] < mp[0] and ml[3] >= mp[0]
        m15_bear = ml[3] < ml[0] and mp[3] > mp[0] and ml[3] <= mp[0]
        if m15_bull:
            htf_m15 = 1.0
        elif m15_bear:
            htf_m15 = -1.0
        else:
            htf_m15 = 1.0 if ml[3] > ml[0] else -1.0
    else:
        htf_m15 = 0.0

    # --- feat_44/45 S/R zone distances
    # swing points via fractal window=3 over last 50 bars (repo semantics)
    sr_bars = bars[-50:]

    def fractals(window: int = 3) -> tuple[list[float], list[float]]:
        if len(sr_bars) < window * 2 + 1:
            return [min(b.low for b in sr_bars)], [max(b.high for b in sr_bars)]
        supports, resistances = [], []
        hs = [b.high for b in sr_bars]
        ls = [b.low for b in sr_bars]
        for i in range(window, len(sr_bars) - window):
            if hs[i] == max(hs[i - window : i + window + 1]):
                resistances.append(hs[i])
            if ls[i] == min(ls[i - window : i + window + 1]):
                supports.append(ls[i])
        if not supports:
            supports = [min(ls)]
        if not resistances:
            resistances = [max(hs)]

        def clean(levels: list[float]) -> list[float]:
            levels = sorted(levels)
            cleaned = [levels[0]]
            for lv in levels[1:]:
                if (lv - cleaned[-1]) / cleaned[-1] > 0.0005:
                    cleaned.append(lv)
            return cleaned

        return clean(supports), clean(resistances)

    supports, resistances = fractals(3)
    nearest_support = None
    nearest_resistance = None
    for s in reversed(sorted(supports)):
        if s < mid_price:
            nearest_support = s
            break
    for r in sorted(resistances):
        if r > mid_price:
            nearest_resistance = r
            break
    if nearest_support is None:
        nearest_support = min(b.low for b in sr_bars)
    if nearest_resistance is None:
        nearest_resistance = max(b.high for b in sr_bars)
    support_zone_dist = max(0.0, (mid_price - nearest_support) / safe_atr)
    resistance_zone_dist = max(0.0, (nearest_resistance - mid_price) / safe_atr)

    # =========================================================================
    # SMC features 46-49 (repo semantics from executable code)
    # =========================================================================
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(5, len(bars) - 5):
        w_highs = [b.high for b in bars[i - 5 : i + 6]]
        w_lows = [b.low for b in bars[i - 5 : i + 6]]
        if bars[i].high == max(w_highs):
            swing_highs.append((i, bars[i].high))
        if bars[i].low == min(w_lows):
            swing_lows.append((i, bars[i].low))
    if not swing_highs:
        swing_highs = [(len(bars) - 25, float(np.max(H)))]
    if not swing_lows:
        swing_lows = [(len(bars) - 25, float(np.min(L)))]
    last_sh = swing_highs[-1][1]
    last_sl = swing_lows[-1][1]

    # feat_47 equilibrium ratio
    ob_price = (H[-2] + L[-2]) / 2.0
    eq_ratio = float(np.clip((ob_price - last_sl) / (last_sh - last_sl + 1e-8), 0.0, 1.0))

    # feat_46 BOS
    bos = 0.0
    prev_shs = [val for idx, val in swing_highs if idx < len(bars) - 2]
    prev_sls = [val for idx, val in swing_lows if idx < len(bars) - 2]
    if ob_type == 1.0 and prev_shs and C[-1] > prev_shs[-1]:
        bos = 1.0
    elif ob_type == -1.0 and prev_sls and C[-1] < prev_sls[-1]:
        bos = 1.0
    elif broke_prev_high or broke_prev_low or choch_bull or choch_bear:
        bos = 0.50

    # feat_48 liquidity swept
    liq = 0.0
    if sweep != 0.0:
        liq = 1.0
    elif ob_type == 1.0 and prev_sls:
        tgt = prev_sls[-1]
        if L[-1] < tgt and C[-1] > tgt:
            liq = 1.0
    elif ob_type == -1.0 and prev_shs:
        tgt = prev_shs[-1]
        if H[-1] > tgt and C[-1] < tgt:
            liq = 1.0

    # feat_49 fib alignment
    fib_dist = abs(eq_ratio - 0.55)
    fib_align = float(np.clip(1.0 - (fib_dist / 0.35), 0.0, 1.0))

    vec = [
        upper_wick_ratio,
        lower_wick_ratio,
        body_to_range_ratio,
        is_doji,
        pinbar_sig,
        engulfing_sig,
        clv,
        consec,
        norm_displacement,
        spike,
        dist_sh,
        dist_sl,
        compression,
        extreme_sig,
        stop_depth,
        sweep,
        s_tokyo,
        s_london,
        s_ny,
        s_overlap,
        lag1 * 100.0,
        lag2 * 100.0,
        lag3 * 100.0,
        lag1_atr,
        vol_z,
        lag1_clv,
        fvg_sig,
        ob_strength,
        choch_sig,
        breakout_sig,
        norm_tk_diff,
        tk_cross,
        kumo_sig,
        norm_kumo_width,
        norm_rsi,
        dist_ema21,
        dist_ema50,
        z,
        ndt,
        ndk,
        htf_h4,
        htf_h1,
        htf_m30,
        htf_m15,
        support_zone_dist,
        resistance_zone_dist,
        bos,
        eq_ratio,
        liq,
        fib_align,
    ]
    assert len(vec) == 50, f"independent vector has {len(vec)}"
    return vec


def sanitize(vec: list[float]) -> list[float]:
    out = []
    for v in vec:
        if math.isnan(v) or math.isinf(v):
            out.append(0.0)
        else:
            out.append(max(-3.0, min(3.0, float(v))))
    return out


# =============================================================================
# Comparison
# =============================================================================


def compare(bars: list[BarData], tick: TickData, symbol: str, label: str) -> dict:
    engine = ScalpFeatureEngine(symbol=symbol)
    fv = engine.compute_from_bars(completed_bars=bars, current_tick=tick)
    runtime = sanitize(fv.to_tensor_input())
    expected_raw = independent_features(bars, tick, symbol)
    expected = sanitize(expected_raw)

    rows = []
    for i, name in enumerate(FEATURE_NAMES):
        r, e = runtime[i], expected[i]
        abs_err = abs(r - e)
        denom = max(abs(e), 1e-12)
        rel_err = abs_err / denom
        tol = 1e-9 + 1e-9 * abs(e)
        status = "PASS" if abs_err <= tol else "FAIL"
        rows.append(
            {
                "index": i,
                "name": name,
                "runtime": r,
                "expected": e,
                "abs_err": abs_err,
                "rel_err": rel_err,
                "status": status,
            }
        )
    failures = [r for r in rows if r["status"] != "PASS"]
    return {
        "label": label,
        "n_bars": len(bars),
        "runtime_raw": runtime,
        "rows": rows,
        "n_fail": len(failures),
        "failures": failures,
    }


def main() -> None:
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    fixtures = {
        "trend": (trend_bars(), lagging_tick(engine, trend_bars())),
        "flat": (flat_bars(), lagging_tick(engine, flat_bars())),
        "doji": (doji_bars(), lagging_tick(engine, doji_bars())),
        "wick_dominant": (wick_dominant_bars(), lagging_tick(engine, wick_dominant_bars())),
        "volume_spike": (volume_spike_bars(), lagging_tick(engine, volume_spike_bars())),
        "reversal": (reversal_bars(), lagging_tick(engine, reversal_bars())),
        "high_vol": (high_vol_bars(), lagging_tick(engine, high_vol_bars())),
    }
    results = {}
    for label, (bars, tick) in fixtures.items():
        res = compare(bars, tick, "XAUUSD", label)
        results[label] = res
        print(f"=== {label}: n_fail={res['n_fail']}/50")
        for f in res["failures"]:
            print(
                f"  FAIL idx={f['index']} {f['name']} runtime={f['runtime']:.10g} expected={f['expected']:.10g} abs={f['abs_err']:.3g} rel={f['rel_err']:.3g}"
            )

    with open(
        r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\scratch\forensic_50d_matrix.json", "w"
    ) as fh:
        json.dump(results, fh, indent=1, default=str)
    print("matrix written to scratch/forensic_50d_matrix.json")


if __name__ == "__main__":
    main()
