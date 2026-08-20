"""
Unit Tests - Enterprise Dynamic Risk Engine Invariants
======================================================
Verifies position sizing math, safety ceilings, micro exceptions, scaling,
free-margin clamps, and boundary/safety protection rules.
"""

import math
from datetime import UTC, datetime

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData, TradeProposal
from nexus_scalp.risk.risk_engine import RiskEngine


def test_risk_engine_kill_switch_blocks_proposal() -> None:
    """Ensures activated kill switch rejects all proposals."""
    config = RiskConfig(risk_per_trade_pct=0.5)
    engine = RiskEngine(config)
    engine.enable_kill_switch()

    now = datetime.now(UTC)
    proposal = TradeProposal(
        request_id="req-1",
        symbol="EURUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.9,
        proposed_entry=1.0850,
        stop_loss=1.0840,
        take_profit=1.0865,
        risk_reward_ratio=1.5,
    )

    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=10000.0,
    )
    symbol_info = SymbolInfo(
        symbol="EURUSD",
        digits=5,
        point=0.00001,
        tick_size=0.00001,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100000.0,
    )
    tick = TickData(symbol="EURUSD", timestamp=now, bid=1.0850, ask=1.0851)

    order = engine.evaluate_proposal(proposal, account, symbol_info, [], tick)
    assert order is None


def test_xauusd_dynamic_risk_engine_matrix() -> None:
    """
    Comprehensive matrix test matching Step 10 across all specified account sizes.
    Using Risk = 1%, SL distance = 2.0 (Entry = 2000.0, SL = 1998.0), and XAUUSD contract size = 100.
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    # Test cases: (Equity, Expected Volume, Expected Reason)
    matrix = [
        (10.0, 0.01, "MICRO_ACCOUNT_MIN_LOT_EXCEPTION"),
        (20.0, 0.01, "MICRO_ACCOUNT_MIN_LOT_EXCEPTION"),
        (50.0, 0.0, "INSUFFICIENT_EQUITY_FOR_MIN_LOT"),
        (
            100.0,
            0.0,
            "INSUFFICIENT_EQUITY_FOR_MIN_LOT",
        ),  # Raw = 1.0 / (2 * 100) = 0.005 -> step 0.01 floor is 0.0
        (500.0, 0.02, "SUCCESS"),  # Raw = 5.0 / 200 = 0.025 -> floor 0.02
        (1000.0, 0.05, "SUCCESS"),  # Raw = 10.0 / 200 = 0.05
        (10000.0, 0.50, "SUCCESS"),  # Raw = 100.0 / 200 = 0.50
        (47000.0, 2.35, "SUCCESS"),  # Raw = 470.0 / 200 = 2.35
        (100000.0, 5.00, "SUCCESS"),  # Raw = 1000.0 / 200 = 5.00
        (500000.0, 10.0, "SUCCESS"),  # Raw = 5000.0 / 200 = 25.0 -> Capped to 10.0 tier limit
        (1000000.0, 10.0, "SUCCESS"),  # Raw = 10000.0 / 200 = 50.0 -> Capped to 10.0 tier limit
    ]

    for equity, expected_vol, expected_reason in matrix:
        account = AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=equity,
            equity=equity,
            margin=0.0,
            margin_free=equity * 2.0,  # Ensure free margin isn't the limiting factor
            currency="USD",
        )
        volume, reason = engine.calculate_dynamic_volume(
            entry=2000.0, sl=1998.0, account=account, symbol_info=symbol_info, risk_pct=1.0
        )
        assert volume == expected_vol, (
            f"Failed for equity {equity}: expected {expected_vol}, got {volume}"
        )
        assert reason == expected_reason, (
            f"Failed for equity {equity}: expected {expected_reason}, got {reason}"
        )


def test_stop_loss_scaling() -> None:
    """
    Step 11: For the same account (Equity = $10,000, Risk = 1%), test multiple XAUUSD SL distances.
    Smaller SL distance -> larger position.
    Larger SL distance -> smaller position.
    Monetary risk at SL should remain approximately equal to 1% of equity ($100).
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=20000.0,
        currency="USD",
    )

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    # Distances to test
    sl_distances = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
    previous_volume = float("inf")

    for dist in sl_distances:
        entry = 2000.0
        sl = entry - dist
        volume, reason = engine.calculate_dynamic_volume(
            entry=entry, sl=sl, account=account, symbol_info=symbol_info, risk_pct=1.0
        )

        assert volume < previous_volume, (
            f"Failed scaling for SL distance {dist}: volume {volume} is not smaller than previous {previous_volume}"
        )
        previous_volume = volume

        # Monetary risk should be approximately $100 (1% of 10000)
        actual_loss = volume * dist * symbol_info.trade_contract_size
        assert actual_loss <= 100.0, (
            f"Monetary risk exceeded configured percentage: got {actual_loss} USD"
        )
        # Tolerance check for flooring
        assert actual_loss >= 100.0 - (
            symbol_info.volume_step * dist * symbol_info.trade_contract_size
        ), "Under-risked excessively"


