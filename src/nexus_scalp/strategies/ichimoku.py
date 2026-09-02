"""
Ichimoku (Ichimili) Strategy — Seeded Built-in
================================================
PHASE 15C: Pine Script `Ichimili - Final Version` and `Ichimili` translated
into pure Python bar-based signal engines.

Variant A (`IchimiliFinalStrategy`): the "Final Version".
  * visible (displaced) Kumo: candle body fully above/below the shifted cloud;
  * future-cloud momentum: both Leading Spans rising / falling;
  * ALTERNATING signals: a BUY is emitted only while the last signal was not a
    BUY, and vice versa (one-in-a-row logic from the Pine).

Variant B (`IchimiliSpacedStrategy`): the spaced-signal version.
  * Rising/falling Leading Span A and B;
  * candle close above/below the CURRENT (unshifted) Kumo;
  * a gap constraint: at least `min_candles_between_signals` bars must elapse
    between consecutive signals.

All math mirrors the Pine reference exactly:
  conversion  = donchian(9)
  base        = donchian(26)
  span A      = (conversion + base) / 2
  span B      = donchian(52)
  displacement = 26 (Pine `offset = displacement - 1` for display; the signal
  uses the displaced value: leadLine1[displacement - 1]).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.strategies.base import (
    BarLike,
    StrategySignal,
    _bars_to_lists,
    donchian_mid,
    register_strategy,
)

# Pine defaults
CONVERSION_PERIODS = 9
BASE_PERIODS = 26
LAGGING_SPAN2_PERIODS = 52
DISPLACEMENT = 26
MIN_CANDLES_BETWEEN_SIGNALS = 6

STRATEGY_ID_FINAL = "STRAT-ICHIMILI-FINAL"
STRATEGY_ID_SPACED = "STRAT-ICHIMILI-SPACED"
DISPLAY_FINAL = "Ichimili Final"
DISPLAY_SPACED = "Ichimili"


def _ichimoku_lines(
    highs: list[float],
    lows: list[float],
    conversion: int = CONVERSION_PERIODS,
    base: int = BASE_PERIODS,
    lagging: int = LAGGING_SPAN2_PERIODS,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Computes conversion/base/spanA/spanB series for every bar index."""
    n = len(highs)
    conversion_line: list[float] = [0.0] * n
    base_line: list[float] = [0.0] * n
    lead1: list[float] = [0.0] * n
    lead2: list[float] = [0.0] * n
    for i in range(n):
        conversion_line[i] = donchian_mid(highs, lows, conversion, i)
        base_line[i] = donchian_mid(highs, lows, base, i)
        lead1[i] = (conversion_line[i] + base_line[i]) / 2.0
        lead2[i] = donchian_mid(highs, lows, lagging, i)
    return conversion_line, base_line, lead1, lead2


