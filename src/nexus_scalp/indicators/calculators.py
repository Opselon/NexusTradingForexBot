"""Indicator calculators — one class per formula (SRP), all pure math.

Each calculator is a stateless, dependency-free value object: prices in,
scalar out. Thresholds follow the conventional TradingView definitions so the
widget text matches what users expect.

Oscillators (11): RSI, Stochastic %K, CCI, ADX, Awesome Oscillator, Momentum,
MACD level, StochRSI Fast, Williams %R, Bull-Bear Power, Ultimate Oscillator.
Moving averages (15): EMA/SMA 6 lengths + Ichimoku baseline, VWMA, HMA.
Pivots: classic / fibonacci / camarilla / woodie / DM (5 families x 7 levels).
"""

from __future__ import annotations

from collections.abc import Sequence


def _sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def _ema(values: Sequence[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for p in values[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _wma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    window = values[-period:]
    denom = period * (period + 1) / 2
    return sum(v * (i + 1) for i, v in enumerate(window)) / denom


# ── oscillators ──────────────────────────────────────────────────────────


def rsi(prices: Sequence[float], period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    gains = [max(0, prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    losses = [max(0, prices[i - 1] - prices[i]) for i in range(1, len(prices))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:], strict=False):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def stochastic_k(prices: Sequence[float], k_period: int = 14) -> float | None:
    if len(prices) < k_period:
        return None
    window = prices[-k_period:]
    lo, hi = min(window), max(window)
    if hi == lo:
        return 50.0
    return 100 * (prices[-1] - lo) / (hi - lo)


def stochastic_slow_k(prices: Sequence[float], k_period: int = 14, smooth: int = 3) -> float | None:
    """Stochastic %K (14, 3, 3): raw %K smoothed by a 3-period SMA (TV %K)."""
    if len(prices) < k_period + smooth - 1:
        return None
    raws: list[float] = []
    for i in range(k_period - 1, len(prices)):
        window = prices[i - k_period + 1 : i + 1]
        lo, hi = min(window), max(window)
        raws.append(50.0 if hi == lo else 100 * (prices[i] - lo) / (hi - lo))
    if len(raws) < smooth:
        return None
    return sum(raws[-smooth:]) / smooth


def cci(prices: Sequence[float], period: int = 20) -> float | None:
    if len(prices) < period:
        return None
    window = prices[-period:]
    tp = list(window)  # close ≈ typical price when we lack HLC
    ma = sum(tp) / period
    md = sum(abs(x - ma) for x in tp) / period
    if md == 0:
        return 0.0
    return (tp[-1] - ma) / (0.015 * md)


def adx(prices: Sequence[float], period: int = 14) -> float | None:
    if len(prices) < period * 2 + 1:
        return None
    # Simplified ADX from closes: directional movement approximated from
    # close deltas; sufficient to emit a 0-100 bounded oscillator.
    ups = [max(0, prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    downs = [max(0, prices[i - 1] - prices[i]) for i in range(1, len(prices))]
    tr = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
    sm_up = sum(ups[:period])
    sm_down = sum(downs[:period])
    sm_tr = sum(tr[:period]) or 1e-9
    dxs: list[float] = []
    for i in range(period, len(ups)):
        sm_up = sm_up - sm_up / period + ups[i]
        sm_down = sm_down - sm_down / period + downs[i]
        sm_tr = sm_tr - sm_tr / period + tr[i]
        denom = sm_tr or 1e-9
        plus_di = 100 * sm_up / denom
        minus_di = 100 * sm_down / denom
        dsum = plus_di + minus_di
        dx = 0.0 if dsum == 0 else 100 * abs(plus_di - minus_di) / dsum
        dxs.append(dx)
    if not dxs:
        return None
    # Wilder smoothing of DX
    adx_val = sum(dxs[:period]) / min(len(dxs), period)
    for d in dxs[period:]:
        adx_val = (adx_val * (period - 1) + d) / period
    return adx_val


def awesome_oscillator(prices: Sequence[float]) -> float | None:
    if len(prices) < 34:
        return None
    s5 = _sma(prices, 5)
    s34 = _sma(prices, 34)
    if s5 is None or s34 is None:
        return None
    return s5 - s34


def momentum(prices: Sequence[float], period: int = 10) -> float | None:
    if len(prices) < period + 1:
        return None
    prev = prices[-(period + 1)]
    if prev == 0:
        return 0.0
    return prices[-1] - prev


def macd_level(prices: Sequence[float]) -> float | None:
    if len(prices) < 26:
        return None
    e12 = _ema(prices, 12)
    e26 = _ema(prices, 26)
    if e12 is None or e26 is None:
        return None
    return e12 - e26


def stoch_rsi(prices: Sequence[float], rsi_period: int = 14) -> float | None:
    if len(prices) < rsi_period * 2:
        return None
    rsis: list[float] = []
    for i in range(rsi_period, len(prices) + 1):
        v = rsi(prices[max(0, i - rsi_period * 2) : i], rsi_period)
        if v is not None:
            rsis.append(v)
    if len(rsis) < rsi_period:
        return None
    window = rsis[-rsi_period:]
    lo, hi = min(window), max(window)
    if hi == lo:
        return 50.0
    return 100 * (window[-1] - lo) / (hi - lo)


def williams_r(prices: Sequence[float], period: int = 14) -> float | None:
    if len(prices) < period:
        return None
    window = prices[-period:]
    lo, hi = min(window), max(window)
    if hi == lo:
        return -50.0
    return -100 * (hi - prices[-1]) / (hi - lo)


def bull_bear_power(prices: Sequence[float], period: int = 13) -> float | None:
    ema = _ema(prices, period)
    if ema is None:
        return None
    return prices[-1] - ema


def ultimate_oscillator(
    prices: Sequence[float], p1: int = 7, p2: int = 14, p3: int = 28
) -> float | None:
    if len(prices) < p3 + 1:
        return None

    def _bp_tr(window: Sequence[float]) -> tuple[float, float]:
        bp = sum(max(0, window[i] - window[i - 1]) for i in range(1, len(window)))
        tr = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window))) or 1e-9
        return bp, tr

    bp1, tr1 = _bp_tr(prices[-p1 - 1 :])
    bp2, tr2 = _bp_tr(prices[-p2 - 1 :])
    bp3, tr3 = _bp_tr(prices[-p3 - 1 :])
    return 100 * ((bp1 / tr1) * 4 + (bp2 / tr2) * 2 + (bp3 / tr3)) / 7


# ── moving averages ────────────────────────────────────────────────────


def sma_value(prices: Sequence[float], period: int) -> float | None:
    return _sma(prices, period)


def ema_value(prices: Sequence[float], period: int) -> float | None:
    return _ema(prices, period)


def vwma(prices: Sequence[float], period: int = 20) -> float | None:
    # volume-weighted approximated with uniform volume when bar volumes absent
    return _sma(prices, period)


def hma(prices: Sequence[float], period: int = 9) -> float | None:
    if len(prices) < period + (period // 2):
        return None
    half = period // 2
    wma_half = _wma(prices, half)
    wma_full = _wma(prices, period)
    if wma_half is None or wma_full is None:
        return None
    diff = [2 * wma_half - wma_full]
    # WMA of the diff over sqrt(period)
    sq = int(period**0.5) or 1
    # need sq points; synthesize by repeating with sliding windows
    series: list[float] = []
    for i in range(sq):
        start = len(prices) - sq + i - half
        if start < 0:
            continue
        wh = _wma(prices[max(0, start) : start + half + 1], half)
        wf = _wma(prices[max(0, start) : start + period + 1], period)
        if wh is not None and wf is not None:
            series.append(2 * wh - wf)
    if len(series) < sq:
        return diff[0]
    return _wma(series, sq)


def ichimoku_baseline(prices: Sequence[float]) -> float | None:
    if len(prices) < 26:
        return None
    window = prices[-26:]
    return (max(window) + min(window)) / 2


# ── pivots ─────────────────────────────────────────────────────────────


def classic_pivots(high: float, low: float, close: float) -> dict[str, float]:
    # TV-proven: R3 = R2 + range, S3 = S2 - range (NOT H+2(P-L)/L-2(H-P)).
    p = (high + low + close) / 3
    rng = high - low
    r1 = 2 * p - low
    r2 = p + rng
    s1 = 2 * p - high
    s2 = p - rng
    return {
        "R3": r2 + rng,
        "R2": r2,
        "R1": r1,
        "P": p,
        "S1": s1,
        "S2": s2,
        "S3": s2 - rng,
    }


def fibonacci_pivots(high: float, low: float, close: float) -> dict[str, float]:
    base = classic_pivots(high, low, close)
    rng = high - low
    p = base["P"]
    return {
        "R3": p + rng * 1.0,
        "R2": p + rng * 0.618,
        "R1": p + rng * 0.382,
        "P": p,
        "S1": p - rng * 0.382,
        "S2": p - rng * 0.618,
        "S3": p - rng * 1.0,
    }


def camarilla_pivots(high: float, low: float, close: float) -> dict[str, float]:
    rng = high - low
    return {
        "R3": close + rng * 1.1 / 4,
        "R2": close + rng * 1.1 / 6,
        "R1": close + rng * 1.1 / 12,
        "P": (high + low + close) / 3,
        "S1": close - rng * 1.1 / 12,
        "S2": close - rng * 1.1 / 6,
        "S3": close - rng * 1.1 / 4,
    }


def woodie_pivots(high: float, low: float, close: float, open_: float) -> dict[str, float]:
    # TV-proven: same R2+-range R3/S3 shape on top of the Woodie P.
    p = (high + low + 2 * close) / 4
    rng = high - low
    r1 = 2 * p - low
    r2 = p + rng
    s1 = 2 * p - high
    s2 = p - rng
    return {
        "R3": r2 + rng,
        "R2": r2,
        "R1": r1,
        "P": p,
        "S1": s1,
        "S2": s2,
        "S3": s2 - rng,
    }


def dm_pivots(high: float, low: float, close: float) -> dict[str, float | None]:
    return {"R1": None, "P": None, "S1": None, "R2": None, "R3": None, "S2": None, "S3": None}


def dm_pivots_with_open(
    high: float, low: float, close: float, open_: float
) -> dict[str, float | None]:
    if close < open_:
        x = high + 2 * low + close
    elif close > open_:
        x = 2 * high + low + close
    else:
        x = high + low + 2 * close
    p = x / 4
    return {
        "R1": x / 2 - low,
        "P": p,
        "S1": x / 2 - high,
        "R2": None,
        "R3": None,
        "S2": None,
        "S3": None,
    }


# ── gauge helpers ──────────────────────────────────────────────────────


def _action_from_value(
    value: float | None,
    *,
    buy_threshold: float,
    sell_threshold: float,
    strong_buy: float | None = None,
    strong_sell: float | None = None,
) -> str:
    if value is None:
        return "Neutral"
    if strong_buy is not None and value >= strong_buy:
        return "Strong buy"
    if strong_sell is not None and value <= strong_sell:
        return "Strong sell"
    if value >= buy_threshold:
        return "Buy"
    if value <= sell_threshold:
        return "Sell"
    return "Neutral"


def rsi_action(v: float | None) -> str:
    # Spec A1: RSI > 70 -> Sell (overbought), RSI < 30 -> Buy (oversold).
    if v is None:
        return "Neutral"
    if v > 70:
        return "Sell"
    if v < 30:
        return "Buy"
    return "Neutral"


def cci_action(v: float | None) -> str:
    # Spec A3: CCI > 100 -> Sell, CCI < -100 -> Buy.
    if v is None:
        return "Neutral"
    if v > 100:
        return "Sell"
    if v < -100:
        return "Buy"
    return "Neutral"


def adx_action(v: float | None) -> str:
    # ADX itself is non-directional; treat high ADX as trend strength proxy;
    # for widget parity, map >25 as Buy/Sell neutral blend — keep Neutral unless extreme
    if v is None:
        return "Neutral"
    if v >= 50:
        return "Buy"
    if v <= 10:
        return "Sell"
    return "Neutral"


def ma_action(price: float | None, ma: float | None) -> str:
    """TradingView rule: close > MA -> Buy, close < MA -> Sell (no deadband)."""
    if price is None or ma is None:
        return "Neutral"
    if price > ma:
        return "Buy"
    if price < ma:
        return "Sell"
    return "Neutral"
