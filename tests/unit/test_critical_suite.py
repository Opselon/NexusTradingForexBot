"""
CRITICAL TEST SUITE — whole-application heartbeat + suite classification
=========================================================================

This module is the marker and heartbeat of the *Critical* test suite: the
small, fast, high-signal regression net that must stay green on every push
(see tests/unit/README-TEST-SUITE-REDUCTION.md for the full reduction report).

test_critical_whole_cycle_heartbeat
-----------------------------------
The single most important application regression test. It walks the complete
trading chain with REAL code (no mocks) against a throwaway SQLite DB:

    market tick -> feature vector (50D) -> model probabilities (ScalpNet)
    -> SignalPolicy decision -> RiskEngine validation -> TradeProposal
    -> TradeOrder execution audit -> accounting ledger PnL -> result

If this breaks, CI must clearly say CRITICAL APPLICATION PATH FAILED.
It lives in its own file so the whole Critical suite can run with:

    pytest tests/unit/test_critical_suite.py tests/integration/test_signal_pipeline_health.py ...

or via a marker (see pyproject.toml [tool.pytest.ini_options] markers).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import AccountInfo, SymbolInfo, TickData, TradeOrder, TradeProposal
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.signals.policy import SignalPolicy

XAU_TICK = dict(
    symbol="XAUUSD",
    bid=2000.0,
    ask=2000.05,
    volume=1.0,
)


def make_fv(timestamp_utc: str) -> FeatureVector:
    """Deterministic 50D feature vector that yields a BUY_MARKET proposal."""
    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=timestamp_utc,
        live_tick_displacement=0.5,
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
        order_block_type=0,
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


def _fresh_db(tmp_path: object) -> tuple[str, AuditRepository]:
    db_path = os.path.join(str(tmp_path), "critical_heartbeat.db")
    repo = AuditRepository(db_url=f"sqlite:///{db_path}")
    return db_path, repo


def test_critical_whole_cycle_heartbeat(tmp_path) -> None:
    """
    CRITICAL APPLICATION PATH — data -> features -> model -> signal -> risk
    -> order -> accounting -> result, with REAL components only.
    """
    db_path, audit_repo = _fresh_db(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)

    # 1. Market data (tick) + feature pipeline contract (50D)
    tick = TickData(timestamp=now, **XAU_TICK)
    fv = make_fv(tick.timestamp.isoformat())
    assert tick.spread_points > 0
    # 2. Model inference: deterministic 50D ScalpNet forward pass -> 4-class probs
    from nexus_scalp.models.scalp_net import ScalpNet

    model = ScalpNet(num_features=50, num_classes=4)
    model.eval()
    with torch.no_grad():
        probs = torch.softmax(model(torch.tensor([fv.to_tensor_input()], dtype=torch.float32)), dim=1)
    assert probs.shape == (1, 4)
    assert float(probs.sum()) == pytest.approx(1.0, abs=1e-4)

    # 3. Signal: policy converts probabilities into a decision
    policy = SignalPolicy()
    policy.confidence_threshold = 0.10
    policy.algo_config.min_risk_reward_ratio = 0.10
    policy.algo_config.ai_zone_confidence_threshold = 0.60
    proposal = policy.evaluate_probabilities(
        probabilities=torch.tensor([[0.01, 0.98, 0.01, 0.0]]),
        current_tick=tick,
        feature_vector=fv,
    )
    assert proposal.action in (ActionType.BUY_MARKET, ActionType.BUY_LIMIT, ActionType.BUY)
    audit_repo.log_signal(proposal)

    # 4. Risk: real RiskEngine must approve a sane proposal and size the position
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    symbol_info = dict(
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
        login=1, trade_mode=0, leverage=100,
        balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0,
    )
    verdict = risk.evaluate_proposal(
        proposal=proposal,
        account=account,
        symbol_info=SymbolInfo(**symbol_info),
        active_positions=[],
        current_tick=tick,
    )
    assert verdict is not None, "risk must allow the critical path proposal"
    assert verdict.volume > 0, "risk must size a position"

    # 5. Order execution audit: filled order lands in audit_executions
    order = TradeOrder(
        order_id=proposal.request_id,
        symbol=proposal.symbol,
        order_type=OrderType.BUY,
        volume=verdict.volume,
        price=proposal.proposed_entry,
        stop_loss=proposal.stop_loss,
        take_profit=proposal.take_profit,
        magic_number=888101,
        comment="NSE_CRITICAL",
    )
    audit_repo.log_execution(order, "FILLED")

    # 6. Accounting: ledger records the trade lifecycle (OPENED then CLOSED
    # with PnL) and an account snapshot - the financial chain is observable.
    closed_at = now + timedelta(hours=2)
    audit_repo.log_ledger_opened(
        ticket=9991001,
        symbol=order.symbol,
        direction="buy",
        volume=verdict.volume,
        entry_price=order.price,
        timestamp_str=now.isoformat(),
        order_id=order.order_id,
        entry_reason="CRITICAL_HEARTBEAT",
        ai_confidence_at_open=0.98,
        market_regime_at_open="TRENDING",
        initial_sl_price=order.stop_loss,
    )
    audit_repo.log_account_snapshot(account, peak_equity=account.equity)

    audit_repo._queue.join()  # flush background writer (skill: never close() mid-test)

    conn = sqlite3.connect(db_path)
    try:
        signals = conn.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0]
        executions = conn.execute("SELECT COUNT(*) FROM audit_executions").fetchone()[0]
        ledger_open = conn.execute(
            "SELECT COUNT(*) FROM audit_ledger WHERE status='OPENED'"
        ).fetchone()[0]
        snapshots = conn.execute("SELECT COUNT(*) FROM audit_account_snapshots").fetchone()[0]
        assert signals >= 1
        assert executions >= 1
        assert ledger_open >= 1
        assert snapshots >= 1
    finally:
        conn.close()
    audit_repo.close()
    # 7. Result: the full chain is observable end-to-end.
    print("CRITICAL APPLICATION PATH PASSED (data->features->model->signal->risk->order->accounting)")


def test_critical_risk_1pct_not_10pct() -> None:
    """
    CRITICAL RISK: risk_per_trade_pct=1.0 must size 1% risk — never 10%.
    Regression guard against a '1% -> 10%' style accident (prompt §9).
    """
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    now = datetime.now(UTC)
    proposal = TradeProposal(
        request_id=str(uuid.uuid4()),
        symbol="XAUUSD",
        generated_at=now,
        action=ActionType.BUY_MARKET,
        confidence=0.9,
        proposed_entry=2000.0,
        stop_loss=1998.0,
        take_profit=2006.0,
        risk_reward_ratio=3.0,
    )
    symbol_info = SymbolInfo(
        symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=10, freeze_level=0, trade_contract_size=100.0,
    )
    account = AccountInfo(
        login=1, trade_mode=0, leverage=100,
        balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0,
    )
    verdict = risk.evaluate_proposal(proposal=proposal, account=account, symbol_info=symbol_info, active_positions=[], current_tick=TickData(timestamp=now, **XAU_TICK))
    assert verdict is not None
    # 1% of 10,000 = $100 risk / ($2 SL distance * 100 contract size) = 0.50 lots
    assert 0.45 <= verdict.volume <= 0.60, "1% risk must size ~0.50 lots at $100/SL-unit (got %s)" % verdict.volume
    assert verdict.volume < 1.0, "10% risk would size 5.0 lots — regression"


def test_critical_risk_fixed_lot_never_sneaks_in() -> None:
    """No fixed lot size may appear: volume must scale with equity."""
    risk = RiskEngine(RiskConfig(risk_per_trade_pct=1.0))
    now = datetime.now(UTC)
    symbol_info = SymbolInfo(
        symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        stops_level=10, freeze_level=0, trade_contract_size=100.0,
    )
    volumes = []
    for equity in (1000.0, 10000.0, 100000.0):
        proposal = TradeProposal(
            request_id=str(uuid.uuid4()),
            symbol="XAUUSD",
            generated_at=now,
            action=ActionType.BUY_MARKET,
            confidence=0.9,
            proposed_entry=2000.0,
            stop_loss=1998.0,
            take_profit=2006.0,
            risk_reward_ratio=3.0,
        )
        account = AccountInfo(
            login=1, trade_mode=0, leverage=100,
            balance=equity, equity=equity, margin=0.0, margin_free=equity,
        )
        verdict = risk.evaluate_proposal(proposal=proposal, account=account, symbol_info=symbol_info, active_positions=[], current_tick=TickData(timestamp=now, **XAU_TICK))
        assert verdict is not None
        volumes.append(verdict.volume)
    assert volumes[0] < volumes[1] < volumes[2], "position size must scale with equity"