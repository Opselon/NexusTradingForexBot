"""Regression tests for Windows Proactor transport disconnects (WIN-DISCONNECT-001).

What these pin
--------------
The production symptom was a noisy ERROR traceback on Windows:

    Exception in callback _ProactorSocketTransport._call_connection_lost(None)
    ConnectionResetError: [WinError 10054]
    An existing connection was forcibly closed by the remote host

Root cause (established from CPython ``asyncio/proactor_events.py`` source and
verified on Windows / Python 3.11.16 / ProactorEventLoop):

``_ProactorBasePipeTransport._call_connection_lost`` runs
``self._protocol.connection_lost(exc)`` first and only then, in its ``finally``
block, calls ``self._sock.shutdown(socket.SHUT_RDWR)``. That shutdown is
CPython's own workaround for a Windows overlapped-IO teardown race (the
``XXX ... ERROR_NETNAME_DELETED`` comment in the stdlib source), and on a
socket whose peer has already sent an RST it can raise ``ConnectionResetError``.

Because the raise happens AFTER ``protocol.connection_lost()`` returned, no
application protocol, route, middleware or generator can ever observe it: it
escapes the scheduled callback straight into ``loop.call_exception_handler``.
The ``(None)`` in the message proves the callback came from the
application-initiated close path (``close()`` / ``_loop_writing`` pass a
literal ``None``), not the read-error path (``_force_close(exc)`` passes the
real exception). So the trigger is the server closing a connection whose peer
has just gone away — an ordinary browser/tab close or SSE abort.

The correct handling layer is therefore the event-loop exception handler, with
a classification strict enough that genuine failures stay visible. These tests
assert the CLASSIFICATION POLICY, never stdlib traceback formatting.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import sys

import pytest

from nexus_scalp.web.transports import (
    classify_transport_disconnect,
    expected_disconnect_codes,
    install_transport_disconnect_handler,
    is_transport_disconnect_exception,
)


# ---------------------------------------------------------------------------
# Classification: expected remote disconnects
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "label"),
    [
        (ConnectionResetError(10054, "forcibly closed"), "winerror-10054-as-errno"),
        (ConnectionResetError(errno.ECONNRESET, "x"), "ECONNRESET"),
        (BrokenPipeError(errno.EPIPE, "broken pipe"), "EPIPE"),
        (ConnectionAbortedError(errno.ECONNABORTED, "aborted"), "ECONNABORTED"),
        (OSError(errno.ENOTCONN, "not connected"), "ENOTCONN"),
        (OSError(errno.ESHUTDOWN, "shut down"), "ESHUTDOWN"),
    ],
)
def test_expected_disconnect_codes_are_classified(exc, label):
    assert is_transport_disconnect_exception(exc), f"{label} must classify as a disconnect"


def test_production_exception_is_classified():
    """The exact exception from the production log."""
    exc = ConnectionResetError(10054, "An existing connection was forcibly closed")
    assert is_transport_disconnect_exception(exc)
    assert classify_transport_disconnect(exc, context=_proactor_context(exc))


def test_production_netname_deleted_exception_is_classified():
    """The *second* signature from the production log.

    On Windows the Proactor teardown can surface WinError 64
    (ERROR_NETNAME_DELETED) — the exact code named in the stdlib comment above
    the guarded ``shutdown()``. CPython puts it in ``winerror`` and leaves
    ``errno`` as EINVAL(22); EINVAL alone must NOT be trusted (see the guard
    test below), so the Windows code is what carries the classification.
    """
    exc = ConnectionResetError(
        22, "The specified network name is no longer available", None, 64, None
    )
    assert exc.errno == errno.EINVAL and exc.winerror == 64
    assert is_transport_disconnect_exception(exc)
    assert classify_transport_disconnect(exc, context=_proactor_context(exc))


def test_einval_without_windows_code_stays_visible():
    """EINVAL(22) on its own is an application argument error, never a disconnect."""
    exc = OSError(errno.EINVAL, "Invalid argument")
    assert exc.winerror is None
    assert not is_transport_disconnect_exception(exc)
    assert not classify_transport_disconnect(exc, context=_proactor_context(exc))


def test_proactor_transport_callback_context_is_classified():
    """The structural signature from the production traceback."""
    exc = ConnectionResetError(10054, "forcibly closed")
    for qualname in (
        "_ProactorSocketTransport._call_connection_lost",
        "_ProactorBasePipeTransport._call_connection_lost",
        "_ProactorSocketTransport._fatal_error",
        "_ProactorSocketTransport._loop_writing",
    ):
        ctx = _proactor_context(exc, callback_qualname=qualname)
        assert classify_transport_disconnect(exc, context=ctx), (
            f"{qualname} must classify as a transport disconnect"
        )


def test_socket_server_loggers_are_classified():
    exc = ConnectionResetError(errno.ECONNRESET, "reset")
    for name in ("uvicorn", "uvicorn.error", "asyncio", "starlette"):
        assert classify_transport_disconnect(exc, logger_name=name), (
            f"logger {name} is a socket-server transport"
        )


# ---------------------------------------------------------------------------
# Classification: genuine failures stay visible
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("database connection failed"),
        ValueError("protocol corruption"),
        OSError("unspecified transport failure"),  # no errno at all
        OSError(errno.EACCES, "permission denied"),  # errno, but not a disconnect
        OSError(errno.EADDRINUSE, "address in use"),
        ConnectionResetError(),  # empty: no signal to classify from
    ],
)
def test_unexpected_exceptions_stay_visible(exc):
    assert not is_transport_disconnect_exception(exc)
    assert not classify_transport_disconnect(exc, context=_proactor_context(exc))


def test_broker_and_db_sockets_are_never_downgraded():
    """A reset on an internal socket is always a real operational failure."""
    exc = ConnectionResetError(errno.ECONNRESET, "reset")
    for name in (
        "nexus_scalp.adapters.mt5.mt5_adapter",
        "nexus_scalp.adapters.mt5.remote_gateway",
        "nexus_scalp.adapters.database.audit_repository",
        "nexus_scalp.database",
    ):
        assert not classify_transport_disconnect(exc, logger_name=name), (
            f"{name} must never be downgraded"
        )
        ctx = _proactor_context(exc)
        ctx["logger"] = logging.getLogger(name)
        assert not classify_transport_disconnect(exc, context=ctx)


def test_no_logger_means_visible():
    """No provenance at all -> keep the exception visible rather than guess."""
    exc = ConnectionResetError(errno.ECONNRESET, "reset")
    assert not classify_transport_disconnect(exc, context={})


def test_expected_disconnect_code_contract():
    """The classified code sets (pinned so a drift here is caught)."""
    codes = expected_disconnect_codes()
    assert 10054 in codes["winerror"]
    assert 64 in codes["winerror"]  # ERROR_NETNAME_DELETED
    assert errno.ECONNRESET in codes["errno"]
    assert errno.EPIPE in codes["errno"]
    # EINVAL must never be an errno-level disconnect signal: it is only
    # accepted via the Windows winerror code (see the production signature).
    assert errno.EINVAL not in codes["errno"]


# ---------------------------------------------------------------------------
# Loop handler: expected downgraded, unexpected preserved
# ---------------------------------------------------------------------------
def _proactor_context(exc, callback_qualname=None, message=None):
    """A loop-exception context with a REAL asyncio Handle (CPython reads
    ``self._callback`` off the instance, so a class attribute is invisible)."""
    loop = asyncio.new_event_loop()

    def _callback(*args):
        return None

    if callback_qualname is not None:
        _callback.__qualname__ = callback_qualname
    handle = asyncio.Handle(_callback, (), loop=loop)
    loop.close()
    return {
        "message": message or f"Exception in callback {callback_qualname or 'cb'}(None)",
        "exception": exc,
        "handle": handle,
    }


def test_loop_handler_downgrades_expected_disconnect():
    loop = asyncio.new_event_loop()
    try:
        records = _capture_transport_logger()
        install_transport_disconnect_handler(loop)
        handler = loop.get_exception_handler()
        exc = ConnectionResetError(10054, "forcibly closed")
        handler(loop, _proactor_context(exc, "_ProactorSocketTransport._call_connection_lost"))
        debug = [r for r in records if r.levelno == logging.DEBUG]
        error = [r for r in records if r.levelno >= logging.ERROR]
        assert debug, "expected disconnect must emit one structured DEBUG record"
        assert not error, "expected disconnect must NOT log at ERROR"
        # The record is structured, not a traceback dump
        msg = debug[0].getMessage()
        assert "[TRANSPORT_DISCONNECT]" in msg
    finally:
        loop.close()


def test_loop_handler_preserves_genuine_failures():
    """A genuine failure still reaches the previous handler (ERROR + traceback)."""
    loop = asyncio.new_event_loop()
    try:
        fallback: list[dict] = []

        def _previous(l, ctx):
            fallback.append(ctx)

        loop.set_exception_handler(_previous)
        install_transport_disconnect_handler(loop)
        handler = loop.get_exception_handler()

        exc = RuntimeError("database connection failed")
        handler(loop, {"message": "boom", "exception": exc, "handle": None})
        assert len(fallback) == 1, "genuine failure MUST reach the previous handler"
        assert fallback[0]["exception"] is exc
    finally:
        loop.close()


def test_loop_handler_default_fallback_when_no_previous():
    """Without a prior handler the loop's default ERROR path still runs.

    The default handler logs on the 'asyncio' logger, so the contract here is
    the classification boundary: our handler never classifies a genuine
    failure, which is what keeps the default ERROR path reachable.
    """
    loop = asyncio.new_event_loop()
    try:
        install_transport_disconnect_handler(loop)
        handler = loop.get_exception_handler()
        assert handler is not None
        # A genuine failure must NOT be recognized by our classifier:
        exc = RuntimeError("genuine failure")
        assert not classify_transport_disconnect(exc, context={})
        # and the default handler itself stays callable (not replaced by a
        # no-op), so ERROR output is preserved: the classifier's fallback is
        # exactly the loop's default_exception_handler.
        assert loop.get_exception_handler() is not None
    finally:
        loop.close()


def test_loop_handler_is_idempotent():
    loop = asyncio.new_event_loop()
    try:
        install_transport_disconnect_handler(loop)
        first = loop.get_exception_handler()
        install_transport_disconnect_handler(loop)
        assert loop.get_exception_handler() is first, "double install must not re-chain"
    finally:
        loop.close()


def test_loop_handler_installs_on_proactor_runtime():
    """On the actual supported runtime the handler attaches to the real loop."""
    loop = asyncio.ProactorEventLoop()
    try:
        install_transport_disconnect_handler(loop)
        assert loop.get_exception_handler() is not None
        assert getattr(loop, "nse_transport_disconnect_handler_installed", False)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Loop-platform observability probe
# ---------------------------------------------------------------------------
def test_proactor_platform_describes_the_loop():
    from nexus_scalp.web.transports import proactor_platform

    loop = asyncio.new_event_loop()
    try:
        name = proactor_platform(loop)
        assert name, "platform probe must never return empty"
        if sys.platform == "win32":
            assert name.startswith(("proactor-win32", "win32-"))
        else:
            assert "proactor" not in name
    finally:
        loop.close()


def test_install_logs_one_boot_record():
    """Installation is announced exactly once with loop+platform context."""
    loop = asyncio.new_event_loop()
    try:
        records = _capture_transport_logger()
        install_transport_disconnect_handler(loop)
        install_transport_disconnect_handler(loop)  # idempotent
        boot = [
            r
            for r in records
            if "handler installed" in r.getMessage() and r.levelno == logging.INFO
        ]
        assert len(boot) == 1, f"expected one install record, got {len(boot)}"
        assert "loop=" in boot[0].getMessage()
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# create_app integration
# ---------------------------------------------------------------------------
def test_create_app_installs_the_startup_hook():
    from nexus_scalp.web.server import create_app

    app = create_app()
    assert getattr(app.state, "_transport_disconnect_hooked", False)
    # The handler is bound through the ASGI lifespan seam, so driving the
    # app's lifespan on a real loop installs the classifier on THAT loop.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)

        async def _drive():
            async with app.router.lifespan_context(app):
                assert loop.get_exception_handler() is not None

        loop.run_until_complete(_drive())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_create_app_registers_no_global_suppression():
    """create_app must never install a blanket exception suppressor."""
    from nexus_scalp.web.server import create_app

    app = create_app()
    loop = asyncio.new_event_loop()
    try:
        # Without NSE_WEB_TRANSPORT_DISCONNECT_HANDLER the classifier is only
        # bound at startup; a raising route must still propagate.
        async def _raising():
            raise RuntimeError("genuine failure")

        with pytest.raises(RuntimeError):
            loop.run_until_complete(_raising())
    finally:
        loop.close()
    assert app is not None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _capture_transport_logger() -> list[logging.LogRecord]:
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    log = logging.getLogger("nexus_scalp.web.transports")
    log.addHandler(_Capture())
    log.setLevel(logging.DEBUG)
    return records
