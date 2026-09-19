import datetime as dt_module
import json
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import torch
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager
from nexus_scalp.signals.policy import SignalPolicy
from nexus_scalp.signals.rule_matrix import RuleMatrixEngine
from nexus_scalp.web.server import create_app


@pytest.fixture
def temp_audit_repo(tmp_path) -> Generator[AuditRepository, None, None]:
    """
    Fixture providing a fresh unique SQLite database for rules testing.

    The database lives under pytest's `tmp_path` rather than the repository root:
    on Windows the SQLite WAL/SHM sidecar files can stay locked briefly after
    close(), so root-relative cleanup silently failed and left hundreds of stray
    `test_rules_audit_*.db` files in the repository (see agents/bugs.md BUG-011).
    """
    db_path = tmp_path / f"test_rules_audit_{uuid.uuid4().hex}.db"
    repo = AuditRepository(f"sqlite:///{db_path.as_posix()}")
    yield repo
    repo.close()
    for ext in ("", "-wal", "-shm"):
        file_path = str(db_path) + ext
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                # tmp_path is disposable; a locked sidecar cannot pollute the repo.
                pass


@pytest.fixture
def rule_engine(temp_audit_repo: AuditRepository) -> RuleMatrixEngine:
    return RuleMatrixEngine(audit_repo=temp_audit_repo)


# ============================================================================
# DATABASE SEEDING & TOGGLING TESTS
# ============================================================================


def test_rule_matrix_ttl_throttling(
    temp_audit_repo: AuditRepository,
    rule_engine: RuleMatrixEngine,
) -> None:
    """Verifies that refresh_cache() throttles DB reads within TTL window unless force=True."""
    # Mock audit.get_trading_rules to track DB calls
    original_get_trading_rules = temp_audit_repo.get_trading_rules
    call_count = 0

    def mock_get_trading_rules():
        nonlocal call_count
        call_count += 1
        return original_get_trading_rules()

    temp_audit_repo.get_trading_rules = mock_get_trading_rules

    # Initial state after engine creation (1 call in __init__)
    call_count = 0

    # Repeated rapid calls within 5s TTL should NOT trigger DB read
    rule_engine.refresh_cache()
    rule_engine.refresh_cache()
    rule_engine.refresh_cache()
    assert call_count == 0

    # Calling with force=True MUST bypass TTL and query DB
    rule_engine.refresh_cache(force=True)
    assert call_count == 1

    # Calling with small ttl_seconds (e.g. -1s to simulate time passing) MUST query DB
    rule_engine.refresh_cache(ttl_seconds=-1.0)
    assert call_count == 2


def test_database_seeding_and_toggling(
    temp_audit_repo: AuditRepository,
    rule_engine: RuleMatrixEngine,
) -> None:
    # Verify all 30+ rules are seeded and disabled by default
    rules = temp_audit_repo.get_trading_rules()
    assert len(rules) >= 30
    for r in rules:
        assert r["is_enabled"] is False

    # Toggle a rule ON
    rule_name = "RULE_FVG_SNIPER_FILL"
    success = temp_audit_repo.toggle_trading_rule(rule_name, True)
    assert success is True

    # Refresh Rule Matrix Cache (force=True) and verify state
    rule_engine.refresh_cache(force=True)
    assert rule_engine.is_enabled(rule_name) is True

    # Toggle a rule with parameters update
    custom_params = {"fvg_min_size_pip": 2.5}
    success = temp_audit_repo.toggle_trading_rule(rule_name, True, json.dumps(custom_params))
    assert success is True
    rule_engine.refresh_cache(force=True)
    assert rule_engine.get_params(rule_name) == custom_params


# ============================================================================
# PRE-TRADE ENTRY RULES TESTS
# ============================================================================


