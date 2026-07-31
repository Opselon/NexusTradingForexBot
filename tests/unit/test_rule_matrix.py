# ruff: noqa: PLR2004
import datetime as dt_module
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Generator
from unittest.mock import MagicMock

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
def temp_audit_repo() -> Generator[AuditRepository, None, None]:
    """Fixture providing a fresh unique SQLite database for rules testing."""
    db_name = f"test_rules_audit_{uuid.uuid4().hex}.db"
    repo = AuditRepository(f"sqlite:///{db_name}")
    yield repo
    repo.close()
    for ext in ("", "-wal", "-shm"):
        file_path = db_name + ext
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


@pytest.fixture
def rule_engine(temp_audit_repo: AuditRepository) -> RuleMatrixEngine:
    return RuleMatrixEngine(audit_repo=temp_audit_repo)


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

    # Refresh Rule Matrix Cache and verify state
    rule_engine.refresh_cache()
    assert rule_engine.is_enabled(rule_name) is True

    # Toggle a rule with parameters update
    custom_params = {"fvg_min_size_pip": 2.5}
    success = temp_audit_repo.toggle_trading_rule(rule_name, True, json.dumps(custom_params))
    assert success is True
    rule_engine.refresh_cache()
    assert rule_engine.get_params(rule_name) == custom_params


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

    # Enable and verify custom entry proposal
    temp_audit_repo.toggle_trading_rule("RULE_FVG_SNIPER_FILL", True)
    rule_engine.refresh_cache()

    proposal = rule_engine.evaluate_pre_trade_entry(tick, fv, None, [0.99, 0.005, 0.005])
    assert proposal is not None
    assert proposal.action == ActionType.BUY_MARKET
    assert proposal.reason_code == "RULE_FVG_SNIPER_FILL"


def test_pre_trade_filters_spread_squeeze(
    rule_engine: RuleMatrixEngine,
    temp_audit_repo: AuditRepository,
) -> None:
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.80)
    fv = MagicMock()

    # Disabled by default -> no filter block
    block_reason = rule_engine.evaluate_pre_trade_filters(tick, fv, None)
    assert block_reason is None

    # Enable and verify spread block
    temp_audit_repo.toggle_trading_rule("RULE_SPREAD_SQUEEZE_ONLY", True)
    rule_engine.refresh_cache()

    block_reason = rule_engine.evaluate_pre_trade_filters(tick, fv, None)
    assert block_reason == "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"


def test_policy_hooks_blocked_by_filter(temp_audit_repo: AuditRepository) -> None:
    rule_engine = RuleMatrixEngine(audit_repo=temp_audit_repo)
    policy = SignalPolicy(confidence_threshold=0.10, rule_matrix=rule_engine)

    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.80)
    fv = MagicMock()
    probs = torch.tensor([[0.1, 0.8, 0.1]])

    # Enable Filter
    temp_audit_repo.toggle_trading_rule("RULE_SPREAD_SQUEEZE_ONLY", True)
    rule_engine.refresh_cache()

    proposal = policy.evaluate_probabilities(probs, tick, fv, None)
    assert proposal.action == ActionType.NO_TRADE
    assert proposal.reason_code == "BLOCKED_BY_RULE_SPREAD_SQUEEZE_ONLY"


def test_order_manager_hooks_exit(temp_audit_repo: AuditRepository) -> None:
    rule_engine = RuleMatrixEngine(audit_repo=temp_audit_repo)
    adapter = MagicMock()
    # Mock close_position to return True
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
        magic=888101
    )

    # RULE 13: Time Decay Exit
    temp_audit_repo.toggle_trading_rule("RULE_TIME_DECAY_CHOP_EXIT", True)
    rule_engine.refresh_cache()

    # Active monitoring evaluates exit
    tick = TickData(symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2334.20, ask=2334.40)

    # Pre-populate all properties on fv mock to prevent comparison errors
    fv = MagicMock()
    fv.atr_m1 = 1.0
    fv.is_below_kumo = False
    fv.is_above_kumo = False
    fv.choch_bearish = False
    fv.choch_bullish = False
    fv.liquidity_sweep_signal = 0
    fv.tenkan_sen = 2334.30
    fv.kijun_sen = 2334.30

    # Set the mock get_positions to return the active position
    adapter.get_positions = MagicMock(return_value=[pos])

    # First tick to bootstrap the position tracking telemetry
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick, feature_vector=fv)

    # Shift entry time back to simulate 5 minutes delay
    om._entry_timestamps[1001] = datetime.now(UTC) - dt_module.timedelta(minutes=5)

    # Second tick to evaluate and trigger decay exit
    om.manage_active_positions(symbol="XAUUSD", current_tick=tick, feature_vector=fv)

    # Verify adapter.close_position was called because of time decay
    adapter.close_position.assert_called_with(ticket=1001)


def test_api_endpoints(temp_audit_repo: AuditRepository) -> None:
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
        "parameters": {"fvg_min_size_pip": 1.2}
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
        magic=888101
    )

    # Mock features and smart metrics
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
    # Penalty 1 should be ratio (0.90) * 40 = 36 points.
    score2, reasons = om._calculate_hold_value_score(pos, 2321.0, fv, 0.25, 1.0)
    assert score2 == 64
    assert any("DRAWDOWN_PENALTY" in r for r in reasons)

    # 3. Simulate high time in drawdown (decay)
    om._time_in_drawdown_sec[1002] = 10.0
    # ratio = 10.0 / ~0.01 > 0.70 -> Penalty 2 (-30) applied
    score3, reasons = om._calculate_hold_value_score(pos, 2330.0, fv, 0.25, 1.0)
    assert score3 == 70
    assert any("TIME_IN_LOSS_DECAY_PENALTY" in r for r in reasons)
