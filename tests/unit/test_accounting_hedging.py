import os
import shutil
import tempfile
from datetime import UTC, datetime

import torch

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import AccountInfo, Position, SymbolInfo, TickData, TradeOrder
from nexus_scalp.features.regime_classifier import (
    MarketRegimeState,
    RecommendedExecutionType,
    RegimeType,
)


class MockMT5Port:
    """Mock Direct MT5 / Broker Port for test isolation."""

    def __init__(self) -> None:
        self.positions: list[Position] = []
        self.sent_orders: list[TradeOrder] = []
        self.closed_deals: list[dict] = []

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            login=123456,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=0.01,
            tick_size=0.01,
            tick_value=1.0,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            stops_level=10,
            freeze_level=0,
            trade_contract_size=100.0,
        )

    def get_last_tick(self, symbol: str) -> TickData:
        return TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=2330.00,
            ask=2330.20,
            volume=1.0,
        )

    def get_positions(self, symbol: str) -> list[Position]:
        return [p for p in self.positions if p.symbol == symbol]

    def send_order(self, order: TradeOrder) -> bool:
        self.sent_orders.append(order)
        return True

    def get_closed_deals_history(self, symbol: str, hours_back: int) -> list[dict]:
        return self.closed_deals

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        for idx, pos in enumerate(self.positions):
            if pos.ticket == ticket:
                self.positions[idx] = Position(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    type=pos.type,
                    volume=pos.volume,
                    price_open=pos.price_open,
                    sl=stop_loss,
                    tp=take_profit,
                    profit=pos.profit,
                    magic=pos.magic,
                )
                return True
        return False