def test_pre_trade_entry_fvg_sniper(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)
    fv = MagicMock()
    fv.fvg_bullish_active = True
    fv.fvg_bearish_active = False

    # Disabled by default -> returns None
    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.99, 0.005, 0.005])
    assert proposal is None

    # Enable and verify custom entry proposal (Bullish FVG -> BUY)
    temp_audit_repo.toggle_trading_rule("RULE_FVG_SNIPER_FILL", True)
    rule_engine.refresh_cache(force=True)

    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.99, 0.005, 0.005])
    assert proposal is not None
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.reason_code == "RULE_FVG_SNIPER_FILL"
    assert proposal.proposed_entry == tick.ask

    # Bearish FVG -> SELL
    fv.fvg_bullish_active = False
    fv.fvg_bearish_active = True
    proposal_sell = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.99, 0.005, 0.005])
    assert proposal_sell is not None
    assert proposal_sell.action == ActionType.SELL_MARKET
    assert proposal_sell.reason_code == "RULE_FVG_SNIPER_FILL"
    assert proposal_sell.proposed_entry == tick.bid


def test_eval_rule_fvg_sniper_fill_direct(
    rule_engine: RuleMatrixEngine,
) -> None:
    """Direct unit test for _eval_rule_fvg_sniper_fill covering bullish, bearish, none, and missing attrs."""
    ts = datetime.now(UTC)
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=2334.20, ask=2334.40)

    # 1. Bullish FVG -> BUY proposal with exact calculations
    fv_bullish = MagicMock()
    fv_bullish.fvg_bullish_active = True
    fv_bullish.fvg_bearish_active = False

    proposal_buy = rule_engine._eval_rule_fvg_sniper_fill(tick, fv_bullish)
    assert proposal_buy is not None
    assert proposal_buy.symbol == "XAUUSD"
    assert proposal_buy.generated_at == ts
    assert proposal_buy.action == ActionType.BUY_MARKET
    assert proposal_buy.confidence == 0.90
    assert proposal_buy.proposed_entry == 2334.40
    assert proposal_buy.stop_loss == round(2334.40 - 1.5, 2)
    assert proposal_buy.take_profit == round(2334.40 + 2.5, 2)
    assert proposal_buy.risk_reward_ratio == 1.67
    assert proposal_buy.reason_code == "RULE_FVG_SNIPER_FILL"
    assert proposal_buy.request_id.startswith("RULE_FVG_SNIPER_FILL_")

    # 2. Bearish FVG -> SELL proposal with exact calculations
    fv_bearish = MagicMock()
    fv_bearish.fvg_bullish_active = False
    fv_bearish.fvg_bearish_active = True

    proposal_sell = rule_engine._eval_rule_fvg_sniper_fill(tick, fv_bearish)
    assert proposal_sell is not None
    assert proposal_sell.symbol == "XAUUSD"
    assert proposal_sell.generated_at == ts
    assert proposal_sell.action == ActionType.SELL_MARKET
    assert proposal_sell.confidence == 0.90
    assert proposal_sell.proposed_entry == 2334.20
    assert proposal_sell.stop_loss == round(2334.20 + 1.5, 2)
    assert proposal_sell.take_profit == round(2334.20 - 2.5, 2)
    assert proposal_sell.risk_reward_ratio == 1.67
    assert proposal_sell.reason_code == "RULE_FVG_SNIPER_FILL"
    assert proposal_sell.request_id.startswith("RULE_FVG_SNIPER_FILL_")

    # 3. Neither FVG active -> returns None
    fv_inactive = MagicMock()
    fv_inactive.fvg_bullish_active = False
    fv_inactive.fvg_bearish_active = False

    proposal_none = rule_engine._eval_rule_fvg_sniper_fill(tick, fv_inactive)
    assert proposal_none is None

    # 4. Feature vector missing attributes (spec object without attributes) -> returns None
    fv_missing = object()
    proposal_missing = rule_engine._eval_rule_fvg_sniper_fill(tick, fv_missing)
    assert proposal_missing is None


