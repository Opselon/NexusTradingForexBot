"""Windows Proactor transport disconnect policy (WIN-DISCONNECT-001).

What this is
------------
A narrow, evidence-backed policy that classifies the exceptions raised by the
Windows asyncio Proactor transport during connection teardown, and one small
lifecycle fix in the web layer that prevents a server-side teardown from
turning an ordinary client disconnect into a noisy ERROR traceback.

Why this exists
---------------
On Windows, ``asyncio`` runs on the ProactorEventLoop, and every connected
socket is owned by ``_ProactorSocketTransport`` / ``_ProactorBasePipeTransport``
(both in the stdlib ``asyncio/proactor_events.py`` — we never patch it).

Their ``_call_connection_lost`` has this shape::

    def _call_connection_lost(self, exc):
        if self._called_connection_lost:
            return
        try:
            self._protocol.connection_lost(exc)
        finally:
            if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
                self._sock.shutdown(socket.SHUT_RDWR)   # <-- can raise
            self._sock.close()
            ...

This ``finally`` block is CPython's guarded fix for its own long-standing
Windows issue (the ``XXX ... ERROR_NETNAME_DELETED`` comment directly above it
in the stdlib source): a cancelled overlapped read on a reset socket makes the
*cleanup itself* fail. The consequence:

* The exception is raised by the transport's own teardown, AFTER the protocol
  has already been notified (``protocol.connection_lost(exc)`` returned).
* Therefore no application protocol, route handler, middleware or generator
  can ever observe it. It escapes straight out of the scheduled callback and
  lands in ``loop.call_exception_handler`` as::

      Exception in callback _ProactorSocketTransport._call_connection_lost(None)
      ConnectionResetError: [WinError 10054]
      An existing connection was forcibly closed by the remote host

* The ``(None)`` in that message is decisive evidence: the callback was
  scheduled by the *application-initiated* close path
  (``_ProactorBaseWritePipeTransport._loop_writing`` / ``_ProactorBasePipeTransport.close()``,
  both of which pass a literal ``None``), NOT by the read-error path
  (``_force_close(exc)`` passes the real exception object). So the trigger is
  the server closing a connection whose peer has just gone away — an ordinary
  browser/tab close, SSE EventSource abort, or a proxy dropping an idle
  keep-alive connection.

Established as an expected, ordinary remote disconnect on this platform —
with the classification kept strict enough that genuine transport failures stay
visible.

What this module deliberately is NOT
------------------------------------
* It is not a ``except Exception: pass``. It is not a blanket
  ``except ConnectionResetError: pass``. The decision is made by
  :func:`classify_transport_disconnect` from the exception's own attributes
  and the caller's lifecycle state.
* It is not applied to the whole application. It is used at exactly the
  event-loop exception handler (the only layer that can see these) plus the
  two web-layer disconnect sites that can observe a departing client directly
  (the ``/ws`` endpoint and the SSE broadcast loop).
* It is not applied to database, broker (MT5), file or subprocess sockets:
  those have no client that can walk away, and their failures are never
  downgraded. See :data:`_NON_REMOTE_SOCKET_LOGGERS`.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import sys
from typing import Any, Final

logger = logging.getLogger("nexus_scalp.web.transports")

# ---------------------------------------------------------------------------
# Signal vocabulary — the OSError errno codes a Win32 reset can surface as.
# ``winerror`` is Windows-specific; ``errno`` is the cross-platform key, so
# the classification works identically when the same code runs on Linux/macOS
# (where the same lifecycle failure surfaces as EPIPE/ECONNRESET on POSIX).
# ---------------------------------------------------------------------------
_WSAECONNRESET: Final[int] = 10054
_WSAECONNABORTED: Final[int] = 10053
_WSAENETRESET: Final[int] = 10052

#: Win32 ``ERROR_NETNAME_DELETED``. This is the *exact* code named in the
#: stdlib comment above the guarded ``shutdown()`` ("it may fail with
#: ERROR_NETNAME_DELETED"), and it is the second signature seen in NSE
#: production logs::
#:
#:     ConnectionResetError(22, 'The specified network name is no longer
#:     available', None, 64, None)      ->  errno=22 (EINVAL), winerror=64
#:
#: errno 22 is deliberately NOT trusted on its own (EINVAL has hundreds of
#: non-socket meanings); only the Windows code, which appears solely on a
#: transport whose peer name/route has already disappeared, is accepted.
_ERROR_NETNAME_DELETED: Final[int] = 64

#: OSError errno codes produced when the remote end is simply gone.
_REMOTE_GONE_ERRNOS: Final[frozenset[int]] = frozenset(
    {
        errno.ECONNRESET,  # POSIX: connection reset by peer
        errno.EPIPE,  # POSIX: broken pipe (write after peer close)
        errno.ECONNABORTED,  # POSIX: software caused connection abort
        errno.ENOTCONN,  # "Transport endpoint is not connected"
        errno.ESHUTDOWN,  # "Cannot send after transport endpoint shutdown"
    }
)

#: ``WinError`` codes produced when the remote end is simply gone.
_REMOTE_GONE_WINERRORS: Final[frozenset[int]] = frozenset(
    {
        _WSAECONNRESET,  # "An existing connection was forcibly closed by the remote host"
        _WSAECONNABORTED,  # "Software caused your host to abort the connection"
        _WSAENETRESET,  # "The connection has been broken due to keep-alive activity"
        _ERROR_NETNAME_DELETED,  # "The specified network name is no longer available"
    }
)

#: Exception types that MAY represent an expected remote disconnect. Narrow by
#: design: anything outside this set is never classified as a disconnect.
_REMOTE_DISCONNECT_TYPES: Final[tuple[type[BaseException], ...]] = (
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    # Windows Proactor callbacks raise the bare OSError for some teardown
    # races instead of one of the specific subclasses.
    OSError,
)


def _errno_of(exc: BaseException) -> int | None:
    """Errno of a socket exception, or None when the exception carries none."""
    try:
        value = getattr(exc, "errno", None)
    except Exception:  # pragma: no cover - defensive attribute read
        return None
    return value if isinstance(value, int) else None


def _winerror_of(exc: BaseException) -> int | None:
    """Windows-specific error code, or None on non-Windows / unavailable."""
    try:
        value = getattr(exc, "winerror", None)
    except Exception:  # pragma: no cover - defensive attribute read
        return None
    return value if isinstance(value, int) else None


def is_transport_disconnect_exception(exc: BaseException) -> bool:
    """True only when ``exc`` is a socket exception whose *only* meaning is
    that the remote end went away.

    The signal must come from the exception itself, not from its message: a
    ``RuntimeError("connection reset")`` raised by application code must never
    be downgraded by this module.
    """
    if not isinstance(exc, _REMOTE_DISCONNECT_TYPES):
        return False
    win = _winerror_of(exc)
    if win is not None:
        return win in _REMOTE_GONE_WINERRORS
    err = _errno_of(exc)
    if err is not None:
        return err in _REMOTE_GONE_ERRNOS
    # An OSError with neither errno nor winerror carries no signal at all —
    # classify as unknown so it stays visible.
    return False


#: ``context`` keys the Proactor transport callback sets when the failure
#: originates in the transport itself (never when a task/future raises).
_TRANSPORT_CONTEXT_KEYS: Final[frozenset[str]] = frozenset({"transport", "protocol"})

#: Loggers owned by socket-server transports. Failures on these are candidate
#: expected disconnects: the remote end is a browser, proxy or API client.
_TRANSPORT_SERVER_LOGGERS: Final[frozenset[str]] = frozenset(
    {
        "asyncio",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "starlette",
    }
)

#: Loggers that must NEVER be downgraded: their sockets have no remote client
#: that can walk away, so a reset there is always a real operational failure.
#: Kept explicit and narrow on purpose.
_NON_REMOTE_SOCKET_LOGGERS: Final[frozenset[str]] = frozenset(
    {
        "nexus_scalp.adapters.mt5.mt5_adapter",
        "nexus_scalp.adapters.mt5.remote_gateway",
        "nexus_scalp.adapters.database.audit_repository",
        "nexus_scalp.database",
        "nexus_scalp.adapters.paper.paper_adapter",
    }
)


def _logger_name_of(context: dict[str, Any]) -> str | None:
    """Best-effort logger name for a loop-exception context (or None)."""
    value = context.get("logger")
    if isinstance(value, logging.Logger):
        return value.name
    if isinstance(value, str):
        return value
    return None


def _transport_of(context: dict[str, Any]) -> Any:
    return context.get("transport")


def _callback_of(context: dict[str, Any]) -> Any:
    handle = context.get("handle")
    if handle is None:
        return None
    return getattr(handle, "_callback", None)


def _callback_qualname(context: dict[str, Any]) -> str:
    cb = _callback_of(context)
    return getattr(cb, "__qualname__", "") or ""


def _is_proactor_transport_callback(context: dict[str, Any]) -> bool:
    """True when the failing callback is the Proactor transport teardown itself.

    This is the structural signature from the production traceback:
    ``_ProactorSocketTransport._call_connection_lost`` /
    ``_ProactorBasePipeTransport._call_connection_lost``.
    """
    qualname = _callback_qualname(context)
    if ".connection_lost" in qualname or "._call_connection_lost" in qualname:
        return True
    # CPython renders the context message from the callback's repr, so the
    # production traceback shape is visible there too
    # ("Exception in callback _ProactorSocketTransport._call_connection_lost(None)").
    message = str(context.get("message") or "")
    if "_call_connection_lost" in message or ".connection_lost" in message:
        return True
    # ``_force_close`` / ``_fatal_error`` / ``_loop_writing`` /
    # ``_loop_reading`` are the other stdlib Proactor teardown callbacks.
    for marker in ("proactor", "Proactor"):
        if marker in qualname or marker in message:
            return True
    return False


def classify_transport_disconnect(
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
    logger_name: str | None = None,
) -> bool:
    """Decide whether ``exc`` is an EXPECTED remote disconnect.

    Policy (all must hold):

    1. The exception is a socket exception whose errno/winerror is one of the
       documented "remote end gone" codes (:func:`is_transport_disconnect_exception`).
    2. It originates from a socket-server transport — either a Proactor
       transport teardown callback (the production traceback shape) or a
       socket-server logger. This is what makes it a *remote* disconnect
       rather than an internal pipeline failure.
    3. The failing socket is not one of the never-downgrade internal sockets
       (:data:`_NON_REMOTE_SOCKET_LOGGERS`) — DB, broker and paper-adapter
       connections stay fully visible even when they raise the same errno.

    Returns False for everything else, including OSErrors with no errno and
    application exceptions of any kind.
    """
    if not is_transport_disconnect_exception(exc):
        return False
    ctx = context or {}
    if _logger_name_of(ctx) in _NON_REMOTE_SOCKET_LOGGERS:
        return False
    if logger_name in _NON_REMOTE_SOCKET_LOGGERS:
        return False
    if _is_proactor_transport_callback(ctx):
        return True
    name = logger_name or _logger_name_of(ctx)
    if name is None:
        # No signal either way: keep the exception visible rather than guess.
        return False
    return name in _TRANSPORT_SERVER_LOGGERS


# ---------------------------------------------------------------------------
# Event-loop exception handler
# ---------------------------------------------------------------------------
_HANDLER_INSTALLED = "nse_transport_disconnect_handler_installed"


def install_transport_disconnect_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Install the disconnect-classifying exception handler on ``loop``.

    Idempotent. The previous handler (if any) is preserved as a fallback: an
    unexpected exception is handed to it, so NSE never *lowers* the visibility
    of a failure some other handler was responsible for.

    Expected disconnects are logged once at DEBUG with the transport and peer
    address, then dropped — no traceback. Everything else reaches the loop's
    default handler unchanged (ERROR + traceback), exactly as before.
    """
    if getattr(loop, _HANDLER_INSTALLED, False):
        return
    previous = loop.get_exception_handler()

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, BaseException) and classify_transport_disconnect(exc, context=context):
            _log_expected_disconnect(exc, context)
            return
        if previous is not None:
            previous(loop_, context)
            return
        loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    setattr(loop, _HANDLER_INSTALLED, True)
    # One INFO at boot so the operator can see which loop platform the policy
    # is guarding (the classification is meaningful on the Windows Proactor;
    # on other loops the handler still runs but the Proactor-callback branch
    # simply never matches).
    logger.info(
        "[WIN-DISCONNECT] transport disconnect handler installed (loop=%s platform=%s)",
        type(loop).__name__,
        proactor_platform(loop),
    )