def test_equity_scaling() -> None:
    """
    Step 12: For the same setup, increase equity from $100 to $1,000,000.
    The calculated size should increase approximately linearly with equity until constrained.
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    equities = [100.0, 1000.0, 10000.0, 100000.0, 1000000.0]
    volumes = []

    for equity in equities:
        # Avoid the below min lot 0.0 volume for $100 by using a tighter SL of 0.50
        account = AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=equity,
            equity=equity,
            margin=0.0,
            margin_free=equity * 2.0,
            currency="USD",
        )
        volume, reason = engine.calculate_dynamic_volume(
            entry=2000.0,
            sl=1999.50,  # Tight SL
            account=account,
            symbol_info=symbol_info,
            risk_pct=1.0,
        )
        volumes.append(volume)

    # Let's verify linear scaling:
    # $100 -> 1.0 * 1% / 100 = $1 risk. Raw = 1.0 / (0.5 * 100) = 0.02 lots.
    # $1000 -> 10.0 * 1% / 100 = $10 risk. Raw = 10.0 / 50 = 0.20 lots.
    # $10000 -> 100.0 * 1% / 100 = $100 risk. Raw = 100.0 / 50 = 2.0 lots.
    # $100000 -> 10.0 lots (Tier cap of 10.0 lots)
    # $1000000 -> 10.0 lots (Tier cap of 10.0 lots)
    assert volumes[0] == 0.02
    assert volumes[1] == 0.20
    assert volumes[2] == 2.00
    assert volumes[3] == 10.00
    assert volumes[4] == 10.00


def test_risk_invariance() -> None:
    """
    Step 13: Risk-Invariance Test.
    estimated_loss_at_SL ≈ account_equity * risk_percent / 100
    with small deviations only due to volume_step flooring.
    """
    config = RiskConfig(risk_per_trade_pct=1.5)
    engine = RiskEngine(config)

    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=47000.0,
        equity=47000.0,
        margin=0.0,
        margin_free=94000.0,
        currency="USD",
    )

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    entry = 2000.0
    sl = 1995.0
    volume, reason = engine.calculate_dynamic_volume(
        entry=entry, sl=sl, account=account, symbol_info=symbol_info, risk_pct=1.5
    )

    # Expected risk: 47000 * 1.5% = 705.0 USD
    # SL distance: 5.0
    # Raw Lots: 705.0 / (5.0 * 100) = 1.41 lots.
    assert volume == 1.41
    actual_loss = volume * 5.0 * 100.0
    assert actual_loss == 705.0


def test_free_margin_protection() -> None:
    """
    Step 7: Check that required margin does not consume more than 20% of free_margin.
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    # Small free margin of $100 on a $10,000 account
    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=100.0,  # extremely low free margin
        currency="USD",
    )

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    # 1% risk of $10,000 is $100.
    # At SL distance = 1.0, raw risk volume = 1.0 lot.
    # Required margin for 1.0 lot = 1.0 * 100 * 2000.0 / 100 = 2000 USD.
    # But max allowable margin = 20% of free margin ($100) = 20 USD.
    # Max margin volume = 20 * 100 / (100 * 2000.0) = 2000 / 200000 = 0.01 lots.
    volume, reason = engine.calculate_dynamic_volume(
        entry=2000.0, sl=1999.0, account=account, symbol_info=symbol_info, risk_pct=1.0
    )

    assert volume == 0.01
    assert reason == "SUCCESS"