def test_pre_trade_entry_judas_and_orderblock(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)

    # 1. Judas Swing Fade
    temp_audit_repo.toggle_trading_rule("RULE_JUDAS_SWING_FADE", True)
    rule_engine.refresh_cache(force=True)

    fv_judas = MagicMock()
    fv_judas.broke_previous_high = True
    fv_judas.broke_previous_low = False
    fv_judas.live_tick_displacement = -0.40  # Bearish rejection

    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv_judas, None, [0.1, 0.8, 0.1])
    assert proposal is not None
    assert proposal.action == ActionType.SELL_MARKET
    assert proposal.reason_code == "RULE_JUDAS_SWING_FADE"

    temp_audit_repo.toggle_trading_rule("RULE_JUDAS_SWING_FADE", False)

    # 2. OrderBlock Tap Reserve
    temp_audit_repo.toggle_trading_rule("RULE_ORDERBLOCK_TAP_RESERVE", True)
    rule_engine.refresh_cache(force=True)

    fv_ob = MagicMock()
    fv_ob.order_block_type = 1  # Bullish OB
    proposal_ob = rule_engine.evaluate_pre_trade_entry(tick, fv_ob, None, [0.1, 0.8, 0.1])
    assert proposal_ob is not None
    assert proposal_ob.action == ActionType.BUY_MARKET
    assert proposal_ob.reason_code == "RULE_ORDERBLOCK_TAP_RESERVE"


def test_pre_trade_entry_gap_and_go_momentum(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)
    fv = MagicMock()

    # June 3, 2024 is a Monday (2024-06-03.weekday() == 0)
    monday_00_00 = datetime(2024, 6, 3, 0, 0, 15)
    wednesday_10_30 = datetime(2024, 6, 5, 10, 30, 0)

    # 1. When rule is disabled -> evaluate_pre_trade_entry returns None even during Monday 00:00
    with patch("nexus_scalp.signals.rule_matrix.datetime") as mock_dt:
        mock_dt.now.return_value = monday_00_00
        proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.7, 0.2])
        assert proposal is None

    # Enable RULE_GAP_AND_GO_MOMENTUM
    temp_audit_repo.toggle_trading_rule("RULE_GAP_AND_GO_MOMENTUM", True)
    rule_engine.refresh_cache(force=True)

    # 2. When rule is enabled but time is outside Monday 00:00 window -> returns None
    with patch("nexus_scalp.signals.rule_matrix.datetime") as mock_dt:
        mock_dt.now.return_value = wednesday_10_30
        proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.7, 0.2])
        assert proposal is None

    # 3. Inside Monday 00:00 window with Bullish momentum (probs[1] > probs[2]) -> BUY_MARKET
    with patch("nexus_scalp.signals.rule_matrix.datetime") as mock_dt:
        mock_dt.now.return_value = monday_00_00
        proposal_buy = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.7, 0.2])
        assert proposal_buy is not None
        assert proposal_buy.action == ActionType.BUY_MARKET
        assert proposal_buy.proposed_entry == tick.ask
        assert proposal_buy.stop_loss == round(tick.ask - 1.5, 2)
        assert proposal_buy.take_profit == round(tick.ask + 2.0, 2)
        assert proposal_buy.risk_reward_ratio == 1.33
        assert proposal_buy.confidence == 0.81
        assert proposal_buy.reason_code == "RULE_GAP_AND_GO_MOMENTUM"
        assert proposal_buy.symbol == tick.symbol

    # 4. Inside Monday 00:00 window with Bearish momentum (probs[1] <= probs[2]) -> SELL_MARKET
    with patch("nexus_scalp.signals.rule_matrix.datetime") as mock_dt:
        mock_dt.now.return_value = monday_00_00
        proposal_sell = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.2, 0.7])
        assert proposal_sell is not None
        assert proposal_sell.action == ActionType.SELL_MARKET
        assert proposal_sell.proposed_entry == tick.bid
        assert proposal_sell.stop_loss == round(tick.bid + 1.5, 2)
        assert proposal_sell.take_profit == round(tick.bid - 2.0, 2)
        assert proposal_sell.risk_reward_ratio == 1.33
        assert proposal_sell.confidence == 0.81
        assert proposal_sell.reason_code == "RULE_GAP_AND_GO_MOMENTUM"
        assert proposal_sell.symbol == tick.symbol


