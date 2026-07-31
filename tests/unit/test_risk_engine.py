"""
Unit Tests - Risk Engine Invariants
===================================
Verifies position sizing math and fail-closed security guards.
"""

from datetime import UTC, datetime

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

    account = AccountInfo(login=123, trade_mode=0, leverage=100, balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0)
    symbol_info = SymbolInfo(symbol="EURUSD", digits=5, point=0.00001, tick_size=0.00001, tick_value=1.0, volume_min=0.01, volume_max=100.0, volume_step=0.01, stops_level=10, freeze_level=0, trade_contract_size=100000.0)
    tick = TickData(symbol="EURUSD", timestamp=now, bid=1.0850, ask=1.0851)

    order = engine.evaluate_proposal(proposal, account, symbol_info, [], tick)
    assert order is None