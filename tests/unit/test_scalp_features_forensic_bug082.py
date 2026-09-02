"""
Forensic 50D Contract Tests — executable-contract truth (BUG-082 audit)
========================================================================
EXTENDS tests/unit/test_scalp_features.py with the forensic verification
matrix proven against the LIVE engine on 2026-08-18:

  * exact 50-dimension + FEATURE_NAMES/active-schema agreement
  * index 1 canonical definition (lower_wick_ratio — executable contract)
  * every index independently recomputed from raw OHLCV on deterministic
    fixtures (trend/flat/doji/wick/volume-spike/reversal/high-vol)
  * determinism (100 identical runs)
  * causality: deep-history mutation (T-1) never changes features at T;
    appending a bar moves the tail window by design (T+1)
  * edge cases: doji / zero-range / volume spike / wick-dominant /
    flat market / strong reversal — no NaN, no Inf, all in [-3, 3]
  * norm_rsi divisor contract (16.66 — doc vs code divergence recorded)
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FEATURE_NAMES, ScalpFeatureEngine
from nexus_scalp.features.schema import active_dimension, active_schema
from nexus_scalp.market_data.bar_aggregator import BarData

# =============================================================================
# Fixture builders (deterministic)
# =============================================================================


def make_bars(
    closes: list[float],
    *,
    ohlc: list[tuple[float, float, float, float]] | None = None,
    volumes: list[int] | None = None,
    start: datetime | None = None,
) -> list[BarData]:
    start = start or datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    bars = []
    for i, c in enumerate(closes):
        if ohlc is not None and i < len(ohlc):
            o, h, l, cl = ohlc[i]
        else:
            o, h, l, cl = c, c + 0.8, c - 0.7, c
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=start + timedelta(minutes=i),
                open=o,
                high=h,
                low=l,
                close=cl,
                tick_volume=int(volumes[i]) if volumes else 100,
                is_complete=True,
            )
        )
    return bars


def trend_bars(n: int = 60) -> list[BarData]:
    return make_bars([2000.0 + i * 0.1 for i in range(n)])


def flat_bars(n: int = 60) -> list[BarData]:
    ohlc = [(2000.0, 2000.0, 2000.0, 2000.0) for _ in range(n)]
    return make_bars([2000.0] * n, ohlc=ohlc)


def doji_bars(n: int = 60) -> list[BarData]:
    ohlc = [(1999.98, 2000.05, 1999.95, 1999.98) for _ in range(n)]
    return make_bars([1999.98] * n, ohlc=ohlc)


def wick_dominant_bars(n: int = 60) -> list[BarData]:
    ohlc = [(2000.0, 2002.0, 1999.9, 2000.05) for _ in range(n)]
    return make_bars([2000.05] * n, ohlc=ohlc)


def volume_spike_bars(n: int = 60) -> list[BarData]:
    bars = trend_bars(n)
    vols = [100] * n
    vols[-2] = 5000
    return make_bars(
        [b.close for b in bars],
        ohlc=[(b.open, b.high, b.low, b.close) for b in bars],
        volumes=vols,
    )


def reversal_bars(n: int = 60) -> list[BarData]:
    closes = [2000.0 + i * 0.1 for i in range(40)] + [2004.0 - i * 0.25 for i in range(20)]
    return make_bars(closes)


def high_vol_bars(n: int = 60) -> list[BarData]:
    rng = np.random.default_rng(42)
    closes = [2000.0]
    for _ in range(n - 1):
        closes.append(closes[-1] + float(rng.normal(0, 2.0)))
    ohlc = [(c - 1.0, c + 2.0, c - 2.0, c) for c in closes]
    return make_bars(closes, ohlc=ohlc)


def lagging_tick(engine: ScalpFeatureEngine, bars: list[BarData]) -> TickData:
    last = bars[-1]
    mid = last.close
    return TickData(
        symbol=engine.symbol,
        timestamp=last.timestamp + timedelta(seconds=30),
        bid=mid - 0.1,
        ask=mid + 0.1,
        volume=1,
    )


def sanitize(vec: list[float]) -> list[float]:
    out = []
    for v in vec:
        if math.isnan(v) or math.isinf(v):
            out.append(0.0)
        else:
            out.append(max(-3.0, min(3.0, float(v))))
    return out


# =============================================================================
# Independent recomputation (numpy/math only — NO repo feature helpers)
# =============================================================================


def ema(arr: np.ndarray, period: int) -> float:
    if len(arr) == 0:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    e = float(arr[0])
    for p in arr[1:]:
        e = p * alpha + e * (1.0 - alpha)
    return e


def independent_50d(bars: list[BarData], tick: TickData) -> list[float]:
    """Independent mathematical recomputation of the executable 50D contract."""
    n = len(bars)
    closes = np.array([b.close for b in bars], dtype=np.float64)
    highs = np.array([b.high for b in bars], dtype=np.float64)
    lows = np.array([b.low for b in bars], dtype=np.float64)
    opens = np.array([b.open for b in bars], dtype=np.float64)
    volumes = np.array([b.tick_volume for b in bars], dtype=np.float64)
    mid_price = (tick.bid + tick.ask) / 2.0

    tail = slice(max(0, n - 55), n)
    C, H, L, O, V = closes[tail], highs[tail], lows[tail], opens[tail], volumes[tail]

    tr = np.maximum(H - L, np.maximum(np.abs(H - np.roll(C, 1)), np.abs(L - np.roll(C, 1))))
    tr[0] = H[0] - L[0]
    atr = float(np.mean(tr[-14:]))
    safe_atr = max(atr, 0.20)
    live_disp = mid_price - C[-1]

    bar_range = max(H[-1] - L[-1], 0.01)
    body_top = max(O[-1], C[-1])
    body_bottom = min(O[-1], C[-1])
    body_size = body_top - body_bottom
    uw = H[-1] - body_top
    lw = body_bottom - L[-1]
    uw_ratio = uw / bar_range
    lw_ratio = lw / bar_range
    body_ratio = body_size / bar_range

    is_doji = 1.0 if body_ratio <= 0.12 else 0.0
    is_hammer = lw_ratio >= 0.55 and body_top >= (H[-1] - bar_range * 0.35)
    is_shoot = uw_ratio >= 0.55 and body_bottom <= (L[-1] + bar_range * 0.35)
    pinbar = (
        float(min(2.0, lw_ratio * 2.0))
        if is_hammer
        else (float(max(-2.0, -uw_ratio * 2.0)) if is_shoot else 0.0)
    )
    eng_bull = C[-2] < O[-2] and C[-1] > O[-1] and O[-1] <= C[-2] and C[-1] >= O[-2]
    eng_bear = C[-2] > O[-2] and C[-1] < O[-1] and O[-1] >= C[-2] and C[-1] <= O[-2]
    engulf = (
        float(min(2.0, 1.0 + body_ratio))
        if eng_bull
        else (float(max(-2.0, -(1.0 + body_ratio))) if eng_bear else 0.0)
    )
    clv = ((C[-1] - L[-1]) - (H[-1] - C[-1])) / bar_range

    color_dir = np.sign(C[-10:] - O[-10:])
    cnt = 0.0
    cur = color_dir[-1]
    if cur != 0:
        for d in reversed(color_dir):
            if d == cur:
                cnt += 1.0
            else:
                break
    consec = float(np.clip((cnt * cur) / 5.0, -1.0, 1.0))

    log_ret_m1 = math.log(C[-1] / C[-2]) if C[-2] > 0 else 0.0
    spike = 1.0 if (abs(live_disp) > safe_atr * 0.6 and (live_disp * log_ret_m1) < 0) else 0.0

    swing_high_20 = float(np.max(H[-20:-1]))
    swing_low_20 = float(np.min(L[-20:-1]))
    dist_sh = (swing_high_20 - mid_price) / safe_atr
    dist_sl = (mid_price - swing_low_20) / safe_atr

    range_5 = float(np.max(H[-5:]) - np.min(L[-5:]) + 1e-8)
    range_20 = float(np.max(H[-20:]) - np.min(L[-20:]) + 1e-8)
    compression = float(np.clip(range_5 / range_20, 0.0, 2.0))

    recent_max_50 = float(np.max(H[-50:]))
    recent_min_50 = float(np.min(L[-50:]))
    total_range = (recent_max_50 - recent_min_50) + 1e-8
    range_pos = (mid_price - recent_min_50) / total_range
    extreme = 1.0 if range_pos >= 0.95 else (-1.0 if range_pos <= 0.05 else 0.0)

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

    dt = tick.timestamp
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    hour = dt.hour
    s_tokyo = 1.0 if 0 <= hour < 8 else 0.0
    s_london = 1.0 if 7 <= hour < 15 else 0.0
    s_ny = 1.0 if 13 <= hour < 21 else 0.0
    s_overlap = 1.0 if 13 <= hour < 15 else 0.0

    lag1 = math.log(C[-2] / C[-3]) if C[-3] > 0 else 0.0
    lag2 = math.log(C[-3] / C[-4]) if C[-4] > 0 else 0.0
    lag3 = math.log(C[-4] / C[-5]) if C[-5] > 0 else 0.0
    tr_lag1 = max(H[-2] - L[-2], abs(H[-2] - C[-3]), abs(L[-2] - C[-3]))
    lag1_atr = tr_lag1 / safe_atr
    vol_mean_20 = float(np.mean(V[-21:-1])) + 1e-8
    vol_std_20 = float(np.std(V[-21:-1])) + 1e-8
    vol_z = (V[-2] - vol_mean_20) / vol_std_20
    lag1_range = max(H[-2] - L[-2], 0.01)
    lag1_clv = ((C[-2] - L[-2]) - (H[-2] - C[-2])) / lag1_range

    fvg_bull = (L[-1] - H[-3]) > safe_atr * 0.20
    fvg_bear = (L[-3] - H[-1]) > safe_atr * 0.20
    fvg_sig = (
        (L[-1] - H[-3]) / safe_atr
        if fvg_bull
        else (-(L[-3] - H[-1]) / safe_atr if fvg_bear else 0.0)
    )

    ob_type = 0.0
    if C[-1] > H[-2] and C[-2] < O[-2]:
        ob_type = 1.0
    elif C[-1] < L[-2] and C[-2] > O[-2]:
        ob_type = -1.0
    ob_strength = ob_type * (V[-1] / vol_mean_20)

    ema20 = ema(C[-20:], 20)
    ema50 = ema(C[-50:], 50)
    ema21 = ema(C[-21:], 21)
    is_down = ema20 < ema50
    is_up = ema20 > ema50
    sh20 = float(np.max(H[-20:-5]))
    sl20 = float(np.min(L[-20:-5]))
    choch_bull = is_down and mid_price > sh20
    choch_bear = is_up and mid_price < sl20
    choch_sig = 1.0 if choch_bull else (-1.0 if choch_bear else 0.0)
    broke_high = mid_price > H[-1]
    broke_low = mid_price < L[-1]
    breakout = 1.0 if broke_high else (-1.0 if broke_low else 0.0)

    tenkan = (float(np.max(H[-9:])) + float(np.min(L[-9:]))) / 2.0
    kijun = (float(np.max(H[-26:])) + float(np.min(L[-26:]))) / 2.0
    span_a = (tenkan + kijun) / 2.0
    span_b = (float(np.max(H[-52:])) + float(np.min(L[-52:]))) / 2.0
    above = mid_price > max(span_a, span_b)
    below = mid_price < min(span_a, span_b)
    norm_tk = (tenkan - kijun) / safe_atr
    prev_t = (float(np.max(H[-10:-1])) + float(np.min(L[-10:-1]))) / 2.0
    prev_k = (float(np.max(H[-27:-1])) + float(np.min(L[-27:-1]))) / 2.0
    tk_cross = 0.0
    if prev_t <= prev_k and tenkan > kijun:
        tk_cross = 1.0
    elif prev_t >= prev_k and tenkan < kijun:
        tk_cross = -1.0
    kumo_sig = 1.0 if above else (-1.0 if below else 0.0)
    norm_kumo = (span_a - span_b) / safe_atr

    diffs = np.diff(C[-15:])
    gains = np.maximum(diffs, 0.0)
    losses = np.maximum(-diffs, 0.0)
    avg_gain = float(np.mean(gains)) + 1e-8
    avg_loss = float(np.mean(losses)) + 1e-8
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    norm_rsi = (rsi - 50.0) / 16.66  # executable contract (doc says /25 — divergence documented)

    dist_ema21 = (mid_price - ema21) / safe_atr
    dist_ema50 = (mid_price - ema50) / safe_atr

    closes_20 = C[-19:] if len(C) >= 19 else C
    window_20 = np.append(closes_20, mid_price)
    mu = float(np.mean(window_20))
    sigma = float(np.std(window_20))
    z = (mid_price - mu) / (sigma + 1e-8)
    ndt = (tenkan - kijun) / (safe_atr * 2.0)
    ndk = (kijun - tenkan) / (safe_atr * 2.0)

    # MTF aggregation (repo semantics: UTC bucket alignment)
    def agg(period_minutes: int) -> list[tuple[float, float, float, float, float]]:
        out = []
        cur = None
        o = h = l = c = 0.0
        v = 0
        for b in bars:
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

    m15 = agg(15)
    m30 = agg(30)
    h1 = agg(60)
    h4 = agg(240)

    if len(h4) >= 3:
        h4c = np.array([x[4] for x in h4], dtype=np.float64)
        h4e = ema(h4c, 3)
        htf_h4 = 1.0 if h4c[-1] > h4e else -1.0
    elif len(h4) >= 1:
        htf_h4 = 1.0 if h4[-1][4] > h4[0][4] else -1.0
    else:
        htf_h4 = 0.0

    htf_h1 = (h1[-1][4] - h1[-2][4]) / safe_atr if len(h1) >= 2 else 0.0

    if len(m30) >= 5:
        m30c = np.array([x[4] for x in m30], dtype=np.float64)
        m30e = ema(m30c, 5)
        htf_m30 = 1.0 if m30c[-1] > m30e else -1.0
    else:
        htf_m30 = 0.0

    if len(m15) >= 2:
        ml, mp = m15[-1], m15[-2]
        bull = ml[3] > ml[0] and mp[3] < mp[0] and ml[3] >= mp[0]
        bear = ml[3] < ml[0] and mp[3] > mp[0] and ml[3] <= mp[0]
        htf_m15 = 1.0 if bull else (-1.0 if bear else (1.0 if ml[3] > ml[0] else -1.0))
    else:
        htf_m15 = 0.0

    # S/R (fractal window=3 over last 50 bars)
    sr_bars = bars[-50:]
    hs = [b.high for b in sr_bars]
    ls = [b.low for b in sr_bars]
    if len(sr_bars) < 7:
        supports, resistances = [min(ls)], [max(hs)]
    else:
        supports, resistances = [], []
        for i in range(3, len(sr_bars) - 3):
            if hs[i] == max(hs[i - 3 : i + 4]):
                resistances.append(hs[i])
            if ls[i] == min(ls[i - 3 : i + 4]):
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

    supports, resistances = clean(supports), clean(resistances)
    ns = nr = None
    for s in reversed(supports):
        if s < mid_price:
            ns = s
            break
    for r in resistances:
        if r > mid_price:
            nr = r
            break
    if ns is None:
        ns = min(ls)
    if nr is None:
        nr = max(hs)
    support_dist = max(0.0, (mid_price - ns) / safe_atr)
    resistance_dist = max(0.0, (nr - mid_price) / safe_atr)

    # SMC (repo executable semantics)
    shs: list[tuple[int, float]] = []
    sls: list[tuple[int, float]] = []
    for i in range(5, len(bars) - 5):
        w_highs = [b.high for b in bars[i - 5 : i + 6]]
        w_lows = [b.low for b in bars[i - 5 : i + 6]]
        if bars[i].high == max(w_highs):
            shs.append((i, bars[i].high))
        if bars[i].low == min(w_lows):
            sls.append((i, bars[i].low))
    if not shs:
        shs = [(len(bars) - 25, float(np.max(H)))]
    if not sls:
        sls = [(len(bars) - 25, float(np.min(L)))]
    last_sh = shs[-1][1]
    last_sl = sls[-1][1]
    ob_price = (H[-2] + L[-2]) / 2.0
    eq_ratio = float(np.clip((ob_price - last_sl) / (last_sh - last_sl + 1e-8), 0.0, 1.0))
    prev_shs = [v for idx, v in shs if idx < len(bars) - 2]
    prev_sls = [v for idx, v in sls if idx < len(bars) - 2]
    bos = 0.0
    if ob_type == 1.0 and prev_shs and C[-1] > prev_shs[-1]:
        bos = 1.0
    elif ob_type == -1.0 and prev_sls and C[-1] < prev_sls[-1]:
        bos = 1.0
    elif broke_high or broke_low or choch_bull or choch_bear:
        bos = 0.50
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
    fib_align = float(np.clip(1.0 - (abs(eq_ratio - 0.55) / 0.35), 0.0, 1.0))

    return [
        uw_ratio,
        lw_ratio,
        body_ratio,
        is_doji,
        pinbar,
        engulf,
        clv,
        consec,
        live_disp / safe_atr,
        spike,
        dist_sh,
        dist_sl,
        compression,
        extreme,
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
        breakout,
        norm_tk,
        tk_cross,
        kumo_sig,
        norm_kumo,
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
        support_dist,
        resistance_dist,
        bos,
        eq_ratio,
        liq,
        fib_align,
    ]


# =============================================================================
# Tests
# =============================================================================

FIXTURES = {
    "trend": trend_bars,
    "flat": flat_bars,
    "doji": doji_bars,
    "wick_dominant": wick_dominant_bars,
    "volume_spike": volume_spike_bars,
    "reversal": reversal_bars,
    "high_vol": high_vol_bars,
}


def _engine_vec(bars: list[BarData], tick: TickData) -> list[float]:
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
    return sanitize(fv.to_tensor_input())


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES))
def test_50d_contract_dimension_and_order(fixture_name: str) -> None:
    """Exactly 50 dims, FEATURE_NAMES length == active schema, no NaN/Inf."""
    bars = FIXTURES[fixture_name]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    vec = _engine_vec(bars, tick)
    assert len(vec) == 50
    assert len(FEATURE_NAMES) == 50
    assert len(FEATURE_NAMES) == active_dimension()
    assert active_schema().schema_id == "scalp_v1"
    for v in vec:
        assert math.isfinite(v)
        assert -3.0 <= v <= 3.0


def test_index1_canonical_lower_wick_ratio() -> None:
    """Executable contract: index 1 = lower_wick_ratio (NOT log_returns)."""
    assert FEATURE_NAMES[1] == "lower_wick_ratio"
    bars = FIXTURES["wick_dominant"]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    vec = _engine_vec(bars, tick)
    # wick-dominant fixture: long upper wick -> tiny lower wick ratio
    assert 0.0 <= vec[1] <= 1.0
    assert vec[1] < 0.5


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES))
def test_independent_recomputation_all_50(fixture_name: str) -> None:
    """Every index matches an independent numpy-only recomputation."""
    bars = FIXTURES[fixture_name]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    runtime = _engine_vec(bars, tick)
    expected = sanitize(independent_50d(bars, tick))
    for i in range(50):
        assert math.isclose(runtime[i], expected[i], rel_tol=1e-9, abs_tol=1e-9), (
            f"fixture={fixture_name} idx={i} name={FEATURE_NAMES[i]} "
            f"runtime={runtime[i]:.10g} expected={expected[i]:.10g}"
        )


def test_determinism_100_runs() -> None:
    """Same input -> identical vector, 100 consecutive runs."""
    bars = FIXTURES["trend"]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    first = engine.compute_from_bars(bars, tick).to_tensor_input()
    for _ in range(100):
        v = engine.compute_from_bars(bars, tick).to_tensor_input()
        assert v == first


def test_causality_t_minus_1_deep_history_mutation() -> None:
    """Changing only a deep-past bar (idx 3) must not change features at T."""
    bars = FIXTURES["reversal"]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    base = engine.compute_from_bars(bars, tick).to_tensor_input()
    mutated = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=b.timestamp,
            open=b.open,
            high=b.high + 3.0,
            low=b.low,
            close=b.close,
            tick_volume=b.tick_volume,
            is_complete=True,
        )
        if i == 3
        else b
        for i, b in enumerate(bars)
    ]
    after = engine.compute_from_bars(mutated, tick).to_tensor_input()
    assert after == base


def test_causality_t_plus_1_tail_window_contract() -> None:
    """
    The engine's contract is 'bars <= T' (a tail window ending at the last
    completed bar). Appending a new bar legitimately moves 'now' — the
    features recompute on the new window. This test documents that contract
    rather than asserting invariance.
    """
    bars = FIXTURES["trend"]()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    base = engine.compute_from_bars(bars, tick).to_tensor_input()
    last = bars[-1]
    future = BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=last.timestamp + timedelta(minutes=1),
        open=last.close,
        high=last.close + 9.0,
        low=last.close - 9.0,
        close=last.close + 5.0,
        tick_volume=9999,
        is_complete=True,
    )
    after = engine.compute_from_bars([*bars, future], tick).to_tensor_input()
    # window shifted: the OLD last bar is now closes[-2]; features legitimately change
    assert after != base  # documents tail-window semantics, not a defect


def test_norm_rsi_divisor_contract() -> None:
    """Executable contract uses (RSI-50)/16.66 (doc §5.5 says /25 — divergence logged)."""
    assert FEATURE_NAMES[34] == "norm_rsi"
    # rsi=50 -> 0.0
    bars = flat_bars()
    tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
    vec = _engine_vec(bars, tick)
    assert abs(vec[34]) < 1e-6


def test_edge_cases_no_nan_no_inf() -> None:
    """Doji / zero-range / volume spike must never produce NaN/Inf."""
    for name, builder in FIXTURES.items():
        bars = builder()
        tick = lagging_tick(ScalpFeatureEngine(symbol="XAUUSD"), bars)
        fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
        vec = fv.to_tensor_input()
        assert all(math.isfinite(v) for v in vec), f"non-finite in {name}"
        assert all(-3.0 <= v <= 3.0 for v in vec), f"out of range in {name}"