# ============================================================================
# PRE-TRADE FILTER RULES TESTS
# ============================================================================


def test_pre_trade_filters_spread_squeeze(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.80
    )  # Spread = 0.60 > 0.25
    fv = MagicMock()

    # Disabled by default -> no filter block
    block_reason = rule_engine.evaluate_pre_trade_filters(tick, fv, None)
    assert block_reason is None

    # Enable and verify spread block
    temp_audit_repo.toggle_trading_rule("RULE_SPREAD_SQUEEZE_ONLY", True)
    rule_engine.refresh_cache(force=True)

    block_reason = rule_engine.evaluate_pre_trade_filters(tick, fv, None)
    assert block_reason == "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"


def test_pre_trade_filters_liquidity_and_macro(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)

    # 1. Liquidity Sweep Confirm
    temp_audit_repo.toggle_trading_rule("RULE_LIQUIDITY_SWEEP_CONFIRM", True)
    rule_engine.refresh_cache(force=True)
    fv = MagicMock()
    fv.liquidity_sweep_signal = 0  # No sweep
    assert (
        rule_engine.evaluate_pre_trade_filters(tick, fv, None)
        == "BLOCKED_BY_RULE_LIQUIDITY_SWEEP_CONFIRM"
    )

    temp_audit_repo.toggle_trading_rule("RULE_LIQUIDITY_SWEEP_CONFIRM", False)

    # 2. AI Macro Alignment
    temp_audit_repo.toggle_trading_rule("RULE_AI_MACRO_ALIGNMENT", True)
    rule_engine.refresh_cache(force=True)
    fv.htf_h4_trend = -0.80  # Heavily bearish
    assert (
        rule_engine.evaluate_pre_trade_filters(tick, fv, None)
        == "BLOCKED_BY_RULE_AI_MACRO_ALIGNMENT"
    )


# ============================================================================
# IN-TRADE EXIT & RISK SAFEGUARD TESTS
# ============================================================================