def _log_expected_disconnect(exc: BaseException, context: dict[str, Any]) -> None:
    """One structured DEBUG record for an expected remote disconnect.

    Deliberately low volume: no traceback, no per-event stack walk. The peer
    address is included when the transport exposes it so an operator can still
    correlate *which* client went away.
    """
    transport = _transport_of(context)
    peer = _peer_of(transport)
    logger.debug(
        "[TRANSPORT_DISCONNECT] expected remote disconnect %s %s peer=%s callback=%s",
        type(exc).__name__,
        _error_code_of(exc),
        peer,
        _callback_qualname(context) or "-",
        extra={
            "event": "TRANSPORT_DISCONNECT",
            "error_code": _error_code_of(exc),
            "exception_type": type(exc).__name__,
            "peer": peer,
            "transport": _transport_repr(transport),
        },
    )


def _error_code_of(exc: BaseException) -> str:
    win = _winerror_of(exc)
    err = _errno_of(exc)
    if win is not None:
        return f"winerror={win}"
    if err is not None:
        return f"errno={err}"
    return "unknown"


def _peer_of(transport: Any) -> str:
    """Best-effort remote address of a transport ('-' when unavailable)."""
    if transport is None:
        return "-"
    getter = getattr(transport, "get_extra_info", None)
    if not callable(getter):
        return "-"
    with contextlib.suppress(Exception):
        value = getter("peername")
        if value:
            return str(value)
    return "-"


