"""Indicators — Service (application layer, SOLID).

Single responsibility: turn a bar window into the full snapshot the widget
and the API consume. Depends only on the BarSource / price-series ports
(DIP); contains no HTTP / FastAPI code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from nexus_scalp.indicators import calculators as calc
from nexus_scalp.indicators.ports import IndicatorResult


class BarSource(Protocol):
    def get_completed_bars(self) -> list[Any]: ...


@dataclass(frozen=True)
class GaugeState:
    label: str  # Neutral | Sell | Buy | Strong sell | Strong buy
    sell: int
    neutral: int
    buy: int
    # needle angle in degrees, 0 = Strong sell (left), 180 = Strong buy (right)
    angle_deg: float


@dataclass(frozen=True)
class PivotMatrix:
    levels: list[str]
    columns: list[str]
    rows: dict[str, dict[str, float | None]]  # level -> {Classic:..., Fibonacci:...}


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    timeframe: str
    bar_count: int
    last_close: float | None
    oscillators: list[IndicatorResult]
    moving_averages: list[IndicatorResult]
    pivots: PivotMatrix
    gauges: dict[str, GaugeState]
    summary_counts: dict[str, int]  # Sell/Neutral/Buy totals (26 items)


# ── internal helpers ─────────────────────────────────────────────────

_VALID_TFS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w", "1M",
              "M1", "M5", "M15", "M30", "H1", "H2", "H4", "1D", "1W"}


def _normalize_tf(tf: str | None) -> str:
    if not tf:
        return "M1"
    tf = tf.strip()
    # keep M1 canonical for engine bars; UI passes "1 minute" etc
    alias = {
        "1 minute": "M1", "5 minutes": "M5", "15 minutes": "M15", "30 minutes": "M30",
        "1 hour": "H1", "2 hours": "H2", "4 hours": "H4", "1 day": "D1", "1 week": "W1", "1 month": "MN1",
        "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1", "2h": "H2", "4h": "H4", "1d": "D1", "1w": "W1", "1M": "MN1",
    }
    return alias.get(tf, tf.upper())


def _gauge_from_counts(sell: int, neutral: int, buy: int) -> GaugeState:
    total = sell + neutral + buy
    if total == 0:
        return GaugeState(label="Neutral", sell=sell, neutral=neutral, buy=buy, angle_deg=90)
    # score in [-1, 1] : buy=+1, sell=-1
    score = (buy - sell) / total
    if score >= 0.55:
        label = "Strong buy"
        angle = 165
    elif score >= 0.15:
        label = "Buy"
        angle = 135
    elif score <= -0.55:
        label = "Strong sell"
        angle = 15
    elif score <= -0.15:
        label = "Sell"
        angle = 45
    else:
        label = "Neutral"
        angle = 90
    # nudge Strong sell Sell inside exact red zones per screenshot fidelity
    if label == "Strong sell":
        angle = 8
    elif label == "Sell":
        angle = 28
    return GaugeState(label=label, sell=sell, neutral=neutral, buy=buy, angle_deg=angle)


def _counts(results: Sequence[IndicatorResult]) -> tuple[int, int, int]:
    s = sum(1 for r in results if r.action == "Sell")
    # Strong variants count toward sell/buy totals for summary
    s += sum(1 for r in results if r.action == "Strong sell")
    b = sum(1 for r in results if r.action == "Buy")
    b += sum(1 for r in results if r.action == "Strong buy")
    n = len(results) - s - b
    return s, n, b


# ── public service ───────────────────────────────────────────────────


class IndicatorService:
    """Computes the full snapshot from a price window.

    Usage:
        svc = IndicatorService()
        snap = svc.snapshot(symbol, bars)   # bars = list[BarData]
        api_dict = svc.to_api_dict(snap)
    """

    def snapshot(self, symbol: str, bars: Sequence[Any], timeframe: str = "M1") -> IndicatorSnapshot:
        prices: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        for b in bars:
            # BarData compatible (open/high/low/close + is_complete)
            if getattr(b, "is_complete", True) is False:
                continue
            c = getattr(b, "close", None)
            if c is None:
                continue
            prices.append(float(c))
            h = getattr(b, "high", c)
            lows.append(float(getattr(b, "low", c)))
            highs.append(float(h))

        last_close = prices[-1] if prices else None
        last_high = highs[-1] if highs else (last_close or 0)
        last_low = lows[-1] if lows else (last_close or 0)
        last_open = float(getattr(bars[-1], "open", last_close or 0)) if bars else (last_close or 0)

        oscillators = self._oscillators(prices)
        moving_averages = self._moving_averages(prices, last_close)

        pivots = self._pivots(last_high, last_low, last_close or 0, last_open)

        osc_s, osc_n, osc_b = _counts(oscillators)
        ma_s, ma_n, ma_b = _counts(moving_averages)
        tot_s, tot_n, tot_b = ma_s + osc_s, ma_n + osc_n, ma_b + osc_b

        gauges = {
            "oscillators": _gauge_from_counts(osc_s, osc_n, osc_b),
            "moving_averages": _gauge_from_counts(ma_s, ma_n, ma_b),
            "summary": _gauge_from_counts(tot_s, tot_n, tot_b),
        }

        return IndicatorSnapshot(
            symbol=symbol,
            timeframe=_normalize_tf(timeframe),
            bar_count=len(prices),
            last_close=last_close,
            oscillators=oscillators,
            moving_averages=moving_averages,
            pivots=pivots,
            gauges=gauges,
            summary_counts={"Sell": tot_s, "Neutral": tot_n, "Buy": tot_b},
        )

    # ── breakdown builders (each single-purpose, SRP) ───────────────

    def _oscillators(self, prices: Sequence[float]) -> list[IndicatorResult]:
        v_rsi = calc.rsi(prices, 14)
        v_k = calc.stochastic_slow_k(prices, 14, 3)
        v_cci = calc.cci(prices, 20)
        v_adx = calc.adx(prices, 14)
        v_ao = calc.awesome_oscillator(prices)
        v_mom = calc.momentum(prices, 10)
        v_macd = calc.macd_level(prices)
        v_stochrsi = calc.stoch_rsi(prices, 14)
        v_willr = calc.williams_r(prices, 14)
        v_bbp = calc.bull_bear_power(prices, 13)
        v_uo = calc.ultimate_oscillator(prices, 7, 14, 28)

        def _osc_action(name: str, v: float | None) -> str:
            if v is None:
                return "Neutral"
            if name == "RSI":
                return calc.rsi_action(v)
            if name == "CCI":
                return calc.cci_action(v)
            if name == "ADX":
                return calc.adx_action(v)
            if name == "Momentum":
                return "Sell" if v < 0 else ("Buy" if v > 0 else "Neutral")
            if name == "MACD":
                return "Buy" if v > 0 else ("Sell" if v < 0 else "Neutral")
            if name == "Awesome":
                return "Buy" if v > 0 else ("Sell" if v < 0 else "Neutral")
            if name in {"StochK", "StochRSI", "WilliamsR", "BullBear", "UO"}:
                # bounded 0-100 or around 0: map 70+/30- bands
                if v >= 70:
                    return "Buy"
                if v <= 30:
                    return "Sell"
                # WilliamsR is -100..0, BullBear around 0, UO 0..100
                if name == "WilliamsR":
                    # -100..0 : -80 Sell, -20 Buy convention, but keep neutral band
                    if v <= -80:
                        return "Sell"
                    if v >= -20:
                        return "Buy"
                    return "Neutral"
                if name == "BullBear":
                    return "Buy" if v > 0 else ("Sell" if v < 0 else "Neutral")
                return "Neutral"
            return "Neutral"

        return [
            IndicatorResult("Relative Strength Index (14)", v_rsi, _osc_action("RSI", v_rsi)),
            IndicatorResult("Stochastic %K (14, 3, 3)", v_k, _osc_action("StochK", v_k)),
            IndicatorResult("Commodity Channel Index (20)", v_cci, _osc_action("CCI", v_cci)),
            IndicatorResult("Average Directional Index (14)", v_adx, _osc_action("ADX", v_adx)),
            IndicatorResult("Awesome Oscillator", v_ao, _osc_action("Awesome", v_ao)),
            IndicatorResult("Momentum (10)", v_mom, _osc_action("Momentum", v_mom)),
            IndicatorResult("MACD Level (12, 26)", v_macd, _osc_action("MACD", v_macd)),
            IndicatorResult("Stochastic RSI Fast (3, 3, 14, 14)", v_stochrsi, _osc_action("StochRSI", v_stochrsi)),
            IndicatorResult("Williams Percent Range (14)", abs(v_willr) if v_willr is not None else None, _osc_action("WilliamsR", v_willr)),  # TV displays |%R|
            IndicatorResult("Bull Bear Power", v_bbp, _osc_action("BullBear", v_bbp)),
            IndicatorResult("Ultimate Oscillator (7, 14, 28)", v_uo, _osc_action("UO", v_uo)),
        ]

    def _moving_averages(self, prices: Sequence[float], last_close: float | None) -> list[IndicatorResult]:
        def _ma_row(name: str, period: int, kind: str) -> IndicatorResult:
            if kind == "EMA":
                v = calc.ema_value(prices, period)
            elif kind == "SMA":
                v = calc.sma_value(prices, period)
            elif kind == "VWMA":
                v = calc.vwma(prices, period)
            elif kind == "HMA":
                v = calc.hma(prices, period)
            else:
                v = None
            return IndicatorResult(name, v, calc.ma_action(last_close, v))

        rows: list[IndicatorResult] = [
            _ma_row("Exponential Moving Average (10)", 10, "EMA"),
            _ma_row("Simple Moving Average (10)", 10, "SMA"),
            _ma_row("Exponential Moving Average (20)", 20, "EMA"),
            _ma_row("Simple Moving Average (20)", 20, "SMA"),
            _ma_row("Exponential Moving Average (30)", 30, "EMA"),
            _ma_row("Simple Moving Average (30)", 30, "SMA"),
            _ma_row("Exponential Moving Average (50)", 50, "EMA"),
            _ma_row("Simple Moving Average (50)", 50, "SMA"),
            _ma_row("Exponential Moving Average (100)", 100, "EMA"),
            _ma_row("Simple Moving Average (100)", 100, "SMA"),
            _ma_row("Exponential Moving Average (200)", 200, "EMA"),
            _ma_row("Simple Moving Average (200)", 200, "SMA"),
            IndicatorResult("Ichimoku Base Line (9, 26, 52, 26)", calc.ichimoku_baseline(prices), calc.ma_action(last_close, calc.ichimoku_baseline(prices))),
            _ma_row("Volume Weighted Moving Average (20)", 20, "VWMA"),
            _ma_row("Hull Moving Average (9)", 9, "HMA"),
        ]
        return rows

    def _pivots(self, high: float, low: float, close: float, open_: float) -> PivotMatrix:
        # guard degenerate
        if high == 0 and low == 0 and close == 0:
            high = low = close = 4430.0
            if open_ == 0:
                open_ = close
        classic = calc.classic_pivots(high, low, close)
        fib = calc.fibonacci_pivots(high, low, close)
        cam = calc.camarilla_pivots(high, low, close)
        wood = calc.woodie_pivots(high, low, close, open_)
        dm = calc.dm_pivots_with_open(high, low, close, open_)
        levels = ["R3", "R2", "R1", "P", "S1", "S2", "S3"]
        columns = ["Classic", "Fibonacci", "Camarilla", "Woodie", "DM"]
        rows: dict[str, dict[str, float | None]] = {}
        for lvl in levels:
            rows[lvl] = {
                "Classic": classic.get(lvl),
                "Fibonacci": fib.get(lvl),
                "Camarilla": cam.get(lvl),
                "Woodie": wood.get(lvl),
                "DM": dm.get(lvl),
            }
        return PivotMatrix(levels=levels, columns=columns, rows=rows)

    # ── serialization (port -> API dict) ─────────────────────────────

    def to_api_dict(self, snap: IndicatorSnapshot) -> dict[str, Any]:
        return {
            "symbol": snap.symbol,
            "timeframe": snap.timeframe,
            "bar_count": snap.bar_count,
            "last_close": snap.last_close,
            "oscillators": [r.as_api_dict() for r in snap.oscillators],
            "moving_averages": [r.as_api_dict() for r in snap.moving_averages],
            "pivots": {
                "levels": snap.pivots.levels,
                "columns": snap.pivots.columns,
                "rows": snap.pivots.rows,
            },
            "gauges": {
                k: {"label": v.label, "sell": v.sell, "neutral": v.neutral, "buy": v.buy, "angle_deg": v.angle_deg}
                for k, v in snap.gauges.items()
            },
            "summary": snap.summary_counts,
        }
