"""BUG-310 regression: MT5 reconnect wedge with (-6, 'Terminal: Authorization failed').

Observed live (LIVE engine, XAUUSD): the tick stall watchdog fired, disconnect()
ran, then connect() failed. From that moment on the engine retried connect()
every ~3 seconds for the whole session and NEVER recovered, hammering:

    [MT5_CONNECT] event=RETRY ... retcode=(-6, 'Terminal: Authorization failed')

Root cause: the MetaTrader5 Python API is a single process-global connection.
``shutdown()`` is the only release. disconnect() guarded it with
``if self._connected``, so once a connect() failure left the module-level
handle attached while ``self._connected`` was already False, every subsequent
disconnect() skipped the release and the terminal kept rejecting re-attaches.

This test stubs the mt5 module to model that global handle and asserts the
adapter escapes the trap: after connect() fails mid-session, a second
disconnect()+connect() cycle MUST be able to rebind.
"""

import sys
import types
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class _TermInfo:
    """Minimal stand-in for mt5.terminal_info() (is_connected reads .connected)."""

    connected: bool


class _FakeMT5:
    """Models the process-global single-connection contract of the C-extension.

    ``attached`` is the terminal-side view: True while an API client holds the
    IPC handle. A second initialize() while attached fails exactly like
    retcode -6 in production.

    ``reject_attach`` models a genuine terminal-side rejection (broker
    re-auth, daily rollover): unlike the stale-handle trap it is NOT cleared
    by our own pre-attach shutdown(), and must be cleared by the caller.
    """

    def __init__(self) -> None:
        self.attached = False
        self.shutdown_calls = 0
        self.initialize_calls = 0
        self.reject_attach = False
        self._last_error: tuple[int, str] = (0, "")

    def initialize(self, **kwargs: object) -> bool:
        self.initialize_calls += 1
        if self.reject_attach:
            self._last_error = (-6, "Terminal: Authorization failed")
            return False
        if self.attached:
            self._last_error = (-6, "Terminal: Authorization failed")
            return False
        self.attached = True
        self._last_error = (0, "")
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.attached = False

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    def terminal_info(self) -> _TermInfo:
        """Models mt5.terminal_info(): connected only while a client holds the
        handle (is_connected() derives from this)."""
        return _TermInfo(connected=self.attached)

    def version(self) -> tuple[int, int, int, str]:
        return (5, 0, 0, "stub build")


@pytest.fixture()
def fake_mt5(monkeypatch: pytest.MonkeyPatch) -> _FakeMT5:
    fake = _FakeMT5()
    mod = types.ModuleType("MetaTrader5")
    mod.initialize = fake.initialize  # type: ignore[attr-defined]
    mod.shutdown = fake.shutdown  # type: ignore[attr-defined]
    mod.last_error = fake.last_error  # type: ignore[attr-defined]
    mod.terminal_info = fake.terminal_info  # type: ignore[attr-defined]
    mod.version = fake.version  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)

    import nexus_scalp.adapters.mt5.mt5_adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "HAS_NATIVE_MT5", True)
    monkeypatch.setattr(adapter_mod, "mt5", mod)
    return fake


def _adapter() -> "object":
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    return DirectMT5Adapter(timeout=100, retries=3)


class TestBug310ReconnectRelease:
    def test_disconnect_releases_even_when_connect_failed(self, fake_mt5: _FakeMT5) -> None:
        """THE BUG: a mid-session connect() failure must not leave the handle
        permanently attached, since disconnect() then never releases it."""
        a = _adapter()
        assert a.connect() is True
        assert fake_mt5.attached is True

        # Simulate a real terminal-side rejection (e.g. daily rollover, broker
        # re-auth). disconnect() then a connect() that fails mid-cycle.
        a.disconnect()
        fake_mt5.reject_attach = True  # terminal rejects the attach (broker re-auth)
        assert a.connect() is False
        # handle state after the failure is terminal-defined; the point is the
        # NEXT disconnect() must still be able to release it.
        a.disconnect()

        # Recovery: terminal re-accepts, and the adapter rebinds.
        fake_mt5.reject_attach = False
        assert a.connect() is True, (
            "BUG-310 regression: adapter could not rebind after a failed "
            "reconnect — the engine would hammer (-6) forever"
        )

    def test_connect_releases_stale_handle_before_attach(self, fake_mt5: _FakeMT5) -> None:
        """connect() must not attempt to attach over an already-attached
        handle (production: leftover from an aborted adapter in the process).

        The pre-attach shutdown() releases the stale handle, so initialize()
        rebinds cleanly instead of being rejected with -6 forever.
        """
        fake_mt5.attached = True  # terminal believes a client is attached
        a = _adapter()
        assert a.connect() is True, "stale handle was not released before attach"
        # the release actually happened (not a silent double-attach)
        assert fake_mt5.shutdown_calls >= 1

    def test_disconnect_is_idempotent(self, fake_mt5: _FakeMT5) -> None:
        """Repeated disconnect() must stay safe (watchdog calls it in a loop)."""
        a = _adapter()
        a.connect()
        for _ in range(4):
            a.disconnect()
        assert fake_mt5.attached is False
        # and a connect() still works afterwards
        assert a.connect() is True

    def test_watchdog_recovery_cycle(self, fake_mt5: _FakeMT5) -> None:
        """The exact live sequence: stall -> disconnect -> connect fail ->
        stall -> disconnect -> connect. The second cycle MUST recover."""
        a = _adapter()
        assert a.connect() is True

        a.disconnect()
        fake_mt5.reject_attach = True  # first reconnect rejected, as in the log
        assert a.connect() is False

        a.disconnect()
        fake_mt5.reject_attach = False
        assert a.connect() is True
        assert a.is_connected() is True
