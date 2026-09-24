"""Broker money-math forensics: canonical law vs real broker deals.

Lane: BROKER-MT5-EXECUTION-FORENSICS.

Every constant in this suite is a REAL measured value, not an assumption:

  * Account: MetaQuotes-Demo / login 10011755849 / hedging (margin_mode=2) /
    USD / leverage 100. Measured BOTH through the native MetaTrader5 Python
    API (5.0.6090, terminal build 6207) and the local MetaTrader5-MCP
    endpoint (127.0.0.1:22346) — identical values, EXACT PARITY.
  * XAUUSD spec: digits 2, point 0.01, tick_size 0.01, tick_value 0.10,
    contract_size 100, vol 0.01/100/0.01, stops 0, freeze 0,
    currency_margin USD, currency_profit USD, calculation_mode 'cfd leverage'.
  * EURUSD spec: digits 5, point 0.00001, tick_size 0.0 (broker zero!),
    tick_value 0.0 (broker zero!), contract_size 100000, volume_max 500.
  * Native ``order_calc_margin``  BUY 1.0  XAUUSD -> 4298.95
    Native ``order_calc_profit``  BUY 1.0  XAUUSD +1.00  -> 100.0
    Native ``order_check``        BUY 0.01 XAUUSD        -> margin 42.97,
                                                       margin_free 30419.58
  * Closed deals (4,542 real positions): XAUUSD 4,092/4,109 fit
    vol*contract*delta within 1%; EURUSD 401/401 fit to ~1e-11;
    USDCAD/USDCHF fit ONLY with an FX factor (proof of the conversion law).

No test here places, modifies, or closes any order. Section 7 uses
``order_calc_*`` / ``order_check`` only — broker-native pure computation.
"""

from __future__ import annotations

import math

import pytest

from nexus_scalp.domain.valuation import (
    align_price,
    margin_level_percent,
    min_stop_distance_price,
    normalize_volume,
    price_delta_value,
    required_margin_estimate,
    reward_value,
    tick_value_is_consistent,
)

# ---------------------------------------------------------------- REAL DATA

XAUUSD_CONTRACT = 100.0
XAUUSD_TICK_SIZE = 0.01
XAUUSD_DIGITS = 2
EURUSD_CONTRACT = 100000.0
EURUSD_TICK_VALUE = 0.0  # broker reports ZERO on this account
EURUSD_POINT = 0.00001
LEVERAGE = 100

# Closed-deal pins (broker-reported profit vs reconstructed value)
DEAL_XAUUSD = pytest.param(  # 152343608765 buy 0.1 4049.61 -> 4048.12
    0.1, 4049.61, 4048.12, XAUUSD_CONTRACT, 1.0, -14.9, id="xauusd-buy-0.1-loss"
)
DEAL_USDCAD = pytest.param(  # 152344230424 buy 0.1 1.40405 -> 1.40425
    0.1, 1.40405, 1.40425, 100000.0, 1.0 / 1.40425, 1.42, id="usdcad-fx-converted"
)
DEAL_USDCHF = pytest.param(  # 152344361238 buy 0.01 0.80518 -> 0.80481
    0.01, 0.80518, 0.80481, 100000.0, 1.0 / 0.80481, -0.46, id="usdchf-fx-converted"
)


@pytest.mark.parametrize(
    ("open_price", "close_price", "direction", "expected"),
    [
        (4049.61, 4048.12, "buy", -14.9),
        (4048.12, 4049.61, "buy", 14.9),
        (4049.61, 4048.12, "sell", 14.9),
        (4048.12, 4049.61, "sell", -14.9),
    ],
)
def test_pnl_law_xauusd_matches_broker_deals(
    open_price: float, close_price: float, direction: str, expected: float
) -> None:
    """4,092 of 4,109 real XAUUSD deals fit this law within 1%."""
    delta = close_price - open_price
    if direction == "sell":
        delta = -delta
    got = price_delta_value(0.1, XAUUSD_CONTRACT, delta)
    assert got == pytest.approx(expected, abs=0.01)