def test_audit_ledger_recording_and_metrics() -> None:
    """Verifies that the audit financial ledger records trades and correctly computes accounting metrics."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_audit.db")
    db_url = f"sqlite:///{db_path}"

    try:
        # Flush interval very low to write immediately in worker
        audit = AuditRepository(db_url=db_url, flush_interval_sec=0.1)

        # Log some snapshots to test drawdown calculations
        acc1 = AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
        )
        acc2 = AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=11000.0,
            margin=0.0,
            margin_free=11000.0,
        )  # peak
        acc3 = AccountInfo(
            login=123,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=9500.0,
            margin=0.0,
            margin_free=9500.0,
        )  # drawdown of 13.64%

        # Directly log snapshots
        audit.log_account_snapshot(acc1, 10000.0)
        # Force wait / sleep or manual write to bypass throttling for snapshot timing
        audit._last_snapshot_time = 0.0  # Force override throttling
        audit.log_account_snapshot(acc2, 11000.0)
        audit._last_snapshot_time = 0.0
        audit.log_account_snapshot(acc3, 11000.0)

        # Log 3 closed trades: 2 wins, 1 loss
        now_str = datetime.now(UTC).isoformat()

        # Trade 1: Buy Win ($200 profit)
        audit.log_ledger_opened(
            ticket=101,
            symbol="XAUUSD",
            direction="BUY",
            volume=1.0,
            entry_price=2300.00,
            timestamp_str=now_str,
        )
        audit.log_ledger_closed(
            ticket=101,
            symbol="XAUUSD",
            direction="BUY",
            volume=1.0,
            entry_price=2300.00,
            exit_price=2302.00,
            status="CLOSED_TP",
            pnl=200.0,
            commission=-2.0,
            swap=0.0,
            duration_sec=45.0,
            timestamp_str=now_str,
        )

        # Trade 2: Sell Win ($150 profit)
        audit.log_ledger_opened(
            ticket=102,
            symbol="XAUUSD",
            direction="SELL",
            volume=0.5,
            entry_price=2305.00,
            timestamp_str=now_str,
        )
        audit.log_ledger_closed(
            ticket=102,
            symbol="XAUUSD",
            direction="SELL",
            volume=0.5,
            entry_price=2305.00,
            exit_price=2302.00,
            status="CLOSED_TP",
            pnl=150.0,
            commission=-1.0,
            swap=-0.5,
            duration_sec=60.0,
            timestamp_str=now_str,
        )

        # Trade 3: Buy Loss (-$100 profit)
        audit.log_ledger_opened(
            ticket=103,
            symbol="XAUUSD",
            direction="BUY",
            volume=1.0,
            entry_price=2310.00,
            timestamp_str=now_str,
        )
        audit.log_ledger_closed(
            ticket=103,
            symbol="XAUUSD",
            direction="BUY",
            volume=1.0,
            entry_price=2310.00,
            exit_price=2309.00,
            status="CLOSED_SL",
            pnl=-100.0,
            commission=-2.0,
            swap=0.0,
            duration_sec=30.0,
            timestamp_str=now_str,
        )

        # Wait for worker thread to flush queues to DB
        import time

        time.sleep(1.0)
        audit.close()

        # Reopen to read data synchronously
        audit_reader = AuditRepository(db_url=db_url)
        metrics = audit_reader.get_account_performance_metrics()

        assert metrics["total_trades"] == 3
        # Win rate should be (2 wins / 3 trades) * 100 = 66.67%
        assert abs(metrics["win_rate"] - 66.67) < 0.1
        # Gross profit: (200-2) + (150-1-0.5) = 198 + 148.5 = 346.5
        # Gross loss: abs(-100-2) = 102.0
        # Profit factor: 346.5 / 102.0 = 3.397 -> ~3.40
        assert abs(metrics["profit_factor"] - 3.40) < 0.05
        # Drawdown: peak was 11000, min was 9500 -> ((11000 - 9500) / 11000) * 100 = 13.64%
        assert abs(metrics["max_drawdown"] - 13.64) < 0.1

        # Test pagination
        trades_page1 = audit_reader.get_ledger_trades(limit=2, offset=0)
        assert len(trades_page1) == 2
        assert trades_page1[0]["ticket"] == 103  # Descending ticket order

        trades_page2 = audit_reader.get_ledger_trades(limit=2, offset=2)
        assert len(trades_page2) == 1
        assert trades_page2[0]["ticket"] == 101

        # Test filters
        filtered_trades = audit_reader.get_ledger_trades(status_filter="CLOSED_SL")
        assert len(filtered_trades) == 1
        assert filtered_trades[0]["ticket"] == 103

        # Test growth chart data
        growth_data = audit_reader.get_equity_growth_chart_data()
        assert len(growth_data) == 3
        assert growth_data[0]["balance"] == 10000.0
        assert growth_data[1]["equity"] == 11000.0

        audit_reader.close()

    finally:
        shutil.rmtree(temp_dir)


def test_intelligent_hedging_trigger_and_policy() -> None:
    """Tests the PyTorch and regime-driven intelligent hedging logic to place exact Buy/Sell Limit hedging orders."""
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_live_audit.db")
    db_url = f"sqlite:///{db_path}"

    try:
        # Load a default configuration
        config_dict = {
            "execution": {
                "symbol": "XAUUSD",
                "mode": "PAPER",
                "magic_number": 888101,
            },
            "model": {
                "model_artifact_path": os.path.join(temp_dir, "non_existent_model.pt"),
                "feature_schema_version": "v1.0",
                "confidence_threshold": 0.20,
            },
            "risk": {
                "risk_per_trade_pct": 2.0,
                "max_account_drawdown_pct": 10.0,
                "max_concurrent_positions": 5,
                "max_spread_points": 50,
                "max_allowed_lots": 10.0,
                "max_margin_usage_pct": 50.0,
            },
            "telegram": {
                "enabled": False,
                "bot_token": "mock_token",
                "admin_id": "mock_id",
            },
        }
        config = AppConfig.model_validate(config_dict)

        # Mock broker/MT5 port
        mock_port = MockMT5Port()
        audit = AuditRepository(db_url=db_url)

        # Instantiate live engine with force_fresh_model=True to create modelweights file
        engine = LiveEngine(
            config=config, adapter=mock_port, audit_repo=audit, force_fresh_model=True
        )
        engine._symbol_info = mock_port.get_symbol_info("XAUUSD")

        # Setup an active position in drawdown with a very low hold_score
        pos = Position(
            ticket=201,
            symbol="XAUUSD",
            type=OrderType.BUY,
            volume=1.0,
            price_open=2340.00,
            sl=2330.00,
            tp=2360.00,
            profit=-150.00,  # drawdown!
            magic=888101,
        )
        mock_port.positions = [pos]

        # Artificially set hold score of position 201 to 40 (which is below the threshold of 50)
        engine.order_manager._hold_score_tracker[201] = 40
        engine.order_manager._entry_timestamps[201] = datetime.now(UTC)
        engine.order_manager._entry_prices[201] = 2340.00
        engine.order_manager._entry_directions[201] = "BUY"
        engine.order_manager._last_known_volume[201] = 1.0

        # Simulate current tick with price in drawdown relative to BUY open price (2340)
        tick = TickData(
            symbol="XAUUSD",
            timestamp=datetime.now(UTC),
            bid=2335.00,
            ask=2335.20,
            volume=1.0,
        )

        # Setup PyTorch probabilities tensor where BUY (index 1) is high -> expecting Buy Limit averaging order
        # Classes: 0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET, 3=WAIT
        probs = torch.tensor([[0.05, 0.85, 0.05, 0.05]], dtype=torch.float32)

        # Setup a mock regime state and feature engine
        regime_state = MarketRegimeState(
            symbol="XAUUSD",
            timestamp_utc=datetime.now(UTC).isoformat(),
            regime_type=RegimeType.RANGING_MEAN_REVERSION,
            regime_probability=0.95,
            order_flow_imbalance=0.0,
            realized_volatility_5m=1.20,
            tick_velocity_per_sec=1.5,
            current_spread_usd=0.20,
            is_macro_news_active=False,
            recommended_execution_type=RecommendedExecutionType.PASSIVE_LIMIT,
            reason="DEFAULT_RANGE",
        )

        completed_bars = []
        fv = engine.feature_engine.compute_from_bars(completed_bars, tick)

        account = mock_port.get_account_info()

        # Execute hedging policy check
        engine._evaluate_hedging_policy(
            active_positions=mock_port.positions,
            tick=tick,
            probs=probs,
            regime_state=regime_state,
            fv=fv,
            account=account,
        )

        # Verify that a Buy Limit hedging order was generated and successfully sent to the broker adapter
        assert len(mock_port.sent_orders) == 1
        sent_order = mock_port.sent_orders[0]
        assert sent_order.order_type == OrderType.BUY_LIMIT
        assert sent_order.price < tick.bid  # Should be lower than bid (averaging)
        assert (
            sent_order.take_profit == pos.price_open
        )  # take profit target is the original entry price for break-even

        # Verify ticket was added to self._hedged_tickets to prevent duplicate hedging
        assert 201 in engine._hedged_tickets

        # Clean up
        engine.audit.close()
        audit.close()

    finally:
        shutil.rmtree(temp_dir)
