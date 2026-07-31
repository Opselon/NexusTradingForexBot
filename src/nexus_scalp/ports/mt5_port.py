"""
MetaTrader 5 Abstract Port Specification
========================================
Defines abstract contracts that all execution adapters must strictly implement.
"""

from abc import ABC, abstractmethod

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
    def get_historical_bars(self, symbol: str, timeframe: str = "M1", count: int = 100) -> list[BarData]:
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