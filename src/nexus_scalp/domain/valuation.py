"""Canonical broker money math — the SINGLE source of truth for NSE.

Lane: BROKER-MT5-EXECUTION-FORENSICS.

Every function here is pure and broker-agnostic. Callers pass the broker's own
contract specification (``SymbolSnapshot.spec`` / ``SymbolInfo``) instead of
embedding per-broker constants.

WHY THIS MODULE EXISTS (measured, not assumed)
-----------------------------------------------
Verified 2026-09-24 against MetaQuotes-Demo account 10011755849 via BOTH the
native MetaTrader5 Python API (5.0.6090, terminal build 6207) and the local
MetaTrader5-MCP endpoint (127.0.0.1:22346):

1. Closed-deal archaeology over 4,542 real broker positions fits

       profit = volume * contract_size * (close - open)   [sign-adjusted]

   to within 1% on 4,092/4,109 XAUUSD deals and to ~1e-11 relative error on
   401 EURUSD deals. Confirmed independently by ``mt5.order_calc_profit``:
   BUY 1.0 XAUUSD +1.00 -> 100.0 USD; BUY 0.1 EURUSD +0.001 -> 10.0 USD.

2. Symbols whose PROFIT currency differs from the ACCOUNT currency require an
   FX factor, proven by real deals:
     - USDCAD buy 0.1, 1.40405 -> 1.40425, broker profit **1.42**
       (= 0.1*100000*0.0002 / 1.40425, NOT 2.00)
     - USDCHF buy 0.01, 0.80518 -> 0.80481, broker profit **-0.46**
       (= 0.01*100000*(-0.00037) / 0.80481, NOT -0.37)
   A local reconstruction that ignores the factor is WRONG by 20-41% on such
   symbols. ``currency_factor`` makes that conversion explicit; when the profit
   currency is unknown the factor must stay 1.0 and the value must be labelled
   an estimate — never presented as broker truth.

3. ``SYMBOL_TRADE_TICK_VALUE`` is NOT trustworthy as a per-lot value on every
   server: this account reports ``trade_tick_value = 0.1`` for XAUUSD where
   ``tick_size(0.01) * contract_size(100) = 1.0`` and ``order_calc_profit``
   proves 1.0. EURUSD reports ``tick_value = 0.0`` outright. Any formula built
   as ``(distance / point) * tick_value * volume`` therefore silently
   mis-scales (observed: 10x UNDERSTATEMENT of reward on live XAUUSD).
   Use :func:`price_delta_value` and :func:`tick_value_is_consistent` to detect
   the contradiction instead of inheriting it.

4. Required margin: ``mt5.order_check`` (authoritative, returns the broker's
   own numbers) gave margin 42.97 for BUY 0.01 XAUUSD @ 4297.42, which equals
   ``contract*price*volume/leverage`` = 100*4297.42*0.01/100 exactly — so the
   classic formula IS correct *for this account* (ACCOUNT_MARGIN_MODE_RETAIL
   HEDGING + SYMBOL_TRADE_CALC_MODE = CFD_LEVERAGE + currency_margin == account
   currency + margin_initial/maintenance == 0). It is an ESTIMATE everywhere
   else; prefer ``IMT5Port.order_calc_margin_snapshot`` /
   ``order_check`` (BROKER_NATIVE) when available.

Account semantics (MQL5 docs, mql5.com/en/docs/.../accountinformation):
    equity   = balance + floating PnL (+ credit, server-side)
    margin   = ACCOUNT_MARGIN (reserved, deposit currency)
    free     = ACCOUNT_MARGIN_FREE (server-computed; do NOT re-derive)
    level    = equity / margin * 100 — the server reports 0.0 when margin == 0,
               which means "not applicable", NOT "0% healthy".
    mode     = ENUM_ACCOUNT_MARGIN_MODE: 0=RETAIL_NETTING, 1=EXCHANGE,
               2=RETAIL_HEDGING (this account: 2 == hedging, corroborated by
               3,068 overlapping same-symbol position intervals in history).
"""

