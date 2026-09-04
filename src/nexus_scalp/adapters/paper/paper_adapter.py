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

import contextlib
import os
import random
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

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
    build_position_snapshot,
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
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.ports.mt5_port import IMT5Port

logger = get_logger("nexus_scalp.adapters.paper")

#: Instruments quoted with 2 decimals and a 100-unit contract size (metals).
_METAL_PREFIXES: tuple[str, ...] = ("XAU", "XAG", "GOLD", "SILVER")


class PaperMT5Adapter(IMT5Port):
    """
    In-Memory Paper Trading Broker Adapter for simulation and offline execution.
    """

    # BUG-232: plausible per-instrument seed baselines. The old hard-coded
    # 2000.00 metal seed froze every PAPER session at a price ~2,400 USD away
    # from the real XAUUSD market, so every PAPER-derived proposal was
    # structurally invalid the moment it touched any real reference (the
    # BUG-231 10016 storm). The seed is overridable via NEXUS_PAPER_SEED_<SYM>
    # (e.g. NEXUS_PAPER_SEED_XAUUSD=4430.5) for replay experiments.
    _SEED_BASELINES: ClassVar[dict[str, float]] = {
        "XAUUSD": 4400.00,
        "XAGUSD": 28.00,
        "EURUSD": 1.08500,
        "GBPUSD": 1.27000,
        "USDJPY": 150.000,
    }
    _DEFAULT_SEED = 2000.00  # fallback for unknown 2-digit instruments

    def __init__(self, initial_balance: float = 10000.0, symbol: str = "EURUSD") -> None:
        self.symbol = symbol
        self.balance = initial_balance
        self.equity = initial_balance
        self._connected = False
        self._is_metal = self._symbol_is_metal(symbol)
        #: Starting mid price, chosen to match the instrument's quote convention.
        self._current_price = self._seed_price(symbol)
        self._positions: list[Position] = []
        self._ticket_counter = 100001
        # BUG-226: execution provenance of the account this adapter represents.
        # Always 'PAPER' for the simulation adapter; the engine and the
        # audit-repository read it to tag ledger rows and snapshots.
        self.current_account_source: str = "PAPER"

    @classmethod
    def _seed_price(cls, symbol: str) -> float:
        """BUG-232: plausible seed price for the simulated instrument.

        Precedence: NEXUS_PAPER_SEED_<SYMBOL> env > per-instrument baseline >
        legacy 2000.00 default (unknown 2-digit instruments).
        """
        upper = (symbol or "").upper()
        env_key = f"NEXUS_PAPER_SEED_{upper}"
        with contextlib.suppress(Exception):
            raw = os.environ.get(env_key, "").strip()
            if raw:
                return float(raw)
        if upper in cls._SEED_BASELINES:
            return cls._SEED_BASELINES[upper]
        return cls._DEFAULT_SEED

    # ------------------------------------------------------------------
    # Instrument conventions
    # ------------------------------------------------------------------

    @staticmethod
    def _symbol_is_metal(symbol: str) -> bool:
        upper = (symbol or "").upper()
        return any(upper.startswith(prefix) for prefix in _METAL_PREFIXES)

    def _ensure_symbol(self, symbol: str) -> None:
        """BUGFIX-G29: confirm `symbol` is tracked by the simulated feed.

        The paper feed emits ticks for any symbol on demand, so this is a
        lightweight guard that keeps `resubscribe_symbol` uniform across
        adapters and surfaces an explicit error for an unconfigured symbol
        instead of silently producing nothing.
        """
        if not symbol:
            raise ValueError("resubscribe_symbol requires a non-empty symbol")

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
    # Broker-aware providers (Phase 14 contract). The paper adapter reports
    # SIMULATED values with explicit provenance - every snapshot is honest
    # about being the in-memory simulation (source='PAPER_SIMULATION').
    # ------------------------------------------------------------------

    def connection_state(self) -> MT5ConnectionState:
        state = MT5ConnectionState()
        if self._connected:
            state.set_state(MT5ConnectionState.CONNECTED, "paper simulation connected")
        else:
            state.set_state(MT5ConnectionState.DISCONNECTED, "paper simulation disconnected")
        return state

    def get_account_snapshot(self) -> AccountSnapshot:
        snap = AccountSnapshot()
        snap.available = True
        snap.source = "PAPER_SIMULATION"
        snap.login = 9990001
        snap.server = "PAPER"
        snap.company = "Nexus Paper Simulator"
        snap.currency = "USD"
        snap.currency_digits = 2
        snap.trade_mode = 0  # Demo / Simulation
        snap.leverage = 100
        snap.trade_allowed = True
        snap.trade_expert = True
        snap.balance = float(self.balance)
        snap.credit = 0.0
        snap.profit = float(self.equity - self.balance)
        snap.equity = float(self.equity)
        snap.margin = 0.0
        snap.margin_free = float(self.equity)
        snap.margin_level = None if self.equity <= 0 else 100.0
        snap.margin_level_source = "PAPER_SIMULATION"
        snap.floating_pnl = float(self.equity - self.balance)
        snap.net_pnl = snap.floating_pnl
        snap.open_positions_count = len(self._positions)
        snap.pending_orders_count = 0
        return snap

    def get_symbol_snapshot(self, symbol: str) -> SymbolSnapshot:
        snap = SymbolSnapshot()
        snap.available = True
        snap.source = "PAPER_SIMULATION"
        digits = self._quote_digits(symbol)
        is_metal = self._symbol_is_metal(symbol)
        snap.spec = {
            "name": symbol,
            "description": "Paper simulated symbol",
            "digits": digits,
            "point": 0.01 if is_metal else 0.00001,
            "trade_tick_size": 0.01 if is_metal else 0.00001,
            "trade_tick_value": 1.0,
            "trade_contract_size": 100.0 if is_metal else 100000.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "trade_stops_level": 10,
            "trade_freeze_level": 0,
            "currency_base": "USD",
            "currency_profit": "USD",
            "currency_margin": "USD",
        }
        try:
            tick = self.get_last_tick(symbol)
        except Exception:
            tick = None
        if tick is not None:
            snap.tick = {
                "bid": tick.bid,
                "ask": tick.ask,
                "last": tick.last,
                "volume": tick.volume,
                "time": int(tick.timestamp.timestamp()),
                "flags": tick.flags,
                "time_utc": tick.timestamp.isoformat(),
            }
            snap.spread_points = round(tick.ask - tick.bid, 8)
            snap.spread_points_source = "PAPER_SIMULATION"
        return snap

    def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot:
        try:
            tick = self.get_last_tick(symbol)
        except Exception:
            snap = BrokerTickSnapshot()
            snap.symbol = symbol
            snap.available = False
            snap.source = "UNAVAILABLE"
            return snap
        snap = BrokerTickSnapshot()
        snap.available = True
        snap.source = "PAPER_SIMULATION"
        snap.symbol = symbol
        snap.bid = tick.bid
        snap.ask = tick.ask
        snap.last = tick.last
        snap.volume = tick.volume
        snap.last_volume = tick.volume
        snap.flags = tick.flags
        snap.time = int(tick.timestamp.timestamp())
        snap.time_utc = tick.timestamp
        snap.freshness_ms = 0.0
        snap.stale = False
        snap.spread_points = round(tick.ask - tick.bid, 8)
        return snap

    def get_all_positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        positions = self.get_positions(symbol=symbol)
        return [build_position_snapshot(p) for p in positions]

    def get_rate_history(
        self,
        symbol: str,
        timeframe: str = "M1",
        count: int = 500,
        from_utc: Any = None,
    ) -> list[RateBarSnapshot]:
        bars = self.get_historical_bars(symbol=symbol, timeframe=timeframe, count=count)
        out: list[RateBarSnapshot] = []
        for b in bars:
            r = RateBarSnapshot()
            r.available = True
            r.source = "PAPER_SIMULATION"
            r.time = int(b.timestamp.timestamp())
            r.time_utc = b.timestamp
            r.open = b.open
            r.high = b.high
            r.low = b.low
            r.close = b.close
            r.tick_volume = b.tick_volume
            out.append(r)
        return out

    def order_calc_margin_snapshot(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        price: float,
    ) -> BrokerCalcSnapshot:
        snap = BrokerCalcSnapshot()
        snap.operation = "order_calc_margin"
        snap.symbol = symbol
        snap.price_open = float(price)
        snap.volume = float(volume)
        snap.available = True
        snap.source = "FALLBACK_ESTIMATE"
        is_metal = self._symbol_is_metal(symbol)
        contract = 100.0 if is_metal else 100000.0
        snap.value = round((contract * float(price) * float(volume)) / 100.0, 4)
        snap.value_source = "FALLBACK_ESTIMATE"
        return snap

    def order_calc_profit_snapshot(
        self,
        symbol: str,
        order_type: int,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> BrokerCalcSnapshot:
        snap = BrokerCalcSnapshot()
        snap.operation = "order_calc_profit"
        snap.symbol = symbol
        snap.price_open = float(price_open)
        snap.price_close = float(price_close)
        snap.volume = float(volume)
        snap.available = True
        snap.source = "FALLBACK_ESTIMATE"
        # Paper: BUY=0 (POSITION_TYPE_BUY). Simulated tick value per lot = 1.0.
        direction = 1.0 if int(order_type) == 0 else -1.0
        snap.value = round(
            direction * (float(price_close) - float(price_open)) * float(volume) * 100.0, 4
        )
        snap.value_source = "FALLBACK_ESTIMATE"
        return snap

    def get_history_deals(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[DealSnapshot]:
        # Paper keeps no deal archive; honest empty result.
        return []

    def get_history_orders(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[HistoryOrderSnapshot]:
        return []

    def get_pending_orders_snapshot(self, symbol: str | None = None) -> list[OrderSnapshot]:
        return []

    def get_tick_history(
        self,
        symbol: str,
        count: int = 500,
        from_utc: Any = None,
        to_utc: Any = None,
    ) -> list[TickHistorySnapshot]:
        return []

    def resubscribe_symbol(self, symbol: str) -> None:
        """BUGFIX-G29: re-arm the live tick feed for `symbol`.

        Paper simulation keeps emitting ticks via ``get_last_tick``; this is a
        no-op that confirms the symbol is still in the simulated feed so the
        engine watchdog can call it uniformly across adapters (the real MT5
        adapter re-issues ``subscribe_symbols`` / CopyTicks under the hood).
        """
        self._ensure_symbol(symbol)

    def get_tick(self, symbol: str) -> TickData:
        """BUGFIX-G29: return one fresh tick for `symbol` (live feed probe).

        The watchdog uses this after a stall to prove the feed is alive again.
        Delegates to the same generator ``get_last_tick`` uses.
        """
        return self.get_last_tick(symbol)

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
        """Generates realistic micro-movement tick snapshots.

        BUG-232: volatility scales with the instrument so the simulated
        stream is a usable market (gold moves in 5-30 cent bursts with
        occasional trend steps, not ±2 cents around a dead seed). The walk is
        mean-reverting to the seed baseline so long sessions cannot drift to
        absurd levels.
        """
        digits = self._quote_digits(symbol)
        upper = (symbol or "").upper()
        if digits == 2:
            # gold-class: burst moves + rare momentum step, spread 25-45c
            step = random.choice([-0.30, -0.15, -0.08, -0.02, 0.0, 0.02, 0.08, 0.15, 0.30, 0.45])
            spread = round(random.uniform(0.25, 0.45), 2)
            baseline = self._seed_price(upper)
        else:
            step = random.choice(
                [-0.00030, -0.00015, -0.00008, -0.00002, 0.0, 0.00002, 0.00008, 0.00015, 0.00030]
            )
            spread = 0.00012  # 1.2 pips
            baseline = self._seed_price(upper)

        candidate = self._current_price + step
        # Mean-revert toward the seed when the walk drifts > 2% away.
        if baseline > 0 and abs(candidate - baseline) > baseline * 0.02:
            candidate = baseline + (candidate - baseline) * 0.5

        self._current_price = round(candidate, digits)
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