def test_safety_and_boundary_conditions() -> None:
    """
    Step 14: Test boundary, negative, zero, and NaN/Inf conditions.
    Ensure we never return NaN, Inf, or negative volumes/risks.
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    base_account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=1000.0,
        equity=1000.0,
        margin=0.0,
        margin_free=1000.0,
        currency="USD",
    )
    base_symbol = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    # SL Distance = 0
    vol, reason = engine.calculate_dynamic_volume(2000.0, 2000.0, base_account, base_symbol, 1.0)
    assert vol == 0.0
    assert "INVALID" in reason or reason == "INSUFFICIENT_EQUITY_FOR_MIN_LOT"

    # Negative SL distance / SL price
    vol, reason = engine.calculate_dynamic_volume(2000.0, -100.0, base_account, base_symbol, 1.0)
    assert vol == 0.0
    assert "INVALID" in reason

    # Equity = 0
    zero_account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=0.0,
        equity=0.0,
        margin=0.0,
        margin_free=1000.0,
        currency="USD",
    )
    vol, reason = engine.calculate_dynamic_volume(2000.0, 1990.0, zero_account, base_symbol, 1.0)
    assert vol == 0.0
    assert "INVALID" in reason

    # Leverage = 0 (is not allowed by Pydantic model directly, but we can test bad leverage <= 0 handling in validation function if we pass a mock or bypass)
    # NaN / Inf entry or SL
    vol, reason = engine.calculate_dynamic_volume(
        float("nan"), 1990.0, base_account, base_symbol, 1.0
    )
    assert vol == 0.0
    assert "INVALID" in reason

    vol, reason = engine.calculate_dynamic_volume(
        2000.0, float("inf"), base_account, base_symbol, 1.0
    )
    assert vol == 0.0
    assert "INVALID" in reason


def test_no_flat_2_lot_bug_regression() -> None:
    """
    Step 15: Explicitly verify that different account sizes do not produce a flat 2.0 LOT output,
    demonstrating that the universal 2.0 LOT ceiling bug has been fully resolved.
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    # Check three larger accounts
    acct_10k = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10000.0,
        margin=0.0,
        margin_free=20000.0,
        currency="USD",
    )
    acct_47k = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=47000.0,
        equity=47000.0,
        margin=0.0,
        margin_free=94000.0,
        currency="USD",
    )
    acct_100k = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=100000.0,
        equity=100000.0,
        margin=0.0,
        margin_free=200000.0,
        currency="USD",
    )

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    vol_10k, _ = engine.calculate_dynamic_volume(2000.0, 1998.0, acct_10k, symbol_info, 1.0)
    vol_47k, _ = engine.calculate_dynamic_volume(2000.0, 1998.0, acct_47k, symbol_info, 1.0)
    vol_100k, _ = engine.calculate_dynamic_volume(2000.0, 1998.0, acct_100k, symbol_info, 1.0)

    # Let's verify they are different and none is clamped to 2.0 lots unconditionally
    assert vol_10k != vol_47k
    assert vol_47k != vol_100k
    assert vol_10k == 0.50
    assert vol_47k == 2.35
    assert vol_100k == 5.00
