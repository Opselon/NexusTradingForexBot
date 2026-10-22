"""OBS-SILENT-PASS Lane A — the silent-pass surfaces now emit log records.

Contract per fix site (worktree fix/obs-engine-silent-passes):
    * the guarded fault path produces exactly the Lane A structured record
      (event token observable in the rendered message), AND
    * the legacy behavior is unchanged — the handler still returns its
      original default / fallback / non-raising outcome.

Driving every real production entrypoint would need a full LiveEngine boot
(structural isolation), so each test drives the narrowest injectable seam:
real production function + minimal doubles + monkeypatched failing
dependency. Test names carry the lane-A F<n> pin from the source comments.

Logging capture: the fix sites log through nexus_scalp's module-level
``get_logger`` bound loggers (structlog stdlib). Under pytest, structlog does
not reliably fan out to caplog handlers before ``configure_logging``, so the
discipline used by test_bug259/test_bug268/test_bug293 is followed: the
MODULE logger is monkeypatched with a probe that captures
``(args, kwargs)`` per severity. The paper-adapter fallback (F5) uses plain
stdlib logging, so ``caplog`` captures it directly.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("NSE_GATEWAY_ALLOW_DEFAULTS", "1")
os.environ.setdefault("NEXUS_PAPER_STRESS_SEED", "42")


# ---------------------------------------------------------------------------
# Log capture discipline
# ---------------------------------------------------------------------------


class _ProbeLogger:
    """Stand-in for a module structlog logger; records (args, kwargs)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, level: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((level, args, kwargs))

    def debug(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self._record("debug", *args, **kwargs)

    def info(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self._record("info", *args, **kwargs)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self._record("warning", *args, **kwargs)

    def error(self, *args: Any, **kwargs: Any) -> None:
        self._record("error", *args, **kwargs)

    def critical(self, *args: Any, **kwargs: Any) -> None:
        self._record("critical", *args, **kwargs)

    def messages(self, *levels: str) -> list[str]:
        wanted = levels or ("warning", "error", "critical")
        return [" ".join(str(a) for a in args) for lvl, args, _kw in self.calls if lvl in wanted]


def assert_single_token(probe: _ProbeLogger, token: str, levels: str = "warning error") -> str:
    """Exactly one probe message must start with ``token``; return it."""
    msgs = probe.messages(*levels.split())
    hits = [m for m in msgs if m.startswith(token)]
    assert hits, f"expected an event token {token!r} in {msgs}"
    assert len(hits) == 1, f"event token {token!r} fired more than once: {hits}"
    return hits[0]


class _TelemetryStub:
    """TelemetryThrottle stand-in: never throttled, no-op stamps."""

    def last_emit(self, ticket: int) -> float:
        return 0.0

    def __getattr__(self, name: str) -> Any:
        return _Auto()


class _StubState:
    """PositionState stand-in for the telemetry render (value -> str)."""

    value = "TEST_STATE"


class _Auto(dict):
    """Tracker/collaborator auto stand-in (dict.pop, callable, ctx-manager)."""

    def __getattr__(self, name: str) -> Any:
        return _Auto()

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


# ---------------------------------------------------------------------------
# F1 — live_engine.py _update_runtime_mode: RUNTIME_MODE_PROBE_FAILED
# (permissive default kept)
# ---------------------------------------------------------------------------


def test_f1_runtime_mode_probe_failure_loud_and_permissive(monkeypatch) -> None:
    """A broken trade_allowed probe must log RUNTIME_MODE_PROBE_FAILED while
    keeping the permissive default (allowed=True -> mode stays LIVE)."""
    from nexus_scalp.application import live_engine as le

    class _Snap:
        @property
        def trade_allowed(self) -> bool:
            raise ValueError("runtime-config probe raised")

    class _Adapter:
        def is_connected(self) -> bool:
            return True

    class _Mode:
        value = "LIVE"

    class _Execution:
        mode = _Mode()

    class _Cfg:
        execution = _Execution()

    engine = object.__new__(le.LiveEngine)
    engine.config = _Cfg()
    engine.adapter = _Adapter()
    engine._account_snapshot = _Snap()
    engine._runtime_mode = None

    probe = _ProbeLogger()
    monkeypatch.setattr(le, "logger", probe)
    le.LiveEngine._update_runtime_mode(engine)

    msg = assert_single_token(probe, "RUNTIME_MODE_PROBE_FAILED", levels="error")
    assert "permissive default" in msg
    assert engine._runtime_mode == "LIVE", "legacy outcome: trading stays allowed"


# ---------------------------------------------------------------------------
# F2 — dispatch.py _clamp_dispatch_volume (both probes):
# DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE (engine-default clamp kept)
# ---------------------------------------------------------------------------


def _clamp_engine(monkeypatch, get_symbol_info=None, get_account_info=None):
    """DispatchEngine over a minimal composition root with stub probes."""
    from nexus_scalp.execution.lifecycle import dispatch as dispatch_mod

    class _Adapter:
        pass

    class _RiskEngine:
        @staticmethod
        def get_clamped_position_size(**kw: Any) -> float:
            return 1.0

    class _Om:
        adapter = _Adapter()
        risk_engine = _RiskEngine()

    if get_symbol_info is not None:
        _Om.adapter.get_symbol_info = staticmethod(get_symbol_info)
    if get_account_info is not None:
        _Om.adapter.get_account_info = staticmethod(get_account_info)

    probe = _ProbeLogger()
    monkeypatch.setattr(dispatch_mod, "logger", probe)
    return dispatch_mod.DispatchEngine(_Om()), probe


def test_f2_dispatch_clamp_symbol_info_failure_warns_keeps_default(monkeypatch) -> None:
    """get_symbol_info raising must warn and clamp against engine defaults."""

    def _boom(symbol: str) -> None:
        raise ValueError("symbol-info probe raised")

    eng, probe = _clamp_engine(monkeypatch, get_symbol_info=_boom)

    out = eng._clamp_dispatch_volume(2.0, symbol="XAUUSD")
    msgs = [
        m
        for m in probe.messages("warning")
        if m.startswith("DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE") and "get_symbol_info(" in m
    ]
    assert msgs, probe.messages("warning")
    assert "clamping against defaults" in msgs[0]
    assert out == 1.0, "legacy outcome: risk-engine clamp result preserved"


def test_f2_dispatch_clamp_account_info_failure_warns_keeps_default(monkeypatch) -> None:
    """get_account_info raising must warn and still return the clamp result."""

    def _boom(symbol: str) -> None:
        raise ValueError("symbol-info probe raised")

    def _boom_acct() -> None:
        raise ValueError("account-info probe raised")

    eng, probe = _clamp_engine(monkeypatch, get_symbol_info=_boom, get_account_info=_boom_acct)

    out = eng._clamp_dispatch_volume(2.0, symbol="XAUUSD")
    msgs = probe.messages("warning")
    assert any(
        m.startswith("DISPATCH_CLAMP_BROKER_SPEC_UNAVAILABLE") and "get_account_info()" in m
        for m in msgs
    ), msgs
    assert out == 1.0, "legacy outcome: clamp path result preserved"


# ---------------------------------------------------------------------------
# F3 — runtime_mode.py configure_broker_identity failure is LOUD and the
# swap still completes (MODE_CONFIGURE_BROKER_IDENTITY_FAILED)
# ---------------------------------------------------------------------------


def test_f3_hot_swap_identity_failure_loud_and_swap_completes(monkeypatch) -> None:
    """configure_broker_identity raising during a PAPER->LIVE hot swap must
    log MODE_CONFIGURE_BROKER_IDENTITY_FAILED, and the swap itself still
    completes (legacy outcome: the identity failure is isolated)."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live import runtime_mode as rm_mod
    from nexus_scalp.domain.enums import ExecutionMode

    class _BoomAdapter:
        def __init__(self) -> None:
            self.magic = "ORIGINAL_MAGIC"
            self.connected = False

        def configure_broker_identity(self, magic: Any = None, bot_symbol: Any = None) -> None:
            raise ValueError("identity probe raised")

        def connect(self) -> bool:
            self.connected = True
            return True

        def disconnect(self) -> None:
            self.connected = False

    class _OrderManager:
        adapter = None
        mt5_adapter = None

    class _Mode:
        value = "PAPER"

    class _Execution:
        mode = _Mode()
        magic_number = 999101
        symbol = "XAUUSD"

    class _Cfg:
        execution = _Execution()

    class _Engine:
        config = _Cfg()
        adapter = PaperMT5Adapter(symbol="XAUUSD")
        order_manager = _OrderManager()
        _mode_override = None
        _runtime_mode = None

        def _update_runtime_mode(self) -> None:
            """Badge refresh follows the swap; out of F3's scope."""
            return None

        def _invalidate_cross_mode_state(self, *a: Any, **kw: Any) -> None:
            return None

    eng = _Engine()
    eng.order_manager.adapter = eng.adapter
    boom_adapter = _BoomAdapter()

    # The LIVE-swap branch imports DirectMT5Adapter from
    # nexus_scalp.adapters.mt5.mt5_adapter at call time: stub the module.
    stub = type(sys)("nexus_scalp.adapters.mt5.mt5_adapter")
    stub.DirectMT5Adapter = lambda **kw: boom_adapter
    monkeypatch.setitem(sys.modules, "nexus_scalp.adapters.mt5.mt5_adapter", stub)

    probe = _ProbeLogger()
    monkeypatch.setattr(rm_mod, "logger", probe)

    # Unbound-delegation contract (seam L13): methods are invoked UNBOUND
    # with the engine as the state surface.
    result = rm_mod.RuntimeModeService.set_execution_mode(eng, ExecutionMode.LIVE, source="TEST")

    assert result["success"] is True, result
    assert result["adapter_swapped"] is True
    assert eng.adapter is boom_adapter, "adapter swap completes despite identity failure"
    assert eng.order_manager.adapter is boom_adapter
    assert boom_adapter.connected is True

    msg = assert_single_token(probe, "MODE_CONFIGURE_BROKER_IDENTITY_FAILED", levels="error")
    assert "STALE magic" in msg
    assert boom_adapter.magic == "ORIGINAL_MAGIC", (
        "legacy: identity failure leaves the adapter's own identity untouched"
    )


# ---------------------------------------------------------------------------
# F4 — order_manager.py AI-flip probability assembly:
# AI_FLIP_PROB_ASSEMBLY_FAILED (exit chain keeps running)
# ---------------------------------------------------------------------------


def test_f4_ai_flip_prob_assembly_failure_loud_and_chain_continues(monkeypatch) -> None:
    """probability assembly (squeeze) raising must log
    AI_FLIP_PROB_ASSEMBLY_FAILED and keep the legacy outcome: the protection
    chain does NOT propagate the fault (defers to the deterministic chain)."""
    from nexus_scalp.domain.enums import OrderType
    from nexus_scalp.execution.protection_ledger import PositionProtectionLedger

    protection = PositionProtectionLedger().get(4242)

    class _Pos:
        ticket = 4242
        type = OrderType.BUY
        profit = 0.0

    class _BoomProbs:
        def squeeze(self) -> None:
            raise ValueError("assembly probe raised")

    class _SymbolInfo:
        digits = 2
        trade_contract_size = 100.0

    import nexus_scalp.execution.order_manager as om_mod

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.algo_config = None

    def _no_giveback(*a: Any, **kw: Any) -> tuple[int, bool]:
        return 0, False

    def _no_breakeven(*a: Any, **kw: Any) -> bool:
        return False

    om.enforce_profit_giveback_protection = _no_giveback  # type: ignore[method-assign]
    om.apply_breakeven_lock = _no_breakeven  # type: ignore[method-assign]
    om.notifier = None
    om._telemetry = _TelemetryStub()
    from nexus_scalp.execution.lifecycle import TicketStateStore
    from nexus_scalp.execution.position_tracker import PositionTrackingLedger

    om._tracking = PositionTrackingLedger()
    om._states = TicketStateStore()
    for _tracker in (
        om._time_in_profit_sec,
        om._time_in_drawdown_sec,
        om._peak_profit_usd,
        om._peak_drawdown_usd,
    ):
        _tracker[4242] = 0.0
    probe = _ProbeLogger()
    monkeypatch.setattr(om_mod, "logger", probe)

    handled = om._run_protection_chain(
        pos=_Pos(),
        ticket=4242,
        now=None,
        current_time=0.0,
        protection=protection,
        price_current=2000.0,
        hold_score=0,
        base_hold_score=0,
        invalidate_reasons=[],
        atr=1.0,
        spread=0.3,
        min_stop_gap=0.1,
        symbol_info=_SymbolInfo(),
        smart_metrics={},
        evidence={},
        pnl_features={},
        probs=_BoomProbs(),
        feature_vector=None,
        regime_state=None,
        current_tick=None,
        holding_duration=0.0,
        debounced_state=_StubState(),
        prot_score=0.0,
    )

    msg = assert_single_token(probe, "AI_FLIP_PROB_ASSEMBLY_FAILED", levels="error")
    assert "fast-reversal exit SKIPPED" in msg
    assert "4242" in msg
    # Legacy outcome: the fault does not propagate (chain falls through to
    # the deterministic protection priority chain / decision stage).
    assert handled is False


# ---------------------------------------------------------------------------
# F5 — paper_adapter.py stdlib logging fallback (caplog capture): when the
# structured get_logger call itself fails, the original tick-path fault is
# re-surfaced through plain stdlib logging and the tick STILL returns.
# ---------------------------------------------------------------------------


def _stress_market_stub(monkeypatch: pytest.MonkeyPatchClass, tick_fn: Any) -> None:
    """The delegated tick path imports PaperStressMarket from
    nexus_scalp.market_data.paper_stress inside _get_last_tick_delegated.
    The production module exposes only generator functions, so tests inject
    a minimal stand-in class so the guarded process_tick_execution block is
    actually reached (seam: the delegated-source injection)."""
    stress_mod = type(sys)("nexus_scalp.market_data.paper_stress")

    class _StubStressMarket:
        def __init__(self, seed: int) -> None:
            self.seed = seed

        def next_tick(self, symbol: str) -> dict[str, float]:
            return tick_fn(symbol)

    stress_mod.PaperStressMarket = _StubStressMarket
    monkeypatch.setitem(sys.modules, "nexus_scalp.market_data.paper_stress", stress_mod)


def test_f5_paper_adapter_observability_failure_falls_back_to_stdlib(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatchClass
) -> None:
    """process_tick_execution raising AND a broken get_logger must produce
    the stdlib fallback record (original fault repr carried) and keep the
    legacy outcome: the tick feed stays alive (tick returned)."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.observability import logging as obs_logging

    _stress_market_stub(monkeypatch, lambda symbol: {"bid": 4400.01, "ask": 4400.09})

    adapter = PaperMT5Adapter(symbol="XAUUSD")

    def _boom_tick(tick: Any) -> None:
        raise RuntimeError("sltp probe raised")

    def _boom_get_logger(name: str) -> Any:
        raise ValueError("observability probe raised")

    adapter.process_tick_execution = _boom_tick  # type: ignore[method-assign]

    with (
        patch.object(obs_logging, "get_logger", _boom_get_logger),
        caplog.at_level(logging.ERROR),
    ):
        out = adapter._get_last_tick_delegated(symbol="XAUUSD", seed=7)

    assert out is not None, "legacy outcome: tick returned even when both loggers fail"
    fallback_hits = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.ERROR
        and "PAPER_SLTP_PROCESSING_FAILED (fallback)" in r.getMessage()
    ]
    assert fallback_hits, [
        (r.name, r.getMessage()) for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert "sltp probe raised" in fallback_hits[0], "original fault repr carried"


def test_f5_observability_primary_path_emits_structured_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatchClass
) -> None:
    """With a healthy get_logger the structured record must be emitted when
    process_tick_execution raises (guards the fallback from silently
    becoming the primary path)."""
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

    _stress_market_stub(monkeypatch, lambda symbol: {"bid": 4400.11, "ask": 4400.19})

    adapter = PaperMT5Adapter(symbol="XAUUSD")

    def _boom_tick(tick: Any) -> None:
        raise RuntimeError("sltp probe raised")

    adapter.process_tick_execution = _boom_tick  # type: ignore[method-assign]

    class _CaptureHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.WARNING)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    # structlog's DEFAULT PrintLoggerFactory prints to stdout and never
    # reaches a stdlib handler tree (same trap documented in this module's
    # docstring and in test_web_security.py) - so capture via the production
    # get_logger seam itself: monkeypatch observability.logging.get_logger
    # to return a probe of the emit chain, keeping the .error(msg, **kw)
    # keyword contract the production call site uses.
    captured: list[tuple[str, dict[str, Any]]] = []
    from nexus_scalp.adapters.paper import paper_adapter as pa_mod

    real_error = logging.getLogger("nexus_scalp.adapters.paper").error

    class _ProbeLogger:
        def error(self, msg: str, **kw: Any) -> None:
            captured.append((msg, dict(kw)))
            real_error("%s %s", msg, kw)  # also exercise the stdlib path

    def _fake_get_logger(name: str) -> Any:
        assert "nexus_scalp.adapters.paper" in name
        return _ProbeLogger()

    from nexus_scalp.observability import logging as obs_logging

    monkeypatch.setattr(obs_logging, "get_logger", _fake_get_logger)
    try:
        adapter._get_last_tick_delegated(symbol="XAUUSD", seed=11)
    finally:
        monkeypatch.undo()
    structured = [msg for msg, _ in captured]
    assert any("PAPER_SLTP_PROCESSING_FAILED" in m for m in structured), captured


# ---------------------------------------------------------------------------
# F6 — gateway/server.py _expected_keys: GATEWAY_SECRET_STORE_READ_FAILED
# (env-or-default chain proceeds)
# ---------------------------------------------------------------------------


def test_f6_gateway_secret_store_failure_warns_and_falls_through(monkeypatch) -> None:
    """A broken SecureSecretStore read must warn and continue down the
    env-or-default chain (legacy outcome unchanged)."""
    from nexus_scalp.gateway import server as srv_mod

    class _BoomStore:
        def __init__(self) -> None:
            pass

        def get_secret(self, name: str) -> str:
            raise ValueError("secret store probe raised")

    monkeypatch.delenv(srv_mod.SECRET_ENV_API_KEY, raising=False)
    monkeypatch.delenv(srv_mod.SECRET_ENV_SECRET, raising=False)
    monkeypatch.setattr(srv_mod, "_allow_live", False)

    stub = type(sys)("nexus_scalp.settings.secret_store")
    stub.SecureSecretStore = _BoomStore
    monkeypatch.setitem(sys.modules, "nexus_scalp.settings.secret_store", stub)

    probe = _ProbeLogger()
    monkeypatch.setattr(srv_mod, "logger", probe)

    key, secret = srv_mod._expected_keys()
    msg = assert_single_token(probe, "GATEWAY_SECRET_STORE_READ_FAILED")
    assert "env-or-default" in msg
    # Legacy outcome: lifecycle continues down the default chain (demo mode
    # or the fail-closed RuntimeError for LIVE — same chain either way).
    assert isinstance(key, str) and isinstance(secret, str)


# ---------------------------------------------------------------------------
# F7 — gateway/server.py _get_adapter demo guard:
# GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED (fails closed)
# ---------------------------------------------------------------------------


def test_f7_gateway_account_snapshot_probe_failure_fails_closed(monkeypatch) -> None:
    """An unreadable account tier must log
    GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED, disconnect the adapter, and
    re-raise (legacy fail-closed outcome unchanged)."""
    from nexus_scalp.gateway import server as srv_mod

    srv_mod.reset_adapter_for_tests()

    class _Adapter:
        def __init__(self) -> None:
            self.disconnected = False

        def connect(self) -> bool:
            return True

        def get_account_snapshot(self) -> None:
            raise ValueError("snapshot probe raised")

        def disconnect(self) -> None:
            self.disconnected = True

    adapter = _Adapter()

    # The production guard refuses to build an adapter off-Windows, so a test
    # running on Linux CI would never reach the probe under test. Pin the
    # platform for the duration of this test (restored by monkeypatch).
    monkeypatch.setattr(srv_mod.sys, "platform", "win32")

    # _get_adapter imports DirectMT5Adapter from
    # nexus_scalp.adapters.mt5.mt5_adapter at call time: stub the module.
    stub = type(sys)("nexus_scalp.adapters.mt5.mt5_adapter")
    stub.DirectMT5Adapter = lambda: adapter
    monkeypatch.setitem(sys.modules, "nexus_scalp.adapters.mt5.mt5_adapter", stub)

    probe = _ProbeLogger()
    monkeypatch.setattr(srv_mod, "logger", probe)

    with pytest.raises(ValueError):  # fail-closed re-raise of the probe fault
        srv_mod._get_adapter()

    msg = assert_single_token(probe, "GATEWAY_ACCOUNT_SNAPSHOT_PROBE_FAILED", levels="error")
    assert "NOT VERIFIED" in msg
    assert adapter.disconnected, "fail-closed: adapter must be disconnected"


# ---------------------------------------------------------------------------
# F8 — order_manager.py adviser forget failure: ADVISER_FORGET_FAILED
# (rate limited, non-fatal teardown)
# ---------------------------------------------------------------------------


def _teardown_skeleton(adviser: Any) -> Any:
    """Real OrderLifecycleManager teardown body on a skeleton instance
    (same discipline as test_position_adviser_forensics' F5 helpers)."""
    import nexus_scalp.execution.order_manager as om_mod

    class _Skeleton(om_mod.OrderLifecycleManager):
        def __init__(self) -> None:
            self._position_adviser = adviser
            self._adviser_forget_warn_times: dict[str, float] = {}

        def __getattr__(self, name: str) -> Any:
            if name.startswith("_") and not name.startswith("__"):
                auto = _Auto()
                setattr(self, name, auto)
                return auto
            raise AttributeError(name)

    return _Skeleton()


def test_f8_adviser_forget_failure_warns(monkeypatch) -> None:
    """adviser.forget raising must warn ADVISER_FORGET_FAILED and keep the
    teardown non-fatal (legacy outcome: teardown completes)."""
    from nexus_scalp.execution import order_manager as om_mod

    class _BoomAdviser:
        def forget(self, ticket: int) -> None:
            raise RuntimeError("forget probe raised")

    mgr = _teardown_skeleton(_BoomAdviser())
    probe = _ProbeLogger()
    monkeypatch.setattr(om_mod, "logger", probe)

    mgr._cleanup_ticket_state(77)
    msg = assert_single_token(probe, "ADVISER_FORGET_FAILED")
    assert "stale adviser per-ticket state" in msg


def test_f8_adviser_forget_rate_limit_silences_immediate_repeat(monkeypatch) -> None:
    """An immediate second forget failure within the 1h window must stay
    silent; one after the window re-fires."""
    from nexus_scalp.execution import order_manager as om_mod

    class _BoomAdviser:
        def forget(self, ticket: int) -> None:
            raise RuntimeError("forget probe raised")

    mgr = _teardown_skeleton(_BoomAdviser())
    probe = _ProbeLogger()
    monkeypatch.setattr(om_mod, "logger", probe)

    clock = iter([100.0, 101.0, 5000.0])
    with patch.object(om_mod.time, "monotonic", lambda: next(clock)):
        mgr._cleanup_ticket_state(1)  # warns (stamp set)
        mgr._cleanup_ticket_state(1)  # rate limited: no second record
    assert len([m for m in probe.messages("warning") if "ADVISER_FORGET_FAILED" in m]) == 1

    with patch.object(om_mod.time, "monotonic", lambda: next(clock)):
        mgr._cleanup_ticket_state(1)  # window elapsed: re-fires
    assert len([m for m in probe.messages("warning") if "ADVISER_FORGET_FAILED" in m]) == 2


def test_f8_adviser_forget_success_is_silent(monkeypatch) -> None:
    """A healthy forget must NOT emit ADVISER_FORGET_FAILED."""
    from nexus_scalp.execution import order_manager as om_mod

    class _OkAdviser:
        def forget(self, ticket: int) -> None:
            pass

    mgr = _teardown_skeleton(_OkAdviser())
    probe = _ProbeLogger()
    monkeypatch.setattr(om_mod, "logger", probe)
    mgr._cleanup_ticket_state(888)
    assert not any(
        m.startswith("ADVISER_FORGET_FAILED") for m in probe.messages("warning", "error")
    )


# ---------------------------------------------------------------------------
# F9 — order_manager.py _broker_close_verified:
# CLOSE_VERIFICATION_UNAVAILABLE (trust the adapter close result)
# ---------------------------------------------------------------------------


def test_f9_close_verification_unavailable_warns_and_trusts(monkeypatch) -> None:
    """get_positions raising must warn CLOSE_VERIFICATION_UNAVAILABLE and
    keep the legacy outcome: trust the adapter close result (return True) —
    never treat an exception as proof the position is open."""
    import nexus_scalp.execution.order_manager as om_mod

    class _BoomAdapter:
        def get_positions(self, symbol: str | None = None) -> None:
            raise ValueError("position probe raised")

    om = om_mod.OrderLifecycleManager.__new__(om_mod.OrderLifecycleManager)
    om.adapter = _BoomAdapter()
    probe = _ProbeLogger()
    monkeypatch.setattr(om_mod, "logger", probe)

    alive = om._broker_close_verified(ticket=31337)
    msg = assert_single_token(probe, "CLOSE_VERIFICATION_UNAVAILABLE")
    assert "trusting adapter close result" in msg
    assert "31337" in msg
    assert alive is True, "legacy outcome: adapter result trusted"
