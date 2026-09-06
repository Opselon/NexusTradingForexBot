# XAUUSD spec helpers — appended to calculators.py, nothing above touched.
# Spec: agents/xauusd_indicator_math.md (Sections A-D).
# Bar-input variants (H/L aware) + spec vote maps + Section-D aggregation.

from __future__ import annotations

from collections.abc import Sequence as _Seq  # noqa: F401  (kept for helper annotations)


def _bar_field(b: object, key: str, default: float = 0.0) -> float:
    if isinstance(b, dict):
        v = b.get(key, default)
        return float(v) if v is not None else default
    return float(getattr(b, key, default) or default)


def cci_from_bars(bars, period: int = 20):
    """CCI(20) on typical price TP=(H+L+C)/3, Spec A3."""
    if len(bars) < period:
        return None
    tps = [
        (
            float(_bar_field(b, "high", _bar_field(b, "close")))
            + float(_bar_field(b, "low", _bar_field(b, "close")))
            + float(_bar_field(b, "close"))
        )
        / 3
        for b in bars[-period:]
    ]
    ma = sum(tps) / period
    md = sum(abs(x - ma) for x in tps) / period
    if md == 0:
        return 0.0
    return (tps[-1] - ma) / (0.015 * md)


def awesome_from_bars(bars):
    """AO = SMA(MP,5) - SMA(MP,34), MP=(H+L)/2, Spec A5."""
    if len(bars) < 34:
        return None
    mps = [
        (
            float(_bar_field(b, "high", _bar_field(b, "close")))
            + float(_bar_field(b, "low", _bar_field(b, "close")))
        )
        / 2
        for b in bars
    ]
    if len(mps) < 34:
        return None
    from nexus_scalp.indicators.calculators import _sma as _s

    s5 = _s(mps, 5)
    s34 = _s(mps, 34)
    if s5 is None or s34 is None:
        return None
    return s5 - s34


def awesome_signal_from_bars(bars) -> str:
    """Spec A5: AO>0 rising -> Buy, AO<0 falling -> Sell, else Neutral."""
    if len(bars) < 35:
        return "Neutral"
    ao_t = awesome_from_bars(bars)
    ao_prev = awesome_from_bars(bars[:-1])
    if ao_t is None or ao_prev is None:
        return "Neutral"
    if ao_t > 0 and ao_t > ao_prev:
        return "Buy"
    if ao_t < 0 and ao_t < ao_prev:
        return "Sell"
    return "Neutral"


def macd_signal_pair(prices, signal_period: int = 9):
    """Returns (macd_t, signal_t): EMA12-EMA26 and EMA(MACD,9), Spec A7.

    Single-pass streaming EMA (O(n)): seeds EMA12/EMA26 with SMA over the
    first 12/26 closes, then updates per bar. MACD series starts at bar 26;
    signal seeds with SMA over the first `signal_period` MACD points.
    """
    if len(prices) < 26 + signal_period - 1:
        return None, None
    k12 = 2.0 / 13.0
    k26 = 2.0 / 27.0
    # stream e12 from bar 12 -> end, e26 from bar 26 -> end
    e12_by_bar: dict[int, float] = {}
    cur12 = sum(prices[:12]) / 12
    e12_by_bar[11] = cur12
    for i in range(12, len(prices)):
        cur12 = prices[i] * k12 + cur12 * (1 - k12)
        e12_by_bar[i] = cur12
    macds: list[float] = []
    cur26 = sum(prices[:26]) / 26
    for i in range(25, len(prices)):
        if i > 25:
            cur26 = prices[i] * k26 + cur26 * (1 - k26)
        macds.append(e12_by_bar[i] - cur26)
    if not macds:
        return None, None
    if len(macds) < signal_period:
        return macds[-1], None
    k = 2.0 / (signal_period + 1)
    sig = sum(macds[:signal_period]) / signal_period
    for m in macds[signal_period:]:
        sig = m * k + sig * (1 - k)
    return macds[-1], sig