from __future__ import annotations

import math

__all__ = [
    "align_price",
    "margin_level_percent",
    "min_stop_distance_price",
    "normalize_volume",
    "price_delta_value",
    "required_margin_estimate",
    "reward_value",
    "tick_value_is_consistent",
]

_EPS = 1e-9


def price_delta_value(
    volume: float,
    contract_size: float,
    price_delta: float,
    currency_factor: float = 1.0,
) -> float:
    """Account-currency value of a price move (the canonical PnL/reward law).

    ``price_delta`` is signed (close - open); for a SELL pass the delta as the
    BUY delta and negate the result, or pass an already-signed delta for the
    position direction.

    ``currency_factor`` converts PROFIT currency -> ACCOUNT currency. Use 1.0
    only when they are the same currency (verified exact for XAUUSD/EURUSD/
    GBPUSD/AUDUSD/NZDUSD on the reference account). For quote-converted symbols
    the factor is ``1 / conversion_rate`` measured against the broker's own
    ``order_calc_profit`` (see module docstring, item 2).

    Returns a raw float; callers decide rounding (``currency_digits``).
    """
    if volume <= 0.0 or contract_size <= 0.0:
        return 0.0
    if price_delta == 0.0 or currency_factor == 0.0:
        return 0.0
    return float(volume) * float(contract_size) * float(price_delta) * float(currency_factor)


def reward_value(
    volume: float,
    contract_size: float,
    price_distance: float,
    currency_factor: float = 1.0,
) -> float:
    """Gross account-currency value of moving ``price_distance`` in your favour.

    Replaces the legacy ``(distance / point) * tick_value * volume`` form, which
    inherits whatever the broker reports in ``SYMBOL_TRADE_TICK_VALUE`` (see
    module docstring, item 3).
    """
    return price_delta_value(volume, contract_size, abs(price_distance), currency_factor)


def required_margin_estimate(
    volume: float,
    contract_size: float,
    price: float,
    leverage: int | float,
) -> float:
    """LOCAL ESTIMATE of margin a new order would require.

    Validated against ``mt5.order_check`` / ``order_calc_margin`` on a
    RETAIL_HEDGING + CFD_LEVERAGE + USD-margin account (exact to the cent).
    It does NOT model margin_initial/maintenance, margin_hedged, tiered or
    dynamic leverage, or currency_margin != account currency — those need the
    broker's own number. Provenance is therefore part of the contract: always
    pair this with ``source`` (ESTIMATE vs BROKER_NATIVE) at the call site.
    """
    if volume <= 0.0 or contract_size <= 0.0 or price <= 0.0:
        return 0.0
    if leverage is None or leverage <= 0:
        return 0.0
    return (float(contract_size) * float(price) * float(volume)) / float(leverage)


def tick_value_is_consistent(
    tick_value: float | None,
    tick_size: float | None,
    contract_size: float | None,
    *,
    relative_tolerance: float = 0.05,
) -> bool | None:
    """Cross-check ``SYMBOL_TRADE_TICK_VALUE`` against contract arithmetic.

    Returns True when the reported value agrees with
    ``tick_size * contract_size`` (per lot), False when it contradicts it —
    including an explicitly reported ``0.0`` while a positive value is
    derivable (a real observation on this account's EURUSD) — and None only
    when it cannot be judged (tick_size/contract missing or non-positive).
    """
    if tick_value is None or not tick_size or not contract_size:
        return None
    expected = float(tick_size) * float(contract_size)
    if expected <= 0:
        return None
    return abs(float(tick_value) - expected) <= relative_tolerance * expected