class IchimiliFinalStrategy:
    """Ichimili "Final Version": displaced-kumo break + rising/falling cloud + alternation."""

    strategy_id = STRATEGY_ID_FINAL
    version = "1.0.0"
    display_name = DISPLAY_FINAL

    def __init__(
        self,
        conversion_periods: int = CONVERSION_PERIODS,
        base_periods: int = BASE_PERIODS,
        lagging_span2_periods: int = LAGGING_SPAN2_PERIODS,
        displacement: int = DISPLACEMENT,
    ) -> None:
        self.conversion_periods = max(1, int(conversion_periods))
        self.base_periods = max(1, int(base_periods))
        self.lagging_span2_periods = max(1, int(lagging_span2_periods))
        self.displacement = max(1, int(displacement))

    # ------------------------------------------------------------------
    # Strategy contract
    # ------------------------------------------------------------------

    def context_definition(self) -> dict[str, Any]:
        return {
            "family": "ichimoku",
            "variant": "final",
            "symbol_agnostic": True,
            "timeframe_agnostic": True,
            "regime": "TRENDING",
            "trend_state": "BULLISH_OR_BEARISH",
        }

    def entry_logic(self) -> dict[str, Any]:
        return {
            "indicator": "ichimoku_kinko_hyo",
            "variant": "final",
            "conversion_periods": self.conversion_periods,
            "base_periods": self.base_periods,
            "lagging_span2_periods": self.lagging_span2_periods,
            "displacement": self.displacement,
            "bull_condition": "candle_body_above_visible_kumo AND future_cloud_bullish",
            "bear_condition": "candle_body_below_visible_kumo AND future_cloud_bearish",
            "signal_rule": "alternating_one_in_a_row",
        }

    def exit_logic(self) -> dict[str, Any]:
        return {"mode": "OPPOSITE_SIGNAL", "note": "exit on the opposite alternating signal"}

    def risk_assumptions(self) -> dict[str, Any]:
        return {
            "direction": "directional_only",
            "stop_model": "kumo_opposite_edge",
            "min_expectancy_r": 0.10,
        }

    # ------------------------------------------------------------------
    # Signal engine
    # ------------------------------------------------------------------

    def evaluate(self, bars: list[BarLike]) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        if not bars:
            return signals
        opens, highs, lows, closes = _bars_to_lists(bars)
        _, _, lead1, lead2 = _ichimoku_lines(
            highs,
            lows,
            self.conversion_periods,
            self.base_periods,
            self.lagging_span2_periods,
        )
        n = len(bars)
        disp = self.displacement
        # Pine uses offset=displacement-1 for plotting; the SIGNAL reads the
        # displaced values at [displacement - 1] (the visible cloud over the
        # current candle).
        shifted = disp - 1

        last_signal_type = 0  # 0 none, 1 bull, -1 bear

        for i in range(n):
            if i < shifted or i < 1 or lead1[i - shifted] == 0.0:
                continue
            # Visible (displaced) cloud over bar i.
            visible_top = max(lead1[i - shifted], lead2[i - shifted])
            visible_bottom = min(lead1[i - shifted], lead2[i - shifted])

            candle_low = min(opens[i], closes[i])
            candle_high = max(opens[i], closes[i])
            candle_above = candle_low > visible_top
            candle_below = candle_high < visible_bottom

            # Future cloud momentum (current vs previous displaced value).
            idx_prev = i - shifted - 1
            if idx_prev < 0:
                continue
            future_bullish = (
                lead1[i - shifted] > lead1[idx_prev] and lead2[i - shifted] > lead2[idx_prev]
            )
            future_bearish = (
                lead1[i - shifted] < lead1[idx_prev] and lead2[i - shifted] < lead2[idx_prev]
            )

            raw_bull = candle_above and future_bullish
            raw_bear = candle_below and future_bearish

            if raw_bull and last_signal_type != 1:
                last_signal_type = 1
                signals.append(
                    StrategySignal(
                        strategy_id=self.strategy_id,
                        direction="BUY",
                        bar_index=i,
                        timestamp=getattr(bars[i], "timestamp", None),
                        confidence=0.6,
                        metadata={
                            "visible_top": float(visible_top),
                            "visible_bottom": float(visible_bottom),
                            "variant": "final",
                        },
                    )
                )
            elif raw_bear and last_signal_type != -1:
                last_signal_type = -1
                signals.append(
                    StrategySignal(
                        strategy_id=self.strategy_id,
                        direction="SELL",
                        bar_index=i,
                        timestamp=getattr(bars[i], "timestamp", None),
                        confidence=0.6,
                        metadata={
                            "visible_top": float(visible_top),
                            "visible_bottom": float(visible_bottom),
                            "variant": "final",
                        },
                    )
                )
        return signals


