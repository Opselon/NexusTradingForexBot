"""ShutdownSupervisor — one owner of process-wide warm teardown.

WHY THIS EXISTS (BUG-304, operator-reported 2026-09-21): closing the
console / window / Ctrl+C / taskkill left the engine "not completely
closed". Three independent causes, all fixed here:

1. ``asyncio.run(gather(server.serve(), engine.run_loop()))`` — the ONLY
   signal-aware object in the process is asyncio's Runner, and it
   installs its SIGINT handler *only when the main thread's handler is
   the default*. On Windows the console's Ctrl+C is delivered as SIGINT;
   Runner._on_sigint cancels the MAIN TASK, which cancels the gather and
   unwinds straight out of ``asyncio.run`` — the engine's
   ``_shutdown_async`` (adapter disconnect, worker stops, audit flush,
   shutdown notification) is NEVER called. The process exits with the
   SQLite WAL file, the broker session and pending audit rows still open.
   Closing the console window is worse: Windows sends no Python-visible
   signal at all, so the process is force-killed mid-write.

2. ``except KeyboardInterrupt`` in both launchers swallowed the
   cancellation and printed a "terminated cleanly" panel — the panel lied
   about a teardown that never ran.

3. Nothing bounded the teardown. ``AuditRepository.close`` waits
   ``_queue.join()`` with no timeout, so a stuck insert could hang exit
   forever and the operator would have to force-kill — reproducing cause 1.

CONTRACT (unchanged public surface):
  * ``ShutdownSupervisor(engine, server=None)`` composes the engine's
    EXISTING ``_shutdown_async`` — it never duplicates teardown logic and
    never keeps shadow mutable state (facade rule).
  * ``request_shutdown(reason)`` is sync, idempotent, signal-handler safe
    (raises only ValueError on a bad phase, never blocks) and is safe to
    call from a non-main thread or a Windows console-control handler.
  * ``wait_for_shutdown(timeout)`` performs ONE bounded teardown on the
    running loop; repeat calls return the recorded outcome.
  * ``install_signal_handlers(loop)`` registers SIGINT/SIGTERM on POSIX.
    On Windows it registers the console CTRL-handler (SIGBREAK too),
    because CPython's Runner-installed SIGINT handler is not reached when
    the window is closed and ``signal.SIGTERM`` is not deliverable.

BOUNDARY: this module owns ONLY the request/drain orchestration. The
teardown steps themselves stay owned by LiveEngine._shutdown_async,
uvicorn.Server.shutdown and AuditRepository.close — composed, never
reimplemented.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
import time
from typing import TYPE_CHECKING, Any

from nexus_scalp.observability.logging import get_logger

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

logger = get_logger("nexus_scalp.application.shutdown")

# Phases are monotonic: DRAIN -> CLOSED. A CLOSED supervisor answers every
# later request with the recorded outcome instead of re-running teardown.
PHASE_DRAIN = "DRAIN"
PHASE_CLOSED = "CLOSED"


def _make_signal_handler(schedule: Any, reason: str) -> Any:
    """Build a signal-handler callable for one named signal (mypy-clean)."""

    def _handler(signum: int, frame: Any) -> None:
        schedule(reason)

    return _handler


class ShutdownSupervisor:
    """Idempotent, bounded, signal-safe warm-shutdown coordinator.

    Composes the engine's existing ``_shutdown_async`` plus uvicorn's
    ``shutdown``; the request side is callable from signal handlers and the
    console-control thread, the drain side runs exactly once.
    """

    #: Overall budget for the whole teardown. Generous but finite: the
    #: operator must never have to reach for taskkill /F because a
    #: teardown step parked forever (BUG-304 cause 3).
    DEFAULT_TIMEOUT_SEC: float = 25.0

    def __init__(self, engine: Any, server: Any = None) -> None:
        self._engine = engine
        self._server = server
        self._phase = PHASE_DRAIN
        self._reason = ""
        self._requested_at: float | None = None
        self._completed_at: float | None = None
        self._outcome = ""
        self._error = ""
        self._lock = threading.Lock()
        self._drain_started = threading.Event()
        self._drain_done = threading.Event()
        self._handlers_installed = False

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def requested_at(self) -> float | None:
        return self._requested_at

    def status(self) -> dict[str, Any]:
        """Operator-visible shutdown state (honest at every instant)."""
        return {
            "phase": self._phase,
            "reason": self._reason,
            "requested_at": self._requested_at,
            "completed_at": self._completed_at,
            "outcome": self._outcome,
            "error": self._error,
            "duration_sec": round(self._completed_at - (self._requested_at or 0.0), 3)
            if self._completed_at and self._requested_at
            else None,
        }

    # ------------------------------------------------------------------
    # request side (signal-handler safe: never blocks, never raises)
    # ------------------------------------------------------------------

    def request_shutdown(self, reason: str = "operator_request") -> bool:
        """Record a warm-shutdown request. Idempotent, non-blocking.

        Returns True when this call is the FIRST request, False on every
        subsequent one. Safe from signal handlers and the Windows
        console-control thread: it only flips state and pokes the loop.

        This is also the single point that ends the tick loop:
        RuntimeLoop polls ``engine._running``, so flipping it here is what
        lets the while-loop exit and fall through to the engine's own
        teardown. Before BUG-304 the request side only recorded state and
        the loop never stopped (live log 2026-09-22: repeated
        CTRL_SHUTDOWN REQUESTED lines, no COMPLETE).
        """
        with self._lock:
            first = self._requested_at is None
            if first:
                self._requested_at = time.time()
                self._reason = reason
                logger.info("[SHUTDOWN] event=REQUESTED reason=%s phase=DRAIN", reason)
        # End the loop on EVERY request path (signal, API, KeyboardInterrupt):
        # a repeat request from a second Ctrl+C must not find the loop still
        # ticking because the first came from a path that forgot to flip it.
        with contextlib.suppress(Exception):
            self._engine._running = False
        return first

    def mark_closed(self, *, outcome: str, error: str = "") -> None:
        """Record the final teardown outcome. Called once by the drain."""
        with self._lock:
            if self._phase == PHASE_CLOSED:
                return
            self._phase = PHASE_CLOSED
            self._completed_at = time.time()
            self._outcome = outcome
            self._error = error

    # ------------------------------------------------------------------
    # drain side
    # ------------------------------------------------------------------

    async def wait_for_shutdown(self, timeout: float | None = None) -> dict[str, Any]:
        """Run the warm teardown ONCE, bounded. Returns the status dict.

        Second and later calls wait for the in-flight drain (or return the
        recorded outcome), so concurrent requesters converge on one result.

        The wait is async-yielding: a blocking primitive here would park the
        event loop thread and defeat the very bound this method guarantees.
        """
        if self._drain_done.is_set():
            return self.status()
        budget = self.DEFAULT_TIMEOUT_SEC if timeout is None else float(timeout)
        if self._drain_started.is_set():
            # Another caller started the drain; yield to the loop while it
            # finishes instead of blocking the loop thread.
            deadline = time.monotonic() + budget
            while not self._drain_done.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            return self.status()
        self._drain_started.set()
        started = time.time()
        outcome = "OK"
        error_text = ""
        try:
            # 1. Stop accepting ticks / new decisions FIRST: the engine's
            #    own flag ends the loop; RuntimeLoop then falls through to
            #    _shutdown_async. Never cancel the task — a hard cancel
            #    would reproduce the original bug (teardown skipped).
            with contextlib.suppress(Exception):
                self._engine._running = False

            # 2. uvicorn: stop accepting connections and let in-flight
            #    requests finish. Bounded so a hung request cannot hold
            #    the process open (BUG-304 cause 3). A failure here is
            #    recorded but never fatal — the engine teardown still runs.
            if self._server is not None:
                try:
                    await asyncio.wait_for(self._server.shutdown(), timeout=min(10.0, budget))
                except TimeoutError:
                    outcome = "TIMEOUT"
                    error_text = "uvicorn.shutdown exceeded its budget"
                except Exception as server_err:
                    outcome = "ERROR"
                    error_text = f"uvicorn.shutdown: {type(server_err).__name__}: {server_err}"

            # 3. Engine teardown (adapter disconnect, worker stops, audit
            #    flush, shutdown notification). Composed — never reimplemented.
            #    A failure is RECORDED, not swallowed: the operator's final
            #    report must not read OK when the teardown raised.
            remaining = budget - (time.time() - started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(self._engine._shutdown_async(), timeout=remaining)
                except TimeoutError:
                    outcome = "TIMEOUT"
                    error_text = "engine._shutdown_async exceeded its budget"
                except Exception as engine_err:
                    outcome = "ERROR"
                    error_text = (
                        f"engine._shutdown_async: {type(engine_err).__name__}: {engine_err}"
                    )
                else:
                    # RuntimeLoop schedules _shutdown_async when its
                    # while-loop exits; give that hand-off a bounded moment
                    # to land when this drain did not come from it.
                    remaining = budget - (time.time() - started)
                    if remaining > 0:
                        with contextlib.suppress(Exception):
                            await asyncio.sleep(min(0.25, remaining))
        except Exception as exc:  # pragma: no cover - defensive
            outcome = "ERROR"
            error_text = f"{type(exc).__name__}: {exc}"
            logger.error("[SHUTDOWN] event=DRAIN_FAILED error=%s", error_text)

        elapsed = time.time() - started
        if elapsed > budget:
            outcome = "TIMEOUT"
        self.mark_closed(outcome=outcome, error=error_text)
        logger.info(
            "[SHUTDOWN] event=COMPLETE outcome=%s duration_sec=%.3f budget_sec=%.3f",
            outcome,
            elapsed,
            budget,
        )
        return self.status()

    # ------------------------------------------------------------------
    # signals
    # ------------------------------------------------------------------

    def install_signal_handlers(self, loop: AbstractEventLoop | None = None) -> None:
        """Register the warm-shutdown handlers.

        POSIX: SIGINT/SIGTERM schedule the request on the loop.
        Windows: same for SIGINT/SIGBREAK plus the console CTRL-handler so
        closing the console window or Ctrl+Break still triggers a warm
        teardown (Windows sends Python no signal on window close).
        """
        if self._handlers_installed:
            return
        self._handlers_installed = True
        _loop = loop

        def _schedule(reason: str) -> None:
            self.request_shutdown(reason)
            # The loop polls engine._running; flipping it here is what makes
            # the while-loop exit so RuntimeLoop falls through to its own
            # _shutdown_async and the gather's `finally` drain runs. Without
            # this the request was recorded but nothing ever stopped the
            # loop (observed in the 2026-09-22 live log: repeated
            # CTRL_SHUTDOWN REQUESTED lines, no COMPLETE).
            with contextlib.suppress(Exception):
                self._engine._running = False
            if _loop is not None and _loop.is_running():
                with contextlib.suppress(RuntimeError):
                    _loop.call_soon_threadsafe(self._async_kick)

        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _make_signal_handler(_schedule, sig_name))
            except (ValueError, OSError, RuntimeError):
                # signal.signal raises ValueError off the main thread; the
                # CTRL-handler below still covers console-close on Windows.
                pass

        if threading.current_thread() is threading.main_thread():
            self._install_console_ctrl_handler(_schedule)

    def _install_console_ctrl_handler(
        self, schedule: Any, ctrl_kind: str = "CTRL_SHUTDOWN"
    ) -> None:
        """Windows-only console-control hook (window close / logoff / Ctrl+Break).

        Uses the stdlib ctypes binding, no new dependency. When the
        console is being closed there is no Python-visible signal, so this
        handler is the ONLY warm-teardown trigger available.
        """
        try:
            import ctypes
        except ImportError:  # pragma: no cover
            return

        # WINFUNCTYPE / windll exist only on Windows; on Linux/macOS the
        # console-close hook has no equivalent, so the whole registration is
        # a no-op there. Guarding here also keeps mypy green on the CI runner
        # (ctypes has no windll attribute off Windows).
        if not hasattr(ctypes, "windll") or not hasattr(ctypes, "WINFUNCTYPE"):
            return

        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

        def _handler(ctrl: int) -> int:
            try:
                schedule(ctrl_kind)
                # Give the drain a bounded window before Windows kills us.
                self._drain_done.wait(timeout=self.DEFAULT_TIMEOUT_SEC)
            except BaseException:
                pass
            return False  # let the default handler proceed after the drain

        try:
            self._ctrl_handler = handler_type(_handler)  # keep the ref alive
            ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl_handler, True)
        except Exception:  # pragma: no cover - non-Windows / no console
            pass

    async def _async_kick(self) -> None:
        """Loop-side wakeup so a signal from another thread is not missed."""
        return


__all__ = ["PHASE_CLOSED", "PHASE_DRAIN", "ShutdownSupervisor"]