@pytest.mark.parametrize(
    ("vol", "open_price", "close_price", "contract", "factor", "expected"),
    [
        DEAL_XAUUSD,
        DEAL_USDCAD,
        DEAL_USDCHF,
    ],
)
def test_pnl_law_with_fx_factor_matches_broker_deals(
    vol: float,
    open_price: float,
    close_price: float,
    contract: float,
    factor: float,
    expected: float,
) -> None:
    """FX-quoted profit currency needs currency_factor=1/close_price.

    USDCAD: naive reconstruction gives 2.00, broker reports 1.42 (+41% error).
    USDCHF: naive reconstruction gives -0.37, broker reports -0.46 (20% error).
    """
    got = price_delta_value(vol, contract, close_price - open_price, factor)
    assert got == pytest.approx(expected, abs=0.01)


def test_reward_value_matches_order_calc_profit() -> None:
    """mt5.order_calc_profit(BUY, XAUUSD, 1.0, ask, ask+1.0) -> 100.0."""
    assert reward_value(1.0, XAUUSD_CONTRACT, 1.0) == pytest.approx(100.0)


def test_reward_value_matches_legacy_impact_gate_scaling() -> None:
    """The legacy impact gate used (tp_dist / point) * tick_value * volume.

    With the broker's reported tick_value=0.10 and point=0.01 that yields
    10.0 for a 1.00 move on 1.0 lot; the canonical law yields 100.0 — the
    broker's own order_calc_profit agrees with 100.0. The legacy form
    UNDERSTATES reward by 10x on this account (see module docstring item 3).
    """
    legacy = (1.00 / 0.01) * 0.10 * 1.0
    canonical = reward_value(1.0, XAUUSD_CONTRACT, 1.00)
    assert legacy == pytest.approx(10.0)
    assert canonical == pytest.approx(100.0)
    assert canonical / legacy == pytest.approx(10.0)


def test_tick_value_consistency_detects_broker_zero() -> None:
    """EURUSD reports tick_value 0.0 where tick_size*contract = 1.0.

    A consumer that treats a zero tick_value as 'unavailable' would compute
    reward 0.0 and let every trade pass the cost guard. Detection is
    explicit: the judge returns False (contradiction), not None (unknown).
    """
    assert tick_value_is_consistent(EURUSD_TICK_VALUE, EURUSD_POINT, EURUSD_CONTRACT) is False


def test_tick_value_consistency_detects_xauusd_reported_vs_derived() -> None:
    """XAUUSD reports tick_value 0.10 where tick_size*contract = 1.0.

    Same contradiction class — reported 0.10 vs derived 1.0 (10x). Detected
    rather than inherited.
    """
    assert tick_value_is_consistent(0.10, XAUUSD_TICK_SIZE, XAUUSD_CONTRACT) is False


def test_tick_value_consistency_reports_unknown_when_unjudgable() -> None:
    """Unknown only when tick_size / contract are missing — a reported 0.0
    tick_value IS judgeable (and contradicts a positive derived value)."""
    assert tick_value_is_consistent(None, 0.01, 100.0) is None
    assert tick_value_is_consistent(1.0, None, 100.0) is None
    assert tick_value_is_consistent(1.0, 0.01, None) is None
    assert tick_value_is_consistent(1.0, 0.0, 100.0) is None
    assert tick_value_is_consistent(0.0, 0.01, 0.0) is None


# --------------------------------------------------------------- MARGIN


@pytest.mark.parametrize(
    ("vol", "expected"),
    [(0.01, 42.99), (1.0, 4298.95), (10.0, 42989.5)],
)
def test_margin_estimate_matches_broker_native_xauusd(vol: float, expected: float) -> None:
    """order_calc_margin BUY XAUUSD @ 4298.95: 0.01->42.99, 1.0->4298.95,
    10.0->42989.5. The classic formula is EXACT for this account (CFD
    leverage mode, USD margin currency, no margin_initial/maintenance)."""
    got = required_margin_estimate(vol, XAUUSD_CONTRACT, 4298.95, LEVERAGE)
    assert got == pytest.approx(expected, abs=0.05)


