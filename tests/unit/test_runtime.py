"""
Runtime and CLI Integration Tests
=================================
Verifies CLI command executions and core LiveEngine event processing at runtime.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from nexus_scalp.application.live_engine import LiveEngine
from nexus_scalp.cli.main import app
from nexus_scalp.configuration.config import AppConfig
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.ports.mt5_port import IMT5Port


class MockMT5Port(IMT5Port):
    """Fully conforming Mock MT5 Port for LiveEngine testing."""

    def __init__(self) -> None:
        self._connected = False
        self.symbol_info = SymbolInfo(
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
        self.account_info = AccountInfo(
            login=123456,
            trade_mode=0,
            leverage=100,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            currency="USD",
        )
        self.ticks: list[TickData] = []
        self.positions: list[Position] = []

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> AccountInfo:
        return self.account_info

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return self.symbol_info

    def get_last_tick(self, symbol: str) -> TickData:
        if self.ticks:
            return self.ticks[-1]
        return TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=1.0850,
            ask=1.0852,
            volume=10.0,
        )

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        now = datetime.now(UTC) - timedelta(minutes=count + 1)
        bars = []
        for i in range(count):
            bar_time = now + timedelta(minutes=i)
            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=bar_time,
                    open=1.0850,
                    high=1.0860,
                    low=1.0840,
                    close=1.0855,
                    tick_volume=100,
                    is_complete=True,
                )
            )
        return bars

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol:
            return [p for p in self.positions if p.symbol == symbol]
        return self.positions

    def send_order(self, order: TradeOrder) -> bool:
        pos = Position(
            ticket=999,
            symbol=order.symbol,
            type=order.order_type,
            volume=order.volume,
            price_open=order.price,
            sl=order.stop_loss,
            tp=order.take_profit,
            profit=0.0,
            magic=order.magic_number,
        )
        self.positions.append(pos)
        return True

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        return True

    def close_position(self, ticket: int) -> bool:
        self.positions = [p for p in self.positions if p.ticket != ticket]
        return True


def test_cli_doctor() -> None:
    """Verifies that the CLI doctor diagnostic runs without error."""
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Host OS Platform" in result.stdout


def test_cli_config_validate() -> None:
    """Verifies that config-validate successfully validates base configuration."""
    runner = CliRunner()
    result = runner.invoke(app, ["config-validate", "--config", "configs/base.yaml"])
    assert result.exit_code == 0
    assert "Configuration is valid" in result.stdout


def test_live_engine_runtime(tmp_path: Path) -> None:
    """Verifies pre-flight checks, cold-start warmup, bootstrapping, and tick ingestion."""
    config = AppConfig.load_from_yaml(Path("configs/base.yaml"))

    # Override model path to temporary location to isolate files
    config.model.model_artifact_path = str(tmp_path / "model.pt")

    adapter = MockMT5Port()

    # 1. Instantiate engine (this triggers _load_or_create_bundle internally)
    engine = LiveEngine(config=config, adapter=adapter, force_fresh_model=True)

    # 2. Pre-flight check
    engine._preflight_or_raise()

    # 3. Cold-start warmup (requires historical bars from adapter)
    asyncio.run(engine._cold_start_warmup(config.execution.symbol))
    assert len(engine._rolling_feature_records) > 0

    # 4. Bootstrap training
    asyncio.run(engine._bootstrap_train_if_ready())

    # 5. Live tick ingestion
    tick = TickData(
        symbol=config.execution.symbol,
        timestamp=datetime.now(UTC),
        bid=1.0850,
        ask=1.0852,
        volume=5.0,
    )
    account = adapter.get_account_info()
    engine._process_tick_pipeline(tick=tick, account=account)
