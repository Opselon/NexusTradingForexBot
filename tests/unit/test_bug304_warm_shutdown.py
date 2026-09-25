"""BUG-304: warm, async, fully-completed shutdown.

Operator report (2026-09-21): "when we close the application or console or
anything like it, it's not completely closed." Root causes pinned here:

1. ``asyncio.run(gather(server.serve(), engine.run_loop()))`` — the ONLY
   signal-aware object in the process is asyncio's Runner, whose SIGINT
   handler CANCELS THE MAIN TASK. The gather unwinds and the process exits
   before ``engine._shutdown_async`` ever runs: broker session, SQLite WAL
   and pending audit rows left open.
2. ``except KeyboardInterrupt`` in both launchers printed "terminated
   cleanly" — a claim with no teardown behind it.
3. Nothing bounded ``AuditRepository.close`` (``_queue.join()`` with no
   timeout), so a stuck insert could park exit forever and force a
   taskkill /F — reproducing cause 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import signal
import threading
import time
from typing import Any

import pytest

from nexus_scalp.application.shutdown import (
    PHASE_CLOSED,
    PHASE_DRAIN,
    ShutdownSupervisor,
)
from tests.e2e.chain_clock import budget_cpu_ms


class _FakeEngine:
    """Minimal engine double that records teardown evidence, not fakes it."""

    def __init__(self, *, shutdown_seconds: float = 0.0, raise_on_shutdown: bool = False):
        self._running = True
        self.shutdown_calls = 0
        self.shutdown_completed = False
        self._shutdown_seconds = shutdown_seconds
        self._raise = raise_on_shutdown

    async def _shutdown_async(self) -> None:
        self.shutdown_calls += 1
        if self._shutdown_seconds:
            await asyncio.sleep(self._shutdown_seconds)
        if self._raise:
            raise RuntimeError("boom")
        self.shutdown_completed = True

    def shutdown_status(self) -> dict[str, Any]:
        if self.shutdown_completed:
            return {"phase": "CLOSED", "completed": True}
        return {"phase": "RUNNING", "completed": False}


class _FakeServer:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


# ---------------------------------------------------------------------------
# 1. The supervisor actually runs the engine's teardown exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_runs_engine_teardown_exactly_once():
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    for _ in range(3):  # repeated requests must not re-run teardown
        await sup.wait_for_shutdown()
    assert engine.shutdown_calls == 1
    assert engine.shutdown_completed is True
    status = sup.status()
    assert status["phase"] == PHASE_CLOSED
    assert status["outcome"] == "OK"


@pytest.mark.asyncio
async def test_request_shutdown_is_idempotent_and_non_blocking():
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    first = sup.request_shutdown("ctrl_c")
    second = sup.request_shutdown("ctrl_c")
    assert first is True
    assert second is False
    assert sup.reason == "ctrl_c"
    assert sup.requested_at is not None
    # request_shutdown must be safe from a signal handler: never blocks.
    # CPU-time bound (ML-QA-004): a wall-clock bound here trips on co-tenant
    # CI scheduler load; process_time() is insensitive to it. 100 idempotent
    # no-op calls cost microseconds, so 1.0 s CPU is ~1000x real margin.
    with budget_cpu_ms(1000.0) as sw:
        for _ in range(100):
            sup.request_shutdown("x")
    assert sw.consumed_ms < 1000.0


@pytest.mark.asyncio
async def test_concurrent_waiters_converge_on_one_drain():
    engine = _FakeEngine(shutdown_seconds=0.1)
    sup = ShutdownSupervisor(engine=engine)
    results = await asyncio.gather(*(sup.wait_for_shutdown() for _ in range(5)))
    assert engine.shutdown_calls == 1
    assert all(r["phase"] == PHASE_CLOSED for r in results)


@pytest.mark.asyncio
async def test_drain_bounds_a_slow_teardown():
    """A teardown exceeding its budget reports TIMEOUT, never hangs."""
    engine = _FakeEngine(shutdown_seconds=0.5)
    sup = ShutdownSupervisor(engine=engine)
    started = time.monotonic()
    status = await sup.wait_for_shutdown(timeout=0.15)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, "drain must not overrun its budget materially"
    assert status["phase"] == PHASE_CLOSED
    assert status["outcome"] == "TIMEOUT"


@pytest.mark.asyncio
async def test_drain_survives_a_raising_teardown():
    """A teardown exception is recorded, not swallowed or re-raised."""
    engine = _FakeEngine(raise_on_shutdown=True)
    sup = ShutdownSupervisor(engine=engine)
    status = await sup.wait_for_shutdown(timeout=5.0)
    assert status["phase"] == PHASE_CLOSED
    assert status["outcome"] == "ERROR"
    assert "boom" in status["error"]


@pytest.mark.asyncio
async def test_drain_stops_the_engine_flag_first():
    """The engine flag flips before teardown so the tick loop stops trading."""
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    seen: list[bool] = []

    async def _spy_shutdown():
        seen.append(engine._running)
        engine.shutdown_completed = True

    engine._shutdown_async = _spy_shutdown  # type: ignore[method-assign]
    await sup.wait_for_shutdown()
    assert seen and seen[0] is False, "_running must be False before teardown runs"


@pytest.mark.asyncio
async def test_drain_shuts_down_the_web_server():
    server = _FakeServer()
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine, server=server)
    await sup.wait_for_shutdown()
    assert server.shutdown_calls == 1


# ---------------------------------------------------------------------------
# 2. Signal-handler installation is fail-isolated and idempotent
# ---------------------------------------------------------------------------


def test_install_signal_handlers_is_idempotent():
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    sup.install_signal_handlers()
    sup.install_signal_handlers()  # second call must not double-register
    assert sup._handlers_installed is True


def test_install_signal_handlers_off_main_thread_is_isolated():
    """signal.signal raises off the main thread; installation must not crash."""
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    errors: list[BaseException] = []

    def _work() -> None:
        try:
            sup.install_signal_handlers()
        except BaseException as exc:
            errors.append(exc)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert errors == [], f"off-main-thread install must be isolated, got {errors}"


def test_request_shutdown_from_another_thread_is_safe():
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    problems: list[BaseException] = []

    def _work() -> None:
        try:
            for _ in range(200):
                sup.request_shutdown("console_close")
        except BaseException as exc:
            problems.append(exc)

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=5.0)
    assert problems == []
    assert sup.reason == "console_close"


# ---------------------------------------------------------------------------
# 3. AuditRepository.close is bounded and idempotent
# ---------------------------------------------------------------------------


def _audit_repo(tmp_path, monkeypatch):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    db = tmp_path / "audit_shutdown.db"
    monkeypatch.setenv("NSE_AUDIT_DB", str(db))
    return AuditRepository(db_url=f"sqlite:///{db}")


def test_audit_close_is_bounded_against_a_stuck_writer(tmp_path, monkeypatch):
    """A writer that never drains must not hang close() (BUG-304 cause 3)."""
    repo = _audit_repo(tmp_path, monkeypatch)
    # Simulate a stuck insert: the queue never empties and the writer never
    # calls task_done, so an unbounded _queue.join() would block forever.
    repo._queue.put(("NEVER_DRAINED", ()))
    started = time.monotonic()
    repo.close()
    elapsed = time.monotonic() - started
    assert elapsed < repo._CLOSE_FLUSH_TIMEOUT_SEC + 2.0, "close() must not hang"


def test_audit_close_is_idempotent(tmp_path, monkeypatch):
    repo = _audit_repo(tmp_path, monkeypatch)
    repo.close()
    repo.close()  # second close must not raise
    assert repo._worker_thread is None
    assert repo._shared_conn is None


def test_audit_close_drains_pending_rows(tmp_path, monkeypatch):
    """The happy path still flushes: pending records land before close."""
    repo = _audit_repo(tmp_path, monkeypatch)
    schema = (
        "CREATE TABLE IF NOT EXISTS signals_test_shutdown "
        "(id INTEGER PRIMARY KEY, ts INTEGER, payload TEXT)"
    )
    repo._queue.put(("_execute_schema", (schema,)))
    # Let the writer consume it (it only processes known shapes, so this
    # may stay queued — the contract is: close does not hang either way).
    deadline = time.monotonic() + 2.0
    while not repo._queue.empty() and time.monotonic() < deadline:
        time.sleep(0.02)
    repo.close()
    assert repo._running is False


# ---------------------------------------------------------------------------
# 4. The pre-fix launcher shape would skip teardown; pin the contract
# ---------------------------------------------------------------------------


def _launcher_module():
    """Import the repo-root launcher for structural pins."""
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "nse_launcher_bug304", repo_root / "NexusTradingForexBot.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_imports_shutdown_supervisor():
    """The launcher must compose the supervisor (no shadow teardown)."""
    module = _launcher_module()
    src = open(module.__file__, encoding="utf-8").read()
    assert "ShutdownSupervisor" in src
    assert "wait_for_shutdown" in src
    # The lying panel is gone: no unconditional "terminated cleanly".
    assert "Nexus Scalp Engine terminated cleanly." not in src


def test_engine_shutdown_async_is_idempotent_guard():
    """LiveEngine._shutdown_async must collapse repeated calls (RuntimeLoop
    calls it on loop exit AND the supervisor calls it as the fallback)."""
    import inspect

    from nexus_scalp.application import live_engine

    src = inspect.getsource(live_engine.LiveEngine._shutdown_async)
    assert "_shutdown_completed" in src


def test_cli_engine_boot_composes_supervisor():
    """`nexus start` uses the same supervisor and the same honest report."""
    import inspect

    from nexus_scalp.cli import engine_boot

    src = inspect.getsource(engine_boot._start_web_and_engine)
    assert "ShutdownSupervisor" in src
    assert "wait_for_shutdown" in src
    assert "KeyboardInterrupt" in src  # the cancelled-gather drain is wired


# ---------------------------------------------------------------------------
# 5. The whole in-process flow: gather + supervisor + teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_cancelled_gather_still_drains():
    """The exact pre-fix failure: cancelling the gather must NOT skip teardown.

    This mirrors the launcher's real shape — the drain runs in the gather's
    ``finally`` block, which is exactly why the supervisor has to exist
    rather than relying on RuntimeLoop's own fall-through.
    """
    engine = _FakeEngine()
    server = _FakeServer()
    sup = ShutdownSupervisor(engine=engine, server=server)

    async def run_concurrently() -> None:
        try:
            await asyncio.gather(_never_returns(), engine_loop(), return_exceptions=False)
        finally:
            await sup.wait_for_shutdown()

    async def _never_returns() -> None:
        await asyncio.sleep(3600)

    async def engine_loop() -> None:
        # emulate RuntimeLoop: exits when _running flips, then tears down
        while engine._running:
            await asyncio.sleep(0.01)
        await engine._shutdown_async()

    task = asyncio.create_task(run_concurrently())
    await asyncio.sleep(0.05)
    # Ctrl+C arrives. The Runner cancels the main task; the supervisor's
    # drain in the `finally` still completes before the task unwinds.
    sup.request_shutdown("ctrl_c")
    engine._running = False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    status = sup.status()
    assert status["phase"] == PHASE_CLOSED, f"teardown skipped: {status}"
    assert engine.shutdown_completed is True, "engine teardown never ran"
    assert server.shutdown_calls == 1, "web server never shut down"


@pytest.mark.asyncio
async def test_drain_after_hard_cancel_completes_on_a_second_call():
    """If the gather was cancelled BEFORE the finally drain ran, the next
    wait_for_shutdown() still completes the teardown — the supervisor never
    reports CLOSED on an un-drained engine."""
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)

    # A first wait got cancelled mid-drain (supervisor state recorded
    # DRAIN but never CLOSED).
    sup.request_shutdown("ctrl_c")
    assert sup.status()["phase"] == PHASE_DRAIN

    status = await sup.wait_for_shutdown()
    assert status["phase"] == PHASE_CLOSED
    assert engine.shutdown_completed is True


@pytest.mark.asyncio
async def test_request_side_also_stops_the_engine_loop():
    """The signal path must not just RECORD a request — it must end the loop.

    Live-log finding 2026-09-22: repeated ``CTRL_SHUTDOWN REQUESTED`` lines
    with no ``COMPLETE``. RuntimeLoop polls ``engine._running``; if the
    request side never flips it, the loop keeps ticking forever and the
    gather's ``finally`` drain never runs.
    """
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    # simulate the signal handler entry point only
    sup.install_signal_handlers()
    sup.request_shutdown("console_close")
    assert engine._running is False, "the loop's stop flag must flip on request"


def test_signal_handler_flips_the_engine_flag():
    """The installed signal callable itself ends the loop (not just the API)."""
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    sup.install_signal_handlers()
    handler = signal.getsignal(signal.SIGBREAK)
    assert engine._running is True
    if callable(handler):
        handler(signal.SIGBREAK, None)  # type: ignore[arg-type]
    assert engine._running is False


@pytest.mark.asyncio
async def test_status_reports_not_run_when_teardown_never_happened():
    """An honest supervisor never claims a close that did not occur."""
    engine = _FakeEngine()
    sup = ShutdownSupervisor(engine=engine)
    status = sup.status()
    assert status["phase"] == PHASE_DRAIN
    assert status["outcome"] == ""
    # engine-level surface mirrors this honesty
    assert engine.shutdown_status()["completed"] is False