def test_in_trade_exits_evaluation(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    pos = Position(
        ticket=2001,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2330.0,
        sl=2320.0,
        tp=2350.0,
        profit=50.0,
        magic=888101,
    )

    # 1. Hit & Run Exit
    temp_audit_repo.toggle_trading_rule("RULE_HIT_AND_RUN_EXIT", True)
    rule_engine.refresh_cache(force=True)
    exit_action = rule_engine.evaluate_in_trade_exits(
        pos=pos, holding_duration_sec=250.0, price_current=2335.0, atr=1.0, mfe_profit=50.0
    )
    assert exit_action == {"action": "CLOSE", "reason": "RULE_HIT_AND_RUN_EXIT"}

    temp_audit_repo.toggle_trading_rule("RULE_HIT_AND_RUN_EXIT", False)

    # 2. Zero Drawdown Trail
    temp_audit_repo.toggle_trading_rule("RULE_ZERO_DRAWDOWN_TRAIL", True)
    rule_engine.refresh_cache(force=True)
    # Profit >= 2.0 pips (pip_size = 0.10 -> price at 2330.30 is +3 pips)
    trail_action = rule_engine.evaluate_in_trade_exits(
        pos=pos, holding_duration_sec=30.0, price_current=2330.30, atr=1.0, mfe_profit=30.0
    )
    assert trail_action is not None
    assert trail_action["action"] == "MODIFY_SL"
    assert trail_action["stop_loss"] == 2330.10
    assert trail_action["reason"] == "RULE_ZERO_DRAWDOWN_TRAIL"


def test_risk_and_safeguards(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    temp_audit_repo.toggle_trading_rule("RULE_CONSECUTIVE_LOSS_FREEZE", True)
    temp_audit_repo.toggle_trading_rule("RULE_DAILY_TARGET_LOCK", True)
    temp_audit_repo.toggle_trading_rule("RULE_CORRELATED_DRAWDOWN_CAP", True)
    rule_engine.refresh_cache(force=True)

    # Guarantee in-memory cache activation
    rule_engine._rules_cache["RULE_CONSECUTIVE_LOSS_FREEZE"] = {
        "is_enabled": True,
        "parameters": {},
    }
    rule_engine._rules_cache["RULE_DAILY_TARGET_LOCK"] = {"is_enabled": True, "parameters": {}}
    rule_engine._rules_cache["RULE_CORRELATED_DRAWDOWN_CAP"] = {
        "is_enabled": True,
        "parameters": {},
    }

    # 1. Consecutive loss freeze
    assert (
        rule_engine.evaluate_risk_and_safeguards(10000.0, 10000.0, consecutive_losses=3)
        == "FREEZE_CONSECUTIVE_LOSSES"
    )

    # 2. Daily target lock (equity growth >= 2%)
    assert (
        rule_engine.evaluate_risk_and_safeguards(10250.0, 10000.0, consecutive_losses=0)
        == "DAILY_TARGET_LOCKED"
    )

    # 3. Drawdown cap (drawdown >= 3%)
    assert (
        rule_engine.evaluate_risk_and_safeguards(9600.0, 10000.0, consecutive_losses=0)
        == "BLOCKED_CORRELATED_DRAWDOWN"
    )


# ============================================================================
# INTEGRATION TESTS (POLICY, ORDER MANAGER & API)
# ============================================================================


def test_policy_hooks_blocked_by_filter(temp_audit_repo: AuditRepository) -> None:
    rule_engine = RuleMatrixEngine(audit_repo=temp_audit_repo)
    policy = SignalPolicy(confidence_threshold=0.10, rule_matrix=rule_engine)

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.80)

    fv = MagicMock()
    fv.atr_m1 = 1.0
    fv.dist_to_swing_low_20 = 1.0
    fv.dist_to_swing_high_20 = 1.0
    fv.senkou_span_b = 2334.0
    fv.tenkan_sen = 2334.30
    fv.kijun_sen = 2334.30
    fv.is_above_kumo = False
    fv.is_below_kumo = False
    fv.trend_strength = 0.0

    probs = torch.tensor([[0.1, 0.8, 0.1]])

    # Enable Filter
    temp_audit_repo.toggle_trading_rule("RULE_SPREAD_SQUEEZE_ONLY", True)
    rule_engine.refresh_cache(force=True)

    proposal = policy.evaluate_probabilities(probs, tick, fv, None)
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"


def test_order_manager_hooks_exit(temp_audit_repo: AuditRepository) -> None:
    rule_engine = RuleMatrixEngine(audit_repo=temp_audit_repo)
    adapter = MagicMock()
    adapter.close_position = MagicMock(return_value=True)
    adapter.get_positions = MagicMock(return_value=[])

    om = OrderLifecycleManager(adapter=adapter, audit_repo=temp_audit_repo, rule_matrix=rule_engine)

    pos = Position(
        ticket=1001,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2330.0,
        sl=2320.0,
        tp=2350.0,
        profit=-50.0,
        magic=888101,
    )

    # RULE 13: Time Decay Exit
    temp_audit_repo.toggle_trading_rule("RULE_TIME_DECAY_CHOP_EXIT", True)
    rule_engine.refresh_cache(force=True)

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)

    fv = MagicMock()
    fv.atr_m1 = 1.0
    fv.is_below_kumo = False
    fv.is_above_kumo = False
    fv.choch_bearish = False
    fv.choch_bullish = False
    fv.liquidity_sweep_signal = 0
    fv.tenkan_sen = 2334.30
    fv.kijun_sen = 2334.30

    adapter.get_positions = MagicMock(return_value=[pos])

    # First tick to bootstrap telemetry
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick, feature_vector=fv)

    # Shift entry time back to simulate 5 minutes delay
    om._entry_timestamps[1001] = datetime.now(UTC) - dt_module.timedelta(minutes=5)

    # Second tick to evaluate and trigger decay exit
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick, feature_vector=fv)

    adapter.close_position.assert_called_with(ticket=1001)