def normalize_volume(
    volume: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
    *,
    operation: str = "floor",
) -> float:
    """Broker-aware volume normalization.

    ``operation='floor'`` rounds DOWN to the step (never inflates risk),
    ``'ceil'`` rounds UP (used when a floor would fall below the minimum),
    ``'nearest'`` rounds to the closest legal step.

    Returns 0.0 when the result cannot be made legal (non-finite input, no
    positive step, or a floored value below ``volume_min``) so a caller fails
    closed instead of silently sending an illegal volume. Rounding never
    exceeds ``volume_max``.
    """
    if not all(math.isfinite(v) for v in (volume, volume_min, volume_max, volume_step)):
        return 0.0
    if volume <= 0.0 or volume_step <= 0.0 or volume_max <= 0.0:
        return 0.0
    if volume_min <= 0.0:
        volume_min = volume_step

    steps = volume / volume_step
    if operation == "ceil":
        quantized = math.ceil(round(steps, 9)) * volume_step
    elif operation == "nearest":
        quantized = round(steps) * volume_step
    else:
        quantized = math.floor(round(steps, 9) + _EPS) * volume_step
    quantized = round(quantized, 10)

    if quantized > volume_max:
        quantized = volume_max
        quantized = math.floor(round(quantized / volume_step, 9)) * volume_step
        quantized = round(quantized, 10)
    if quantized < volume_min:
        return 0.0
    return quantized


def align_price(
    price: float,
    tick_size: float | None,
    digits: int | None,
    *,
    direction: str = "nearest",
) -> float:
    """Align a price to the broker's tick grid, then to ``digits``.

    ``direction``:
      - ``'nearest'``: closest legal tick.
      - ``'down'`` / ``'up'``: floor/ceiling on the tick grid — used to round
        TOWARD SAFETY (a protective BUY stop below the market rounds DOWN so it
        can never drift closer to price; a protective SELL stop rounds UP).

    ``tick_size`` is authoritative; ``digits`` only formats the final value so
    we never emit ``4296.419999999999``. A missing/invalid tick_size degrades
    to digits-only rounding (never raises — hot path).
    """
    if not math.isfinite(price):
        return price
    if tick_size and tick_size > 0 and math.isfinite(tick_size):
        steps = price / tick_size
        if direction == "down":
            quantized = math.floor(round(steps, 9) + _EPS) * tick_size
        elif direction == "up":
            quantized = math.ceil(round(steps, 9) - _EPS) * tick_size
        else:
            quantized = round(steps) * tick_size
    else:
        quantized = price
    if digits is not None and digits >= 0:
        return round(float(quantized), int(digits))
    return float(quantized)


def min_stop_distance_price(
    stops_level: int | float,
    point: float,
    *,
    safety_points: int | float = 0,
) -> float:
    """Minimum SL/TP distance in PRICE units, derived from broker constraints.

    ``stops_level`` and ``point`` come from the broker (SYMBOL_TRADE_STOPS_
    LEVEL / SYMBOL_POINT). ``safety_points`` is an optional NSE floor counted
    in POINTS, so it scales with the symbol: 10 points is 0.10 on 2-digit
    XAUUSD (the legacy hardcoded behaviour, preserved) but only 0.0001 on a
    5-digit FX pair — a fixed price-unit constant would demand 100 pips there.
    """
    try:
        level = max(float(stops_level), 0.0)
        safety = max(float(safety_points), 0.0)
        pt = float(point)
    except (TypeError, ValueError):
        return 0.0
    if pt <= 0.0 or not math.isfinite(pt):
        return 0.0
    return (max(level, safety)) * pt


def margin_level_percent(equity: float, margin: float) -> float | None:
    """MT5 margin level, preserving the server's 'not applicable' semantics.

    MQL5 defines margin level as ``equity / margin * 100``. When no margin is
    reserved the server reports ``0.0``; that must surface as ``None`` (N/A) so
    a UI never renders "Margin level 0%" as if it were a healthy number.
    """
    if margin is None or equity is None:
        return None
    if not math.isfinite(margin) or not math.isfinite(equity) or margin <= 0.0:
        return None
    return (float(equity) / float(margin)) * 100.0
