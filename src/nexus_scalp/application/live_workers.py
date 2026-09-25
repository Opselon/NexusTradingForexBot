"""
LiveWorkerSupervisor — extracted Cluster 2 (Background Worker Lifecycle).

Single owner for background worker lifecycle, idempotent start/stop,
safe error-isolated kicking, and shutdown orchestration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live_workers")


def _last_cycle_hint(worker_fn: Callable[[], Any]) -> str:
    """LD-4: best-effort last-known cycle duration of a timed-out worker.

    Kicked worker fns are bound ``tick`` methods, so the worker object is
    recovered from ``__self__`` — no new plumbing, and nothing here runs on the
    happy path. Every access is defensive: this is reached from the kick path
    and must never raise (which would mask the timeout it is reporting).

    Returns a ``last_cycle_duration_sec=<s>`` fragment when the worker exposes
    a duration, else ``last_cycle_duration=unknown`` — the point is that an
    operator can tell a slow-but-healthy cycle (duration near the budget) from
    a genuinely wedged call (duration far below the budget).
    """
    try:
        worker = getattr(worker_fn, "__self__", None)
        raw = getattr(worker, "last_cycle_duration", None)
        if raw is None:
            # IncidentWorker exposes milliseconds instead.
            raw_ms = getattr(worker, "cycle_duration_ms", None)
            if raw_ms is None:
                return "last_cycle_duration=unknown"
            raw = float(raw_ms) / 1000.0
        dur = float(raw)
    except Exception:
        return "last_cycle_duration=unknown"
    return f"last_cycle_duration_sec={dur:.1f}" if dur > 0.0 else "last_cycle_duration=unknown"


class WorkerSupervisor:
    """Manages start, stop, and kicking of background pipeline workers."""

    @staticmethod
    def start_worker(
        name: str,
        worker: Any,
        is_started: bool,
        set_started: Callable[[bool], None],
    ) -> bool:
        """Idempotently starts a synchronous worker with exception isolation."""
        if is_started:
            return True
        set_started(True)
        try:
            if hasattr(worker, "start"):
                worker.start()
            return True
        except Exception as err:
            logger.error(f"[{name.upper()}] event=START status=FAILED", error=str(err))
            set_started(False)
            return False

    @staticmethod
    async def stop_worker(
        name: str,
        worker: Any,
        set_started: Callable[[bool], None],
    ) -> None:
        """Idempotently stops a synchronous worker with exception isolation."""
        set_started(False)
        try:
            if hasattr(worker, "stop"):
                worker.stop()
        except Exception as err:
            logger.error(f"[{name.upper()}] event=STOP status=FAILED", error=str(err))

    @staticmethod
    def kick_worker(
        name: str,
        worker_fn: Callable[[], Any],
        inflight_workers: set[str],
        background_tasks: set[asyncio.Task[Any]],
        timeout_sec: float = 120.0,
    ) -> None:
        """Dispatches a worker synchronously via asyncio.to_thread with timeout and backpressure."""
        if name in inflight_workers:
            return
        inflight_workers.add(name)

        async def _run() -> None:
            try:
                await asyncio.wait_for(asyncio.to_thread(worker_fn), timeout=timeout_sec)
            except TimeoutError:
                logger.error(
                    "[WORKER_KICK] event=TIMEOUT worker=%s timeout_sec=%s %s — detaching hung call",
                    name,
                    timeout_sec,
                    _last_cycle_hint(worker_fn),
                )
            except asyncio.CancelledError:
                pass
            except Exception as wkr_err:
                logger.warning("[WORKER_KICK] event=FAILED worker=%s error=%s", name, wkr_err)
            finally:
                inflight_workers.discard(name)

        task = asyncio.create_task(_run())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