def _transport_repr(transport: Any) -> str:
    if transport is None:
        return "-"
    return type(transport).__name__


# ---------------------------------------------------------------------------
# Windows Proactor availability (for observability/tests)
# ---------------------------------------------------------------------------
def proactor_platform(loop: asyncio.AbstractEventLoop | None = None) -> str:
    """Short description of the running loop platform ('proactor-win32' etc.).

    Pass ``loop`` to describe a specific loop; otherwise the current one.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    if loop is None:
        return "no-loop"
    name = type(loop).__name__
    if sys.platform == "win32":
        return "proactor-win32" if "Proactor" in name else f"win32-{name.lower()}"
    return name.lower()


def is_proactor_runtime() -> bool:
    """True when this process runs the Windows Proactor event loop."""
    return sys.platform == "win32" and "Proactor" in proactor_platform()


def expected_disconnect_codes() -> dict[str, list[int]]:
    """The classified code sets (observability + regression-test contract)."""
    return {
        "errno": sorted(_REMOTE_GONE_ERRNOS),
        "winerror": sorted(_REMOTE_GONE_WINERRORS),
    }


__all__ = [
    "classify_transport_disconnect",
    "expected_disconnect_codes",
    "install_transport_disconnect_handler",
    "is_proactor_runtime",
    "is_transport_disconnect_exception",
    "proactor_platform",
]
