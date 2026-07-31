"""
Paper Trading Simulation Adapter
================================
Simulates real-time market tick generation, account balance updates, 
and instant simulated order executions without requiring an active MT5 terminal process.
"""

from datetime import datetime, timezone
import random
from typing import List, Optional

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
)
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.adapters.paper")


class PaperMT5Adapter(IMT5Port):
    """
    In-Memory Paper Trading Broker Adapter for simulation and offline execution.
    """

    def __init__(self, initial_balance: float = 10000.0, symbol: str = "EURUSD") -> None:
        self.symbol = symbol
        self.balance = initial_balance
        self.equity = initial_balance
        self._connected = False
        self._current_price = 1.08500
        self._positions: List[Position] = []
        self._ticket_counter = 100001

    def connect(self) -> bool:
        """Initializes paper trading simulation state."""
        self._connected = True
        logger.info("Connected to Paper Simulation Broker Adapter", initial_balance=self.balance)
        return True

    def disconnect(self) -> None:
        """Disconnects simulation adapter."""
        self._connected = False
        logger.info("Paper Trading simulation disconnected.")

    def is_connected(self) -> bool:
        return self._connected

    def get_account_info(self) -> AccountInfo:
        """Returns virtual account snapshot."""
        return AccountInfo(
            login=9990001,
            trade_mode=0,  # Demo / Simulation
            leverage=100,
            balance=self.balance,
            equity=self.equity,
            margin=0.0,
            margin_free=self.equity,
            currency="USD",
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Returns market rules for simulated EURUSD."""
        return SymbolInfo(
            symbol=symbol,
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

    def get_last_tick(self, symbol: str) -> TickData:
        """Generates realistic micro-movement tick snapshots."""
        # Random walk micro step
        step = random.choice([-0.00002, -0.00001, 0.0, 0.00001, 0.00002])
        self._current_price = round(self._current_price + step, 5)
        spread = 0.00012  # 1.2 pips spread

        bid = round(self._current_price, 5)
        ask = round(bid + spread, 5)

        return TickData(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bid=bid,
            ask=ask,
            last=bid,
            volume=float(random.randint(1, 15)),
            flags=6,
        )

    def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Returns active open simulated positions."""
        if symbol:
            return [p for p in self._positions if p.symbol == symbol]
        return list(self._positions)

    def send_order(self, order: TradeOrder) -> bool:
        """Simulates immediate market fill of trade orders."""
        self._ticket_counter += 1
        pos = Position(
            ticket=self._ticket_counter,
            symbol=order.symbol,
            type=order.order_type,
            volume=order.volume,
            price_open=order.price,
            sl=order.stop_loss,
            tp=order.take_profit,
            profit=0.0,
            magic=order.magic_number,
        )
        self._positions.append(pos)
        logger.info(
            "PAPER ORDER FILLED SIMULATION",
            ticket=pos.ticket,
            symbol=pos.symbol,
            type=pos.type.value,
            price=pos.price_open,
            volume=pos.volume,
        )
        return True

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        """Closes simulated open position."""
        if volume is not None:
            # Simulate a partial close
            for p in self._positions:
                if p.ticket == ticket:
                    if volume < p.volume:
                        new_pos = Position(
                            ticket=p.ticket,
                            symbol=p.symbol,
                            type=p.type,
                            volume=round(p.volume - volume, 2),
                            price_open=p.price_open,
                            sl=p.sl,
                            tp=p.tp,
                            profit=p.profit,
                            magic=p.magic,
                        )
                        self._positions.remove(p)
                        self._positions.append(new_pos)
                        logger.info("PAPER POSITION PARTIALLY CLOSED", ticket=ticket, closed_vol=volume, remaining_vol=new_pos.volume)
                        return True
        self._positions = [p for p in self._positions if p.ticket != ticket]
        logger.info("PAPER POSITION CLOSED", ticket=ticket)
        return True