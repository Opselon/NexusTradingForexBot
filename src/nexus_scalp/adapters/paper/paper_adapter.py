"""
Paper Trading Simulation Adapter
================================
Simulates real-time market tick generation, account balance updates,
and instant simulated order executions without requiring an active MT5 terminal process.

This adapter is the designed NO-BROKER execution boundary: integration tests use
it to exercise the real dispatch path (`OrderLifecycleManager.dispatch_order`)
deterministically. It therefore implements the FULL `IMT5Port` contract,
including `get_historical_bars`, `modify_position`, `execute_market_order` and
`place_pending_order`.

Symbol awareness: the instrument specification is derived from the symbol name so
gold (2 digits, 0.01 point, 100 contract size) is not silently simulated with
FX-style 5-digit specs, which would corrupt every risk/lot calculation performed
against this adapter.
"""

import random
from datetime import UTC, datetime, timedelta

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
)
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.adapters.paper")

#: Instruments quoted with 2 decimals and a 100-unit contract size (metals).
_METAL_PREFIXES: tuple[str, ...] = ("XAU", "XAG", "GOLD", "SILVER")


class PaperMT5Adapter(IMT5Port):
    """
    In-Memory Paper Trading Broker Adapter for simulation and offline execution.
    """

    def __init__(self, initial_balance: float = 10000.0, symbol: str = "EURUSD") -> None:
        self.symbol = symbol
        self.balance = initial_balance
        self.equity = initial_balance
        self._connected = False
        self._is_metal = self._symbol_is_metal(symbol)
        #: Starting mid price, chosen to match the instrument's quote convention.
        self._current_price = 2000.00 if self._is_metal else 1.08500
        self._positions: list[Position] = []
        self._ticket_counter = 100001

    # ------------------------------------------------------------------
    # Instrument conventions
    # ------------------------------------------------------------------

    @staticmethod
    def _symbol_is_metal(symbol: str) -> bool:
        upper = (symbol or "").upper()
        return any(upper.startswith(prefix) for prefix in _METAL_PREFIXES)

    def _quote_digits(self, symbol: str) -> int:
        return 2 if self._symbol_is_metal(symbol) else 5

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Account & instrument metadata
    # ------------------------------------------------------------------

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
        """
        Returns symbol-appropriate market rules.

        Metals use 2 digits / 0.01 point / 100 contract size; everything else
        keeps the original 5-digit FX convention.
        """
        if self._symbol_is_metal(symbol):
            return SymbolInfo(
                symbol=symbol,
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

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_last_tick(self, symbol: str) -> TickData:
        """Generates realistic micro-movement tick snapshots."""
        digits = self._quote_digits(symbol)
        if digits == 2:
            step = random.choice([-0.02, -0.01, 0.0, 0.01, 0.02])
            spread = 0.20
        else:
            step = random.choice([-0.00002, -0.00001, 0.0, 0.00001, 0.00002])
            spread = 0.00012  # 1.2 pips

        self._current_price = round(self._current_price + step, digits)
        bid = round(self._current_price, digits)
        ask = round(bid + spread, digits)

        return TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=bid,
            ask=ask,
            last=bid,
            volume=float(random.randint(1, 15)),
            flags=6,
        )

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """
        Generates a deterministic-shaped synthetic OHLC history.

        Bars are produced by a bounded random walk around the current simulated
        price so warmup paths (feature engine, HTF aggregation) can be exercised
        offline. Timestamps are contiguous and ascending, matching the live
        contract that `get_historical_bars` returns completed bars only.
        """
        bar_minutes = {
            "M1": 1,
            "M5": 5,
            "M15": 15,
            "M30": 30,
            "H1": 60,
            "H4": 240,
        }.get(str(timeframe).upper(), 1)

        digits = self._quote_digits(symbol)
        amplitude = 0.50 if digits == 2 else 0.0005
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        price = self._current_price
        bars: list[BarData] = []

        for i in range(max(0, int(count)), 0, -1):
            drift = random.uniform(-amplitude, amplitude)
            open_p = round(price, digits)
            close_p = round(open_p + drift, digits)
            high_p = round(max(open_p, close_p) + abs(drift) * 0.5, digits)
            low_p = round(min(open_p, close_p) - abs(drift) * 0.5, digits)
            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=str(timeframe).upper(),
                    timestamp=now - timedelta(minutes=bar_minutes * i),
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    tick_volume=random.randint(50, 250),
                    is_complete=True,
                )
            )
            price = close_p

        return bars

    # ------------------------------------------------------------------
    # Positions & execution
    # ------------------------------------------------------------------

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Returns active open simulated positions."""
        if symbol:
            return [p for p in self._positions if p.symbol == symbol]
        return list(self._positions)

    def send_order(self, order: TradeOrder) -> bool:
        """Simulates immediate market fill of trade orders."""
        return (
            self._open_simulated_position(
                symbol=order.symbol,
                order_type=order.order_type,
                volume=order.volume,
                price=order.price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                magic=order.magic_number,
            )
            > 0
        )

    def execute_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        """Simulates a market order fill and returns the assigned ticket."""
        return self._open_simulated_position(
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def place_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        """
        Simulates acceptance of a pending order.

        The simulation fills pendings immediately (no resting-order engine); the
        returned ticket lets callers exercise the pending bookkeeping path.
        """
        return self._open_simulated_position(
            symbol=symbol,
            order_type=order_type,
            volume=volume,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def _open_simulated_position(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
        magic: int = 0,
    ) -> int:
        """Creates the simulated position and returns its ticket (0 on refusal)."""
        if volume <= 0.0 or price <= 0.0:
            logger.warning("PAPER ORDER REJECTED (invalid size/price)", volume=volume, price=price)
            return 0

        self._ticket_counter += 1
        pos = Position(
            ticket=self._ticket_counter,
            symbol=symbol,
            type=order_type,
            volume=volume,
            price_open=price,
            sl=stop_loss,
            tp=take_profit,
            profit=0.0,
            magic=magic,
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
        return pos.ticket

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """
        Updates SL/TP on a simulated position.

        Positions are frozen domain models, so the entry is replaced via
        `model_copy` rather than mutated in place.
        """
        for index, pos in enumerate(self._positions):
            if pos.ticket == ticket:
                self._positions[index] = pos.model_copy(
                    update={"sl": float(stop_loss), "tp": float(take_profit)}
                )
                logger.info(
                    "PAPER POSITION MODIFIED",
                    ticket=ticket,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
                return True
        logger.warning("PAPER MODIFY FAILED (unknown ticket)", ticket=ticket)
        return False

    def modify_order(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """Pending orders are filled immediately here, so this defers to positions."""
        return self.modify_position(ticket, stop_loss, take_profit)

    def cancel_pending_order(self, ticket: int) -> bool:
        """Removes a simulated (immediately-filled) pending order."""
        return self.close_position(ticket)

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        """Closes simulated open position."""
        if volume is not None:
            # Simulate a partial close
            for p in self._positions:
                if p.ticket == ticket and volume < p.volume:
                    new_pos = p.model_copy(update={"volume": round(p.volume - volume, 2)})
                    self._positions.remove(p)
                    self._positions.append(new_pos)
                    logger.info(
                        "PAPER POSITION PARTIALLY CLOSED",
                        ticket=ticket,
                        closed_vol=volume,
                        remaining_vol=new_pos.volume,
                    )
                    return True
        before = len(self._positions)
        self._positions = [p for p in self._positions if p.ticket != ticket]
        if len(self._positions) == before:
            logger.warning("PAPER CLOSE FAILED (unknown ticket)", ticket=ticket)
            return False
        logger.info("PAPER POSITION CLOSED", ticket=ticket)
        return True