def macd_action_spec(prices) -> str:
    """Spec A7: MACD > Signal -> Buy, MACD < Signal -> Sell."""
    macd, sig = macd_signal_pair(prices, 9)
    if macd is None or sig is None:
        return "Neutral"
    if macd > sig:
        return "Buy"
    if macd < sig:
        return "Sell"
    return "Neutral"


def stoch_k_action_spec(v) -> str:
    """Spec A2: SlowK > 80 -> Sell, < 20 -> Buy."""
    if v is None:
        return "Neutral"
    if v > 80:
        return "Sell"
    if v < 20:
        return "Buy"
    return "Neutral"


def stoch_rsi_k_spec(prices, rsi_period: int = 14, k_smooth: int = 3):
    """Stochastic RSI Fast %K: SMA(StochRSI,3), Spec A8 with 80/20 bands."""
    from nexus_scalp.indicators.calculators import rsi as _rsi

    cells = []
    for i in range(rsi_period, len(prices) + 1):
        v = _rsi(prices[max(0, i - rsi_period * 2) : i], rsi_period)
        if v is not None:
            cells.append(v)
    if len(cells) < rsi_period + k_smooth - 1:
        return None
    stoch_vals = []
    for j in range(rsi_period - 1, len(cells)):
        win = cells[j - rsi_period + 1 : j + 1]
        lo, hi = min(win), max(win)
        stoch_vals.append(50.0 if hi == lo else 100 * (win[-1] - lo) / (hi - lo))
    if len(stoch_vals) < k_smooth:
        return None
    return sum(stoch_vals[-k_smooth:]) / k_smooth


def stoch_rsi_action_spec(v) -> str:
    """Spec A8: %K > 80 -> Sell, < 20 -> Buy."""
    if v is None:
        return "Neutral"
    if v > 80:
        return "Sell"
    if v < 20:
        return "Buy"
    return "Neutral"


def williams_r_from_bars(bars, period: int = 14):
    """Williams %R (14) canonical -100..0, Spec A9."""
    if len(bars) < period:
        return None
    win_h = max(float(_bar_field(b, "high", _bar_field(b, "close"))) for b in bars[-period:])
    win_l = min(float(_bar_field(b, "low", _bar_field(b, "close"))) for b in bars[-period:])
    close = float(_bar_field(bars[-1], "close"))
    if win_h == win_l:
        return -50.0
    return -100 * (win_h - close) / (win_h - win_l)


def williams_r_action_spec(v) -> str:
    """Spec A9 canonical -100..0: > -20 -> Sell, < -80 -> Buy."""
    if v is None:
        return "Neutral"
    if v > -20:
        return "Sell"
    if v < -80:
        return "Buy"
    return "Neutral"


def bull_bear_from_bars(bars, period: int = 13):
    """Elder Ray BBP = (H-EMA13) + (L-EMA13), Spec A10."""
    from nexus_scalp.indicators.calculators import _ema as _e2

    if len(bars) < period:
        return None
    closes = [float(_bar_field(b, "close")) for b in bars]
    ema = _e2(closes, period)
    if ema is None:
        return None
    h = float(_bar_field(bars[-1], "high", _bar_field(bars[-1], "close")))
    lo = float(_bar_field(bars[-1], "low", _bar_field(bars[-1], "close")))
    return (h - ema) + (lo - ema)