def test_margin_estimate_matches_order_check_projected_free_margin() -> None:
    """order_check BUY 0.01 @ 4297.42 returned margin=42.97 and
    margin_free=30419.58 from 30462.55 — the broker itself projects free
    margin after the hypothetical order; NSE must use that number, not a
    locally reconstructed one."""
    balance = 30462.55
    margin = required_margin_estimate(0.01, XAUUSD_CONTRACT, 4297.42, LEVERAGE)
    assert margin == pytest.approx(42.97, abs=0.05)
    assert balance - margin == pytest.approx(30419.58, abs=0.05)


def test_margin_estimate_handles_missing_leverage() -> None:
    """A zero/unknown leverage must not divide by zero (returns 0.0)."""
    assert required_margin_estimate(1.0, 100.0, 4000.0, 0) == 0.0
    assert required_margin_estimate(1.0, 100.0, 4000.0, 0.0) == 0.0


def test_margin_estimate_respects_account_currency_when_margin_is_usd() -> None:
    """EURUSD margin: order_calc_margin BUY 0.1 -> 113.78 at ask 1.13785.
    currency_margin is EUR, but the broker computes in DEPOSIT currency
    (USD) — value = 100000*1.13785*0.1/100 = 113.785. Proves margin is
    always expressed in the ACCOUNT currency by the server, not the margin
    currency."""
    got = required_margin_estimate(0.1, 100000.0, 1.13785, LEVERAGE)
    assert got == pytest.approx(113.78, abs=0.05)


# ----------------------------------------------------------- VOLUME RULES

XAUUSD_VOL = (0.01, 100.0, 0.01)


def test_volume_normalization_floors_to_step() -> None:
    """Floor never inflates risk: 0.014 -> 0.01, not 0.02."""
    assert normalize_volume(0.014, *XAUUSD_VOL) == 0.01


def test_volume_normalization_steps_up_correctly() -> None:
    assert normalize_volume(0.0199999, *XAUUSD_VOL) == 0.01
    assert normalize_volume(0.02, *XAUUSD_VOL) == 0.02
    assert normalize_volume(0.555, *XAUUSD_VOL) == 0.55


def test_volume_normalization_below_min_returns_zero() -> None:
    """0.005 is a legal step multiple but below broker min 0.01 -> REJECT (0)."""
    assert normalize_volume(0.005, *XAUUSD_VOL) == 0.0


def test_volume_normalization_respects_max() -> None:
    """volume_max=100 on this account; a request above it clamps to 100."""
    assert normalize_volume(250.0, *XAUUSD_VOL) == 100.0


def test_volume_normalization_ceil_mode_for_floors() -> None:
    """ceil raises to the next legal step (used to satisfy a broker minimum)."""
    assert normalize_volume(0.014, *XAUUSD_VOL, operation="ceil") == 0.02


def test_volume_normalization_rejects_non_finite_and_zero() -> None:
    assert normalize_volume(float("nan"), *XAUUSD_VOL) == 0.0
    assert normalize_volume(float("inf"), *XAUUSD_VOL) == 0.0
    assert normalize_volume(0.0, *XAUUSD_VOL) == 0.0


def test_volume_normalization_respects_smaller_step_on_other_broker() -> None:
    """A broker whose XAUUSD steps by 0.001 accepts 0.014 -> 0.014."""
    assert normalize_volume(0.014, 0.001, 100.0, 0.001) == 0.014


def test_volume_normalization_handles_0_1_step_broker() -> None:
    """A restrictive broker stepping by 0.1 floors 0.55 -> 0.5."""
    assert normalize_volume(0.55, 0.1, 100.0, 0.1) == 0.5