def test_api_endpoints(temp_audit_repo: AuditRepository, monkeypatch) -> None:
    # WEB-AUTH-P0: local PAPER fixture — documented auth opt-out (see
    # tests/integration/test_accounting_api.py); the auth gate landed after
    # this test was written and 401s every endpoint without it.
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    engine = MagicMock()
    engine.audit = temp_audit_repo
    app = create_app(engine_ref=engine)
    client = TestClient(app)

    # Get rules
    response = client.get("/api/rules")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 30

    # Toggle a rule via API
    payload = {
        "rule_name": "RULE_FVG_SNIPER_FILL",
        "is_enabled": True,
        "parameters": {"fvg_min_size_pip": 1.2},
    }
    response = client.post("/api/rules/toggle", json=payload)
    assert response.status_code == 200
    assert response.json() == {"success": True}

    # Verify updated database state
    rules = temp_audit_repo.get_trading_rules()
    target_rule = next(r for r in rules if r["rule_name"] == "RULE_FVG_SNIPER_FILL")
    assert target_rule["is_enabled"] is True
    assert json.loads(target_rule["parameters"])["fvg_min_size_pip"] == 1.2


def test_dynamic_hold_score_calculation(temp_audit_repo: AuditRepository) -> None:
    """Verifies that hold_score drops dynamically based on real-time drawdown and spread metrics."""
    adapter = MagicMock()
    om = OrderLifecycleManager(adapter=adapter, audit_repo=temp_audit_repo)

    pos = Position(
        ticket=1002,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2330.0,
        sl=2320.0,
        tp=2350.0,
        profit=-50.0,
        magic=888101,
    )

    fv = MagicMock()
    fv.atr_m1 = 1.0
    fv.is_above_kumo = False
    fv.is_below_kumo = False

    om._entry_timestamps[1002] = datetime.now(UTC)
    om._entry_prices[1002] = 2330.0
    om._entry_sls[1002] = 2320.0
    om._time_in_drawdown_sec[1002] = 0.0

    # 1. Price is at 2330.0 (No loss yet, no time in drawdown)
    score1, reasons = om._calculate_hold_value_score(pos, 2330.0, fv, 0.25, 1.0)
    assert score1 == 100

    # 2. Price drops to 2321.0 (90% of the way to SL)
    # The loss is 9.0 points out of 10.0 initial risk.
    # Penalty 1 uses the CONVEX curve (80 * ratio^1.5) so the score collapses well
    # below the de-risk band long before the emergency horizon:
    #   80 * (0.90 ** 1.5) = 68 -> Score 32.
    score2, reasons = om._calculate_hold_value_score(pos, 2321.0, fv, 0.25, 1.0)
    assert score2 == 32
    assert any("DRAWDOWN_PENALTY" in r for r in reasons)

    # 3. Simulate high time in drawdown (decay)
    # Backdate entry timestamp so elapsed trade duration is 20s and time in drawdown is 16s (80% > 70%)
    # BUG-259: the penalty denominator is the TICK-THREADED now (G3 clock-domain
    # parity) — the wall clock is never consulted, so the tick stamp is explicit.
    om._entry_timestamps[1002] = datetime.now(UTC) - dt_module.timedelta(seconds=20)
    om._time_in_drawdown_sec[1002] = 16.0
    score3, reasons = om._calculate_hold_value_score(
        pos, 2330.0, fv, 0.25, 1.0, now=datetime.now(UTC)
    )
    assert score3 == 70
    assert any("TIME_IN_LOSS_DECAY_PENALTY" in r for r in reasons)