class IchimiliSpacedStrategy:
    """Ichimili spaced-signal version: current-kumo close break + min-gap between signals."""

    strategy_id = STRATEGY_ID_SPACED
    version = "1.0.0"
    display_name = DISPLAY_SPACED

    def __init__(
        self,
        conversion_periods: int = CONVERSION_PERIODS,
        base_periods: int = BASE_PERIODS,
        lagging_span2_periods: int = LAGGING_SPAN2_PERIODS,
        displacement: int = DISPLACEMENT,
        min_candles_between_signals: int = MIN_CANDLES_BETWEEN_SIGNALS,
    ) -> None:
        self.conversion_periods = max(1, int(conversion_periods))
        self.base_periods = max(1, int(base_periods))
        self.lagging_span2_periods = max(1, int(lagging_span2_periods))
        self.displacement = max(1, int(displacement))
        self.min_candles_between_signals = max(1, int(min_candles_between_signals))

    # ------------------------------------------------------------------
    # Strategy contract
    # ------------------------------------------------------------------

    def context_definition(self) -> dict[str, Any]:
        return {
            "family": "ichimoku",
            "variant": "spaced",
            "symbol_agnostic": True,
            "timeframe_agnostic": True,
            "regime": "TRENDING",
            "trend_state": "BULLISH_OR_BEARISH",
        }

    def entry_logic(self) -> dict[str, Any]:
        return {
            "indicator": "ichimoku_kinko_hyo",
            "variant": "spaced",
            "conversion_periods": self.conversion_periods,
            "base_periods": self.base_periods,
            "lagging_span2_periods": self.lagging_span2_periods,
            "displacement": self.displacement,
            "min_candles_between_signals": self.min_candles_between_signals,
            "bull_condition": "spanA_rising AND spanB_rising AND close_above_kumo",
            "bear_condition": "spanA_falling AND spanB_falling AND close_below_kumo",
            "signal_rule": "min_gap_between_signals",
        }

    def exit_logic(self) -> dict[str, Any]:
        return {"mode": "OPPOSITE_SIGNAL_OR_GAP", "note": "exit on opposite signal or trend flip"}

    def risk_assumptions(self) -> dict[str, Any]:
        return {
            "direction": "directional_only",
            "stop_model": "kumo_opposite_edge",
            "min_expectancy_r": 0.10,
        }

    # ------------------------------------------------------------------
    # Signal engine
    # ------------------------------------------------------------------

    def evaluate(self, bars: list[BarLike]) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        if not bars:
            return signals
        _opens, highs, lows, closes = _bars_to_lists(bars)
        _, _, lead1, lead2 = _ichimoku_lines(
            highs,
            lows,
            self.conversion_periods,
            self.base_periods,
            self.lagging_span2_periods,
        )
        n = len(bars)

        last_signal_bar: int | None = None

        for i in range(1, n):
            if lead1[i] == 0.0 or lead2[i] == 0.0:
                continue
            span_a_rising = lead1[i] > lead1[i - 1]
            span_a_falling = lead1[i] < lead1[i - 1]
            span_b_rising = lead2[i] > lead2[i - 1]
            span_b_falling = lead2[i] < lead2[i - 1]

            kumo_top = max(lead1[i], lead2[i])
            kumo_bottom = min(lead1[i], lead2[i])
            candle_above = closes[i] > kumo_top
            candle_below = closes[i] < kumo_bottom

            bull_condition = span_a_rising and span_b_rising and candle_above
            bear_condition = span_a_falling and span_b_falling and candle_below

            signal_allowed = last_signal_bar is None or (
                i - last_signal_bar >= self.min_candles_between_signals
            )

            bull_signal = bull_condition and signal_allowed
            bear_signal = bear_condition and signal_allowed

            if bull_signal or bear_signal:
                last_signal_bar = i
                signals.append(
                    StrategySignal(
                        strategy_id=self.strategy_id,
                        direction="BUY" if bull_signal else "SELL",
                        bar_index=i,
                        timestamp=getattr(bars[i], "timestamp", None),
                        confidence=0.6,
                        metadata={
                            "kumo_top": float(kumo_top),
                            "kumo_bottom": float(kumo_bottom),
                            "variant": "spaced",
                            "gap_bars": (i - last_signal_bar if last_signal_bar is not None else 0),
                        },
                    )
                )
        return signals


# ---------------------------------------------------------------------------
# Registration (import-time, deterministic order)
# ---------------------------------------------------------------------------
register_strategy(IchimiliFinalStrategy())
register_strategy(IchimiliSpacedStrategy())

__all__ = [
    "STRATEGY_ID_FINAL",
    "STRATEGY_ID_SPACED",
    "IchimiliFinalStrategy",
    "IchimiliSpacedStrategy",
]
