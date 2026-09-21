"""BUG-311 (2026-09-22): shadow-recorder faults must never trip the hot-path
consecutive-error circuit breaker.

ROOT CAUSE
----------
TickPipeline.run_post_policy_stages calls ``LiveEngine._record_shadow_decision``
and ``LiveEngine._record_shadow70_observation``. Both delegates did::

    from nexus_scalp.application.live.shadow_recorder import ShadowRecorder
    ShadowRecorder(self).record_shadow_decision(...)

with the IMPORT outside any try/except. The recorder's own body was already
failure-isolated, but the import line was not, so an unimportable or
half-written ``shadow_recorder`` module (observed while a concurrent agent
rewrote it: the file's mtime moved past the last ImportError 28s later)
propagated ``ImportError`` straight up into the tick-pipeline exception
handler. 83 errors inside a 600s window tripped HotPathErrorCircuit and put
the engine in DEGRADED ("new entries BLOCKED") — a purely observational
subsystem (spec 17: "a shadow fault must NEVER affect production execution")
had taken down live trading.

This test corrupts the module on purpose (the exact failure shape seen in
production: the class is absent) and asserts the hot path survives.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime


def _corrupt_shadow_module() -> None:
    """Replaces shadow_recorder with a module exposing NO ShadowRecorder."""
    name = "nexus_scalp.application.live.shadow_recorder"
    bogus = types.ModuleType(name)
    bogus.__file__ = "<corrupted-mid-write>"
    # The exact production symptom: the name the delegate imports is absent.
    sys.modules[name] = bogus


def _restore_shadow_module() -> None:
    sys.modules.pop("nexus_scalp.application.live.shadow_recorder", None)


def _stub_logger(monkeypatch) -> list:
    """Captures ERROR calls on the live_engine module logger.

    Repo convention (test_bug274_wrapper_state_leak): the structlog host
    routing in observability/logging.py writes to its own file/console
    handlers and stdlib caplog.records stays empty, so log assertions here
    stub the module logger instead.
    """
    import nexus_scalp.application.live_engine as live_engine_mod

    calls: list = []
    monkeypatch.setattr(
        live_engine_mod.logger,
        "error",
        lambda *a, **k: calls.append((a, k)),
    )
    return calls


def _make_engine():
    """Returns an object with the two delegates bound as LiveEngine methods."""
    from nexus_scalp.application.live_engine import LiveEngine

    host = types.SimpleNamespace()
    host._record_shadow_decision = LiveEngine._record_shadow_decision.__get__(host, object)
    host._record_shadow70_observation = LiveEngine._record_shadow70_observation.__get__(
        host, object
    )
    return host


def _tick():
    from nexus_scalp.domain.models import TickData

    return TickData(
        symbol="XAUUSD",
        timestamp=datetime(2026, 9, 22, tzinfo=UTC),
        bid=4362.22,
        ask=4362.71,
    )


def test_record_shadow_decision_swallows_importerror(monkeypatch):
    """An unimportable shadow_recorder logs and returns — never raises."""
    engine = _make_engine()
    calls = _stub_logger(monkeypatch)
    _corrupt_shadow_module()
    try:
        # Must not raise: this is the call TickPipeline makes per tick.
        engine._record_shadow_decision(
            tick=_tick(),
            fv=None,
            regime_state=None,
            proposal=None,
        )
    finally:
        _restore_shadow_module()

    assert any("DELEGATE_FAILURE" in str(c) for c in calls), (
        "the isolated fault must be visible in the log"
    )


def test_record_shadow70_observation_swallows_importerror(monkeypatch):
    engine = _make_engine()
    calls = _stub_logger(monkeypatch)
    _corrupt_shadow_module()
    try:
        engine._record_shadow70_observation(tick=_tick(), fv=None, proposal=None)
    finally:
        _restore_shadow_module()

    assert any("DELEGATE_FAILURE" in str(c) for c in calls), (
        "the isolated fault must be visible in the log"
    )


def test_circuit_recovery_after_transient_fault():
    """The breaker resets itself once a full clean window passes.

    BUG-311 part 2: without any success signal on the clean path, a transient
    10-minute fault left the engine permanently DEGRADED until an operator
    ran the CLI release command. The breaker's own flapping guard is kept (a
    single success inside the window must NOT reset).
    """
    from nexus_scalp.risk.runtime_safety import HotPathErrorCircuit

    circuit = HotPathErrorCircuit()
    t0 = 1_000_000.0
    # 100 consecutive failures within the 600s window -> tripped.
    for i in range(100):
        circuit.record_error(t0 + i, ImportError("cannot import name 'ShadowRecorder'"))
    assert circuit.is_tripped(t0 + 100), "fixture sanity: the fault is real"

    # A single clean tick inside the window must NOT reset (flapping guard).
    circuit.record_success(t0 + 100)
    assert circuit.is_tripped(t0 + 100), "one success must not mask an active fault"

    # A full clean window since the last error must recover without an operator.
    circuit.record_success(t0 + 100 + 601)
    assert not circuit.is_tripped(t0 + 100 + 601), (
        "a full clean window must recover the circuit without an operator"
    )