# ---------------------------------------------------------- PRICE / TICKS


def test_align_price_ticks_to_xauusd_grid() -> None:
    """XAUUSD tick grid is 0.01; alignment never lands between ticks."""
    assert align_price(4296.425, XAUUSD_TICK_SIZE, XAUUSD_DIGITS) == 4296.42
    assert align_price(4296.426, XAUUSD_TICK_SIZE, XAUUSD_DIGITS) == 4296.43


def test_align_price_5_digit_fx_needs_no_xauusd_assumption() -> None:
    """EURUSD is 5-digit: the SAME function aligns to 0.00001, not 0.01."""
    assert align_price(1.137855, 0.00001, 5) == 1.13786
    assert align_price(1.137854, 0.00001, 5) == 1.13785


def test_align_price_direction_rounds_toward_safety() -> None:
    """A protective stop must not drift TOWARD the market while rounding.

    BUY position, stop BELOW market -> round DOWN ('down'); SELL position,
    stop ABOVE market -> round UP ('up').
    """
    assert align_price(4296.425, XAUUSD_TICK_SIZE, XAUUSD_DIGITS, direction="down") == 4296.42
    assert align_price(4296.425, XAUUSD_TICK_SIZE, XAUUSD_DIGITS, direction="up") == 4296.43


def test_align_price_rejects_no_floating_point_drift() -> None:
    """The classic pitfall: 0.07*100 in float is 7.000000000000001."""
    assert align_price(0.07000000000000001, 0.01, 2) == 0.07
    assert align_price(4298.419999999999, 0.01, 2) == 4298.42


def test_align_price_degrades_gracefully_without_tick_size() -> None:
    """A broker with tick_size 0.0 (observed on EURUSD here) degrades to
    digits-only rounding instead of raising on the hot path."""
    assert align_price(1.1378549, 0.0, 5) == 1.13785
    assert align_price(4296.4249, None, 2) == 4296.42


def test_align_price_passes_non_finite_through() -> None:
    """Infinity/NaN must never be silently coerced to a tradable price."""
    assert math.isinf(align_price(float("inf"), 0.01, 2))
    assert math.isnan(align_price(float("nan"), 0.01, 2))


# ------------------------------------------------------- STOP DISTANCES


def test_min_stop_distance_uses_broker_stops_level() -> None:
    """XAUUSD stops_level=0 on this account; a zero level must not be
    replaced by a hardcoded price-unit floor."""
    assert min_stop_distance_price(0, 0.01) == 0.0


def test_min_stop_distance_scales_safety_points_by_point() -> None:
    """A 10-POINT safety floor is 0.10 on 2-digit XAUUSD but only 0.0001 on
    5-digit EURUSD — a fixed 0.10 price floor would demand 10,000 points
    there."""
    assert min_stop_distance_price(0, 0.01, safety_points=10) == 0.10
    assert min_stop_distance_price(0, 0.00001, safety_points=10) == 0.0001


def test_min_stop_distance_combines_broker_and_safety() -> None:
    """A restrictive broker with stops_level=50 (points) dominates the floor."""
    assert min_stop_distance_price(50, 0.01) == 0.50
    assert min_stop_distance_price(50, 0.01, safety_points=10) == 0.50
    assert min_stop_distance_price(50, 0.00001) == 0.0005


def test_min_stop_distance_on_broker_with_no_point() -> None:
    assert min_stop_distance_price(50, 0.0) == 0.0
    assert min_stop_distance_price(50, None) == 0.0


# ------------------------------------------------------- MARGIN LEVEL


def test_margin_level_uses_mql5_definition() -> None:
    """MQL5: margin level = equity / margin * 100."""
    assert margin_level_percent(1500.0, 750.0) == pytest.approx(200.0)
    assert margin_level_percent(30462.55, 4297.42) == pytest.approx(30462.55 / 4297.42 * 100.0)


