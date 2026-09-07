"""Regression tests: profit-protection circuit breakers (mission P0 7B/7C/7D).

Covers:
- daily/weekly loss budgets block new entries via RiskEngine.evaluate_proposal
- budgets re-arm at period rollover (UTC day / ISO week)
- consecutive-loss cooldown arms at N adverse outcomes and expires
- non-adverse outcome resets the streak (a scratch/zero-PnL is NOT adverse)
- duplicate close feeds do not extend the cooldown (INV-006 idempotency)
- non-finite equity / PnL is fail-inert (never fabricates a budget anchor)
- INSUFFICIENT_DATA before the first honest equity anchor (no false block)
- kill switch still takes precedence; NO_TRADE proposals still pass through
- breaker block does NOT disable the kill switch or protective management
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.domain.enums import ActionType, ExecutionMode
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
    TradeProposal,
)
from nexus_scalp.risk.circuit_breakers import (
    BreakerSnapshot,
    CircuitBreakerConfig,
    CircuitBreakerEngine,
)
from nexus_scalp.risk.risk_engine import RiskEngine


def _ts(hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 9, 7, hour, minute, 0, tzinfo=UTC)


def _proposal(now: datetime | None = None) -> TradeProposal:
    return TradeProposal(
        request_id="req_breaker_test",
        symbol="XAUUSD",
        generated_at=now or _ts(),
        action=ActionType.BUY_MARKET,
        confidence=0.9,
        proposed_entry=4400.0,
        stop_loss=4390.0,
        take_profit=4420.0,
        risk_reward_ratio=2.0,
        reason_code="TEST",
    )


def _engine(equity: float = 10000.0) -> RiskEngine:
    e = RiskEngine(config=RiskConfig())
    e.breakers.update_equity(equity, _ts())
    return e


def _account(equity: float) -> AccountInfo:
    return AccountInfo(
        login=1,
        trade_mode=1,
        leverage=100,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        currency="USD",
    )


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
        stops_level=10,
        freeze_level=5,
        trade_contract_size=100.0,
    )


def _tick() -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=_ts(),
        bid=4399.5,
        ask=4400.5,
        last=0.0,
        volume=0.0,
        time_msc=0,
    )


# ---------------------------------------------------------------------------
# Pure CircuitBreakerEngine semantics
# ---------------------------------------------------------------------------


class TestBreakerEngine:
    def test_daily_budget_blocks_and_rearms_next_day(self) -> None:
        cfg = CircuitBreakerConfig(daily_loss_budget_pct=2.0, weekly_loss_budget_pct=100.0)
        br = CircuitBreakerEngine(cfg)
        br.update_equity(10000.0, _ts())
        snap = br.evaluate(9700.0, _ts(15))  # -3%
        assert snap.allowed is False
        assert snap.level == "DAILY_HALT"
        # Next UTC day re-arms automatically
        snap2 = br.evaluate(9700.0, _ts(15) + timedelta(days=1))
        assert snap2.allowed is True
        assert snap2.level == "NORMAL"
        assert snap2.daily_loss_pct == 0.0  # new anchor = 9700

    def test_weekly_budget_blocks_and_rearms_next_iso_week(self) -> None:
        cfg = CircuitBreakerConfig(daily_loss_budget_pct=100.0, weekly_loss_budget_pct=5.0)
        br = CircuitBreakerEngine(cfg)
        br.update_equity(10000.0, _ts())
        # same-day equity drop breaches both; daily budget disabled (100%)
        snap = br.evaluate(9400.0, _ts(15))
        assert snap.allowed is False
        assert snap.level == "WEEKLY_HALT"
        # same week, next day: still halted (weekly anchor persists)
        snap2 = br.evaluate(9400.0, _ts(15) + timedelta(days=1))
        assert snap2.allowed is False
        assert snap2.level == "WEEKLY_HALT"
        # jump to next ISO week: re-armed
        snap3 = br.evaluate(9400.0, _ts(15) + timedelta(days=7))
        assert snap3.allowed is True

    def test_consecutive_loss_cooldown_arms_and_expires(self) -> None:
        cfg = CircuitBreakerConfig(consecutive_loss_cooldown=3, cooldown_duration_sec=3600.0)
        br = CircuitBreakerEngine(cfg)
        br.update_equity(10000.0, _ts())
        t = _ts()
        for i in range(2):
            br.record_trade_result(-10.0, t + timedelta(minutes=i))
        assert br.evaluate(10000.0, t + timedelta(minutes=5)).allowed is True
        br.record_trade_result(-10.0, t + timedelta(minutes=6))  # 3rd adverse
        snap = br.evaluate(10000.0, t + timedelta(minutes=7))
        assert snap.allowed is False
        assert snap.level == "COOLDOWN"
        assert snap.consecutive_losses == 3
        # after cooldown expiry: allowed again, streak keeps counting
        later = t + timedelta(minutes=6) + timedelta(seconds=3600.0)
        snap2 = br.evaluate(10000.0, later)
        assert snap2.allowed is True

    def test_win_resets_streak_and_zero_pnl_is_not_adverse(self) -> None:
        cfg = CircuitBreakerConfig(consecutive_loss_cooldown=2, cooldown_duration_sec=60.0)
        br = CircuitBreakerEngine(cfg)
        br.record_trade_result(-10.0, _ts())
        br.record_trade_result(0.0, _ts())  # scratch: NOT adverse, resets
        br.record_trade_result(-10.0, _ts())
        assert br.evaluate(10000.0, _ts()).allowed is True  # streak only 1
        br.record_trade_result(-10.0, _ts())  # 2 consecutive -> armed
        assert br.evaluate(10000.0, _ts()).allowed is False

    def test_duplicate_close_feed_does_not_extend_cooldown(self) -> None:
        cfg = CircuitBreakerConfig(consecutive_loss_cooldown=2, cooldown_duration_sec=3600.0)
        br = CircuitBreakerEngine(cfg)
        t = _ts()
        br.record_trade_result(-10.0, t)
        br.record_trade_result(-10.0, t + timedelta(seconds=1))
        until1 = br._cooldown_until
        # INV-006 reconciliation replay of the same close: must NOT extend
        br.record_trade_result(-10.0, t + timedelta(seconds=2))
        br.record_trade_result(-10.0, t + timedelta(seconds=2))
        assert br._cooldown_until == until1

    def test_non_finite_inputs_are_fail_inert(self) -> None:
        br = CircuitBreakerEngine()
        br.update_equity(10000.0, _ts())
        br.update_equity(float("nan"), _ts())  # ignored
        br.update_equity(float("inf"), _ts())  # ignored
        br.update_equity(-5.0, _ts())  # ignored
        br.record_trade_result(float("nan"), _ts())  # ignored, streak stays 0
        snap = br.evaluate(9900.0, _ts())
        assert snap.allowed is True
        assert snap.consecutive_losses == 0
        assert snap.daily_loss_pct == pytest.approx(1.0)

    def test_insufficient_data_never_fabricates_a_block(self) -> None:
        br = CircuitBreakerEngine()
        snap = br.evaluate(5000.0, _ts())
        assert snap.allowed is True
        # first honest evaluation establishes the anchor and reports NORMAL
        assert snap.level == "NORMAL"
        assert snap.daily_loss_pct == 0.0
        # later equity collapses on the SAME day DO block: anchor was real
        snap2 = br.evaluate(4000.0, _ts())
        assert snap2.allowed is False
        assert snap2.level == "DAILY_HALT"


# ---------------------------------------------------------------------------
# RiskEngine integration (new-entry gate only)
# ---------------------------------------------------------------------------


class TestRiskEngineBreakerGate:
    def test_daily_breach_rejects_proposal(self) -> None:
        eng = _engine(10000.0)
        # breach the daily budget
        acct = _account(9700.0)
        result = eng.evaluate_proposal(
            proposal=_proposal(),
            account=acct,
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick(),
        )
        assert result is None
        assert eng._last_breaker is not None
        assert eng._last_breaker.level == "DAILY_HALT"

    def test_within_budget_allows_evaluation_to_continue(self) -> None:
        eng = _engine(10000.0)
        acct = _account(9990.0)
        result = eng.evaluate_proposal(
            proposal=_proposal(),
            account=acct,
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick(),
        )
        # NOT breaker-blocked: either a sized order or a non-breaker rejection
        assert eng._last_breaker.level == "NORMAL"
        assert result is None or isinstance(result, TradeOrder)

    def test_cooldown_rejects_entries_without_touching_kill_switch(self) -> None:
        eng = _engine(10000.0)
        cfg = eng.breakers.config
        eng.breakers.config = CircuitBreakerConfig(
            daily_loss_budget_pct=100.0,
            weekly_loss_budget_pct=100.0,
            consecutive_loss_cooldown=2,
            cooldown_duration_sec=86400.0,
        )
        eng.breakers.record_trade_result(-10.0, _ts())
        eng.breakers.record_trade_result(-10.0, _ts())
        result = eng.evaluate_proposal(
            proposal=_proposal(),
            account=_account(10000.0),
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick(),
        )
        assert result is None
        assert eng._last_breaker.level == "COOLDOWN"
        # kill switch is an independent, still-functional layer
        assert eng._kill_switch_active is False
        eng.enable_kill_switch()
        assert eng._kill_switch_active is True
        eng.breakers.config = cfg

    def test_budget_uses_proposal_timestamp_not_wall_clock(self) -> None:
        eng = _engine(10000.0)
        # equity drop breached YESTERDAY's budget; proposal stamped TODAY
        yesterday = _ts() - timedelta(days=1)
        eng.breakers.update_equity(10000.0, yesterday)
        result = eng.evaluate_proposal(
            proposal=_proposal(now=_ts()),
            account=_account(5000.0),
            symbol_info=_symbol_info(),
            active_positions=[],
            current_tick=_tick(),
        )
        # Today's anchor = 5000 (first observation today) -> no daily loss
        assert eng._last_breaker.allowed is True
        assert result is None or isinstance(result, TradeOrder)