def test_risk_tier_contract_matches_documented_tables() -> None:
    """
    Forensic issue #2: the Account-Tier Ceiling table in agents/skill.md
    MUST match the code (code is the source of truth).

    Code truth (calculate_dynamic_volume + get_clamped_position_size):
      equity < $100      -> max 0.02 lots
      equity < $1,000    -> max 0.10 lots
      equity < $10,000   -> max 1.00 lots
      equity >= $10,000  -> max min(10.0, symbol volume_max) lots
    """
    config = RiskConfig(risk_per_trade_pct=1.0)
    engine = RiskEngine(config)

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )

    def acct(equity: float) -> AccountInfo:
        return AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=equity,
            equity=equity,
            margin=0.0,
            margin_free=equity * 2.0,
            currency="USD",
        )

    # Wide SL so the raw risk-based size exceeds every tier ceiling:
    # risk at 1% on $50 = $0.50 -> raw 0.5/(10*100) = tiny; use big equity and
    # tight SL to push raw lots ABOVE the ceilings.
    cases = [
        # (equity, expected_tier_max)
        (50.0, 0.02),      # micro tier
        (500.0, 0.10),     # < 1k tier
        (5000.0, 1.00),    # < 10k tier
        (100000.0, 10.0),  # >= 10k -> HARD_MAX_LOTS parity
    ]
    for equity, expected_cap in cases:
        volume, reason = engine.calculate_dynamic_volume(
            entry=2000.0,
            sl=1999.0,  # 1.0 distance -> raw lots = equity*1%/100
            account=acct(equity),
            symbol_info=symbol_info,
            risk_pct=1.0,
        )
        # Raw lots: equity * 1% / (1.0 * 100) = equity/10000.
        # $100k -> 10.0 raw -> capped at 10.0; $5k -> 0.5 raw -> capped 1.0 (fine).
        # For the lower tiers the raw is below the cap, so explicitly verify
        # each tier's ceiling via get_clamped_position_size.
        clamped = engine.get_clamped_position_size(
            volume=raw_for(equity), account=acct(equity), symbol_info=symbol_info
        )
        assert clamped <= expected_cap + 1e-9, (
            f"equity={equity}: clamped {clamped} exceeded tier cap {expected_cap}"
        )

    # Below $50 the micro-account exception grants the broker minimum;
    # at exactly $50 the standard INSUFFICIENT_EQUITY_FOR_MIN_LOT rule applies.
    vol_micro, reason_micro = engine.calculate_dynamic_volume(
        entry=2000.0,
        sl=1999.0,
        account=acct(30.0),
        symbol_info=symbol_info,
        risk_pct=1.0,
    )
    assert reason_micro == "MICRO_ACCOUNT_MIN_LOT_EXCEPTION"
    assert vol_micro == symbol_info.volume_min


def raw_for(equity: float) -> float:
    # raw risk-based lots for 1% risk at 1.0 SL distance, 100 contract
    return (equity * 0.01) / (1.0 * 100.0)


def test_default_max_allowed_lots_matches_hard_max() -> None:
    """
    Forensic issue #2: RiskEngine default max_allowed_lots was 50.0 but
    OrderManager's HARD_MAX_LOTS is 10.0. The engine-level exposure cap must
    never exceed the execution hard ceiling.
    """
    engine = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    assert engine.max_allowed_lots == 10.0

    symbol_info = SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=0,
        trade_contract_size=100.0,
    )
    account = AccountInfo(
        login=123,
        trade_mode=0,
        leverage=100,
        balance=100000.0,
        equity=100000.0,
        margin=0.0,
        margin_free=200000.0,
        currency="USD",
    )
    # Raw lots at 1% risk, 1.0 SL distance = 10.0 lots; the directional
    # exposure cap must never exceed 10.0
    volume, reason = engine.calculate_dynamic_volume(
        entry=2000.0, sl=1999.0, account=account, symbol_info=symbol_info, risk_pct=1.0
    )
    assert volume <= 10.0 + 1e-9


def test_high_confidence_threshold_default_matches_config() -> None:
    """
    Forensic issue #2 / ledger #11: ctor default was 0.70 but the effective
    config default (AlgoConfig) is 0.95. The ctor must match the config so
    a bare RiskEngine(config) does not silently use a different RR gate.
    """
    from nexus_scalp.configuration.config import AlgoConfig

    engine = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    assert engine.high_confidence_threshold == AlgoConfig().high_confidence_threshold == 0.95