def ultimate_from_bars(bars, p1: int = 7, p2: int = 14, p3: int = 28):
    """UO with BP=C-min(L,Cprev), TR=max(H,Cprev)-min(L,Cprev), Spec A11."""
    if len(bars) < p3 + 1:
        return None

    def _avg(bp_sum, tr_sum):
        return (bp_sum / tr_sum) if tr_sum != 0 else 0.0

    def _win_avg(wbars):
        bp_sum = 0.0
        tr_sum = 0.0
        for i in range(1, len(wbars)):
            c = float(_bar_field(wbars[i], "close"))
            h = float(_bar_field(wbars[i], "high", c))
            lo = float(_bar_field(wbars[i], "low", c))
            cp = float(_bar_field(wbars[i - 1], "close"))
            bp_sum += c - min(lo, cp)
            tr_sum += max(h, cp) - min(lo, cp)
        return bp_sum, tr_sum

    bp1, tr1 = _win_avg(bars[-(p1 + 1) :])
    bp2, tr2 = _win_avg(bars[-(p2 + 1) :])
    bp3, tr3 = _win_avg(bars[-(p3 + 1) :])
    if tr1 == 0 and tr2 == 0 and tr3 == 0:
        return None
    return 100 * (4 * _avg(bp1, tr1) + 2 * _avg(bp2, tr2) + _avg(bp3, tr3)) / 7


def uo_action_spec(v) -> str:
    """Spec A11: UO > 70 -> Sell, < 30 -> Buy."""
    if v is None:
        return "Neutral"
    if v > 70:
        return "Sell"
    if v < 30:
        return "Buy"
    return "Neutral"


def adx_signal_from_bars(bars, period: int = 14) -> str:
    """Spec A4: ADX < 20 -> Neutral else +DI > -DI -> Buy else Sell."""
    if len(bars) < period * 2 + 1:
        return "Neutral"
    hs = [float(_bar_field(b, "high", _bar_field(b, "close"))) for b in bars]
    ls = [float(_bar_field(b, "low", _bar_field(b, "close"))) for b in bars]
    cs = [float(_bar_field(b, "close")) for b in bars]
    trs, pdms, mdms = [], [], []
    for i in range(1, len(cs)):
        up = hs[i] - hs[i - 1]
        down = ls[i - 1] - ls[i]
        pdms.append(up if (up > down and up > 0) else 0.0)
        mdms.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(hs[i] - ls[i], abs(hs[i] - cs[i - 1]), abs(ls[i] - cs[i - 1])))
    st = sum(trs[:period]) or 1e-9
    sp = sum(pdms[:period])
    sm = sum(mdms[:period])
    dxs = []
    pdi0 = 100 * sp / (st or 1e-9)
    mdi0 = 100 * sm / (st or 1e-9)
    s0 = pdi0 + mdi0
    dxs.append(0.0 if s0 == 0 else 100 * abs(pdi0 - mdi0) / s0)
    for i in range(period, len(trs)):
        st = st - st / period + trs[i]
        sp = sp - sp / period + pdms[i]
        sm = sm - sm / period + mdms[i]
        dn = st or 1e-9
        pdi = 100 * sp / dn
        mdi = 100 * sm / dn
        s = pdi + mdi
        dxs.append(0.0 if s == 0 else 100 * abs(pdi - mdi) / s)
    if len(dxs) < period:
        return "Neutral"
    adv = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adv = (adv * (period - 1) + d) / period
    if adv < 20:
        return "Neutral"
    dn = st or 1e-9
    pdi_last = 100 * sp / dn
    mdi_last = 100 * sm / dn
    if pdi_last > mdi_last:
        return "Buy"
    if mdi_last > pdi_last:
        return "Sell"
    return "Neutral"


def vwma_from_bars(bars, period: int = 20):
    """VWMA = sum(C*Vol)/sum(Vol), Spec B. Falls back to SMA when Vol absent."""
    from nexus_scalp.indicators.calculators import _sma as _s2

    if len(bars) < period:
        return None
    closes = [float(_bar_field(b, "close")) for b in bars[-period:]]
    vols = [
        float(_bar_field(b, "volume", _bar_field(b, "tick_volume", 0)) or 0) for b in bars[-period:]
    ]
    if all(v == 0 for v in vols):
        vols = [float(_bar_field(b, "tick_volume", 0) or 0) for b in bars[-period:]]
    denom = sum(vols)
    if denom == 0:
        return _s2(closes, period)
    return sum(c * v for c, v in zip(closes, vols, strict=False)) / denom