def test_eval_rule_judas_swing_fade(
    rule_engine: RuleMatrixEngine,
) -> None:
    """Directly tests _eval_rule_judas_swing_fade for short, long, boundary, and missing attribute conditions."""
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)

    # Scenario 1: Broke previous high + strong negative displacement -> Short proposal
    fv_short = MagicMock()
    fv_short.broke_previous_high = True
    fv_short.broke_previous_low = False
    fv_short.live_tick_displacement = -0.35

    proposal_short = rule_engine._eval_rule_judas_swing_fade(tick, fv_short)
    assert proposal_short is not None
    assert proposal_short.symbol == "XAUUSD"
    assert proposal_short.action == ActionType.SELL_MARKET
    assert proposal_short.confidence == 0.88
    assert proposal_short.proposed_entry == 2334.20
    assert proposal_short.stop_loss == 2336.00  # 2334.20 + 1.8
    assert proposal_short.take_profit == 2332.00  # 2334.20 - 2.2
    assert proposal_short.risk_reward_ratio == 1.22
    assert proposal_short.reason_code == "RULE_JUDAS_SWING_FADE"

    # Scenario 2: Broke previous low + strong positive displacement -> Long proposal
    fv_long = MagicMock()
    fv_long.broke_previous_high = False
    fv_long.broke_previous_low = True
    fv_long.live_tick_displacement = 0.35

    proposal_long = rule_engine._eval_rule_judas_swing_fade(tick, fv_long)
    assert proposal_long is not None
    assert proposal_long.symbol == "XAUUSD"
    assert proposal_long.action == ActionType.BUY_MARKET
    assert proposal_long.confidence == 0.88
    assert proposal_long.proposed_entry == 2334.40
    assert proposal_long.stop_loss == 2332.60  # 2334.40 - 1.8
    assert proposal_long.take_profit == 2336.60  # 2334.40 + 2.2
    assert proposal_long.risk_reward_ratio == 1.22
    assert proposal_long.reason_code == "RULE_JUDAS_SWING_FADE"

    # Scenario 3: Boundary displacement values (-0.30 and 0.30 exactly) -> None
    fv_bound_short = MagicMock()
    fv_bound_short.broke_previous_high = True
    fv_bound_short.broke_previous_low = False
    fv_bound_short.live_tick_displacement = -0.30
    assert rule_engine._eval_rule_judas_swing_fade(tick, fv_bound_short) is None

    fv_bound_long = MagicMock()
    fv_bound_long.broke_previous_high = False
    fv_bound_long.broke_previous_low = True
    fv_bound_long.live_tick_displacement = 0.30
    assert rule_engine._eval_rule_judas_swing_fade(tick, fv_bound_long) is None

    # Scenario 4: Neither high nor low broken -> None
    fv_none = MagicMock()
    fv_none.broke_previous_high = False
    fv_none.broke_previous_low = False
    fv_none.live_tick_displacement = -0.50
    assert rule_engine._eval_rule_judas_swing_fade(tick, fv_none) is None

    # Scenario 5: Missing attributes on feature vector -> None safely via getattr defaults
    class EmptyFeatureVector:
        pass

    assert rule_engine._eval_rule_judas_swing_fade(tick, EmptyFeatureVector()) is None


def test_pre_trade_entry_bollinger_burst_fade(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)
    fv = MagicMock()
    fv.is_at_extreme_high = True
    fv.is_at_extreme_low = False

    # Disabled by default -> returns None
    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.8, 0.1])
    assert proposal is None

    # Enable rule and evaluate
    temp_audit_repo.toggle_trading_rule("RULE_BOLLINGER_BURST_FADE", True)
    rule_engine.refresh_cache(force=True)

    # 1. Extreme High -> SELL MARKET
    proposal_high = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.8, 0.1])
    assert proposal_high is not None
    assert proposal_high.action == ActionType.SELL_MARKET
    assert proposal_high.symbol == tick.symbol
    assert proposal_high.proposed_entry == tick.bid
    assert proposal_high.stop_loss == round(tick.bid + 1.6, 2)
    assert proposal_high.take_profit == round(tick.bid - 2.2, 2)
    assert proposal_high.confidence == 0.84
    assert proposal_high.risk_reward_ratio == 1.38
    assert proposal_high.reason_code == "RULE_BOLLINGER_BURST_FADE"

    # 2. Extreme Low -> BUY MARKET
    fv.is_at_extreme_high = False
    fv.is_at_extreme_low = True
    proposal_low = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.8, 0.1])
    assert proposal_low is not None
    assert proposal_low.action == ActionType.BUY_MARKET
    assert proposal_low.symbol == tick.symbol
    assert proposal_low.proposed_entry == tick.ask
    assert proposal_low.stop_loss == round(tick.ask - 1.6, 2)
    assert proposal_low.take_profit == round(tick.ask + 2.2, 2)
    assert proposal_low.confidence == 0.84
    assert proposal_low.risk_reward_ratio == 1.38
    assert proposal_low.reason_code == "RULE_BOLLINGER_BURST_FADE"

    # 3. Neither extreme -> returns None
    fv.is_at_extreme_high = False
    fv.is_at_extreme_low = False
    proposal_none = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.1, 0.8, 0.1])
    assert proposal_none is None

    # 4. Attributes missing on fv -> returns None
    fv_empty = object()
    proposal_empty = rule_engine._eval_rule_bollinger_burst_fade(tick, fv_empty)  # type: ignore[arg-type]
    assert proposal_empty is None


