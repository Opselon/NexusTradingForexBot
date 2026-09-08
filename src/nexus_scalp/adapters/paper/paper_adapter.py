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
import json
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from nexus_scalp.adapters.paper.replay_source import ReplayDataUnavailableError
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


def _get_seed() -> int | None:
    """Read deterministic seed from NEXUS_PAPER_STRESS_SEED if set.

    Returns int seed or None when unset/unparseable. Accepts both
    integer and float-like strings for convenience.
    """
    raw = os.environ.get("NEXUS_PAPER_STRESS_SEED", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return int(float(raw))
        except Exception:
            return None


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

    #: Baseline simulated spread for gold-class instruments: 8-18 cents.
    #: The legacy 25-45c band priced every PAPER signal out (spread/ATR gate
    #: blocked 100% of fills), so baseline is now tight enough to trade while
    #: stress runs widen it explicitly via PaperStressSpread /
    #: NEXUS_PAPER_SPREAD_SCALE (see _effective_spread_scale).
    _METAL_SPREAD_RANGE: ClassVar[tuple[float, float]] = (0.08, 0.18)
    _FX_SPREAD: ClassVar[float] = 0.00012  # 1.2 pips

    def __init__(
        self,
        initial_balance: float = 10000.0,
        symbol: str = "EURUSD",
        replay_source: Any | None = None,
    ) -> None:
        self.symbol = symbol
        self.balance = initial_balance
        self.equity = initial_balance
        self._connected = False
        self._is_metal = self._symbol_is_metal(symbol)
        #: Starting mid price, chosen to match the instrument's quote convention.
        self._current_price = self._seed_price(symbol)
        self._positions: list[Position] = []
        self._ticket_counter = 100001
        #: Task C: stress spread override (None = baseline 8-18c gold band).
        #: Installed by PaperStressSpread.install() or set_stress_spread();
        #: resolved per tick in _effective_spread_scale().
        self._stress_spread_scale: float | None = None
        self._stress_spread_profile: Any | None = None
        # PAPER DATA INTEGRITY (P0 phase 4): the market-data mode of this
        # adapter is EXPLICIT. SYNTHETIC (default) = AR(1) simulated walk for
        # CI/determinism; REPLAY = real historical chronology served by the
        # replay_source (paper experience then carries real-market stats).
        # A replay construction with an unusable source raises at __init__
        # (fail-closed) — it can never silently degrade to synthetic ticks.
        if replay_source is not None:
            if getattr(replay_source, "source_mode", "") != "REPLAY":
                raise ValueError("replay_source must be a ReplayTickSource (source_mode='REPLAY')")
            self._replay_source: Any | None = replay_source
            self.market_data_mode = "REPLAY"
            self.replay_provenance: dict[str, Any] = replay_source.identity()
            logger.info(
                "[PAPER] event=REPLAY_MODE_ATTACHED",
                symbol=symbol,
                provenance=self.replay_provenance,
            )
        else:
            self._replay_source = None
            self.market_data_mode = "SYNTHETIC"
            self.replay_provenance = {
                "market_data_mode": "SYNTHETIC",
                "symbol": symbol,
                "spread_model": "SIMULATED_8_18C_METAL_BAND",
                "slippage_model": "PAPER_ADAPTER_DETERMINISTIC",
            }
        # Evidence guards E-H
        self._last_tick: Any | None = None
        self._last_tick_time: Any | None = None
        self._seen_order_ids: set[str] = set()
        # BUG-226: execution provenance of the account this adapter represents.
        # Always 'PAPER' for the simulation adapter; the engine and the
        # audit-repository read it to tag ledger rows and snapshots.
        self.current_account_source: str = "PAPER"
        # Determinism capsizer: per-instance seeded RNG + AR(1) state.
        # When NEXUS_PAPER_STRESS_SEED is set the tick stream is fully
        # reproducible (same seed -> same sequence); otherwise _rng is
        # system-seeded but the walk remains autocorrelated (phi=0.6).
        self._seed: int | None = _get_seed()
        self._rng: random.Random = random.Random(self._seed)
        self._prev_step: float = 0.0
        self._vol_block: float = 1.0
        self._vol_block_ticks_left: int = 0
        # PAPER Persistence Phase 2: durability across restarts
        self._initial_balance: float = float(initial_balance)
        self._closed_tickets: set[int] = set()
        self._last_tick_iso: str | None = None
        # Priority 4: per-adapter execution ledger (audit trail). Every order
        # attempt appends one dict here — fills AND rejections — so
        # requested_price != fill_price (BUY@ask + slippage), spread, and
        # rejection_reason are observable from data, not from log prints.
        # Fields: ts, symbol, order_type, volume, requested_price,
        # bid_at_request, ask_at_request, spread, fill_price, slippage,
        # latency_ticks, rejection_reason (None on fill), is_fill, ticket.
        self._execution_ledger: list[dict[str, Any]] = []

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
    # Spread profile (Task C)
    # ------------------------------------------------------------------

    def _effective_spread_scale(self) -> float:
        """Resolve spread scale: adapter override > NEXUS_PAPER_SPREAD_SCALE env > 1.0."""
        if self._stress_spread_scale is not None:
            try:
                return float(self._stress_spread_scale)
            except Exception:
                pass
        raw = os.environ.get("NEXUS_PAPER_SPREAD_SCALE", "").strip()
        if raw:
            try:
                s = float(raw)
                return max(0.1, min(s, 10.0))
            except Exception:
                pass
        return 1.0

    def set_stress_spread(self, scale: float) -> None:
        """Install a stress spread multiplier. Baseline PAPER stays 8-18c (scale 1.0)."""
        self._stress_spread_scale = float(scale)

    def clear_stress_spread(self) -> None:
        """Restore baseline spread profile."""
        self._stress_spread_scale = None
        self._stress_spread_profile = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # PAPER Persistence Phase 2 — JSON file durability (no client/UI changes)
    # ------------------------------------------------------------------

    def _persist_enabled(self) -> bool:
        raw = os.environ.get("NEXUS_PAPER_PERSIST", "1").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _persist_path(self) -> Path:
        # Test-isolation seam: NEXUS_DATA_ROOT redirects the state file without
        # code changes (conftest sets it per pytest run).
        env_root = os.environ.get("NEXUS_DATA_ROOT", "").strip()
        if env_root:
            try:
                return Path(env_root) / "paper_state.json"
            except Exception:
                pass
        try:
            from nexus_scalp.release import paths as rpaths  # type: ignore

            root = rpaths.get_data_root()
            return Path(root) / "paper_state.json"
        except Exception:
            return Path.cwd() / "artifacts" / "paper_state.json"

    def _persist_state(self) -> None:
        if not self._persist_enabled():
            return
        path = self._persist_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # _last_tick_iso: prefer cached tick time else now
            if (
                self._last_tick is not None
                and getattr(self._last_tick, "timestamp", None) is not None
            ):
                try:
                    self._last_tick_iso = self._last_tick.timestamp.isoformat()  # type: ignore[union-attr]
                except Exception:
                    self._last_tick_iso = datetime.now(UTC).isoformat()
            elif self._last_tick_iso is None:
                self._last_tick_iso = datetime.now(UTC).isoformat()
            payload = {
                "balance": float(self.balance),
                "equity": float(self.equity),
                "_ticket_counter": int(self._ticket_counter),
                "_positions": [p.model_dump(mode="json") for p in self._positions],
                "_last_tick_iso": self._last_tick_iso,
                "closed_tickets": sorted(int(x) for x in getattr(self, "_closed_tickets", set())),
                "symbol": str(self.symbol),
                "initial_balance": float(getattr(self, "_initial_balance", self.balance)),
            }
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
            with contextlib.suppress(Exception):
                if os.name != "nt":
                    os.chmod(path, 0o600)
        except Exception as exc:  # never break trading path on persist failure
            with contextlib.suppress(Exception):
                logger.warning("PAPER persist failed", error=str(exc), path=str(path))

    def _load_state(self) -> bool:
        if not self._persist_enabled():
            return False
        path = self._persist_path()
        if not path.exists():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            return False
        # Provenance guard: symbol must match; initial_balance is NOT used to gate or reset.
        try:
            saved_symbol = str(data.get("symbol", ""))
            if saved_symbol and saved_symbol != str(self.symbol):
                logger.info(
                    "PAPER persist skip (symbol mismatch)", saved=saved_symbol, current=self.symbol
                )
                return False
        except Exception:
            pass
        try:
            self.balance = float(data.get("balance", self.balance))
            self.equity = float(data.get("equity", self.equity))
            self._ticket_counter = int(data.get("_ticket_counter", self._ticket_counter))
            self._last_tick_iso = data.get("_last_tick_iso")
            # closed tickets
            ct = data.get("closed_tickets", [])
            self._closed_tickets = set(int(x) for x in ct) if isinstance(ct, list) else set()
            # positions
            raw_positions = data.get("_positions", [])
            restored: list[Any] = []
            if isinstance(raw_positions, list):
                for d in raw_positions:
                    try:
                        restored.append(Position.model_validate(d))
                    except Exception:
                        continue
            self._positions = restored
            # Recompute equity from restored positions (persisted balance stays authoritative).
            with contextlib.suppress(Exception):
                self._refresh_position_profits()
                self._refresh_account()
            logger.info(
                "PAPER persist restored",
                path=str(path),
                balance=self.balance,
                positions=len(self._positions),
                closed=len(self._closed_tickets),
            )
            return True
        except Exception as exc:
            with contextlib.suppress(Exception):
                logger.warning("PAPER load failed", error=str(exc), path=str(path))
            return False

    def _clear_persisted_state(self) -> None:
        """Remove the persistence file (test teardown helper)."""
        try:
            p = self._persist_path()
            if p.exists():
                p.unlink()
        except Exception:
            pass
        # also clear in-memory closed set for the instance
        with contextlib.suppress(Exception):
            self._closed_tickets = set()

    def connect(self) -> bool:
        """Initializes paper trading simulation state."""
        self._connected = True
        # Restore durable state if present — loaded balance wins over initial_balance (no hidden reset).
        with contextlib.suppress(Exception):
            self._load_state()
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

    # ------------------------------------------------------------------
    # Accounting (Tasks B/D) — unrealized, realized, reconciliation
    # ------------------------------------------------------------------

    def _contract_size(self, symbol: str) -> float:
        return 100.0 if self._symbol_is_metal(symbol) else 100000.0

    def _realized_pnl(self, pos: Any, close_price: float, close_volume: float) -> float:
        contract = self._contract_size(pos.symbol)
        if pos.type == OrderType.BUY:
            return round(
                (float(close_price) - float(pos.price_open)) * float(close_volume) * contract, 2
            )
        # SELL
        return round(
            (float(pos.price_open) - float(close_price)) * float(close_volume) * contract, 2
        )

    def _current_price_for_pnl(self, pos: Any) -> float | None:
        """Mid-close price for unrealized: BUY→bid, SELL→ask."""
        # Prefer cached last tick for that symbol; otherwise generate one
        try:
            tick = (
                self._last_tick
                if (
                    self._last_tick is not None
                    and getattr(self._last_tick, "symbol", None) == pos.symbol
                )
                else None
            )
            if tick is None:
                tick = self.get_last_tick(pos.symbol)
        except Exception:
            return None
        if pos.type == OrderType.BUY:
            return float(tick.bid)
        return float(tick.ask)

    def _floating_pnl(self) -> float:
        total = 0.0
        for pos in self._positions:
            cp = self._current_price_for_pnl(pos)
            if cp is None:
                # fall back to stored profit
                total += float(getattr(pos, "profit", 0.0))
                continue
            total += self._realized_pnl(pos, cp, float(pos.volume))
        return round(total, 2)

    def _refresh_account(self) -> None:
        """Recompute equity = balance + unrealized."""
        floating = self._floating_pnl()
        self.equity = round(float(self.balance) + floating, 2)

    def reconcile_accounting(self) -> dict[str, Any]:
        """Task D: assert equity == balance + unrealized within 1 cent.

        Returns dict with balance/equity/floating and ok flag; raises
        AssertionError if invariant violated.
        """
        floating = self._floating_pnl()
        expected_equity = round(float(self.balance) + floating, 2)
        ok = abs(float(self.equity) - expected_equity) < 0.015
        info: dict[str, Any] = {
            "balance": float(self.balance),
            "equity": float(self.equity),
            "floating_pnl": floating,
            "expected_equity": expected_equity,
            "ok": ok,
        }
        assert ok, f"equity invariant broken: {info}"
        # also assert each position profit matches tick (within 2c)
        for pos in self._positions:
            cp = self._current_price_for_pnl(pos)
            if cp is not None:
                expected = self._realized_pnl(pos, cp, float(pos.volume))
                assert abs(float(pos.profit) - expected) < 0.03, (
                    f"pos {pos.ticket} profit stale: {pos.profit} vs {expected}"
                )
        return info

    def _refresh_position_profits(self) -> None:
        """Rewrite position.profit to match current tick (frozen model)."""
        refreshed: list[Any] = []
        for pos in self._positions:
            cp = self._current_price_for_pnl(pos)
            if cp is None:
                refreshed.append(pos)
                continue
            pnl = self._realized_pnl(pos, cp, float(pos.volume))
            if abs(float(pos.profit) - pnl) > 0.005:
                refreshed.append(pos.model_copy(update={"profit": float(pnl)}))
            else:
                refreshed.append(pos)
        self._positions = refreshed

    def get_account_snapshot(self) -> AccountSnapshot:
        # keep equity consistent before snapshot
        self._refresh_position_profits()
        self._refresh_account()
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
        """Returns virtual account snapshot (equity = balance + unrealized)."""
        self._refresh_position_profits()
        self._refresh_account()
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

        Determinism (capsizer): when ``NEXUS_PAPER_STRESS_SEED`` is set the
        tick stream is reproducible — the adapter owns a per-instance
        ``random.Random(self._seed)`` via ``_get_seed()`` so the same seed
        yields the same sequence. Dynamics are AR(1) with ``phi=0.6`` and
        persistent volatility blocks (calm/storm regimes of 15-45 ticks)
        rather than independent uniform steps. If
        ``market_data.paper_stress.PaperStressMarket`` is present and a seed
        is set, ticks/bars delegate there; otherwise the local AR(1) fallback
        is used. Spread baseline is tight ``8-18c`` for XAUUSD
        (``_METAL_SPREAD_RANGE``) scaled by ``_effective_spread_scale()``.
        """
        # Delegation: when seeded and PaperStressMarket exists, prefer it.
        # Keep narrow suppress so missing/incompatible module never breaks adapter.
        _seed_now = getattr(self, "_seed", None)
        if _seed_now is None:
            _seed_now = _get_seed()
            # refresh per-instance seed/rng if env changed mid-session
            if _seed_now is not None and _seed_now != getattr(self, "_seed", None):
                self._seed = _seed_now
                self._rng = random.Random(_seed_now)
        # PAPER REPLAY MODE: serve the REAL historical chronology while it
        # lasts. The replay source owns pricing (recorded bid/ask, or the
        # dataset-builder close+spread convention for bar records) and the
        # HISTORICAL timestamp — wall-clock is never substituted in replay.
        # Auto SL/TP execution and persistence still run on every replayed
        # tick, so paper positions behave exactly as in synthetic mode.
        if getattr(self, "market_data_mode", "SYNTHETIC") == "REPLAY":
            digits = self._quote_digits(symbol)
            src = getattr(self, "_replay_source", None)
            nxt = src.next_tick() if src is not None else None
            if nxt is not None:
                tick = TickData(
                    symbol=symbol,
                    timestamp=nxt["timestamp"],
                    bid=round(float(nxt["bid"]), digits),
                    ask=round(float(nxt["ask"]), digits),
                    last=round(float(nxt["bid"]), digits),
                    volume=float(nxt.get("volume", 0.0) or 0.0),
                    flags=6,
                )
                self._current_price = tick.bid
                self._last_tick = tick
                self._last_tick_time = tick.timestamp
                try:
                    self._last_tick_iso = tick.timestamp.isoformat()
                except Exception:
                    self._last_tick_iso = None
                try:
                    self.process_tick_execution(tick)
                except Exception as exc:  # defensive: tick feed must never break
                    logger.error(
                        "PAPER_SLTP_PROCESSING_FAILED",
                        error=repr(exc),
                        symbol=symbol,
                    )
                with contextlib.suppress(Exception):
                    self._persist_state()
                return tick
            # End of historical data: REPLAY is fail-closed for market data —
            # an exhausted replay source must NOT silently flip to a synthetic
            # random walk (that would fabricate "market" experience). Surface
            # the stall honestly: freeze on the last historical tick.
            if self._last_tick is not None:
                logger.warning(
                    "[PAPER] event=REPLAY_EXHAUSTED — market data frozen at last "
                    "historical tick (no synthetic fallback)"
                )
                return self._last_tick
            raise ReplayDataUnavailableError(
                "Paper REPLAY mode: no historical records available and no "
                "seed tick — cannot serve market data."
            )
        if _seed_now is not None:
            with contextlib.suppress(Exception):
                from nexus_scalp.market_data.paper_stress import PaperStressMarket  # type: ignore

                m = PaperStressMarket(seed=_seed_now)  # type: ignore
                for _attr in ("next_tick", "get_tick", "generate_tick", "tick"):
                    fn = getattr(m, _attr, None)
                    if callable(fn):
                        d = fn(symbol)
                        if isinstance(d, dict) and "bid" in d and "ask" in d:
                            digits = self._quote_digits(symbol)
                            tick = TickData(
                                symbol=symbol,
                                timestamp=datetime.now(UTC),
                                bid=round(float(d["bid"]), digits),
                                ask=round(float(d["ask"]), digits),
                                last=round(float(d.get("last", d["bid"])), digits),
                                volume=float(d.get("volume", self._rng.randint(1, 15))),
                                flags=int(d.get("flags", 6)),
                            )
                            self._current_price = tick.bid
                            self._last_tick = tick
                            self._last_tick_time = tick.timestamp
                            try:
                                self.process_tick_execution(tick)
                            except Exception as exc:  # defensive: tick feed must never break
                                try:
                                    from nexus_scalp.observability.logging import get_logger as _gl

                                    _gl("nexus_scalp.adapters.paper").error(
                                        "PAPER_SLTP_PROCESSING_FAILED",
                                        error=repr(exc),
                                        symbol=symbol,
                                    )
                                except Exception:
                                    pass
                            return tick
        digits = self._quote_digits(symbol)
        upper = (symbol or "").upper()
        scale = self._effective_spread_scale()
        baseline = self._seed_price(upper)
        # Volatility block: persistent calm/storm multiplier 15-45 ticks.
        if getattr(self, "_vol_block_ticks_left", 0) <= 0:
            self._vol_block = self._rng.uniform(0.65, 1.65)
            self._vol_block_ticks_left = self._rng.randint(15, 45)
        self._vol_block_ticks_left -= 1

        phi = 0.6
        if digits == 2:
            lo, hi = self._METAL_SPREAD_RANGE
            lo *= scale
            hi *= scale
            lo = max(0.02, min(lo, 2.0))
            hi = max(lo + 0.02, min(hi, 3.0))
            noise = self._rng.gauss(0, 0.09)
            step = phi * self._prev_step + noise * self._vol_block
            step = max(-0.55, min(0.55, step))
            spread = round(self._rng.uniform(lo, hi), 2)
        else:
            noise = self._rng.gauss(0, 0.00009)
            step = phi * self._prev_step + noise * self._vol_block
            step = max(-0.00045, min(0.00045, step))
            spread = round(self._FX_SPREAD * scale, 5)

        self._prev_step = step

        candidate = self._current_price + step
        # Mean-revert toward the seed when the walk drifts > 2% away.
        if baseline > 0 and abs(candidate - baseline) > baseline * 0.02:
            candidate = baseline + (candidate - baseline) * 0.5

        self._current_price = round(candidate, digits)
        bid = round(self._current_price, digits)
        ask = round(bid + spread, digits)

        tick = TickData(
            symbol=symbol,
            timestamp=datetime.now(UTC),
            bid=bid,
            ask=ask,
            last=bid,
            volume=float(self._rng.randint(1, 15)),
            flags=6,
        )
        # cache for stale-tick guard and floating-PnL
        self._last_tick = tick
        self._last_tick_time = tick.timestamp
        # PAPER Reality Phase 2: automatic SL/TP execution — every generated
        # tick is immediately checked against open positions (broker-managed
        # stop realism). process_tick_execution uses the cached tick only, so
        # this never recurses back into get_last_tick.
        try:
            self.process_tick_execution(tick)
        except Exception as exc:  # defensive: tick feed must never break
            logger.error(
                "PAPER_SLTP_PROCESSING_FAILED",
                error=repr(exc),
                symbol=symbol,
            )
        # Durability: the tick may have mutated state (auto SL/TP close) —
        # record its timestamp and persist post-execution state.
        try:
            self._last_tick_iso = tick.timestamp.isoformat()
        except Exception:
            self._last_tick_iso = None
        with contextlib.suppress(Exception):
            self._persist_state()
        return tick

    # ------------------------------------------------------------------
    # PAPER Reality Phase 2 — automatic SL/TP execution on every tick
    # ------------------------------------------------------------------

    def _slippage_for_close(self, symbol: str, side: str) -> float:
        """Deterministic adverse slippage for a stop-level close.

        Drawn from the instance RNG (``self._rng``) so a seeded run replays
        identically: same seed -> same slip sequence -> same close prices.
        STOP orders absorb adverse slippage only (never favourable), matching
        broker stop semantics.
        """
        is_metal = self._symbol_is_metal(symbol)
        if is_metal:
            slip = self._rng.uniform(0.01, 0.04)  # 1-4 cents adverse
        else:
            slip = self._rng.uniform(0.00002, 0.00006)
        return slip

    def process_tick_execution(self, tick: Any | None = None) -> list[dict[str, Any]]:
        """Evaluate open positions against the CURRENT tick and auto-execute SL/TP.

        Called automatically at the end of :meth:`get_last_tick` so paper
        positions behave like broker-managed stops (no manual close needed).
        May also be called directly with an explicit tick.

        Semantics (broker-style):
          - BUY  SL (STOP): fills when ``tick.bid <= pos.sl`` at
            ``min(tick.bid, pos.sl)`` MINUS deterministic slippage.
          - BUY  TP (LIMIT): fills when ``tick.bid >= pos.tp`` AT ``pos.tp``
            (limit semantics — fill at the limit price, no positive slippage).
          - SELL SL (STOP): fills when ``tick.ask >= pos.sl`` at
            ``max(tick.ask, pos.sl)`` PLUS deterministic slippage.
          - SELL TP (LIMIT): fills when ``tick.ask <= pos.tp`` AT ``pos.tp``.

        Iterates a SNAPSHOT copy of ``self._positions`` so each position is
        evaluated independently; closes go through the existing
        :meth:`close_position` path exactly once per event (the ticket is
        removed from ``self._positions`` first, so a manual close racing the
        auto close cannot double-credit balance).

        Returns a list of dicts ``{ticket, event, close_price, realized_pnl}``
        (empty when nothing triggered).
        """
        triggered: list[dict[str, Any]] = []
        # Use the CURRENT tick: explicit argument, else the last generated one.
        if tick is None:
            tick = self._last_tick
        if tick is None:
            return triggered
        if not self._positions:
            return triggered

        # SNAPSHOT: independent evaluation per position, immune to list churn
        # caused by closes during iteration.
        for pos in list(self._positions):
            # Only positions on the tick's symbol react to this tick.
            if getattr(pos, "symbol", None) != getattr(tick, "symbol", None):
                continue
            # Already-fired guard: if the ticket vanished from the live book
            # between snapshot and evaluation, skip (closed manually mid-loop).
            live = next((p for p in self._positions if p.ticket == pos.ticket), None)
            if live is None:
                continue
            side = pos.type.value if hasattr(pos.type, "value") else str(pos.type)
            is_buy = pos.type == OrderType.BUY
            bid = float(tick.bid)
            ask = float(tick.ask)
            digits = self._quote_digits(pos.symbol)

            sl = float(pos.sl) if pos.sl else 0.0
            tp = float(pos.tp) if pos.tp else 0.0

            event: str | None = None
            close_price: float | None = None

            if is_buy:
                # BUY SL: bid touched-or-crossed the stop -> STOP fill with
                # adverse slippage (worse than the stop level).
                if sl > 0 and bid <= sl:
                    slip = self._slippage_for_close(pos.symbol, "BUY")
                    close_price = round(min(bid, sl) - slip, digits)
                    if close_price <= 0:
                        close_price = min(bid, sl)
                    event = "SL_HIT"
                # BUY TP: bid reached the target -> LIMIT fill at TP.
                elif tp > 0 and bid >= tp:
                    close_price = round(float(tp), digits)
                    event = "TP_HIT"
            # SELL SL: ask touched-or-crossed the stop -> STOP fill with
            # adverse slippage (worse than the stop level).
            elif sl > 0 and ask >= sl:
                slip = self._slippage_for_close(pos.symbol, "SELL")
                close_price = round(max(ask, sl) + slip, digits)
                event = "SL_HIT"
            # SELL TP: ask reached the target -> LIMIT fill at TP.
            elif tp > 0 and ask <= tp:
                close_price = round(float(tp), digits)
                event = "TP_HIT"

            if event is None or close_price is None:
                continue

            balance_before = float(self.balance)
            ticket = int(pos.ticket)
            volume = float(pos.volume)

            # Guarded single-credit close: remove the ticket from the live
            # book BEFORE crediting, so a concurrent manual close_position()
            # for the same ticket finds nothing and returns False (and vice
            # versa — exactly one path can ever credit balance for a ticket).
            self._positions = [p for p in self._positions if p.ticket != ticket]

            # Credit via the SAME accounting primitives close_position() uses
            # (full-close branch): _realized_pnl + balance mutation +
            # _refresh_account. close_position() itself cannot be re-used
            # verbatim here because it re-locates the ticket in
            # self._positions — which we just removed as the double-credit
            # guard — and would fall back to a fresh tick from get_last_tick
            # (recursion). Same helpers => same numbers, zero double credit.
            realized_pnl = self._realized_pnl(pos, close_price, volume)
            self.balance = round(float(self.balance) + realized_pnl, 2)
            self._refresh_account()

            triggered.append(
                {
                    "ticket": ticket,
                    "event": event,
                    "close_price": float(close_price),
                    "realized_pnl": float(realized_pnl),
                }
            )
            # Contract line: [PAPER_SLTP] event=SL_HIT|TP_HIT ticket side sl/tp
            # price close_price realized_pnl balance_before balance_after.
            # The event NAME rides in the message prefix because 'event' is the
            # reserved structlog record key (kwargs named event collide).
            logger.info(
                f"[PAPER_SLTP] event={event}",
                ticket=ticket,
                side=side,
                sl=sl,
                tp=tp,
                price=float(close_price),
                close_price=float(close_price),
                realized_pnl=float(realized_pnl),
                balance_before=balance_before,
                balance_after=float(self.balance),
            )

        # Durability: persist after any auto SL/TP close so a restart sees
        # the same balance and closed_tickets set (no double credit).
        if triggered:
            self._closed_tickets.update(int(t["ticket"]) for t in triggered)
            with contextlib.suppress(Exception):
                self._persist_state()
        # Equity already refreshed per close above.
        return triggered

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """
        Generates a deterministic-shaped synthetic OHLC history.

        Bars are produced by a bounded random walk around the current simulated
        price so warmup paths (feature engine, HTF aggregation) can be exercised
        offline. Timestamps are contiguous and ascending, matching the live
        contract that `get_historical_bars` returns completed bars only.

        PAPER REPLAY MODE: when a replay source is attached, history comes from
        the REAL historical record UP TO the replay cursor (causal: only the
        past, never ahead of the served tick). If the cursor has not passed the
        first `count` bars yet, the earliest available prefix is returned —
        matching live semantics where history is bounded by session start.
        """
        if getattr(self, "market_data_mode", "SYNTHETIC") == "REPLAY":
            src = getattr(self, "_replay_source", None)
            hist = src.history_bars(timeframe, count) if src is not None else []
            out: list[BarData] = []
            for h in hist:
                out.append(
                    BarData(
                        symbol=symbol,
                        timeframe=str(timeframe).upper(),
                        timestamp=h["timestamp"],
                        open=float(h["open"]),
                        high=float(h["high"]),
                        low=float(h["low"]),
                        close=float(h["close"]),
                        tick_volume=int(h.get("tick_volume", 0) or 0),
                        is_complete=True,
                    )
                )
            return out
        # Delegation: seeded bar history via paper_stress when available.
        _seed_now = getattr(self, "_seed", None)
        if _seed_now is None:
            _seed_now = _get_seed()
        if _seed_now is not None:
            with contextlib.suppress(Exception):
                from nexus_scalp.market_data.paper_stress import PaperStressMarket  # type: ignore

                m = PaperStressMarket(seed=_seed_now)  # type: ignore
                for _attr in ("get_bars", "generate_bars", "get_historical_bars", "bars"):
                    fn = getattr(m, _attr, None)
                    if callable(fn):
                        out = fn(symbol, timeframe, count)
                        if isinstance(out, list) and out and isinstance(out[0], dict):
                            # Convert dict bars to BarData
                            bar_minutes2 = {
                                "M1": 1,
                                "M5": 5,
                                "M15": 15,
                                "M30": 30,
                                "H1": 60,
                                "H4": 240,
                            }.get(str(timeframe).upper(), 1)
                            now2 = datetime.now(UTC).replace(second=0, microsecond=0)
                            res: list[BarData] = []
                            for idx, d in enumerate(out[: int(count)]):
                                res.append(
                                    BarData(
                                        symbol=str(d.get("symbol", symbol)),
                                        timeframe=str(d.get("timeframe", timeframe)).upper(),
                                        timestamp=d.get("time")
                                        or d.get("timestamp")
                                        or (
                                            now2
                                            - timedelta(minutes=bar_minutes2 * (int(count) - idx))
                                        ),
                                        open=round(float(d["open"]), self._quote_digits(symbol)),
                                        high=round(float(d["high"]), self._quote_digits(symbol)),
                                        low=round(float(d["low"]), self._quote_digits(symbol)),
                                        close=round(float(d["close"]), self._quote_digits(symbol)),
                                        tick_volume=int(d.get("volume", d.get("tick_volume", 100))),
                                        is_complete=True,
                                    )
                                )
                            return res
            # Also try the actually-shipped PaperStressScenario class
            with contextlib.suppress(Exception):
                from nexus_scalp.market_data.paper_stress import (  # type: ignore
                    PaperStressScenario,
                    Regime,
                )

                scen = PaperStressScenario(symbol=symbol, regime_sequence=[Regime.RANGE])  # type: ignore
                dict_bars = scen.generate_bars(int(count), str(timeframe), seed=_seed_now)  # type: ignore
                if isinstance(dict_bars, list) and dict_bars:
                    # PaperStressScenario generates around 1.0; re-anchor to plausible price
                    # so bars don't collapse to 1.0 when delegation is used blindly.
                    # If values look anchored (<100 for XAU), skip delegation and use fallback.
                    probe = float(dict_bars[0].get("close", dict_bars[0].get("open", 0)) or 0)
                    digits_probe = self._quote_digits(symbol)
                    # Heuristic: delegate only if close is within 50% of seed baseline
                    baseline_probe = self._seed_price(symbol.upper())
                    if baseline_probe > 0 and abs(probe - baseline_probe) / baseline_probe < 0.6:
                        bar_minutes2 = {
                            "M1": 1,
                            "M5": 5,
                            "M15": 15,
                            "M30": 30,
                            "H1": 60,
                            "H4": 240,
                        }.get(str(timeframe).upper(), 1)
                        now2 = datetime.now(UTC).replace(second=0, microsecond=0)
                        res2: list[BarData] = []
                        for idx, d in enumerate(dict_bars[: int(count)]):
                            res2.append(
                                BarData(
                                    symbol=str(d.get("symbol", symbol)),
                                    timeframe=str(d.get("timeframe", timeframe)).upper(),
                                    timestamp=now2
                                    - timedelta(minutes=bar_minutes2 * (int(count) - idx)),
                                    open=round(float(d["open"]), digits_probe),
                                    high=round(float(d["high"]), digits_probe),
                                    low=round(float(d["low"]), digits_probe),
                                    close=round(float(d["close"]), digits_probe),
                                    tick_volume=int(d.get("volume", 100)),
                                    is_complete=True,
                                )
                            )
                        if res2:
                            return res2
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
            drift = self._rng.uniform(-amplitude, amplitude)
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
                    tick_volume=self._rng.randint(50, 250),
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
        """Simulates immediate market fill. F: duplicate order_id guard."""
        oid = getattr(order, "order_id", None)
        if oid:
            if oid in self._seen_order_ids:
                logger.warning("PAPER ORDER REJECTED (duplicate order_id)", order_id=oid)
                # Priority 4: ledger the duplicate rejection so it is data, not just a log line.
                try:
                    _, lbid, lask, lspread = self._ledger_quote(order.symbol)
                except Exception:
                    lbid = lask = lspread = 0.0
                self._ledger_append(
                    symbol=order.symbol,
                    order_type=order.order_type,
                    volume=order.volume,
                    requested_price=order.price,
                    bid=lbid,
                    ask=lask,
                    spread=lspread,
                    fill_price=None,
                    slippage=None,
                    rejection_reason="duplicate_order_id",
                )
                return False
            self._seen_order_ids.add(oid)
            # also reject if any open position already reflects that order_id via magic/comment deduplication window
            # simple: still-open order_id map
        ok = (
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
        if not ok and oid:
            # release id so retry after failure is allowed (only block while open)
            self._seen_order_ids.discard(oid)
        return ok

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

    def _is_stale_tick(self) -> bool:
        """H: reject when last tick older than 30s."""
        if self._last_tick_time is None:
            return False
        try:
            # PAPER REPLAY MODE: historical tick timestamps are ALWAYS older
            # than 30s of wall-clock — the stale-tick guard is a LIVE-feed
            # protection and must not reject fills in replay. Replay data
            # freshness is governed by the source chronology itself.
            if getattr(self, "market_data_mode", "SYNTHETIC") == "REPLAY":
                return False
            age = (datetime.now(UTC) - self._last_tick_time).total_seconds()
            return age > 30.0
        except Exception:
            return False

    def _margin_required(self, symbol: str, price: float, volume: float) -> float:
        contract = self._contract_size(symbol)
        return round((contract * float(price) * float(volume)) / 100.0, 4)

    # ------------------------------------------------------------------
    # Execution ledger (Priority 4 — audit trail)
    # ------------------------------------------------------------------

    def _ledger_quote(self, symbol: str) -> tuple[Any | None, float, float, float]:
        """Return (tick, bid, ask, spread) at request time for the ledger.

        Never advances the walk — reuses the cached last tick when it matches
        the symbol so ledger bid/ask reflect the quote the order saw.
        Returns (None, 0.0, 0.0, 0.0) when no cached quote exists.
        """
        tick = (
            self._last_tick
            if (self._last_tick is not None and getattr(self._last_tick, "symbol", None) == symbol)
            else None
        )
        if tick is None:
            return None, 0.0, 0.0, 0.0
        bid = float(tick.bid)
        ask = float(tick.ask)
        return tick, bid, ask, round(ask - bid, 6)

    def _ledger_append(
        self,
        *,
        symbol: str,
        order_type: OrderType,
        volume: float,
        requested_price: float,
        bid: float,
        ask: float,
        spread: float,
        fill_price: float | None,
        slippage: float | None,
        rejection_reason: str | None,
        ticket: int = 0,
        latency_ticks: int = 0,
    ) -> dict[str, Any]:
        """Append one execution-ledger row and return it.

        Every order attempt (fill or rejection) lands here exactly once.
        ``rejection_reason`` is non-None iff ``is_fill`` is False.
        """
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "symbol": symbol,
            "order_type": getattr(order_type, "value", str(order_type)),
            "volume": float(volume),
            "requested_price": float(requested_price),
            "bid_at_request": float(bid),
            "ask_at_request": float(ask),
            "spread": float(spread),
            "fill_price": (float(fill_price) if fill_price is not None else None),
            "slippage": (float(slippage) if slippage is not None else None),
            "latency_ticks": int(latency_ticks),
            "rejection_reason": rejection_reason,
            "is_fill": rejection_reason is None,
            "ticket": int(ticket),
        }
        self._execution_ledger.append(entry)
        return entry

    def get_execution_ledger(self) -> list[dict[str, Any]]:
        """Read-only copy of the execution ledger (audit consumers)."""
        return [dict(e) for e in self._execution_ledger]

    def clear_execution_ledger(self) -> None:
        """Reset the ledger (per-scenario isolation in tests/stress runs)."""
        self._execution_ledger = []

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
        """Creates the simulated position and returns its ticket (0 on refusal).

        E) Fill-price realism: BUY fills at current ask, SELL at current bid
           (not order.price), plus deterministic seed-based slippage so fill !=
           requested price when the market moved.  G) Margin check vs free
           margin.  H) Stale-tick guard (>30s).
        Priority 4: every path appends one execution-ledger row; rejections
        carry ``rejection_reason`` and fills record requested vs fill price,
        spread and slippage as data (not log prints).
        """
        if volume <= 0.0 or price <= 0.0:
            logger.warning("PAPER ORDER REJECTED (invalid size/price)", volume=volume, price=price)
            self._ledger_append(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                requested_price=float(price),
                bid=0.0,
                ask=0.0,
                spread=0.0,
                fill_price=None,
                slippage=None,
                rejection_reason="invalid_size_or_price",
            )
            with contextlib.suppress(Exception):
                self._persist_state()
            return 0

        # H: stale tick guard
        if self._is_stale_tick():
            logger.warning("PAPER ORDER REJECTED (stale tick >30s)", symbol=symbol, price=price)
            _, lbid, lask, lspread = self._ledger_quote(symbol)
            self._ledger_append(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                requested_price=float(price),
                bid=lbid,
                ask=lask,
                spread=lspread,
                fill_price=None,
                slippage=None,
                rejection_reason="stale_tick_gt_30s",
            )
            with contextlib.suppress(Exception):
                self._persist_state()
            return 0

        # G: margin check
        try:
            self._refresh_position_profits()
            self._refresh_account()
            free = float(self.equity)  # margin==0 in paper; free == equity
            req = self._margin_required(symbol, float(price), float(volume))
            if req > free:
                logger.warning(
                    "PAPER ORDER REJECTED (insufficient margin)",
                    symbol=symbol,
                    required=req,
                    free=free,
                )
                _, lbid, lask, lspread = self._ledger_quote(symbol)
                self._ledger_append(
                    symbol=symbol,
                    order_type=order_type,
                    volume=volume,
                    requested_price=float(price),
                    bid=lbid,
                    ask=lask,
                    spread=lspread,
                    fill_price=None,
                    slippage=None,
                    rejection_reason="insufficient_margin",
                )
                with contextlib.suppress(Exception):
                    self._persist_state()
                return 0
        except Exception:
            pass

        # E: fill-price realism — fill at current tick bid/ask, not order.price
        latency_ticks = 0
        try:
            tick = (
                self._last_tick
                if (
                    self._last_tick is not None
                    and getattr(self._last_tick, "symbol", None) == symbol
                )
                else None
            )
            if tick is None:
                tick = self.get_last_tick(symbol)
                latency_ticks = 1  # order waited one tick for a fresh quote
            digits = self._quote_digits(symbol)
            is_metal = self._symbol_is_metal(symbol)
            if order_type == OrderType.BUY:
                fill = float(tick.ask)
            else:
                fill = float(tick.bid)

            # deterministic slippage from seed: small adverse slip
            # derive from ticket counter + price so it is reproducible
            slip_seed = (int(self._ticket_counter * 1009) ^ int(abs(fill * 100))) % 997
            rng = random.Random(slip_seed)
            if is_metal:
                slip = round(rng.uniform(0.01, 0.04), 2)  # 1-4c adverse
            else:
                slip = round(rng.uniform(0.00002, 0.00006), 5)
            # adverse: BUY pays higher, SELL gets lower
            if order_type == OrderType.BUY:
                fill = round(fill + slip, digits)
            else:
                fill = round(fill - slip, digits)
                if fill <= 0:
                    fill = float(tick.bid)
            fill_tick = tick
        except Exception:
            fill = float(price)
            fill_tick = None
            digits = self._quote_digits(symbol)

        self._ticket_counter += 1
        # initial unrealized relative to fill vs current tick
        try:
            init_pnl = self._realized_pnl(
                type(
                    "P",
                    (),
                    {
                        "symbol": symbol,
                        "type": order_type,
                        "price_open": fill,
                        "volume": float(volume),
                    },
                )(),
                fill,
                float(volume),
            )
            # unrealized at fill moment is ~ -slip*volume*contract (small loss)
            init_pnl = 0.0  # overwrite — pnl is computed fresh on snapshot; keep 0 at open
        except Exception:
            init_pnl = 0.0

        pos = Position(
            ticket=self._ticket_counter,
            symbol=symbol,
            type=order_type,
            volume=volume,
            price_open=fill,
            sl=stop_loss,
            tp=take_profit,
            profit=float(init_pnl),
            magic=magic,
        )
        self._positions.append(pos)
        # refresh equity after fill
        with __import__("contextlib").suppress(Exception):
            self._refresh_position_profits()
            self._refresh_account()
        # Priority 4: ledger fill row — requested vs fill observable as data.
        if fill_tick is not None:
            self._ledger_append(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                requested_price=float(price),
                bid=float(fill_tick.bid),
                ask=float(fill_tick.ask),
                spread=round(float(fill_tick.ask) - float(fill_tick.bid), 6),
                fill_price=float(fill),
                slippage=round(
                    float(fill)
                    - (
                        float(fill_tick.ask)
                        if order_type == OrderType.BUY
                        else float(fill_tick.bid)
                    ),
                    6,
                ),
                rejection_reason=None,
                ticket=pos.ticket,
                latency_ticks=latency_ticks,
            )
        else:
            # defensive: quote unavailable at fill time — still record the row
            self._ledger_append(
                symbol=symbol,
                order_type=order_type,
                volume=volume,
                requested_price=float(price),
                bid=0.0,
                ask=0.0,
                spread=0.0,
                fill_price=float(fill),
                slippage=round(float(fill) - float(price), 6),
                rejection_reason=None,
                ticket=pos.ticket,
                latency_ticks=latency_ticks,
            )
        logger.info(
            "PAPER ORDER FILLED SIMULATION",
            ticket=pos.ticket,
            symbol=pos.symbol,
            type=pos.type.value,
            requested=price,
            fill=pos.price_open,
            volume=pos.volume,
            slip=round(fill - float(price), 6),
        )
        with contextlib.suppress(Exception):
            self._persist_state()
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
        """Closes simulated position: computes realized PnL, mutates balance/equity.

        Task B: BUY PnL=(close-bid/ask - open)*vol*contract (100 metals), SELL inverted.
        Partial closes correctly split volume and credit proportional PnL.
        Duplicate-close guard: if ticket is in _closed_tickets (persisted), do not credit again.
        """
        # Duplicate-close guard (already closed & persisted — never double-credit).
        try:
            if int(ticket) in getattr(self, "_closed_tickets", set()):
                logger.warning("PAPER CLOSE IGNORED (already closed)", ticket=ticket)
                return False
        except Exception:
            pass
        # locate position
        target = next((p for p in self._positions if p.ticket == ticket), None)
        if target is None:
            logger.warning("PAPER CLOSE FAILED (unknown ticket)", ticket=ticket)
            # If ticket was once open but already removed, treat as duplicate guard above.
            return False

        # close price: BUY closes at bid, SELL at ask
        try:
            tick = (
                self._last_tick
                if (
                    self._last_tick is not None
                    and getattr(self._last_tick, "symbol", None) == target.symbol
                )
                else None
            )
            if tick is None:
                tick = self.get_last_tick(target.symbol)
            close_price = float(tick.bid) if target.type == OrderType.BUY else float(tick.ask)
        except Exception:
            close_price = float(target.price_open)

        # partial close
        if volume is not None and float(volume) < float(target.volume) - 1e-9:
            vol = float(volume)
            pnl = self._realized_pnl(target, close_price, vol)
            self.balance = round(float(self.balance) + pnl, 2)
            remaining_vol = round(float(target.volume) - vol, 2)
            # recompute remaining unrealized at current tick
            new_profit = self._realized_pnl(
                type(
                    "P",
                    (),
                    {
                        "symbol": target.symbol,
                        "type": target.type,
                        "price_open": float(target.price_open),
                        "volume": remaining_vol,
                    },
                )(),
                close_price,
                remaining_vol,
            )
            new_pos = target.model_copy(
                update={"volume": remaining_vol, "profit": float(new_profit)}
            )
            self._positions.remove(target)
            self._positions.append(new_pos)
            self._refresh_account()
            logger.info(
                "PAPER POSITION PARTIALLY CLOSED",
                ticket=ticket,
                closed_vol=vol,
                remaining_vol=remaining_vol,
                realized_pnl=pnl,
                balance=self.balance,
                equity=self.equity,
            )
            # Partial closes do not mark the ticket as fully closed; just persist the split.
            with contextlib.suppress(Exception):
                self._persist_state()
            return True

        # full close
        pnl = self._realized_pnl(target, close_price, float(target.volume))
        self.balance = round(float(self.balance) + pnl, 2)
        self._positions = [p for p in self._positions if p.ticket != ticket]
        self._refresh_account()
        # Record closed ticket so a restart never double-credits it.
        try:
            self._closed_tickets.add(int(ticket))
        except Exception:
            pass
        # release dedup key if any (conservative: drop one arbitrary id — send_order tracks order_id->ticket externally)
        logger.info(
            "PAPER POSITION CLOSED",
            ticket=ticket,
            close_price=close_price,
            realized_pnl=pnl,
            balance=self.balance,
            equity=self.equity,
        )
        # Durability: write state after every close (balance/tickets/positions).
        with contextlib.suppress(Exception):
            self._persist_state()
        return True