def hma_spec(prices, period: int = 9):
    """HMA(9): half=round-half-up(9/2)=5, sqrt=3, WMA pipeline, Spec B."""
    import math as _math

    from nexus_scalp.indicators.calculators import _wma as _w

    if len(prices) < period + _math.ceil(period / 2):
        return None
    half = int(period / 2 + 0.5) if period % 2 == 1 else period // 2
    sq = round(_math.sqrt(period)) or 1
    wh = _w(prices, half)
    wf = _w(prices, period)
    if wh is None or wf is None:
        return None
    raw = 2 * wh - wf
    series = []
    for k in range(sq):
        end = len(prices) - k
        sh = end - half
        sf = end - period
        if sh < 0 or sf < 0:
            continue
        ah = _w(prices[sh:end], half)
        af = _w(prices[sf:end], period)
        if ah is not None and af is not None:
            series.append(2 * ah - af)
    series.reverse()
    if len(series) < sq:
        return raw
    return _w(series, sq)


def ichimoku_baseline_from_bars(bars):
    """Kijun-sen (HH(26)+LL(26))/2 on H/L, Spec B."""
    if len(bars) < 26:
        return None
    highs = [float(_bar_field(b, "high", _bar_field(b, "close"))) for b in bars[-26:]]
    lows = [float(_bar_field(b, "low", _bar_field(b, "close"))) for b in bars[-26:]]
    return (max(highs) + min(lows)) / 2


def gauge_from_rating_spec(rating: float, sell: int, neutral: int, buy: int):
    """Spec D: rating=(buy-sell)/n; buckets +-0.5/+-0.1, angles 8/28/90/135/165."""
    if rating <= -0.5:
        label, angle = "Strong sell", 8
    elif rating <= -0.1:
        label, angle = "Sell", 28
    elif rating < 0.1:
        label, angle = "Neutral", 90
    elif rating < 0.5:
        label, angle = "Buy", 135
    else:
        label, angle = "Strong buy", 165
    return {"label": label, "sell": sell, "neutral": neutral, "buy": buy, "angle_deg": angle}


# -- TV-parity vote layer (reverse-engineered from TV's own pasted panels) --
# Evidence (TV M1/M5 pastes): MOM +0.945->Sell +2.075->Sell (sign rule dead);
# StochK 14.87->Neutral (80/20 dead); W%R abs 85.3->Neutral (abs-level dead);
# BBP +0.099/+0.161->Neutral (sign dead); MACD +0.021->Buy vs +0.273->Sell
# (MACD-vs-signal ALIVE); AO flat +0.686/+1.245->Neutral (directional ALIVE).
# Model: MOM/BBP/W%R directional (slope), StochK/StochRSI K-vs-D crossover,
# RSI/CCI/UO level bands, ADX DI-gated, MA sign, ICH deadband. TV displays
# Williams as POSITIVE (abs) — vote uses raw canonical slope.


def _slope_vote(cur, prev, eps: float = 1e-9) -> str:
    """Directional vote: rising -> Buy, falling -> Sell, flat -> Neutral."""
    if cur is None or prev is None:
        return "Neutral"
    if cur > prev + eps:
        return "Buy"
    if cur < prev - eps:
        return "Sell"
    return "Neutral"


def momentum_pair_from_closes(closes, period: int = 10):
    """(MOM_t, MOM_{t-1}) for the directional vote."""
    if len(closes) < period + 2:
        return None, None
    return closes[-1] - closes[-(period + 1)], closes[-2] - closes[-(period + 2)]


def momentum_vote_tv(closes, period: int = 10) -> str:
    cur, prev = momentum_pair_from_closes(closes, period)
    return _slope_vote(cur, prev)


