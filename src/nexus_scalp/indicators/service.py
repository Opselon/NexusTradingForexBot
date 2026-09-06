"""Indicators — Service (application layer, SOLID).

Single responsibility: turn a bar window into the full snapshot the widget
and the API consume. Depends only on the BarSource / price-series ports
(DIP); contains no HTTP / FastAPI code.

Spec: agents/xauusd_indicator_math.md (Sections A-D). All formulas/thresholds/votes
and the Section-D aggregation match the spec exactly. Every timeframe uses the
existing BarResampler -> OHLC + volume per bucket; calculators receive H/L
where the spec requires it (CCI, ADX, AO, W%R, BBP, UO, Ichimoku, VWMA).

Pivots Section C: prior completed bucket's H/L/C (/O for DeMark), TradingView
convention; M1..H4 fallback is prior bar's range (same rule, different bucket size).
Summary Section D: rating = (buy-sell)/n with +-0.5/+-0.1 buckets.
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

_VALID_TFS = {
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "1d",
    "1w",
    "1M",
    "M1",
    "M5",
    "M15",
    "M30",
    "H1",
    "H2",
    "H4",
    "1D",
    "1W",
}


def _normalize_tf(tf: str | None) -> str:
    if not tf:
        return "M1"
    tf = tf.strip()
    # keep M1 canonical for engine bars; UI passes "1 minute" etc
    alias = {
        "1 minute": "M1",
        "5 minutes": "M5",
        "15 minutes": "M15",
        "30 minutes": "M30",
        "1 hour": "H1",
        "2 hours": "H2",
        "4 hours": "H4",
        "1 day": "D1",
        "1 week": "W1",
        "1 month": "MN1",
        "1m": "M1",
        "5m": "M5",
        "15m": "M15",
        "30m": "M30",
        "1h": "H1",
        "2h": "H2",
        "4h": "H4",
        "1d": "D1",
        "1w": "W1",
        "1M": "MN1",
    }
    return alias.get(tf, tf.upper())


def _gauge_from_rating(rating: float, sell: int, neutral: int, buy: int) -> GaugeState:
    """Spec Section D: rating = (buy-sell)/n; buckets +-0.5/+-0.1."""
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
    return GaugeState(label=label, sell=sell, neutral=neutral, buy=buy, angle_deg=angle)


def _gauge_from_counts_spec(sell: int, neutral: int, buy: int) -> GaugeState:
    """Aggregate: rating = (-sell + buy) / total, spec Section D."""
    total = sell + neutral + buy
    if total == 0:
        return GaugeState(label="Neutral", sell=sell, neutral=neutral, buy=buy, angle_deg=90)
    rating = (buy - sell) / total
    return _gauge_from_rating(rating, sell, neutral, buy)


def _counts(results: Sequence[IndicatorResult]) -> tuple[int, int, int]:
    s = sum(1 for r in results if r.action == "Sell")
    # Strong variants count toward sell/buy totals for summary
    s += sum(1 for r in results if r.action == "Strong sell")
    b = sum(1 for r in results if r.action == "Buy")
    b += sum(1 for r in results if r.action == "Strong buy")
    n = len(results) - s - b
    return s, n, b


def _sign_vote(v: float | None) -> str:
    """Spec A6/A10: >0 -> Buy, <0 -> Sell, =0/None -> Neutral."""
    if v is None:
        return "Neutral"
    if v > 0:
        return "Buy"
    if v < 0:
        return "Sell"
    return "Neutral"


# ── public service ───────────────────────────────────────────────────


class IndicatorService:
    """Computes the full snapshot from a price window.

    Usage:
        svc = IndicatorService()
        snap = svc.snapshot(symbol, bars)   # bars = list[BarData]
        api_dict = svc.to_api_dict(snap)
    """

    def snapshot(
        self, symbol: str, bars: Sequence[Any], timeframe: str = "M1"
    ) -> IndicatorSnapshot:
        from nexus_scalp.indicators.resample import BarResampler

        tf = _normalize_tf(timeframe)
        rows = BarResampler(tf).resample(bars)
        # keep per-bar H/L/Vol for calculators that need it (§A, §B, §C)
        closes: list[float] = [r["close"] for r in rows]
        last_close = closes[-1] if closes else None

        # Daily/weekly/monthly pivots use the PREVIOUS completed bucket's H/L/C
        # (TradingView convention); M1..H4 fall back to the prior bar's range.
        if len(rows) >= 2:
            pv = rows[-2]
            pv_high, pv_low, pv_close, pv_open = pv["high"], pv["low"], pv["close"], pv["open"]
        elif rows:
            pv = rows[-1]
            pv_high, pv_low, pv_close, pv_open = pv["high"], pv["low"], pv["close"], pv["open"]
        else:
            pv_high = pv_low = pv_close = pv_open = last_close or 0

        oscillators = self._oscillators(rows)
        moving_averages = self._moving_averages(rows, last_close)
        pivots = self._pivots(pv_high, pv_low, pv_close, pv_open)
        osc_s, osc_n, osc_b = _counts(oscillators)
        ma_s, ma_n, ma_b = _counts(moving_averages)
        tot_s, tot_n, tot_b = ma_s + osc_s, ma_n + osc_n, ma_b + osc_b
        gauges = {
            "oscillators": _gauge_from_counts_spec(osc_s, osc_n, osc_b),
            "moving_averages": _gauge_from_counts_spec(ma_s, ma_n, ma_b),
            "summary": _gauge_from_counts_spec(tot_s, tot_n, tot_b),
        }
        return IndicatorSnapshot(
            symbol=symbol,
            timeframe=tf,
            bar_count=len(closes),
            last_close=last_close,
            oscillators=oscillators,
            moving_averages=moving_averages,
            pivots=pivots,
            gauges=gauges,
            summary_counts={"Sell": tot_s, "Neutral": tot_n, "Buy": tot_b},
        )

    # ── breakdown builders (each single-purpose, SRP) ───────────────

    def _oscillators(self, rows: Sequence[dict[str, Any]]) -> list[IndicatorResult]:
        # Spec Section A via additive xauusd_spec helpers; falls back to
        # closes-only template math only when the H/L window is too short.
        from nexus_scalp.indicators import xauusd_spec as spec

        closes = [r["close"] for r in rows]
        v_rsi = calc.rsi(closes, 14)  # Wilder, unchanged (spec A1)
        v_k = calc.stochastic_slow_k(closes, 14, 3)  # HH/LL over closes (spec A2)
        v_cci = spec.cci_from_bars(rows, 20) if len(rows) >= 20 else calc.cci(closes, 20)
        v_ao = spec.awesome_from_bars(rows) if len(rows) >= 34 else calc.awesome_oscillator(closes)
        v_mom = calc.momentum(closes, 10)  # C_t - C_{t-10} (spec A6)
        v_macd_level = calc.macd_level(closes)
        v_stochrsi = (
            spec.stoch_rsi_k_spec(closes, 14, 3)
            if len(closes) >= 31
            else calc.stoch_rsi(closes, 14)
        )
        v_willr = (
            spec.williams_r_from_bars(rows, 14) if len(rows) >= 14 else calc.williams_r(closes, 14)
        )
        v_bbp = (
            spec.bull_bear_from_bars(rows, 13)
            if len(rows) >= 13
            else calc.bull_bear_power(closes, 13)
        )
        v_uo = (
            spec.ultimate_from_bars(rows, 7, 14, 28)
            if len(rows) >= 29
            else calc.ultimate_oscillator(closes, 7, 14, 28)
        )
        # ADX value for display (closes template ADX), vote via DI logic spec A4
        v_adx_vote = (
            spec.adx_signal_from_bars(rows, 14)
            if len(rows) >= 29
            else (
                "Neutral" if calc.adx(closes, 14) is None else calc.adx_action(calc.adx(closes, 14))
            )
        )

        return [
            IndicatorResult("Relative Strength Index (14)", v_rsi, calc.rsi_action(v_rsi)),
            IndicatorResult("Stochastic %K (14, 3, 3)", v_k, spec.stoch_k_action_spec(v_k)),
            IndicatorResult("Commodity Channel Index (20)", v_cci, calc.cci_action(v_cci)),
            IndicatorResult(
                "Average Directional Index (14)",
                calc.adx(closes, 14),
                v_adx_vote,
            ),
            IndicatorResult(
                "Awesome Oscillator",
                v_ao,
                spec.awesome_signal_from_bars(rows)
                if len(rows) >= 35
                else (
                    "Neutral"
                    if v_ao is None
                    else ("Buy" if (v_ao or 0) > 0 else "Sell" if (v_ao or 0) < 0 else "Neutral")
                ),
            ),
            IndicatorResult(
                "Momentum (10)",
                v_mom,
                _sign_vote(v_mom),
            ),
            IndicatorResult("MACD Level (12, 26)", v_macd_level, spec.macd_action_spec(closes)),
            IndicatorResult(
                "Stochastic RSI Fast (3, 3, 14, 14)",
                v_stochrsi,
                spec.stoch_rsi_action_spec(v_stochrsi),
            ),
            # Williams %R: display canonical -100..0, vote on canonical scale
            IndicatorResult(
                "Williams Percent Range (14)", v_willr, spec.williams_r_action_spec(v_willr)
            ),
            IndicatorResult(
                "Bull Bear Power",
                v_bbp,
                _sign_vote(v_bbp),
            ),
            IndicatorResult("Ultimate Oscillator (7, 14, 28)", v_uo, spec.uo_action_spec(v_uo)),
        ]

    def _moving_averages(
        self, rows: Sequence[dict[str, Any]], last_close: float | None
    ) -> list[IndicatorResult]:
        # Spec Section B via additive xauusd_spec helpers (H/L/Vol aware).
        from nexus_scalp.indicators import xauusd_spec as _spec

        closes = [r["close"] for r in rows]

        def _ma_row(name: str, period: int, kind: str) -> IndicatorResult:
            if kind == "EMA":
                v = calc.ema_value(closes, period)
            elif kind == "SMA":
                v = calc.sma_value(closes, period)
            elif kind == "VWMA":
                v = _spec.vwma_from_bars(rows, period)  # volume-weighted; SMA fallback
            elif kind == "HMA":
                v = _spec.hma_spec(closes, period)
            else:
                v = None
            return IndicatorResult(name, v, calc.ma_action(last_close, v))

        rows_out: list[IndicatorResult] = [
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
            IndicatorResult(
                "Ichimoku Base Line (9, 26, 52, 26)",
                _spec.ichimoku_baseline_from_bars(rows),
                calc.ma_action(last_close, _spec.ichimoku_baseline_from_bars(rows)),
            ),
            _ma_row("Volume Weighted Moving Average (20)", 20, "VWMA"),
            _ma_row("Hull Moving Average (9)", 9, "HMA"),
        ]
        return rows_out

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
                k: {
                    "label": v.label,
                    "sell": v.sell,
                    "neutral": v.neutral,
                    "buy": v.buy,
                    "angle_deg": v.angle_deg,
                }
                for k, v in snap.gauges.items()
            },
            "summary": snap.summary_counts,
        }