def test_pre_trade_entry_news_spike_fade(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    from nexus_scalp.features.regime_classifier import MarketRegimeState, RegimeType

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)
    fv = MagicMock()
    fv.live_tick_displacement = 3.0

    news_regime = MagicMock()
    news_regime.regime_type = RegimeType.MACRO_NEWS_FREEZE

    normal_regime = MagicMock()
    normal_regime.regime_type = RegimeType.RANGING_MEAN_REVERSION

    # 1. Disabled by default -> returns None
    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, news_regime, [0.33, 0.33, 0.34])
    assert proposal is None

    # Enable rule
    temp_audit_repo.toggle_trading_rule("RULE_NEWS_SPIKE_FADE", True)
    rule_engine.refresh_cache(force=True)

    # 2. regime_state is None -> returns None
    assert rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.33, 0.33, 0.34]) is None

    # 3. Not MACRO_NEWS_FREEZE regime -> returns None
    assert rule_engine.evaluate_pre_trade_entry(tick, fv, normal_regime, [0.33, 0.33, 0.34]) is None

    # 4. MACRO_NEWS_FREEZE, but |disp| < 2.5 -> returns None
    fv.live_tick_displacement = 2.4
    assert rule_engine.evaluate_pre_trade_entry(tick, fv, news_regime, [0.33, 0.33, 0.34]) is None

    # 5. Massive positive displacement (disp >= 2.5) -> Fade with SELL_MARKET
    fv.live_tick_displacement = 2.8
    proposal_sell = rule_engine.evaluate_pre_trade_entry(tick, fv, news_regime, [0.33, 0.33, 0.34])
    assert proposal_sell is not None
    assert proposal_sell.action == ActionType.SELL_MARKET
    assert proposal_sell.proposed_entry == tick.bid
    assert proposal_sell.stop_loss == round(tick.bid + 3.0, 2)
    assert proposal_sell.take_profit == round(tick.bid - 4.5, 2)
    assert proposal_sell.reason_code == "RULE_NEWS_SPIKE_FADE"
    assert proposal_sell.confidence == 0.85
    assert proposal_sell.risk_reward_ratio == 1.50

    # 6. Massive negative displacement (disp <= -2.5) -> Fade with BUY_MARKET
    fv.live_tick_displacement = -3.2
    proposal_buy = rule_engine.evaluate_pre_trade_entry(tick, fv, news_regime, [0.33, 0.33, 0.34])
    assert proposal_buy is not None
    assert proposal_buy.action == ActionType.BUY_MARKET
    assert proposal_buy.proposed_entry == tick.ask
    assert proposal_buy.stop_loss == round(tick.ask - 3.0, 2)
    assert proposal_buy.take_profit == round(tick.ask + 4.5, 2)
    assert proposal_buy.reason_code == "RULE_NEWS_SPIKE_FADE"
    assert proposal_buy.confidence == 0.85
    assert proposal_buy.risk_reward_ratio == 1.50


# ============================================================================
# PRE-TRADE FILTER RULES TESTS
# ============================================================================
