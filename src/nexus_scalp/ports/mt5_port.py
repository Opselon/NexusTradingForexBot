"""
MetaTrader 5 Abstract Port Specification
========================================
Defines abstract contracts that all execution adapters must strictly implement.
"""

from abc import ABC, abstractmethod
from typing import Any

from nexus_scalp.adapters.mt5.diagnostics import MT5ConnectionState
from nexus_scalp.adapters.mt5.providers import (
    AccountSnapshot,
    BrokerCalcSnapshot,
    BrokerTickSnapshot,
    DealSnapshot,
    HistoryOrderSnapshot,
    OrderSnapshot,
    PositionSnapshot,
    RateBarSnapshot,
    SymbolSnapshot,
    TickHistorySnapshot,
)
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import (
    AccountInfo,
    Position,
    SymbolInfo,
    TickData,
    TradeOrder,
)
from nexus_scalp.market_data.bar_aggregator import BarData


class IMT5Port(ABC):
    """
    Abstract Port defining mandatory operations for communicating with MetaTrader 5.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establishes connection with terminal or remote gateway."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully releases resources and shuts down active connection."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Checks current connectivity status."""
        pass

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Retrieves real-time account balance, equity, and leverage state."""
        pass

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Retrieves specification metadata and market rules for a symbol."""
        pass

    @abstractmethod
    def get_last_tick(self, symbol: str) -> TickData:
        """Retrieves the latest available tick for a symbol."""
        pass

    @abstractmethod
    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """
        Retrieves historical completed OHLC bars directly from MT5 rates database.
        """
        pass

    @abstractmethod
    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Retrieves list of active open positions, optionally filtered by symbol."""
        pass

    @abstractmethod
    def send_order(self, order: TradeOrder) -> bool:
        """Submits a trade execution request to the broker."""
        pass

    @abstractmethod
    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """Modifies Stop Loss and Take Profit targets on an existing open position ticket."""
        pass

    @abstractmethod
    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        """Closes an open position specified by broker ticket ID."""
        pass

    def get_closed_deals_history(self, symbol: str, hours_back: int = 24) -> list[dict]:
        """Retrieves closed deals history for a symbol."""
        return []

    def execute_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        """Executes a market order and returns the ticket."""
        return 0

    def place_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        """Places a pending order and returns the ticket."""
        return 0

    def modify_order(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        """Modifies an order's SL and TP."""
        return False

    def cancel_pending_order(self, ticket: int) -> bool:
        """Cancels a pending order."""
        return False

    # =========================================================================
    # BROKER-AWARE PROVIDERS (Phase 14 MT5 forensic architecture)
    # -------------------------------------------------------------------------
    # Concrete adapters override these with real broker implementations; the
    # default implementations are honest "unavailable" responses so a consumer
    # NEVER receives fake data from a stub. Every snapshot carries provenance
    # (BROKER_NATIVE / FALLBACK_ESTIMATE / UNAVAILABLE) + captured_at + error.
    # =========================================================================

    def connection_state(self) -> MT5ConnectionState:
        """Real MT5 connection state (never derived from config)."""
        return MT5ConnectionState()

    def get_account_snapshot(self) -> AccountSnapshot:
        """Full typed account snapshot from mt5.account_info()."""
        snap = AccountSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        return snap

    def get_symbol_snapshot(self, symbol: str) -> SymbolSnapshot:
        """Symbol specification + current tick (explicitly separated)."""
        snap = SymbolSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        return snap

    def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot:
        """Current tick from mt5.symbol_info_tick() with official fields."""
        snap = BrokerTickSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        return snap

    def get_all_positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        """ALL account positions (never restricted to bot magic/symbol)."""
        return []

    def get_pending_orders_snapshot(self, symbol: str | None = None) -> list[OrderSnapshot]:
        """Active pending orders via mt5.orders_get()."""
        return []

    def get_history_orders(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[HistoryOrderSnapshot]:
        """Historical orders via mt5.history_orders_get()."""
        return []

    def get_history_deals(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[DealSnapshot]:
        """Historical deals via mt5.history_deals_get()."""
        return []

    def get_rate_history(
        self,
        symbol: str,
        timeframe: str = "M1",
        count: int = 500,
        from_utc: Any = None,
    ) -> list[RateBarSnapshot]:
        """Official MT5 rate history via copy_rates_from_pos / copy_rates_range.

        Timestamps are normalized to UTC (task §2); when `from_utc` is given
        copy_rates_range is used, otherwise copy_rates_from_pos(count).
        """
        return []

    def get_tick_history(
        self,
        symbol: str,
        count: int = 500,
        from_utc: Any = None,
        to_utc: Any = None,
    ) -> list[TickHistorySnapshot]:
        """Tick history via copy_ticks_from / copy_ticks_range (UTC)."""
        return []

    def order_calc_profit_snapshot(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> BrokerCalcSnapshot:
        """Broker-native profit calc via mt5.order_calc_profit()."""
        snap = BrokerCalcSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        snap.operation = "order_calc_profit"
        snap.symbol = symbol
        return snap

    def order_calc_margin_snapshot(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        price: float,
    ) -> BrokerCalcSnapshot:
        """Broker-native margin calc via mt5.order_calc_margin()."""
        snap = BrokerCalcSnapshot()
        snap.available = False
        snap.source = "UNAVAILABLE"
        snap.operation = "order_calc_margin"
        snap.symbol = symbol
        return snap

    def get_terminal_state(self) -> dict[str, Any]:
        """terminal_info() diagnostic subset (safe; no credentials)."""
        return {"available": False, "reason": "UNSUPPORTED_BY_ADAPTER"}