def bull_bear_pair_from_bars(bars, period: int = 13):
    cur = bull_bear_from_bars(bars, period)
    prev = bull_bear_from_bars(bars[:-1], period) if len(bars) >= period + 1 else None
    return cur, prev


def bull_bear_vote_tv(bars, period: int = 13) -> str:
    cur, prev = bull_bear_pair_from_bars(bars, period)
    return _slope_vote(cur, prev)


def williams_pair_from_bars(bars, period: int = 14):
    cur = williams_r_from_bars(bars, period)
    prev = williams_r_from_bars(bars[:-1], period) if len(bars) >= period + 1 else None
    return cur, prev


def williams_vote_tv(bars, period: int = 14) -> str:
    """TV W%R vote is directional on raw %R (flat 60.9/85.3 abs -> Neutral)."""
    cur, prev = williams_pair_from_bars(bars, period)
    return _slope_vote(cur, prev)


def stoch_kd_pair(closes, k_period: int = 14, smooth: int = 3, d_smooth: int = 3):
    """(SlowK %K, SlowD %D): D = SMA(K_series, 3). Returns (None,None) if short."""
    from nexus_scalp.indicators.calculators import stochastic_slow_k as _sk

    if len(closes) < k_period + smooth - 1 + d_smooth - 1:
        return None, None
    # K series: SlowK evaluated on prefixes ending at successive bars
    ks = []
    for end in range(len(closes) - d_smooth + 1, len(closes) + 1):
        v = _sk(closes[:end], k_period, smooth)
        if v is None:
            return None, None
        ks.append(v)
    if len(ks) < d_smooth:
        return None, None
    d = sum(ks[-d_smooth:]) / d_smooth
    return ks[-1], d


def stoch_k_vote_tv(closes, k_period: int = 14, smooth: int = 3, eps: float = 0.5) -> str:
    """TV Stoch %K vote is K-vs-D crossover (14.87 deep-oversold still Neutral)."""
    k, d = stoch_kd_pair(closes, k_period, smooth)
    if k is None or d is None:
        # fallback: strict 80/20 only at extremes, else Neutral
        from nexus_scalp.indicators.calculators import stochastic_slow_k as _sk2

        v = _sk2(closes, k_period, smooth)
        if v is None:
            return "Neutral"
        if v >= 90:
            return "Sell"
        if v <= 10:
            return "Buy"
        return "Neutral"
    if k > d + eps:
        return "Buy"
    if k < d - eps:
        return "Sell"
    return "Neutral"


def stoch_rsi_kd_pair(closes, rsi_period: int = 14, k_smooth: int = 3, d_smooth: int = 3):
    """(StochRSI %K, %D): D = SMA(K_series, 3)."""
    if len(closes) < rsi_period * 2 + k_smooth + d_smooth:
        return None, None
    ks = []
    for end in range(len(closes) - d_smooth + 1, len(closes) + 1):
        v = stoch_rsi_k_spec(closes[:end], rsi_period, k_smooth)
        if v is None:
            return None, None
        ks.append(v)
    if len(ks) < d_smooth:
        return None, None
    return ks[-1], sum(ks[-d_smooth:]) / d_smooth


def stoch_rsi_vote_tv(closes, eps: float = 0.5) -> str:
    """TV StochRSI vote is K-vs-D crossover (17.84 Buy = crossed up)."""
    k, d = stoch_rsi_kd_pair(closes)
    if k is None or d is None:
        return stoch_rsi_action_spec(stoch_rsi_k_spec(closes, 14, 3))
    if k > d + eps:
        return "Buy"
    if k < d - eps:
        return "Sell"
    return "Neutral"


def ichimoku_vote_tv(close, base, deadband: float = 0.35) -> str:
    """Ichimoku vote with Neutral deadband (TV: 4430.143 Neutral at ~0.06-0.4 diff)."""
    if close is None or base is None:
        return "Neutral"
    if abs(close - base) <= deadband:
        return "Neutral"
    if close > base:
        return "Buy"
    return "Sell"
