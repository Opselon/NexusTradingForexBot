"""Execution-truth log contract (Lane C, GROUND_TRUTH.md §D).

The signal-generation layer must never claim an order was EXECUTED.

``signals/policy.py::_evaluate_predictive_limit`` builds a ``TradeProposal``
(``decision_stage=PREDICTIVE_LIMIT_GENERATION``). At the proven base the INFO
line next to it read::

    PREDICTIVE LIMIT EXECUTED: Placing {action} at 50% Equilibrium {price}!

That fires at PROPOSAL GENERATION - before any pre-dispatch gate, before any
dispatch attempt, before any broker acceptance. A proposal that is later
rejected by the risk engine, refused at dispatch, or rejected by the broker
still printed the word EXECUTED, and the trace vocabulary in the same
contract reserves EXECUTED for broker-evidence-backed states. Log vocabulary
is the contract here; nothing about gate math or dispatch semantics changed.

These tests pin the renamed intent-stage vocabulary so the defect cannot
silently return. They drive the real ``SignalPolicy`` method with the same
lightweight fixtures as ``test_policy_predictive_limit.py`` and capture the
module logger with the repo's established double pattern (structlog host
routing defeats stdlib caplog - see ``test_bug274_wrapper_state_leak.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals import policy as policy_module
from nexus_scalp.signals.policy import SignalPolicy


class MockBar:
    """Minimal bar double matching the sibling predictive-limit suite."""

    def __init__(self, low: float, high: float):
        self.low = low
        self.high = high


def _make_tick(bid: float = 2000.0, ask: float = 2000.2) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=datetime.now(UTC),
        bid=bid,
        ask=ask,
        volume=1.0,
    )


def _make_feature_vector(order_block_type: int = 1) -> FeatureVector:
    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.1,
        log_return_m1=0.0,
        atr_m1=2.00,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
        price_compression_flag_ratio=1.0,
        is_at_extreme_high=False,
        is_at_extreme_low=False,
        stop_hunt_depth=0.0,
        session_tokyo=True,
        session_london=False,
        session_ny=False,
        session_overlap_london_ny=False,
        lag_1_log_return=0.0,
        lag_2_log_return=0.0,
        lag_3_log_return=0.0,
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
        fvg_bullish_active=False,
        fvg_bearish_active=False,
        order_block_type=order_block_type,
        liquidity_sweep_signal=0,
        choch_bullish=False,
        choch_bearish=False,
        broke_previous_high=False,
        broke_previous_low=False,
        rapid_reversal_spike=False,
        rapid_reversal_spike_val=0.0,
        tenkan_sen=2000.0,
        kijun_sen=2000.0,
        senkou_span_a=2000.0,
        senkou_span_b=2000.0,
        tk_cross_signal=0,
        is_above_kumo=True,
        is_below_kumo=False,
        rsi_14=50.0,
        dist_to_ema_21=1.0,
        dist_to_ema_50=1.0,
        cross_asset_z_score=0.0,
        htf_h4_trend=1.0,
        htf_h1_momentum=1.0,
        htf_m30_structure=1.0,
        htf_m15_confirmation=1.0,
        support_zone_dist=5.0,
        resistance_zone_dist=5.0,
        trend_strength=1.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
    )


@pytest.fixture
def policy_logs(monkeypatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Capture every ``policy`` module logger call (level, args, kwargs)."""
    got: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(level: str):
        def _fn(*args: Any, **kwargs: Any) -> None:
            got.append((level, args, kwargs))

        return _fn

    double = SimpleNamespace(
        critical=_record("critical"),
        error=_record("error"),
        warning=_record("warning"),
        info=_record("info"),
        debug=_record("debug"),
    )
    monkeypatch.setattr(policy_module, "logger", double)
    return got


def _drive_predictive_limit(order_block_type: int = 1, completed_bars=None):
    """Invoke the real predictive-limit evaluation on a real SignalPolicy."""
    policy = SignalPolicy()
    tick = _make_tick()
    bars = completed_bars
    if bars is None:
        bars = [MockBar(low=1990.0, high=2010.0) for _ in range(20)]
    return policy._evaluate_predictive_limit(
        valid_ob=True,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=order_block_type,
        current_tick=tick,
        atr=2.0,
        completed_bars=bars,
        execution_id="EXEC-TRUTH-001",
        now=tick.timestamp,
        confidence=0.8,
        confidence_before_filters=0.8,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )


def _drive_predictive_limit_no_ob():
    """Invoke the predictive-limit evaluation with an invalid order block."""
    policy = SignalPolicy()
    tick = _make_tick()
    return policy._evaluate_predictive_limit(
        valid_ob=False,
        smc_god_mode_active=False,
        total_exposure=0,
        order_block_type=1,
        current_tick=tick,
        atr=2.0,
        completed_bars=[MockBar(low=1990.0, high=2010.0) for _ in range(20)],
        execution_id="EXEC-TRUTH-002",
        now=tick.timestamp,
        confidence=0.8,
        confidence_before_filters=0.8,
        regime_str="TRENDING",
        regime_conf=0.9,
        trend_strength=1.0,
    )


