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
                    "[WORKER_KICK] event=TIMEOUT worker=%s timeout_sec=%s — detaching hung call",
                    name,
                    timeout_sec,
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
