"""Unit tests for compute_advanced_metrics (accounting advanced risk math)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.accounting.aggregation import compute_advanced_metrics
from nexus_scalp.accounting.models import (
    ExitClassification,
    TradeOutcome,
    TradeRecord,
)


def _trade(
    ticket: int,
    net_pnl: float,
    closed: bool = True,
    realized_r: float | None = None,
) -> TradeRecord:
    now = datetime.now(UTC)
    return TradeRecord(
        ticket=ticket,
        symbol="XAUUSD",
        direction="BUY",
        volume=0.1,
        entry_price=2000.0,
        exit_price=2000.0 + net_pnl,
        gross_pnl=net_pnl,
        commission=0.0,
        swap=0.0,
        net_pnl=net_pnl,
        opened_at=now - timedelta(hours=1),
        closed_at=now if closed else None,
        duration_sec=3600.0,
        exit_mechanism_raw="TP",
        exit_classification=ExitClassification.TAKE_PROFIT,
        outcome=TradeOutcome.WIN if net_pnl > 0 else TradeOutcome.LOSS,
        risk_free_state=False,
        was_sl_modified=False,
        initial_sl=1990.0,
        final_sl=1990.0,
        mae_points=0.0,
        mfe_points=0.0,
        mae_usd=0.0,
        mfe_usd=0.0,
        realized_r=realized_r,
    )


class TestAdvancedMetrics:
    def test_empty_inputs(self):
        m = compute_advanced_metrics([])
        assert m["sample_trades"] == 0
        assert m["net_pnl"] is None
        assert m["profit_factor"] is None
        assert m["sharpe_ratio"] is None

    def test_basic_stats(self):
        trades = [
            _trade(1, 100.0, realized_r=1.0),
            _trade(2, -50.0, realized_r=-0.5),
            _trade(3, 50.0, realized_r=0.5),
            _trade(4, 0.0),
        ]
        m = compute_advanced_metrics(trades)
        assert m["sample_trades"] == 4
        assert m["net_pnl"] == 100.0
        assert m["win_rate"] == 66.67  # 2 wins / 3 decided
        assert m["profit_factor"] == 3.0  # 150 / 50
        assert m["expectancy"] == 33.33  # 100 / 3
        assert m["average_win"] == 75.0
        assert m["average_loss"] == -50.0
        assert m["payoff_ratio"] == 1.5
        assert m["avg_r"] == 0.3333  # (1.0 - 0.5 + 0.5) / 3
        assert m["max_consecutive_wins"] == 1
        assert m["max_consecutive_losses"] == 1

    def test_streaks(self):
        trades = [
            _trade(1, 10.0),
            _trade(2, 10.0),
            _trade(3, -5.0),
            _trade(4, -5.0),
            _trade(5, -5.0),
            _trade(6, 10.0),
        ]
        m = compute_advanced_metrics(trades)
        assert m["max_consecutive_wins"] == 2
        assert m["max_consecutive_losses"] == 3

    def test_sqn_requires_r_sample(self):
        # Only 2 trades with R -> SQN stays None (needs >= 5)
        trades = [_trade(1, 10.0, realized_r=1.0), _trade(2, -5.0, realized_r=-0.5)]
        m = compute_advanced_metrics(trades)
        assert m["sqn"] is None
        # 6 trades with R -> SQN computes
        trades6 = [
            _trade(i, 10.0 if i % 2 else -5.0, realized_r=1.0 if i % 2 else -0.5) for i in range(6)
        ]
        m6 = compute_advanced_metrics(trades6)
        assert m6["sqn"] is not None

    def test_open_trades_excluded(self):
        trades = [_trade(1, 10.0, closed=True), _trade(2, 999.0, closed=False)]
        m = compute_advanced_metrics(trades)
        assert m["sample_trades"] == 1
        assert m["net_pnl"] == 10.0

    def test_equity_curve_risk(self):
        trades = [_trade(i, 10.0 if i % 2 else -5.0) for i in range(10)]
        eq = [{"timestamp": f"t{i}", "equity": 10000.0 + i * 10.0} for i in range(20)]
        m = compute_advanced_metrics(trades, equity_points=eq)
        assert m["equity_volatility_pct"] is not None
        assert m["sharpe_ratio"] is not None
        # recovery_factor / calmar need drawdown > 0 (monotonic rising curve -> None)
        assert m["recovery_factor"] is None

    def test_none_when_no_losses(self):
        trades = [_trade(1, 10.0), _trade(2, 5.0)]
        m = compute_advanced_metrics(trades)
        assert m["profit_factor"] is None  # no losses -> undefined
        assert m["win_rate"] == 100.0
        assert m["payoff_ratio"] is None

    def test_blanket_no_crash_on_single(self):
        m = compute_advanced_metrics([_trade(1, 10.0)])
        assert m["sample_trades"] == 1
        assert m["net_pnl"] == 10.0
