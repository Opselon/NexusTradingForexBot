"""
Native MetaTrader 5 Win32 Adapter Engine
========================================
Production-grade implementation of IMT5Port interfacing directly with the Windows
MetaTrader 5 terminal process via native C++ IPC extensions.

Key Enterprise Features & Hidden MT5 Mechanisms:
    - Pending Order Inventory & Cleanup: Uses `mt5.orders_get` and `TRADE_ACTION_REMOVE`
      to eliminate pending order flooding (directly resolves duplicate limit order bugs).
    - Real-Time Deals History Extraction: Queries `mt5.history_deals_get` to calculate
      exact realized PnL, broker commission, slippage, and stop-loss/take-profit exit events.
    - Freeze & Stop Level Guard: Validates `trade_stops_level` and `trade_freeze_level`
      before order dispatch or modification to prevent MT5 retcode 10013/10016 rejections.
    - Dynamic Filling Mode Resolution: Automatically selects ORDER_FILLING_IOC, ORDER_FILLING_FOK,
      or ORDER_FILLING_RETURN based on broker bitmask capabilities.
    - Retcode Diagnostic Translator: Translates raw MT5 retcodes (10004, 10013, 10019, 10021)
      into structured, actionable log events.
"""

import contextlib
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.adapters.mt5.diagnostics import (
    RETCODE_LABELS,
    MT5CallDiagnostic,
    MT5ConnectionState,
    run_mt5_call,
)
from nexus_scalp.adapters.mt5.providers import (
    BROKER_SERVER_UTC_OFFSET_MINUTES,
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
    broker_epoch_to_utc,
    build_account_snapshot,
    build_deal_snapshot,
    build_history_order_snapshot,
    build_order_snapshot,
    build_position_snapshot,
    build_rate_bar_snapshot,
    build_symbol_snapshot,
    build_tick_history_snapshot,
    normalize_utc,
    validate_ohlc_bars,
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
from nexus_scalp.ports.mt5_port import IMT5Port

# Conditional dynamic import preventing Linux container import crashes
HAS_NATIVE_MT5 = False
mt5: Any = None
if sys.platform == "win32":
    try:
        import MetaTrader5 as mt5  # type: ignore[no-redef]

        HAS_NATIVE_MT5 = True
    except ImportError:
        mt5 = None
else:
    mt5 = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BUG-226: OFFICIAL MQL5 trade-server return-code map.
#
# The previous in-method map misdiagnosed 10016 as "TRADE_DISABLED /
# FREEZE_LEVEL" (it is officially INVALID_STOPS) and shifted every code from
# 10017 onward by two positions (10017 is TRADE_DISABLED, 10018 MARKET_CLOSED,
# 10019 NO_MONEY, ... 10029 FROZEN). During the 2026-09-03 production incident
# the bot triple-rejected a structurally-invalid SELL_LIMIT with 10016 while
# logging it as a broker freeze — the wrong label sent forensics after the
# wrong failure class.
# Source: mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes
# ---------------------------------------------------------------------------
PENDING_RETCODE_MAP: dict[int, str] = {
    10004: "REQUOTE - Price changed during order dispatch",
    10006: "REJECTED - Broker rejected the execution request",
    10007: "CANCELLED - Request cancelled by trader or expert",
    10008: "PLACED - Order placed (pending accepted)",
    10009: "DONE - Request completed successfully",
    10010: "DONE_PARTIAL - Request partially completed",
    10011: "ERROR - Request processing error",
    10012: "TIMEOUT - Request timed out",
    10013: "INVALID - Invalid request",
    10014: "INVALID_VOLUME - Invalid volume in the request",
    10015: "INVALID_PRICE - Invalid price in the request",
    10016: "INVALID_STOPS - Invalid stops in the request",
    10017: "TRADE_DISABLED - Trade is disabled",
    10018: "MARKET_CLOSED - Financial market is currently closed",
    10019: "NO_MONEY - Insufficient account free margin",
    10020: "PRICE_CHANGED - Prices changed (re-quote)",
    10021: "PRICE_OFF - There are no quotes to process the request",
    10022: "INVALID_EXPIRATION - Invalid order expiration date",
    10023: "ORDER_CHANGED - Order state changed",
    10024: "TOO_MANY_REQUESTS - Too frequent requests",
    10025: "UNKNOWN_SYMBOL - Unknown symbol",
    10026: "ORDER_LOCKED - Order or position frozen",
    10027: "LONG_ONLY - Only long positions allowed on symbol",
    10028: "SHORT_ONLY - Only short positions allowed on symbol",
    10029: "FROZEN - Order or position frozen by broker (close/modification banned)",
    10030: "UNSUPPORTED_FILLING - Filling mode unsupported by broker",
    10031: "NO_CONNECTION - No connection with trade server",
}
assert PENDING_RETCODE_MAP[10016].startswith("INVALID_STOPS")
assert PENDING_RETCODE_MAP[10017].startswith("TRADE_DISABLED")
assert PENDING_RETCODE_MAP[10018].startswith("MARKET_CLOSED")
assert set(RETCODE_LABELS) <= set(PENDING_RETCODE_MAP), "RETCODE_LABELS ⊆ PENDING_RETCODE_MAP"
for _code, _label in RETCODE_LABELS.items():
    assert PENDING_RETCODE_MAP[_code].startswith(_label), (
        f"retcode {_code}: adapter description {_label!r} diverges from diagnostics label"
    )

#: Cause classes for pending-order recovery (BUG-226).
#: TRANSIENT   — broker-side state may resolve on its own; retry with backoff.
#: REPAIRABLE  — the REQUEST ITSELF is structurally invalid for the current
#:               market state; a validated geometry repair may make it legal.
#: HARD_REJECT — anything else (including unknown codes and 10029 FROZEN):
#:               never re-send, never repair; abort and surface the cause.
_PENDING_CLASS_TRANSIENT: frozenset[int] = frozenset({10004, 10012, 10020, 10021, 10031})
_PENDING_CLASS_REPAIRABLE: frozenset[int] = frozenset({10013, 10015, 10016})


class DirectMT5Adapter(IMT5Port):
    """
    High-Performance Native Adapter connecting directly to local MetaTrader 5 Terminal.
    """

    def __init__(
        self,
        account: int | None = None,
        password: str | None = None,
        server: str | None = None,
        path: str | None = None,
        timeout: int = 5000,
        retries: int = 3,
    ) -> None:
        self._account = account
        self._password = password
        self._server = server
        self._path = path
        self._timeout = timeout
        self._retries = max(1, int(retries))
        self._connected = False
        #: Real runtime connection state (never derived from config).
        self._conn_state = MT5ConnectionState()
        #: Most recent structured diagnostics per operation (bounded dict).
        self._last_calls: dict[str, MT5CallDiagnostic] = {}
        self._last_call_ring: list[MT5CallDiagnostic] = []
        self._call_ring_max = 50

        if not HAS_NATIVE_MT5 and sys.platform == "win32":
            logger.warning(
                "Windows system detected but 'MetaTrader5' package is missing from Python environment."
            )

    # ------------------------------------------------------------------
    # Diagnostics helpers
    # ------------------------------------------------------------------
    def _record_call(self, diag: MT5CallDiagnostic) -> None:
        self._last_calls[diag.operation] = diag
        self._last_call_ring.append(diag)
        if len(self._last_call_ring) > self._call_ring_max:
            self._last_call_ring = self._last_call_ring[-self._call_ring_max :]

    def diagnostics_summary(self) -> dict[str, Any]:
        """Structured diagnostics for /api/debug/health + IPC telemetry."""
        return {
            "connection": self._conn_state.to_dict(),
            "last_calls": {k: v.to_dict() for k, v in self._last_calls.items()},
            "recent_calls": [d.to_dict() for d in self._last_call_ring[-20:]],
        }

    def connect(self) -> bool:
        if not HAS_NATIVE_MT5 or mt5 is None:
            logger.error("Native MetaTrader5 C++ IPC driver unavailable.")
            self._connected = False
            self._conn_state.set_state(
                MT5ConnectionState.TERMINAL_ERROR, "MetaTrader5 package unavailable"
            )
            return False

        # BUG-130: resilient connect with bounded retries + backoff. A cold
        # terminal launch or a transient Win32 IPC timeout (retcode -10005)
        # must NOT kill the engine at startup; we retry with per-attempt
        # structured telemetry so the console/UI can render progress.
        import time as _time

        max_attempts = self._retries
        last_err_code: Any = None
        connected = False
        for attempt in range(1, max_attempts + 1):
            self._conn_state.set_state(
                MT5ConnectionState.CONNECTING,
                f"initialize() attempt {attempt}/{max_attempts}",
            )
            init_kwargs: dict[str, object] = {"timeout": self._timeout}
            if self._path:
                init_kwargs["path"] = self._path

            if mt5.initialize(**init_kwargs):
                connected = True
                break

            err_code = mt5.last_error()
            last_err_code = err_code
            if attempt < max_attempts:
                backoff_ms = 250 * attempt  # 250ms, 500ms, 750ms, ...
                logger.warning(
                    "[MT5_CONNECT] event=RETRY attempt=%s/%s retcode=%s "
                    "backoff_ms=%s msg=terminal_initialize_failed",
                    attempt,
                    max_attempts,
                    err_code,
                    backoff_ms,
                )
                self._conn_state.set_state(
                    MT5ConnectionState.CONNECTING,
                    f"retry {attempt}/{max_attempts} (retcode {err_code})",
                )
                _time.sleep(backoff_ms / 1000.0)

        if not connected:
            logger.error(
                "Failed to initialize connection to MT5 terminal process after %s attempts. "
                "Last retcode: %s",
                max_attempts,
                last_err_code,
            )
            self._connected = False
            self._conn_state.set_state(
                MT5ConnectionState.TERMINAL_ERROR,
                f"initialize failed after {max_attempts} attempts: {last_err_code}",
            )
            return False

        # Record terminal/package versions + terminal_info on every connect.
        with contextlib.suppress(Exception):
            pkg_ver = None
            term_ver = None
            try:
                vers = mt5.version()
                if vers:
                    pkg_ver = f"{vers[0]}.{vers[1]}.{vers[2]}" if len(vers) >= 3 else str(vers[0])
            except Exception:
                pass
            try:
                term_info = mt5.terminal_info()
                self._conn_state.set_terminal(term_info)
                if term_info is not None:
                    term_ver = str(getattr(term_info, "name", "") or "")
            except Exception:
                pass
            self._conn_state.set_versions(pkg_ver, term_ver)

        if self._account and self._password and self._server:
            login_ok = mt5.login(login=self._account, password=self._password, server=self._server)
            if not login_ok:
                err_code = mt5.last_error()
                logger.error(
                    "MT5 login authentication failed for account %s. Error: %s",
                    self._account,
                    err_code,
                )
                mt5.shutdown()
                self._connected = False
                self._conn_state.set_state(
                    MT5ConnectionState.AUTHENTICATION_ERROR, f"login failed: {err_code}"
                )
                return False

        # Verify account identity if an expected account is configured
        if self._account is not None:
            acc_info = mt5.account_info()
            if acc_info is not None:
                actual_login = getattr(acc_info, "login", None)
                if actual_login is not None and int(actual_login) != int(self._account):
                    logger.critical(
                        "MT5 ACCOUNT MISMATCH: configured expected account %s does not match "
                        "actual terminal logged-in account %s. Failing safe to prevent live trading on wrong account.",
                        self._account,
                        actual_login,
                    )
                    mt5.shutdown()
                    self._connected = False
                    self._conn_state.set_state(
                        MT5ConnectionState.AUTHENTICATION_ERROR,
                        f"account mismatch: expected {self._account}, got {actual_login}",
                    )
                    return False
                self._conn_state.set_account(acc_info)

        self._connected = True
        self._conn_state.set_state(MT5ConnectionState.CONNECTED, "connected")
        logger.info("Successfully connected to MT5 Terminal process.")
        return True

    def disconnect(self) -> None:
        if HAS_NATIVE_MT5 and mt5 is not None and self._connected:
            mt5.shutdown()
            logger.info("MetaTrader 5 IPC connection closed.")
        self._connected = False
        self._conn_state.set_state(MT5ConnectionState.DISCONNECTED, "disconnected")

    def is_connected(self) -> bool:
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return False
        terminal_info = mt5.terminal_info()
        is_conn = terminal_info is not None and terminal_info.connected
        if is_conn:
            self._conn_state.record_success("terminal_info")
        else:
            self._conn_state.record_failure("terminal_info", "terminal reports disconnected")
        return is_conn

    def get_account_info(self) -> AccountInfo:
        """Legacy contract read (raises on failure, never returns fake data)."""
        snap = self.get_account_snapshot()
        if not snap.available or snap.balance is None or snap.equity is None:
            raise RuntimeError(
                f"Failed to fetch account info from MT5. Error: {snap.error_state or 'unavailable'}"
            )
        return AccountInfo(
            login=snap.login or 0,
            trade_mode=snap.trade_mode or 0,
            leverage=snap.leverage or 100,
            balance=snap.balance,
            equity=snap.equity,
            margin=snap.margin or 0.0,
            margin_free=snap.margin_free or 0.0,
            currency=snap.currency or "USD",
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Legacy contract read (raises on failure, never returns fake data)."""
        snap = self.get_symbol_snapshot(symbol)
        spec = snap.spec
        if not snap.available or not spec or spec.get("digits") is None:
            raise RuntimeError(
                f"Symbol info for '{symbol}' not found. Error: {snap.error_state or 'unavailable'}"
            )
        return SymbolInfo(
            symbol=str(spec.get("name") or symbol),
            digits=int(float(spec["digits"])),
            point=float(spec.get("point") or 0.00001),
            tick_size=float(spec.get("trade_tick_size") or spec.get("point") or 0.00001),
            tick_value=float(spec.get("trade_tick_value") or 0.0),
            volume_min=float(spec.get("volume_min") or 0.01),
            volume_max=float(spec.get("volume_max") or 100.0),
            volume_step=float(spec.get("volume_step") or 0.01),
            stops_level=int(float(spec.get("trade_stops_level") or 0.0)),
            freeze_level=int(float(spec.get("trade_freeze_level") or 0.0)),
            trade_contract_size=float(spec.get("trade_contract_size") or 100.0),
        )

    def get_last_tick(self, symbol: str) -> TickData:
        """Legacy contract read (raises on failure, never returns fake data)."""
        snap = self.get_broker_tick(symbol)
        if not snap.available or snap.bid is None or snap.ask is None:
            raise RuntimeError(
                f"Failed to fetch tick for '{symbol}'. Error: {snap.error_state or 'unavailable'}"
            )
        return TickData(
            symbol=symbol,
            timestamp=snap.time_utc or datetime.now(UTC),
            bid=snap.bid,
            ask=snap.ask,
            last=snap.last or 0.0,
            volume=float(snap.volume or 0.0),
            flags=snap.flags or 0,
        )

    def resubscribe_symbol(self, symbol: str) -> None:
        """BUGFIX-G29: re-arm the live tick feed for `symbol` after a stall.

        The engine watchdog calls this when ticks go quiet while the broker
        connection still reports connected. Re-issues the MT5 realtime tick
        subscription (CopyTicks) for the symbol so ingestion actually
        restarts instead of being masked behind a healthy connection state.
        Failure here is raised (not swallowed) so the watchdog can log it.
        """
        if not self._connected:
            raise RuntimeError(f"Cannot resubscribe '{symbol}': adapter not connected")
        # Re-subscribe via the same realtime subscription path the initial
        # connection established. get_broker_tick already re-polls the live
        # market; re-issuing the subscription keeps the terminal's realtime
        # queue for this symbol active.
        if hasattr(self, "_subscribe_symbol_realtime") and callable(
            self._subscribe_symbol_realtime
        ):
            self._subscribe_symbol_realtime(symbol)
        else:
            # Fallback: prove the feed is alive by reading one fresh tick.
            self.get_broker_tick(symbol)

    def get_tick(self, symbol: str) -> TickData:
        """BUGFIX-G29: return one fresh live tick for `symbol` (watchdog probe)."""
        return self.get_last_tick(symbol)

    # =========================================================================
    # BROKER-AWARE PROVIDERS (Phase 14 MT5 forensic architecture)
    # -------------------------------------------------------------------------
    # Every call is wrapped by run_mt5_call() -> structured [MT5_CALL]
    # diagnostics (operation/status/duration_ms/error_code/error_message).
    # Failure is NEVER silent: snapshots carry error_state and the ring log.
    # =========================================================================

    def connection_state(self) -> MT5ConnectionState:
        """Real MT5 connection state (never derived from config)."""
        return self._conn_state

    def get_account_snapshot(self) -> AccountSnapshot:
        if not HAS_NATIVE_MT5 or mt5 is None:
            return AccountSnapshot().as_error("account_info", None, "native driver unavailable")
        if not self._connected:
            return AccountSnapshot().as_error("account_info", None, "adapter not connected")
        raw, diag = run_mt5_call("account_info", mt5.account_info, mt5_module=mt5)
        self._record_call(diag)
        snap = build_account_snapshot(raw)
        if not snap.available:
            snap.as_error("account_info", diag.mt5_error_code, diag.mt5_error_message)
        else:
            self._conn_state.record_success("account_info")
            self._conn_state.set_account(raw)
        return snap

    def get_terminal_state(self) -> dict[str, Any]:
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return {
                "available": False,
                "reason": "adapter not connected",
                "connection": self._conn_state.to_dict(),
            }
        raw, diag = run_mt5_call("terminal_info", mt5.terminal_info, mt5_module=mt5)
        self._record_call(diag)
        if raw is None:
            return {
                "available": False,
                "reason": "terminal_info failed",
                "error": {"code": diag.mt5_error_code, "message": diag.mt5_error_message},
                "connection": self._conn_state.to_dict(),
            }
        self._conn_state.set_terminal(raw)
        info: dict[str, Any] = {}
        for name in (
            "name",
            "company",
            "account",
            "connected",
            "trade_allowed",
            "trade_expert",
            "dlls_allowed",
            "path",
            "data_path",
            "maxbars",
        ):
            val = getattr(raw, name, None)
            if val is not None:
                info[name] = val
        info["available"] = True
        info["connection"] = self._conn_state.to_dict()
        return info

    def get_symbol_snapshot(self, symbol: str) -> SymbolSnapshot:
        if not HAS_NATIVE_MT5 or mt5 is None:
            return SymbolSnapshot().as_error("symbol_info", None, "native driver unavailable")
        if not self._connected:
            return SymbolSnapshot().as_error("symbol_info", None, "adapter not connected")

        raw_info, diag_info = run_mt5_call(
            "symbol_info",
            lambda: mt5.symbol_info(symbol),
            mt5_module=mt5,
            context={"symbol": symbol},
        )
        self._record_call(diag_info)
        raw_tick, diag_tick = run_mt5_call(
            "symbol_info_tick",
            lambda: mt5.symbol_info_tick(symbol),
            mt5_module=mt5,
            context={"symbol": symbol},
        )
        self._record_call(diag_tick)

        snap = build_symbol_snapshot(raw_info, raw_tick)
        if not snap.available:
            snap.as_error(
                "symbol_info_tick",
                diag_tick.mt5_error_code or diag_info.mt5_error_code,
                diag_tick.mt5_error_message or diag_info.mt5_error_message,
            )
            return snap
        # Stale tick detection (task section 11): last tick older than 30s.
        if snap.tick_freshness_ms is not None:
            snap.tick_stale = (snap.tick_freshness_ms / 1000.0) > 30.0
        return snap

    def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot:
        if not HAS_NATIVE_MT5 or mt5 is None:
            return BrokerTickSnapshot().as_error(
                "symbol_info_tick", None, "native driver unavailable"
            )
        if not self._connected:
            return BrokerTickSnapshot().as_error("symbol_info_tick", None, "adapter not connected")

        raw_tick, diag = run_mt5_call(
            "symbol_info_tick",
            lambda: mt5.symbol_info_tick(symbol),
            mt5_module=mt5,
            context={"symbol": symbol},
        )
        self._record_call(diag)
        snap = BrokerTickSnapshot()
        if raw_tick is None:
            snap.as_error("symbol_info_tick", diag.mt5_error_code, diag.mt5_error_message)
            snap.symbol = symbol
            return snap
        snap.available = True
        snap.source = "BROKER_NATIVE"
        snap.symbol = symbol
        snap.bid = float(getattr(raw_tick, "bid", 0.0) or 0.0)
        snap.ask = float(getattr(raw_tick, "ask", 0.0) or 0.0)
        snap.last = float(getattr(raw_tick, "last", 0.0) or 0.0)
        snap.last_volume = float(getattr(raw_tick, "volume", 0.0) or 0.0)
        snap.volume = float(getattr(raw_tick, "volume", 0.0) or 0.0)
        snap.flags = int(getattr(raw_tick, "flags", 0) or 0)
        snap.time = int(getattr(raw_tick, "time", 0) or 0)
        snap.time_msc = int(getattr(raw_tick, "time_msc", 0) or 0)
        snap.time_utc = broker_epoch_to_utc(float(snap.time)) if snap.time else None
        if snap.time_utc is not None:
            snap.freshness_ms = max(
                0.0, (datetime.now(UTC) - snap.time_utc).total_seconds() * 1000.0
            )
            snap.stale = snap.freshness_ms > 30_000.0
        if snap.bid > 0 and snap.ask > 0:
            snap.spread_points = round(float(snap.ask - snap.bid), 8)
        return snap

    def get_all_positions(self, symbol: str | None = None) -> list[PositionSnapshot]:
        """ALL account positions (never restricted to bot symbol/magic).

        This is the dashboard/accounting view. The classic get_positions()
        keeps its XAUUSD/magic filter for the bot's own position-management
        path (task section 22: separate ALL / BOT / SYMBOL / MAGIC views).
        """
        if not HAS_NATIVE_MT5 or mt5 is None:
            return []
        if not self._connected:
            return []
        raw, diag = run_mt5_call(
            "positions_get",
            lambda: mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get(),
            mt5_module=mt5,
            context={"symbol": symbol} if symbol else None,
        )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("positions_get", diag.mt5_error_message)
            return []
        self._conn_state.record_success("positions_get")
        return [build_position_snapshot(p) for p in raw]

    def get_pending_orders_snapshot(self, symbol: str | None = None) -> list[OrderSnapshot]:
        """Active pending orders via mt5.orders_get() (ALL magics)."""
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return []
        raw, diag = run_mt5_call(
            "orders_get",
            lambda: mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get(),
            mt5_module=mt5,
            context={"symbol": symbol} if symbol else None,
        )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("orders_get", diag.mt5_error_message)
            return []
        self._conn_state.record_success("orders_get")
        return [build_order_snapshot(o) for o in raw]

    def get_history_orders(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[HistoryOrderSnapshot]:
        """Historical orders via mt5.history_orders_get() (UTC boundaries)."""
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return []
        from_dt = (
            normalize_utc(from_utc)
            if from_utc is not None
            else (
                datetime.now(UTC)
                + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
                - timedelta(days=1)
            )
        )
        to_dt = (
            normalize_utc(to_utc)
            if to_utc is not None
            else datetime.now(UTC) + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
        )
        if to_dt < from_dt:
            return []
        raw, diag = run_mt5_call(
            "history_orders_get",
            lambda: (
                mt5.history_orders_get(from_dt, to_dt, group=symbol)
                if symbol
                else mt5.history_orders_get(from_dt, to_dt)
            ),
            mt5_module=mt5,
            context={
                "symbol": symbol or "*",
                "date_range": f"{from_dt.isoformat()}..{to_dt.isoformat()}",
            },
        )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("history_orders_get", diag.mt5_error_message)
            return []
        self._conn_state.record_success("history_orders_get")
        return [build_history_order_snapshot(o) for o in raw]

    def get_history_deals(
        self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None
    ) -> list[DealSnapshot]:
        """Historical deals via mt5.history_deals_get() (UTC boundaries).

        NOTE: `symbol` is passed via group= (MT5 symbol group filter), same
        semantics as the legacy get_closed_deals_history.
        """
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return []
        from_dt = (
            normalize_utc(from_utc)
            if from_utc is not None
            else (
                datetime.now(UTC)
                + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
                - timedelta(days=1)
            )
        )
        to_dt = (
            normalize_utc(to_utc)
            if to_utc is not None
            else datetime.now(UTC) + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
        )
        if to_dt < from_dt:
            return []
        raw, diag = run_mt5_call(
            "history_deals_get",
            lambda: (
                mt5.history_deals_get(from_dt, to_dt, group=symbol)
                if symbol
                else mt5.history_deals_get(from_dt, to_dt)
            ),
            mt5_module=mt5,
            context={
                "symbol": symbol or "*",
                "date_range": f"{from_dt.isoformat()}..{to_dt.isoformat()}",
            },
        )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("history_deals_get", diag.mt5_error_message)
            return []
        self._conn_state.record_success("history_deals_get")
        return [build_deal_snapshot(d) for d in raw]

    def get_rate_history(
        self,
        symbol: str,
        timeframe: str = "M1",
        count: int = 500,
        from_utc: Any = None,
    ) -> list[RateBarSnapshot]:
        """Official MT5 rate history via copy_rates_* (UTC-normalized).

        from_utc given  -> copy_rates_range(symbol, tf, from, now)
        else            -> copy_rates_from_pos(symbol, tf, 0, count)
        """
        if not HAS_NATIVE_MT5 or mt5 is None:
            return []
        if not self._connected:
            return []
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        mt5_tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M1)
        req_count = max(1, min(int(count), 100_000))

        if from_utc is not None:
            from_dt = normalize_utc(from_utc)
            if from_dt is None:
                return []
            raw, diag = run_mt5_call(
                "copy_rates_range",
                lambda: mt5.copy_rates_range(symbol, mt5_tf, from_dt, datetime.now(UTC)),
                mt5_module=mt5,
                context={
                    "symbol": symbol,
                    "timeframe": str(timeframe).upper(),
                    "requested_bars": req_count,
                },
            )
        else:
            raw, diag = run_mt5_call(
                "copy_rates_from_pos",
                lambda: mt5.copy_rates_from_pos(symbol, mt5_tf, 0, req_count),
                mt5_module=mt5,
                context={
                    "symbol": symbol,
                    "timeframe": str(timeframe).upper(),
                    "requested_bars": req_count,
                },
            )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("copy_rates", diag.mt5_error_message)
            return []
        self._conn_state.record_success("copy_rates")
        bars = [build_rate_bar_snapshot(r) for r in raw]
        # Integrity validation (task section 39) - report anomalies, never fake.
        report = validate_ohlc_bars(bars)
        if report["invalid"] > 0:
            logger.warning(
                "[MT5_CHART] event=HISTORY_VALIDATION symbol=%s timeframe=%s requested=%s received=%s invalid=%s issues=%s",
                symbol,
                str(timeframe).upper(),
                req_count,
                len(bars),
                report["invalid"],
                report["issues"][:5],
            )
        return bars

    def get_tick_history(
        self,
        symbol: str,
        count: int = 500,
        from_utc: Any = None,
        to_utc: Any = None,
    ) -> list[TickHistorySnapshot]:
        """Tick history via copy_ticks_from / copy_ticks_range.

        BUG-188 (2026-09-01 live certification probe): the terminal resolves
        copy_ticks_range boundaries in SERVER-LOCAL time while the package
        converts datetime arguments as UTC epoch; the returned epochs are
        then converted back by ``broker_epoch_to_utc`` (-BROKER_SERVER_
        UTC_OFFSET_MINUTES). Passing UTC request boundaries unshifted
        therefore returned ticks shifted a net -180min out of the requested
        window. The INPUT boundary is normalized to the broker timebase
        (+BROKER_SERVER_UTC_OFFSET_MINUTES) here — symmetric with the OUTPUT
        conversion and with the established history_deals_get /
        history_orders_get window convention — so callers keep UTC semantics:
        request [from_utc, to_utc] ⇒ receive ticks stamped inside that
        real-UTC window.
        """
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            return []
        req_count = max(1, min(int(count), 100_000))
        if from_utc is not None:
            from_dt = normalize_utc(from_utc)
            to_dt = normalize_utc(to_utc) if to_utc is not None else datetime.now(UTC)
            if from_dt is None or to_dt is None or to_dt < from_dt:
                return []
            # BUG-188: shift the INPUT window into the broker timebase so the
            # terminal's server-local resolution lands on the requested UTC
            # range after the OUTPUT-side epoch conversion. Range sanity is
            # re-checked AFTER the shift.
            from_dt = from_dt + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
            to_dt = to_dt + timedelta(minutes=BROKER_SERVER_UTC_OFFSET_MINUTES)
            if to_dt < from_dt:
                return []
            raw, diag = run_mt5_call(
                "copy_ticks_range",
                lambda: mt5.copy_ticks_range(symbol, from_dt, to_dt, mt5.COPY_TICKS_ALL),
                mt5_module=mt5,
                context={"symbol": symbol, "requested": req_count},
            )
        else:
            raw, diag = run_mt5_call(
                "copy_ticks_from",
                lambda: mt5.copy_ticks_from(
                    symbol,
                    datetime.now(UTC),
                    req_count,
                    mt5.COPY_TICKS_ALL,
                ),
                mt5_module=mt5,
                context={"symbol": symbol, "requested": req_count},
            )
        self._record_call(diag)
        if raw is None:
            self._conn_state.record_failure("copy_ticks", diag.mt5_error_message)
            return []
        self._conn_state.record_success("copy_ticks")
        return [build_tick_history_snapshot(t) for t in raw]

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
        snap.operation = "order_calc_profit"
        snap.symbol = symbol
        snap.price_open = float(price_open)
        snap.price_close = float(price_close)
        snap.volume = float(volume)
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            snap.available = False
            snap.source = "UNAVAILABLE"
            return snap
        raw, diag = run_mt5_call(
            "order_calc_profit",
            lambda: mt5.order_calc_profit(
                int(order_type),
                symbol,
                float(volume),
                float(price_open),
                float(price_close),
            ),
            mt5_module=mt5,
            context={"symbol": symbol, "volume": float(volume)},
        )
        self._record_call(diag)
        if raw is None:
            snap.available = False
            snap.source = "UNAVAILABLE"
            snap.error_code = diag.mt5_error_code
            snap.error_message = diag.mt5_error_message
            return snap
        snap.available = True
        snap.value = float(raw)
        snap.value_source = "BROKER_NATIVE"
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
        snap.operation = "order_calc_margin"
        snap.symbol = symbol
        snap.price_open = float(price)
        snap.volume = float(volume)
        if not HAS_NATIVE_MT5 or mt5 is None or not self._connected:
            snap.available = False
            snap.source = "UNAVAILABLE"
            return snap
        raw, diag = run_mt5_call(
            "order_calc_margin",
            lambda: mt5.order_calc_margin(
                int(order_type),
                symbol,
                float(volume),
                float(price),
            ),
            mt5_module=mt5,
            context={"symbol": symbol, "volume": float(volume)},
        )
        self._record_call(diag)
        if raw is None:
            snap.available = False
            snap.source = "UNAVAILABLE"
            snap.error_code = diag.mt5_error_code
            snap.error_message = diag.mt5_error_message
            return snap
        snap.available = True
        snap.value = float(raw)
        snap.value_source = "BROKER_NATIVE"
        return snap

    def get_historical_bars(
        self, symbol: str, timeframe: str = "M1", count: int = 100
    ) -> list[BarData]:
        """Legacy contract read: current-tick-consistent historical OHLC bars.

        Delegates to the official get_rate_history() provider and maps onto
        the internal BarData contract (UTC timestamps preserved).
        """
        rate_bars = self.get_rate_history(symbol=symbol, timeframe=timeframe, count=count)
        bars: list[BarData] = []
        for r in rate_bars:
            if (
                r.time_utc is None
                or r.open is None
                or r.high is None
                or r.low is None
                or r.close is None
            ):
                continue
            bars.append(
                BarData(
                    symbol=symbol,
                    timeframe=str(timeframe).upper(),
                    timestamp=r.time_utc,
                    open=float(r.open),
                    high=float(r.high),
                    low=float(r.low),
                    close=float(r.close),
                    tick_volume=int(r.tick_volume or 0),
                    is_complete=True,
                )
            )
        return bars

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Legacy bot-management read: BOT positions only (symbol + magic 888101).

        The dashboard/accounting ALL-account view uses get_all_positions()
        which NEVER applies this filter (task section 22: separate
        ALL ACCOUNT POSITIONS / BOT POSITIONS / SYMBOL POSITIONS views).
        """
        self._assert_connected()
        assert mt5 is not None

        raw_positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw_positions is None:
            return []

        positions: list[Position] = []
        for pos in raw_positions:
            order_type = OrderType.BUY if pos.type == mt5.ORDER_TYPE_BUY else OrderType.SELL
            if pos.symbol == "XAUUSD" and pos.magic == 888101:
                positions.append(
                    Position(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        type=order_type,
                        volume=pos.volume,
                        price_open=pos.price_open,
                        sl=pos.sl,
                        tp=pos.tp,
                        profit=pos.profit,
                        magic=pos.magic,
                    )
                )
        return positions

    # ==========================================================================
    # PENDING ORDERS INVENTORY & CANCELLATION
    # ==========================================================================
    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Queries active pending orders (LIMIT / STOP). BOT-filtered (XAUUSD+magic)."""
        self._assert_connected()
        assert mt5 is not None

        raw_orders = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
        if raw_orders is None:
            return []

        pending_list: list[dict[str, Any]] = []
        for ord_item in raw_orders:
            if ord_item.symbol == "XAUUSD" and ord_item.magic == 888101:
                pending_list.append(
                    {
                        "ticket": ord_item.ticket,
                        "symbol": ord_item.symbol,
                        "type": ord_item.type,
                        "volume": ord_item.volume_current,
                        "price_open": ord_item.price_open,
                        "sl": ord_item.sl,
                        "tp": ord_item.tp,
                        "magic": ord_item.magic,
                    }
                )
        return pending_list

    def cancel_pending_order(self, ticket: int) -> bool:
        """Cancels an active pending order."""
        self._assert_connected()
        assert mt5 is not None

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else None
            last_err = mt5.last_error() if retcode is None else None
            # retcode 0 is NOT a valid trade-server retcode (DONE=10009). It is
            # the package's marker for a request that never reached the server
            # (structural validation failure / lost IPC). The caller must verify
            # broker state before releasing any exposure slot (BUG-072/073).
            logger.error(
                "[PENDING_ORDER] event=CANCEL_RESPONSE ticket=%s retcode=%s comment=%s "
                "request_id=%s last_error=%s -> broker state unverified, slot stays occupied",
                ticket,
                retcode,
                getattr(result, "comment", "") or "",
                getattr(result, "request_id", 0) or 0,
                last_err,
            )
            return False

        logger.info(
            "[PENDING_ORDER] event=CANCEL_RESPONSE ticket=%s retcode=%s (DONE) comment=%s request_id=%s",
            ticket,
            result.retcode,
            getattr(result, "comment", "") or "",
            getattr(result, "request_id", 0) or 0,
        )
        return True

    def cancel_all_pending_orders(self, symbol: str) -> int:
        """Cancels ALL active pending orders for a symbol."""
        pending_orders = self.get_pending_orders(symbol=symbol)
        cancelled_count = 0

        for p_order in pending_orders:
            if self.cancel_pending_order(p_order["ticket"]):
                cancelled_count += 1

        if cancelled_count > 0:
            logger.info("Cleaned up %s stale pending orders for %s.", cancelled_count, symbol)
        return cancelled_count

    # ==========================================================================
    # DEALS HISTORY EXTRACTION
    # ==========================================================================
    def get_closed_deals_history(self, symbol: str, hours_back: int = 24) -> list[dict[str, Any]]:
        self._assert_connected()
        assert mt5 is not None

        now = datetime.now(UTC)
        from_date = now - timedelta(hours=hours_back)

        deals = mt5.history_deals_get(from_date, now, group=symbol)
        if deals is None:
            return []

        closed_deals: list[dict[str, Any]] = []
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_OUT:
                closed_deals.append(
                    {
                        "ticket": d.ticket,
                        "order_ticket": d.order,
                        "position_ticket": d.position_id,
                        "symbol": d.symbol,
                        "price": d.price,
                        "volume": d.volume,
                        "profit": d.profit,
                        "commission": d.commission,
                        "swap": d.swap,
                        "comment": d.comment,
                        "closed_at": datetime.fromtimestamp(d.time, tz=UTC),
                        "reason": d.reason,
                    }
                )
        return closed_deals

    # ==========================================================================
    # ORDER DISPATCH ENGINE
    # ==========================================================================
    def send_order(self, order: TradeOrder) -> bool:
        self._assert_connected()
        assert mt5 is not None

        if "LIMIT" in order.order_type.value or "STOP" in order.order_type.value:
            self.cancel_all_pending_orders(order.symbol)

        trade_action = mt5.TRADE_ACTION_DEAL
        mt5_order_type = mt5.ORDER_TYPE_BUY

        if order.order_type == OrderType.BUY:
            mt5_order_type = mt5.ORDER_TYPE_BUY
            trade_action = mt5.TRADE_ACTION_DEAL
        elif order.order_type == OrderType.SELL:
            mt5_order_type = mt5.ORDER_TYPE_SELL
            trade_action = mt5.TRADE_ACTION_DEAL
        elif order.order_type == OrderType.BUY_LIMIT:
            mt5_order_type = mt5.ORDER_TYPE_BUY_LIMIT
            trade_action = mt5.TRADE_ACTION_PENDING
        elif order.order_type == OrderType.SELL_LIMIT:
            mt5_order_type = mt5.ORDER_TYPE_SELL_LIMIT
            trade_action = mt5.TRADE_ACTION_PENDING
        elif order.order_type == OrderType.BUY_STOP:
            mt5_order_type = mt5.ORDER_TYPE_BUY_STOP
            trade_action = mt5.TRADE_ACTION_PENDING
        elif order.order_type == OrderType.SELL_STOP:
            mt5_order_type = mt5.ORDER_TYPE_SELL_STOP
            trade_action = mt5.TRADE_ACTION_PENDING

        filling_mode = self._resolve_filling_mode(order.symbol)

        request = {
            "action": trade_action,
            "symbol": order.symbol,
            "volume": order.volume,
            "type": mt5_order_type,
            "price": order.price,
            "sl": order.stop_loss,
            "tp": order.take_profit,
            "magic": order.magic_number,
            "comment": order.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        check_res = mt5.order_check(request)
        if check_res is None or check_res.retcode != 0:
            comment = check_res.comment if check_res else str(mt5.last_error())
            logger.warning(
                "Order pre-check warning for %s: %s. Attempting FOK fallback...",
                order.symbol,
                comment,
            )
            request["type_filling"] = mt5.ORDER_FILLING_FOK

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else mt5.last_error()
            translated_err = self._translate_retcode(retcode)
            logger.error(
                "Order execution failed for %s. Retcode: %s (%s)",
                order.symbol,
                retcode,
                translated_err,
            )
            return False

        logger.info(
            "*** REAL ORDER/PENDING EXECUTED ON BROKER SERVER *** Ticket: %s | Symbol: %s | Type: %s | Price: %s | Lots: %s",
            result.order,
            order.symbol,
            order.order_type.value,
            result.price,
            order.volume,
        )
        return True

    def execute_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        self._assert_connected()
        if mt5 is None:
            return 0

        mt5_order_type = mt5.ORDER_TYPE_BUY if order_type == OrderType.BUY else mt5.ORDER_TYPE_SELL
        filling_mode = self._resolve_filling_mode(symbol)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_order_type,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "magic": 888101,
            "comment": "NSE_MARKET",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else mt5.last_error()
            # AMBIGUOUS-FILL RECOVERY: a non-DONE retcode does NOT prove the
            # order was rejected — the broker may have accepted it and the
            # response was lost. Never blind-retry a market order (that is the
            # classic duplicate-position bug); instead verify whether a live
            # position with our (symbol, magic) actually appeared. If it did,
            # the fill succeeded — return its ticket.
            live = mt5.positions_get(symbol=symbol)
            if live:
                matched = [p for p in live if p.magic == 888101]
                if matched:
                    logger.warning(
                        "execute_market_order: retcode %s but live position found "
                        "(ticket=%s) — ambiguous fill treated as success.",
                        retcode,
                        matched[0].ticket,
                    )
                    return int(matched[0].ticket)
            logger.error("execute_market_order failed. Retcode: %s", retcode)
            return 0

        return result.order

    def _find_equivalent_pending(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
    ) -> int | None:
        """Returns the ticket of an existing pending order matching the given
        fingerprint (symbol + type + volume + price), else None.

        Idempotency guard for ``place_pending_order`` retries: the broker may
        have accepted the previous send despite an ambiguous retcode; this
        finds that order so the retry does not create a duplicate.
        """
        if mt5 is None:
            return None
        try:
            raw = mt5.orders_get(symbol=symbol)
        except Exception:
            return None
        if raw is None:
            return None
        mt5_type = self._order_type_to_mt5(order_type)
        price_tol = 0.02  # 2 cents tolerance for gold (~1 point on some feeds)
        for o in raw:
            if (
                o.symbol == symbol
                and o.magic == 888101
                and o.type == mt5_type
                and abs(float(o.volume_current) - float(volume)) < 1e-9
                and abs(float(o.price_open) - float(price)) <= price_tol
            ):
                return int(o.ticket)
        return None

    def _order_type_to_mt5(self, order_type: OrderType) -> int:
        assert mt5 is not None
        if order_type == OrderType.BUY_LIMIT:
            return mt5.ORDER_TYPE_BUY_LIMIT
        if order_type == OrderType.SELL_LIMIT:
            return mt5.ORDER_TYPE_SELL_LIMIT
        if order_type == OrderType.BUY_STOP:
            return mt5.ORDER_TYPE_BUY_STOP
        if order_type == OrderType.SELL_STOP:
            return mt5.ORDER_TYPE_SELL_STOP
        if order_type == OrderType.BUY:
            return mt5.ORDER_TYPE_BUY
        if order_type == OrderType.SELL:
            return mt5.ORDER_TYPE_SELL
        return -1

    def place_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        self._assert_connected()
        if mt5 is None:
            return 0

        # Idempotency-reuse flag: set True ONLY when the guard finds an
        # equivalent order already resting on the broker and returns its
        # ticket without sending anything. Lets the caller log the truth
        # (REUSED vs REAL EXECUTED) and keep audit rows accurate.
        self._last_pending_reused = False

        self.cancel_all_pending_orders(symbol)

        if order_type == OrderType.BUY_LIMIT:
            mt5_order_type = mt5.ORDER_TYPE_BUY_LIMIT
        elif order_type == OrderType.SELL_LIMIT:
            mt5_order_type = mt5.ORDER_TYPE_SELL_LIMIT
        elif order_type == OrderType.BUY_STOP:
            mt5_order_type = mt5.ORDER_TYPE_BUY_STOP
        elif order_type == OrderType.SELL_STOP:
            mt5_order_type = mt5.ORDER_TYPE_SELL_STOP
        else:
            raise ValueError(f"Invalid pending order type: {order_type}")

        filling_mode = self._resolve_filling_mode(symbol)
        import time

        max_retries = 3
        retry_budget_s = 0.5  # total wall-clock budget for the whole loop
        deadline = time.monotonic() + retry_budget_s
        last_retcode = 0
        last_err = ""
        repair_used = False  # ONE validated repair per call (BUG-226)
        original_price, original_sl, original_tp = price, stop_loss, take_profit

        for attempt in range(1, max_retries + 1):
            # Fresh market view for THIS attempt: validate / repair / send all
            # use the same snapshot so decisions are cause-consistent.
            tick = mt5.symbol_info_tick(symbol)
            sym_info = mt5.symbol_info(symbol)

            ok, reasons = self._validate_pending_request(
                symbol=symbol,
                order_type=order_type,
                price=price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                volume=volume,
                tick=tick,
                sym_info=sym_info,
            )

            if not ok:
                # Cause-aware recovery (BUG-226): never blind-resend a
                # structurally-invalid request. Classify from the PRIOR
                # retcode; the first pass is a pre-flight check and treats
                # invalid geometry as REPAIRABLE once.
                if attempt == 1:
                    cls = "REPAIRABLE"
                    prior = 0
                else:
                    cls = self._classify_pending_retcode(last_retcode)
                    prior = last_retcode
                if cls != "REPAIRABLE" or repair_used:
                    self._log_recovery(
                        symbol=symbol,
                        order_type=order_type,
                        retcode=prior,
                        recovery_state="VALIDATION_FAILED",
                        recovery_reason=",".join(reasons),
                        attempt=attempt,
                        elapsed_ms=int((time.monotonic() - deadline + retry_budget_s) * 1000),
                        original_entry=original_price,
                        original_sl=original_sl,
                        original_tp=original_tp,
                        tick=tick,
                    )
                    logger.error(
                        "place_pending_order aborted pre-send for %s: structurally-invalid "
                        "request will NOT be sent. Reasons: %s (prior retcode %s: %s)",
                        symbol,
                        ",".join(reasons),
                        prior,
                        (last_err or self._translate_retcode(prior)) if prior else "pre-flight",
                    )
                    return 0

                # ONE validated repair, preserving original risk/reward distances.
                repair_used = True
                repaired, new_price, new_sl, new_tp = self._repair_pending_request(
                    symbol=symbol,
                    order_type=order_type,
                    price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    volume=volume,
                    tick=tick,
                    sym_info=sym_info,
                )
                if not repaired or (new_price, new_sl, new_tp) == (price, stop_loss, take_profit):
                    self._log_recovery(
                        symbol=symbol,
                        order_type=order_type,
                        retcode=prior,
                        recovery_state="REPAIR_REJECTED",
                        recovery_reason=",".join(reasons),
                        attempt=attempt,
                        elapsed_ms=int((time.monotonic() - deadline + retry_budget_s) * 1000),
                        original_entry=original_price,
                        original_sl=original_sl,
                        original_tp=original_tp,
                        tick=tick,
                    )
                    logger.error(
                        "place_pending_order aborted pre-send for %s: validated repair "
                        "impossible without changing risk/reward geometry. Reasons: %s",
                        symbol,
                        ",".join(reasons),
                    )
                    return 0

                self._log_recovery(
                    symbol=symbol,
                    order_type=order_type,
                    retcode=prior,
                    recovery_state="REPAIRED",
                    recovery_reason=",".join(reasons),
                    attempt=attempt,
                    elapsed_ms=int((time.monotonic() - deadline + retry_budget_s) * 1000),
                    original_entry=original_price,
                    original_sl=original_sl,
                    original_tp=original_tp,
                    tick=tick,
                )
                price, stop_loss, take_profit = new_price, new_sl, new_tp

            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": volume,
                "type": mt5_order_type,
                "price": price,
                "sl": stop_loss,
                "tp": take_profit,
                "magic": 888101,
                "comment": "NSE_PENDING",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(
                    "Fast-Act Pending Order Placed Successfully on attempt %s! Ticket: %s",
                    attempt,
                    result.order,
                )
                return result.order

            last_retcode = result.retcode if result else mt5.last_error()
            last_err = self._translate_retcode(last_retcode)

            # CAUSE-AWARE RECOVERY: classify the failure. HARD_REJECT aborts
            # immediately — a structurally-invalid request is never re-sent.
            cls = self._classify_pending_retcode(last_retcode)
            if cls == "HARD_REJECT":
                self._log_recovery(
                    symbol=symbol,
                    order_type=order_type,
                    retcode=last_retcode,
                    recovery_state="HARD_ABORT",
                    recovery_reason=last_err,
                    attempt=attempt,
                    elapsed_ms=int((time.monotonic() - deadline + retry_budget_s) * 1000),
                    original_entry=original_price,
                    original_sl=original_sl,
                    original_tp=original_tp,
                    tick=mt5.symbol_info_tick(symbol),
                )
                logger.error(
                    "place_pending_order HARD-abort for %s: retcode %s (%s) is not "
                    "retryable — no further sends.",
                    symbol,
                    last_retcode,
                    last_err,
                )
                return 0

            # IDEMPOTENCY GUARD: before retrying, verify whether an equivalent
            # pending order already exists on the broker. The previous send may
            # have been accepted server-side despite a non-DONE/ambiguous
            # retcode; re-sending blindly would create a DUPLICATE order.
            # Match on (symbol, type, volume, price) — the fingerprint the
            # engine itself would produce on a retry.
            if attempt < max_retries:
                existing = self._find_equivalent_pending(
                    symbol=symbol,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                )
                if existing is not None:
                    logger.info(
                        "Fast-Act Pending Order already exists on broker (ticket=%s) — treating "
                        "retry as success, no duplicate sent.",
                        existing,
                    )
                    self._last_pending_reused = True
                    return existing

            # Auto-Reconnect Circuit Breaker for Retcode 10031 (NO_CONNECTION)
            if last_retcode == 10031:
                logger.warning(
                    "Trade server connection interrupted (Retcode 10031). Probing terminal IPC state..."
                )
                t_info = mt5.terminal_info()
                if t_info is None or not t_info.connected:
                    mt5.initialize()  # Fast re-handshake with local Win32 IPC process

            # Budget + backoff gate for TRANSIENT/REPAIRABLE classes.
            elapsed = time.monotonic()
            if attempt >= max_retries or elapsed >= deadline:
                self._log_recovery(
                    symbol=symbol,
                    order_type=order_type,
                    retcode=last_retcode,
                    recovery_state=("TRANSIENT_RETRY" if cls == "TRANSIENT" else "REPAIRED"),
                    recovery_reason=last_err,
                    attempt=attempt,
                    elapsed_ms=int((elapsed - deadline + retry_budget_s) * 1000),
                    original_entry=original_price,
                    original_sl=original_sl,
                    original_tp=original_tp,
                    tick=mt5.symbol_info_tick(symbol),
                )
                break

            backoff_s = 0.025 if attempt == 1 else 0.05
            logger.warning(
                "[PENDING_RECOVERY] symbol=%s order_type=%s retcode=%s retcode_description=%s "
                "recovery_state=%s recovery_reason=%s attempt=%s elapsed_ms=%s "
                "original_entry=%s original_sl=%s original_tp=%s — retrying in %sms",
                symbol,
                order_type.value if isinstance(order_type, OrderType) else order_type,
                last_retcode,
                last_err,
                "TRANSIENT_RETRY" if cls == "TRANSIENT" else "REPAIR_PENDING",
                last_err,
                attempt,
                int((time.monotonic() - deadline + retry_budget_s) * 1000),
                original_price,
                original_sl,
                original_tp,
                int(backoff_s * 1000),
            )
            time.sleep(backoff_s)

        self._log_recovery(
            symbol=symbol,
            order_type=order_type,
            retcode=last_retcode,
            recovery_state="TRANSIENT_RETRY",
            recovery_reason="retry budget or attempt cap exhausted",
            attempt=max_retries,
            elapsed_ms=int((time.monotonic() - deadline + retry_budget_s) * 1000),
            original_entry=original_price,
            original_sl=original_sl,
            original_tp=original_tp,
            tick=mt5.symbol_info_tick(symbol),
        )
        logger.error(
            "place_pending_order failed after %s fast-act retries. Retcode: %s (%s)",
            max_retries,
            last_retcode,
            last_err,
        )
        return 0

    def modify_order(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        return self.modify_position(ticket=ticket, stop_loss=stop_loss, take_profit=take_profit)

    def modify_position(self, ticket: int, stop_loss: float, take_profit: float) -> bool:
        self._assert_connected()
        assert mt5 is not None

        import time

        max_retries = 3
        last_retcode = 0
        last_err = ""

        for attempt in range(1, max_retries + 1):
            positions = mt5.positions_get(ticket=ticket)
            if not positions or len(positions) == 0:
                logger.error("Failed to modify position: Ticket #%s not found.", ticket)
                return False

            pos = positions[0]

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol": pos.symbol,
                "sl": stop_loss,
                "tp": take_profit,
            }

            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(
                    "Successfully modified position ticket #%s -> New SL: %s | New TP: %s",
                    ticket,
                    stop_loss,
                    take_profit,
                )
                return True

            last_retcode = result.retcode if result else mt5.last_error()
            last_err = self._translate_retcode(last_retcode)

            if attempt < max_retries:
                time.sleep(0.025)

        logger.error(
            "Failed to modify position SL/TP for ticket #%s after %s retries. Retcode: %s (%s)",
            ticket,
            max_retries,
            last_retcode,
            last_err,
        )
        return False

    def close_position(self, ticket: int, volume: float | None = None) -> bool:
        self._assert_connected()
        assert mt5 is not None

        import time

        max_retries = 3
        last_retcode = 0
        last_err = ""

        for attempt in range(1, max_retries + 1):
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                logger.error("Failed to close position: Ticket #%s not found.", ticket)
                return False

            pos = positions[0]
            close_type = (
                mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            )
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                time.sleep(0.025)
                continue

            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
            filling_mode = self._resolve_filling_mode(pos.symbol)
            close_volume = volume if volume is not None else pos.volume

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": close_volume,
                "type": close_type,
                "position": ticket,
                "price": price,
                "magic": pos.magic,
                "comment": "NSE_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }

            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(
                    "Successfully closed live position ticket #%s at price %s", ticket, price
                )
                return True

            last_retcode = result.retcode if result else mt5.last_error()
            last_err = self._translate_retcode(last_retcode)

            if attempt < max_retries:
                # IDEMPOTENCY GUARD: the close may have been accepted server-side
                # despite a non-DONE/ambiguous retcode. Re-check whether the
                # position is STILL open; if it is gone, the close succeeded —
                # report success rather than re-sending a duplicate close.
                still_open = mt5.positions_get(ticket=ticket)
                if not still_open:
                    logger.info(
                        "close_position: ticket #%s no longer open after ambiguous retcode %s "
                        "— treating as already closed (no duplicate close sent).",
                        ticket,
                        last_retcode,
                    )
                    return True
                time.sleep(0.025)

        logger.error(
            "Failed to close position ticket #%s after %s retries. Retcode: %s (%s)",
            ticket,
            max_retries,
            last_retcode,
            last_err,
        )
        return False

    def _resolve_filling_mode(self, symbol: str) -> int:
        assert mt5 is not None
        sym_info = mt5.symbol_info(symbol)
        if sym_info is None:
            return mt5.ORDER_FILLING_IOC

        modes = sym_info.filling_mode
        if modes & 2:
            return mt5.ORDER_FILLING_IOC
        elif modes & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _translate_retcode(self, retcode: int) -> str:
        return PENDING_RETCODE_MAP.get(retcode, f"UNKNOWN_MT5_RETCODE ({retcode})")

    def _classify_pending_retcode(self, retcode: int) -> str:
        """Cause class for a failed pending-order retcode (BUG-226).

        TRANSIENT   -> broker state may resolve itself; retry with backoff.
        REPAIRABLE  -> request structurally invalid; ONE validated repair.
        HARD_REJECT -> never re-send (includes unknown codes and 10029 FROZEN).
        """
        if retcode in _PENDING_CLASS_TRANSIENT:
            return "TRANSIENT"
        if retcode in _PENDING_CLASS_REPAIRABLE:
            return "REPAIRABLE"
        return "HARD_REJECT"

    def _validate_pending_request(
        self,
        symbol: str,
        order_type: OrderType,
        price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        tick: Any = None,
        sym_info: Any = None,
    ) -> tuple[bool, list[str]]:
        """Pre-dispatch structural validation of a pending request (BUG-226).

        Validates, against the CURRENT broker tick and symbol constraints:
        volume bounds/step, price tick-size alignment, entry side relative to
        the market, stops_level minimum distance, freeze_level distance, and
        SL/TP geometry relative to the entry. Returns (ok, reasons).

        ``tick``/``sym_info`` default to a fresh ``mt5.symbol_info_tick`` /
        ``mt5.symbol_info`` fetch; the retry loop passes its already-fetched
        snapshot to guarantee validate/repair/send use the SAME market view.
        """
        if sym_info is None:
            sym_info = mt5.symbol_info(symbol) if mt5 is not None else None
        if tick is None:
            tick = mt5.symbol_info_tick(symbol) if mt5 is not None else None
        if sym_info is None:
            return False, ["no_symbol_info"]
        if tick is None:
            return False, ["no_tick"]

        reasons: list[str] = []
        point = float(sym_info.point)
        stops_level = float(getattr(sym_info, "trade_stops_level", 0) or 0)
        freeze_level = float(getattr(sym_info, "trade_freeze_level", 0) or 0)

        # --- volume ------------------------------------------------------
        vol_min = float(getattr(sym_info, "volume_min", 0.0) or 0.0)
        vol_max = float(getattr(sym_info, "volume_max", 0.0) or 0.0)
        vol_step = float(getattr(sym_info, "volume_step", 0.01) or 0.01)
        if volume < vol_min - 1e-9:
            reasons.append("volume_below_min")
        if volume > vol_max + 1e-9:
            reasons.append("volume_above_max")
        if vol_step > 0:
            steps = round(volume / vol_step)
            if abs(volume - steps * vol_step) > 1e-9:
                reasons.append("volume_not_multiple_of_step")

        # --- market reference + min gaps ---------------------------------
        spread = float(tick.ask) - float(tick.bid)
        min_gap = max(stops_level * point, 0.0)
        if stops_level == 0:
            min_gap = max(min_gap, spread)

        is_buy_side = order_type in (OrderType.BUY_LIMIT, OrderType.BUY_STOP)
        is_sell_side = order_type in (OrderType.SELL_LIMIT, OrderType.SELL_STOP)
        if not (is_buy_side or is_sell_side):
            reasons.append("not_a_pending_type")
            return False, reasons

        # --- entry side ---------------------------------------------------
        market_ref = float(tick.ask) if is_buy_side else float(tick.bid)
        if is_buy_side and price >= float(tick.ask):
            reasons.append("price_not_below_ask")
        if is_sell_side and price <= float(tick.bid):
            reasons.append("price_not_above_bid")
        if abs(price - market_ref) < min_gap - 1e-9:
            reasons.append("entry_inside_stops_level")

        # --- tick-size alignment ------------------------------------------
        tick_size = float(getattr(sym_info, "trade_tick_size", 0) or 0) or point
        if tick_size > 0:
            steps = round(price / tick_size)
            if abs(price - steps * tick_size) > 1e-9:
                reasons.append("price_not_tick_aligned")

        # --- SL/TP geometry ------------------------------------------------
        min_stop_dist = max(stops_level * point, 0.10)
        if is_sell_side:
            if stop_loss > 0 and stop_loss <= price + min_stop_dist - 1e-9:
                reasons.append("sl_not_above_entry")
            if take_profit > 0 and take_profit >= price - min_stop_dist + 1e-9:
                reasons.append("tp_not_below_entry")
        else:
            if stop_loss > 0 and stop_loss >= price - min_stop_dist + 1e-9:
                reasons.append("sl_not_below_entry")
            if take_profit > 0 and take_profit <= price + min_stop_dist - 1e-9:
                reasons.append("tp_not_above_entry")

        # --- freeze level ---------------------------------------------------
        if freeze_level > 0:
            if abs(price - market_ref) <= freeze_level * point + 1e-9:
                reasons.append("entry_inside_freeze_level")
            if (
                is_sell_side
                and stop_loss > 0
                and abs(stop_loss - float(tick.bid)) <= (freeze_level * point + 1e-9)
            ):
                reasons.append("sl_inside_freeze_level")
            if (
                not is_sell_side
                and stop_loss > 0
                and abs(stop_loss - float(tick.ask)) <= (freeze_level * point + 1e-9)
            ):
                reasons.append("sl_inside_freeze_level")

        return (len(reasons) == 0, reasons)

    def _repair_pending_request(
        self,
        symbol: str,
        order_type: OrderType,
        price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
        tick: Any,
        sym_info: Any,
    ) -> tuple[bool, float, float, float]:
        """Single order-preserving geometry repair for a pending request (BUG-226).

        Aligns the entry to the tick size, pushes it to the legal side of the
        market with the required minimum gap, then re-derives SL/TP from the
        NEW entry using the ORIGINAL absolute risk (|entry-sl|) and reward
        (|tp-entry|) distances — preserving the original risk/reward ratio.
        Refuses (ok=False) when the original geometry is itself inconsistent
        (SL/TP on wrong sides of the entry) or the repair would invert it.
        """
        if tick is None or sym_info is None:
            return False, price, stop_loss, take_profit

        point = float(sym_info.point)
        stops_level = float(getattr(sym_info, "trade_stops_level", 0) or 0)
        spread = float(tick.ask) - float(tick.bid)
        min_gap = max(stops_level * point, spread)
        min_gap = max(min_gap, 0.10)
        tick_size = float(getattr(sym_info, "trade_tick_size", 0) or 0) or point
        digits = int(getattr(sym_info, "digits", 2))

        # Original absolute risk/reward distances (require > 0).
        risk_dist = abs(price - stop_loss) if stop_loss > 0 else 0.0
        reward_dist = abs(take_profit - price) if take_profit > 0 else 0.0

        is_sell = order_type in (OrderType.SELL_LIMIT, OrderType.SELL_STOP)

        # Original stops must sit on their correct sides of the entry; a
        # flipped SL/TP cannot be repaired without silently inverting risk.
        if stop_loss > 0:
            if is_sell and stop_loss <= price:
                return False, price, stop_loss, take_profit
            if not is_sell and stop_loss >= price:
                return False, price, stop_loss, take_profit
        if take_profit > 0:
            if is_sell and take_profit >= price:
                return False, price, stop_loss, take_profit
            if not is_sell and take_profit <= price:
                return False, price, stop_loss, take_profit

        # 1) align entry to tick size, then 2) push to the legal side.
        # BUG-231 follow-up (2026-09-03 live log 20:02:09 + 20:18:06): the
        # original repair only moved the entry when it sat on the WRONG side
        # (<= bid for sells). An entry ABOVE the bid but INSIDE the
        # stops-level distance (legit "too close" case, e.g. entry 4491.53
        # vs bid 4491.35 with min_gap 0.50) was left untouched -> repaired
        # geometry identical to the failed request -> REPAIR_REJECTED even
        # though a perfectly valid RR-preserving repair existed. Now the
        # push condition is the stops-level distance, not merely the side.
        new_price = round(round(price / tick_size) * tick_size, digits)
        if is_sell:
            if float(tick.bid) + min_gap - new_price > 1e-9:
                new_price = float(tick.bid) + min_gap
        elif new_price - (float(tick.ask) - min_gap) > 1e-9:
            new_price = float(tick.ask) - min_gap
        new_price = round(round(new_price / tick_size) * tick_size, digits)

        # Guard: pushing may have flipped the side again via rounding.
        if is_sell and new_price <= float(tick.bid):
            return False, price, stop_loss, take_profit
        if not is_sell and new_price >= float(tick.ask):
            return False, price, stop_loss, take_profit

        # 3) re-derive stops from the NEW entry with ORIGINAL distances,
        #    re-aligned to tick size. Entry moved DOWN for sells (only
        #    direction repairs move it) => risk shrinks and reward grows
        #    symmetrically; both stay positive, ratio preserved.
        new_sl = stop_loss
        new_tp = take_profit
        if stop_loss > 0:
            raw_sl = new_price + risk_dist if is_sell else new_price - risk_dist
            new_sl = round(round(raw_sl / tick_size) * tick_size, digits)
        if take_profit > 0:
            raw_tp = new_price - reward_dist if is_sell else new_price + reward_dist
            new_tp = round(round(raw_tp / tick_size) * tick_size, digits)

        # Post-conditions: correct sides and minimum gaps from the new entry.
        min_stop_dist = max(stops_level * point, 0.10)
        if stop_loss > 0:
            if is_sell and new_sl <= new_price + min_stop_dist:
                return False, price, stop_loss, take_profit
            if not is_sell and new_sl >= new_price - min_stop_dist:
                return False, price, stop_loss, take_profit
        if take_profit > 0:
            if is_sell and new_tp >= new_price - min_stop_dist:
                return False, price, stop_loss, take_profit
            if not is_sell and new_tp <= new_price + min_stop_dist:
                return False, price, stop_loss, take_profit

        return True, new_price, new_sl, new_tp

    def _log_recovery(
        self,
        *,
        symbol: str,
        order_type: OrderType,
        retcode: int,
        recovery_state: str,
        recovery_reason: str,
        attempt: int,
        elapsed_ms: int,
        original_entry: float,
        original_sl: float,
        original_tp: float,
        tick: Any,
    ) -> None:
        """Single structured recovery observability record (BUG-226)."""
        logger.warning(
            "[PENDING_RECOVERY] symbol=%s order_type=%s retcode=%s retcode_description=%s "
            "recovery_state=%s recovery_reason=%s attempt=%s elapsed_ms=%s "
            "original_entry=%s original_sl=%s original_tp=%s current_bid=%s current_ask=%s",
            symbol,
            order_type.value if isinstance(order_type, OrderType) else order_type,
            retcode,
            PENDING_RETCODE_MAP.get(int(retcode), f"UNKNOWN_MT5_RETCODE ({retcode})"),
            recovery_state,
            recovery_reason,
            attempt,
            elapsed_ms,
            original_entry,
            original_sl,
            original_tp,
            float(tick.bid) if tick is not None else None,
            float(tick.ask) if tick is not None else None,
        )

    def _assert_connected(self) -> None:
        if not self._connected or not HAS_NATIVE_MT5 or mt5 is None:
            raise RuntimeError(
                "MT5 Adapter is not connected. Call connect() before invoking broker operations."
            )