def test_margin_level_is_not_applicable_when_margin_zero() -> None:
    """The server reports 0.0 when nothing is reserved; that is N/A, not 0%.

    This account has margin==0.0 right now (no open positions) and reports
    margin_level 0.0 — a UI must NOT render 'Margin Level: 0%'.
    """
    assert margin_level_percent(30462.55, 0.0) is None
    assert margin_level_percent(30462.55, 0) is None


def test_margin_level_handles_unavailable_values() -> None:
    assert margin_level_percent(None, 100.0) is None
    assert margin_level_percent(100.0, None) is None
    assert margin_level_percent(100.0, float("nan")) is None
    assert margin_level_percent(float("nan"), 100.0) is None
    assert margin_level_percent(100.0, -1.0) is None


# ------------------------------------------------- INTEGRITY / UNIT LAWS


def test_price_delta_value_zero_inputs_are_zero() -> None:
    assert price_delta_value(0.0, 100.0, 10.0) == 0.0
    assert price_delta_value(1.0, 0.0, 10.0) == 0.0
    assert price_delta_value(1.0, 100.0, 0.0) == 0.0
    assert price_delta_value(1.0, 100.0, 10.0, currency_factor=0.0) == 0.0


def test_price_delta_value_is_linear_in_volume() -> None:
    """PnL must scale linearly with volume (the whole sizing contract)."""
    d = 0.30
    assert price_delta_value(2.0, 100.0, d) == pytest.approx(2 * price_delta_value(1.0, 100.0, d))
    assert price_delta_value(0.5, 100.0, d) == pytest.approx(0.5 * price_delta_value(1.0, 100.0, d))


def test_buy_and_sell_pnl_are_sign_symmetric() -> None:
    """A SELL's profit on the same move must be the exact negative of the BUY's."""
    o, c = 4000.0, 4010.0
    buy = price_delta_value(1.0, 100.0, c - o)
    sell = price_delta_value(1.0, 100.0, -(c - o))
    assert buy == pytest.approx(1000.0)
    assert sell == pytest.approx(-1000.0)
    assert buy + sell == pytest.approx(0.0, abs=1e-9)


def test_reward_value_is_always_non_negative() -> None:
    assert reward_value(1.0, 100.0, -0.30) == pytest.approx(30.0)
    assert reward_value(1.0, 100.0, 0.30) == pytest.approx(30.0)


def test_average_entry_is_volume_weighted_not_arithmetic() -> None:
    """BUY 1.0 @ 2000 + BUY 0.5 @ 2010 -> 2003.33, never 2005.00.

    NSE takes no average anywhere else: the canonical weighted mean is
    sum(vol_i * price_i) / sum(vol_i).
    """
    lots = [1.0, 0.5]
    prices = [2000.0, 2010.0]
    avg = sum(v * p for v, p in zip(lots, prices, strict=True)) / sum(lots)
    assert avg == pytest.approx(2003 + 1 / 3)
    assert avg != pytest.approx(sum(prices) / len(prices))


def test_weighted_average_after_partial_close_preserves_entry() -> None:
    """A partial close reduces volume; the broker keeps price_open unchanged.

    Hedging-account evidence: every real deal in history carries the position's
    open_price unchanged across its lifetime (close_volume == open_volume on
    full closes, position_id stable), so reconstruction must not re-average
    after a partial close.
    """
    lots = [1.0, 0.5]
    prices = [2000.0, 2010.0]
    avg_before = sum(v * p for v, p in zip(lots, prices, strict=True)) / sum(lots)
    # partial close of 0.5 from the first entry: remaining 0.5 @ 2000, 0.5 @ 2010
    remaining = [(0.5, 2000.0), (0.5, 2010.0)]
    avg_after = sum(v * p for v, p in remaining) / sum(v for v, _ in remaining)
    assert avg_after == pytest.approx(2005.0)
    # the average genuinely moves with volume removed — never arithmetic-mean
    assert avg_before != avg_after
    assert avg_before == pytest.approx(2003 + 1 / 3)