def test_predictive_limit_emits_intent_vocabulary_not_executed(policy_logs):
    """The proposal-generation INFO must say ORDER_INTENT_CREATED, not EXECUTED.

    This is the regression pin for the proven defect: a proposal that has
    passed NO gate and reached NO broker previously logged "PREDICTIVE LIMIT
    EXECUTED" - a log-as-authority claim indistinguishable from a real fill.
    """
    proposal = _drive_predictive_limit()

    assert proposal is not None, "fixture must produce a proposal (OB valid)"
    infos = [rec for rec in policy_logs if rec[0] == "info"]
    truthy = [rec for rec in infos if "PREDICTIVE LIMIT" in str(rec[1])]
    assert len(truthy) == 1, f"expected one PREDICTIVE LIMIT info, got {truthy!r}"

    (level, args, kwargs) = truthy[0]
    message = " ".join(str(part) for part in args)

    # intent vocabulary present; execution claim absent
    assert "ORDER_INTENT_CREATED" in message, f"intent vocabulary missing: {message!r}"
    assert "EXECUTED" not in message, f"false EXECUTED claim survived: {message!r}"

    # the operator-useful content is preserved (no information lost in the rename)
    assert "BUY_LIMIT" in message or "SELL_LIMIT" in message, "action missing"
    assert "Equilibrium" in message, "equilibrium placement context missing"


def test_predictive_limit_intent_carries_structured_stage_fields(policy_logs):
    """The intent line annotates which stage it was emitted at.

    A reader of the structured record must be able to tell this is a
    PROPOSAL (not an order): ``decision_stage`` is the proposal's own stage
    and ``request_id``/``execution_id`` join the line to the downstream
    dispatch/broker records that are the only ones entitled to say EXECUTED.
    """
    proposal = _drive_predictive_limit()

    assert proposal is not None
    infos = [rec for rec in policy_logs if rec[0] == "info"]
    truthy = [rec for rec in infos if "ORDER_INTENT_CREATED" in str(rec[1])]
    assert len(truthy) == 1, f"expected one intent info, got {truthy!r}"

    (_, _args, kwargs) = truthy[0]
    assert kwargs.get("decision_stage") == "PREDICTIVE_LIMIT_GENERATION"
    # the log's ids must be the PROPOSAL's ids, so the join is real
    assert kwargs.get("request_id") == proposal.request_id
    assert kwargs.get("execution_id") == proposal.execution_id
    assert kwargs.get("action") == proposal.action.value


def test_predictive_limit_source_no_longer_carries_executed_claim():
    """Source-level guard: the false claim string cannot be reintroduced.

    Greps the constant prefix (the message is assembled at runtime, so a full
    string search would not match once the f-string is rebuilt).
    """
    import inspect

    source = inspect.getsource(policy_module.SignalPolicy._evaluate_predictive_limit)
    # The docstring/comment vocabulary below deliberately avoids the claim word:
    # a source-level guard must trip only on a real assertion, not on the
    # explanation of the rule it enforces.
    assert "EXECUTED" not in source, (
        "PREDICTIVE_LIMIT generation must never claim EXECUTED (§D: the "
        "proposal has passed no gate and reached no broker)"
    )
    assert "ORDER_INTENT_CREATED" in source


@pytest.mark.parametrize(
    "order_block_type,expected_action", [(1, ActionType.BUY_LIMIT), (-1, ActionType.SELL_LIMIT)]
)
def test_predictive_limit_intent_vocabulary_both_directions(
    policy_logs, order_block_type, expected_action
):
    """The contract holds for both limit directions, not just the default."""
    proposal = _drive_predictive_limit(order_block_type=order_block_type)

    assert proposal is not None
    assert proposal.action == expected_action
    infos = [rec for rec in policy_logs if rec[0] == "info"]
    truthy = [rec for rec in infos if "PREDICTIVE LIMIT" in str(rec[1])]
    assert len(truthy) == 1
    message = " ".join(str(part) for part in truthy[0][1])
    assert "ORDER_INTENT_CREATED" in message
    assert "EXECUTED" not in message
    assert expected_action.value in message


def test_predictive_limit_no_proposal_emits_no_execution_claim(policy_logs):
    """No proposal -> no log line at all, in particular no execution claim."""
    proposal = _drive_predictive_limit_no_ob()

    assert proposal is None
    truthy = [rec for rec in policy_logs if "EXECUTED" in str(rec[1])]
    assert truthy == [], f"invalid-OB path must not claim execution, got {truthy!r}"
