"""
MT5 Runtime Diagnostics & Structured Operation Wrapper
=======================================================
Centralized, structured diagnostics for every MetaTrader 5 broker call.

Contract (see agents/skill.md "MT5 operation wrapper"):

    [MT5_CALL]
    operation=account_info
    status=SUCCESS | FAILED
    duration_ms=...
    result_count=...
    mt5_error_code=...
    mt5_error_message=...

Every MT5 call site in the adapters reports through this module so a failure
is NEVER silent: the caller always learns WHICH operation failed, WHICH error
code MT5 returned, HOW LONG it took, and whether the failure is retryable.

PRIVACY: this module never receives passwords, tokens or credentials. The
`context` dict is free-form but callers must not include secrets.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexus_scalp.adapters.mt5.diagnostics")

#: MT5 trade-server return codes relevant to runtime diagnostics.
#:
#: OFFICIAL MQL5 labels per
#: https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes
#: (BUG-226 forensic correction: 10016=INVALID_STOPS, 10017=TRADE_DISABLED,
#: 10018=MARKET_CLOSED, 10019=NO_MONEY, 10020=PRICE_CHANGED, 10021=PRICE_OFF,
#: 10022=INVALID_EXPIRATION, 10023=ORDER_CHANGED, 10024=TOO_MANY_REQUESTS —
#: the previous map was shifted by two codes from 10016 onward and
#: misdiagnosed the production 10016 rejection).
RETCODE_LABELS: dict[int, str] = {
    10004: "REQUOTE",
    10006: "REJECTED",
    10007: "CANCELLED",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10011: "ERROR",
    10012: "TIMEOUT",
    10013: "INVALID",
    10014: "INVALID_VOLUME",
    10015: "INVALID_PRICE",
    10016: "INVALID_STOPS",
    10017: "TRADE_DISABLED",
    10018: "MARKET_CLOSED",
    10019: "NO_MONEY",
    10020: "PRICE_CHANGED",
    10021: "PRICE_OFF",
    10022: "INVALID_EXPIRATION",
    10023: "ORDER_CHANGED",
    10024: "TOO_MANY_REQUESTS",
    10025: "UNKNOWN_SYMBOL",
    10026: "ORDER_LOCKED",
    10027: "LONG_ONLY",
    10028: "SHORT_ONLY",
    10029: "FROZEN",
    10030: "UNSUPPORTED_FILLING",
    10031: "NO_CONNECTION",
}


class MT5OperationError(RuntimeError):
    """Raised when a broker operation returns an unusable result.

    Carries the structured diagnostic so the caller can re-raise without
    losing the operation/error/duration context. NEVER include credentials.
    """

    def __init__(self, diag: MT5CallDiagnostic) -> None:
        self.diag = diag
        super().__init__(
            f"MT5 operation '{diag.operation}' failed: "
            f"error_code={diag.mt5_error_code} error_message={diag.mt5_error_message or 'n/a'}"
        )


@dataclass
class MT5CallDiagnostic:
    """Structured record of one broker call (success OR failure)."""

    operation: str
    status: str  # SUCCESS | FAILED
    duration_ms: float = 0.0
    result_count: int | None = None
    mt5_error_code: int | None = None
    mt5_error_message: str | None = None
    exception_type: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 3),
            "result_count": self.result_count,
            "mt5_error_code": self.mt5_error_code,
            "mt5_error_message": self.mt5_error_message,
            "exception_type": self.exception_type,
            "context": dict(self.context),
        }

    def log_line(self) -> str:
        """Single-line structured log entry (safe for console/file)."""
        parts = [
            "[MT5_CALL]",
            f"operation={self.operation}",
            f"status={self.status}",
            f"duration_ms={round(self.duration_ms, 3)}",
        ]
        if self.result_count is not None:
            parts.append(f"result_count={self.result_count}")
        if self.mt5_error_code is not None:
            parts.append(f"error_code={self.mt5_error_code}")
        if self.mt5_error_message:
            parts.append(f"error_message={self.mt5_error_message}")
        if self.exception_type:
            parts.append(f"exception_type={self.exception_type}")
        for key, value in self.context.items():
            parts.append(f"{key}={value}")
        return " ".join(parts)


def retcode_label(code: int | None) -> str:
    """Human-readable label for an MT5 trade-server retcode (None-safe)."""
    if code is None:
        return "UNKNOWN"
    return RETCODE_LABELS.get(int(code), f"UNKNOWN_MT5_RETCODE ({code})")


def run_mt5_call(
    operation: str,
    fn: Callable[[], Any],
    *,
    mt5_module: Any,
    context: dict[str, Any] | None = None,
    logger_name: str = "nexus_scalp.adapters.mt5",
) -> tuple[Any, MT5CallDiagnostic]:
    """Executes one MT5 operation with full structured diagnostics.

    Args:
        operation: Logical operation name (account_info, symbol_info_tick, ...).
        fn: Zero-argument callable performing the broker call. The caller
            captures result.retcode / result.comment itself when relevant.
        mt5_module: The MetaTrader5 module (used for last_error()).
        context: Optional structured context (symbol, timeframe, ticket, ...).

    Returns:
        (result, diagnostic). `result` may be None when the call failed.

    Raises:
        Nothing. A raised exception inside `fn` is captured into the
        diagnostic (status=FAILED, exception_type=...) and re-raised, so the
        DIAGNOSTIC is always recorded before the exception propagates.
    """
    start = time.perf_counter()
    diag = MT5CallDiagnostic(operation=operation, status="SUCCESS", context=dict(context or {}))
    try:
        result = fn()
    except Exception as exc:
        diag.status = "FAILED"
        diag.exception_type = type(exc).__name__
        diag.duration_ms = (time.perf_counter() - start) * 1000.0
        _emit(diag, logger_name)
        raise
    diag.duration_ms = (time.perf_counter() - start) * 1000.0
    if result is None:
        diag.status = "FAILED"
        try:
            err = mt5_module.last_error()
        except Exception:
            err = None
        if isinstance(err, tuple) and len(err) >= 2:
            diag.mt5_error_code = int(err[0])
            diag.mt5_error_message = str(err[1])
        elif err is not None:
            diag.mt5_error_message = str(err)
    elif hasattr(result, "__len__") and not isinstance(result, (str, bytes)):
        diag.result_count = len(result)
    _emit(diag, logger_name)
    return result, diag


#: Failure-storm throttle (2026-09-09 disk/log-hygiene pass): during a
#: degraded broker connection the adapter retries at ~100ms cadence; without
#: suppression each retry emitted an IDENTICAL WARNING line into logs/error
#: (measured production drivers of multi-GB log trees). The FIRST failure of
#: a (operation, error_code, exception) signature always logs; identical
#: repeats inside the suppression window are counted and summarized once
#: after the window closes (honest SUPPRESSED_N line — nothing hidden).
#: Recoveries (SUCCESS after failures) are NEVER suppressed.
_MT5_STORM_WINDOW_SEC = 30.0
_MT5_STORM_LOCK = threading.Lock()
_MT5_STORM_STATE: dict[tuple[str, int | None, str], dict[str, Any]] = {}


def _storm_key(diag: MT5CallDiagnostic) -> tuple[str, int | None, str]:
    """Stable signature of an identical repeating failure."""
    return (
        diag.operation,
        diag.mt5_error_code,
        diag.exception_type or diag.mt5_error_message or "",
    )


def reset_storm_suppression() -> None:
    """Test/ops seam: clear the failure-storm throttle state."""
    with _MT5_STORM_LOCK:
        _MT5_STORM_STATE.clear()


def _emit(diag: MT5CallDiagnostic, logger_name: str) -> None:
    """Logs the diagnostic: WARNING on failure (storm-throttled), DEBUG on success."""
    target = logging.getLogger(logger_name)
    if diag.status != "FAILED":
        # recovery or routine success: always visible, and it closes every
        # storm registered for this operation (any error signature)
        with _MT5_STORM_LOCK:
            for key in [k for k in _MT5_STORM_STATE if k[0] == diag.operation]:
                _MT5_STORM_STATE.pop(key, None)
        target.debug(diag.log_line())
        return

    key = _storm_key(diag)
    now = time.monotonic()
    with _MT5_STORM_LOCK:
        state = _MT5_STORM_STATE.get(key)
        if state is None:
            _MT5_STORM_STATE[key] = {"first": now, "last": now, "suppressed": 0}
            target.warning(diag.log_line())
            return
        if now - state["last"] > _MT5_STORM_WINDOW_SEC:
            # window closed: summarize what was held back, then re-arm
            suppressed = int(state["suppressed"])
            state.update({"first": now, "last": now, "suppressed": 0})
            if suppressed > 0:
                target.warning(
                    "[MT5_CALL] event=FAILURE_STORM_SUMMARY operation=%s error_code=%s "
                    "suppressed_identical_failures=%d window_sec=%.0f (first failure always logged)",
                    diag.operation,
                    diag.mt5_error_code,
                    suppressed,
                    _MT5_STORM_WINDOW_SEC,
                )
            target.warning(diag.log_line())
            return
        state["last"] = now
        state["suppressed"] = int(state["suppressed"]) + 1
    # suppressed inside the window: visible on the DEBUG channel only, so a
    # DEBUG-level operator still sees every attempt and nothing is truly hidden
    target.debug("%s (storm-suppressed; identical failure repeats inside window)", diag.log_line())


class MT5ConnectionState:
    """Real MT5 connection state machine (DISCONNECTED/CONNECTING/CONNECTED/
    DEGRADED/AUTHENTICATION_ERROR/TERMINAL_ERROR/UNKNOWN) with per-operation
    bookkeeping (last success/failure, age, terminal+package versions).

    The dashboard MODE display must derive from this state, NOT from config.
    """

    CONNECTED = "CONNECTED"
    CONNECTING = "CONNECTING"
    DISCONNECTED = "DISCONNECTED"
    DEGRADED = "DEGRADED"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    TERMINAL_ERROR = "TERMINAL_ERROR"
    UNKNOWN = "UNKNOWN"

    def __init__(self) -> None:
        self._state: str = self.DISCONNECTED
        self._terminal_version: str | None = None
        self._package_version: str | None = None
        self._account_login: int | None = None
        self._server: str | None = None
        self._company: str | None = None
        self._trade_allowed: bool | None = None
        self._trade_expert: bool | None = None
        self._last_success: str | None = None
        self._last_failure: str | None = None
        self._last_error: str | None = None
        self._connected_at: float | None = None

    # -- state transitions -------------------------------------------------
    def set_state(self, state: str, detail: str | None = None) -> None:
        self._state = state
        if state in (self.CONNECTED, self.DEGRADED):
            if self._connected_at is None:
                self._connected_at = time.monotonic()
        else:
            self._connected_at = None
        if detail:
            self._last_failure = None if state == self.CONNECTED else detail
            self._last_error = None if state == self.CONNECTED else detail
        logger.debug("[MT5_CONN] state=%s detail=%s", state, detail)

    def record_success(self, operation: str) -> None:
        self._last_success = operation
        if self._state in (self.DISCONNECTED, self.AUTHENTICATION_ERROR, self.TERMINAL_ERROR):
            self._state = self.CONNECTED
            if self._connected_at is None:
                self._connected_at = time.monotonic()

    def record_failure(self, operation: str, detail: str | None = None) -> None:
        self._last_failure = operation
        self._last_error = detail

    def mark_degraded(self, reason: str) -> None:
        if self._state == self.CONNECTED:
            self._state = self.DEGRADED
        self._last_failure = reason

    # -- setters ------------------------------------------------------------
    def set_terminal(self, terminal_info: Any) -> None:
        """Feeds terminal_info() and version() data into the state."""
        if terminal_info is not None:
            self._trade_allowed = bool(getattr(terminal_info, "trade_allowed", None))
            self._trade_expert = bool(getattr(terminal_info, "trade_expert", None))
            company = getattr(terminal_info, "company", None)
            if company:
                self._company = str(company)

    def set_versions(self, package_version: str | None, terminal_version: str | None) -> None:
        self._package_version = package_version
        self._terminal_version = terminal_version

    def set_account(self, account_info: Any) -> None:
        if account_info is None:
            return
        login = getattr(account_info, "login", None)
        if login is not None:
            self._account_login = int(login)
        server = getattr(account_info, "server", None)
        if server:
            self._server = str(server)
        company = getattr(account_info, "company", None)
        if company:
            self._company = str(company)
        trade_allowed = getattr(account_info, "trade_allowed", None)
        trade_expert = getattr(account_info, "trade_expert", None)
        if trade_allowed is not None:
            self._trade_allowed = bool(trade_allowed)
        if trade_expert is not None:
            self._trade_expert = bool(trade_expert)

    # -- readers ------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == self.CONNECTED

    def to_dict(self) -> dict[str, Any]:
        """Full connection diagnostic (safe subset; never passwords)."""
        age_sec: float | None = None
        if self._connected_at is not None:
            age_sec = max(0.0, time.monotonic() - self._connected_at)
        return {
            "state": self._state,
            "terminal_version": self._terminal_version,
            "package_version": self._package_version,
            "account_login": self._account_login,
            "server": self._server,
            "company": self._company,
            "trade_allowed": self._trade_allowed,
            "trade_expert": self._trade_expert,
            "last_successful_operation": self._last_success,
            "last_failed_operation": self._last_failure,
            "last_error": self._last_error,
            "connection_age_sec": age_sec,
        }
