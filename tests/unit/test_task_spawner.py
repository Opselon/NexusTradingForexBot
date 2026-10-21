"""Tests for named asyncio task spawning and failure attribution (OBS-011).

Asserts the contract of nexus_scalp.observability.task_spawner:
- A fire-and-forget task whose coroutine raises logs exactly one structured
  record containing task_name, component, error_type, error, and parent_id
- A task awaited by a caller propagates the original exception unchanged,
  stamped with the task name (accessible via .nse_task_name)
- Normal completion logs nothing
- Cancellation logs nothing
- install_named_task_exception_handler routes loop errors cleanly
- current_task_name() reports the running task's name
"""

import asyncio

import pytest
from structlog.testing import capture_logs

from nexus_scalp.observability.task_spawner import (
    current_task_name,
    install_named_task_exception_handler,
    spawn_named_task,
)


async def test_spawn_named_task_names_the_task():
    captured_name = None

    async def _worker():
        nonlocal captured_name
        captured_name = current_task_name()

    task = spawn_named_task(_worker(), name="test.worker.simple", component="TEST")
    await task
    assert captured_name == "nse.test.worker.simple"
    assert task.get_name() == "nse.test.worker.simple"


async def test_spawn_named_task_silent_on_success():
    async def _happy():
        return 42

    with capture_logs() as cap:
        task = spawn_named_task(_happy(), name="test.happy", component="TEST")
        res = await task
    assert res == 42
    failures = [e for e in cap if "[TASK_FAILED]" in e.get("event", "")]
    assert failures == []


async def test_spawn_named_task_silent_on_cancellation():
    async def _slow():
        await asyncio.sleep(10)

    with capture_logs() as cap:
        task = spawn_named_task(_slow(), name="test.cancelled", component="TEST")
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    failures = [e for e in cap if "[TASK_FAILED]" in e.get("event", "")]
    assert failures == []


async def test_spawn_named_task_logs_failure_for_unawaited_task():
    """Fire-and-forget task failure must log a structured, attributable record."""
    with capture_logs() as cap:

        async def _bad_worker():
            raise RuntimeError("simulated audit disk corruption")

        spawn_named_task(
            _bad_worker(),
            name="worker.audit_flush",
            component="AUDIT_WRITER",
            parent_id="req-tx-999",
        )

        for _ in range(5):
            await asyncio.sleep(0.01)

    failures = [e for e in cap if "[TASK_FAILED]" in e.get("event", "")]
    assert len(failures) == 1
    rec = failures[0]
    assert rec["task_name"] == "nse.worker.audit_flush"
    assert rec["component"] == "AUDIT_WRITER"
    assert rec["error_type"] == "RuntimeError"
    assert "simulated audit disk corruption" in rec["error"]
    assert rec["parent_id"] == "req-tx-999"


async def test_spawn_named_task_awaited_still_raises_original_exception():
    """Awaiting callers must still receive the original exception instance."""

    async def _failing():
        raise ValueError("bad config shape")

    task = spawn_named_task(
        _failing(),
        name="test.awaited_fail",
        component="CONFIG",
    )
    with pytest.raises(ValueError) as excinfo:
        await task

    assert str(excinfo.value) == "bad config shape"
    assert getattr(excinfo.value, "nse_task_name", None) == "nse.test.awaited_fail"


async def test_loop_exception_handler_integration():
    """install_named_task_exception_handler routes loop errors cleanly."""
    loop = asyncio.get_running_loop()
    with capture_logs() as cap:
        install_named_task_exception_handler(loop)

        context = {
            "message": "uncaught callback error",
            "exception": KeyError("missing_field"),
        }
        loop.call_exception_handler(context)

    records = [e for e in cap if "[LOOP_EXCEPTION]" in e.get("event", "")]
    assert len(records) == 1
    rec = records[0]
    assert rec["error_type"] == "KeyError"
    assert "uncaught callback error" in rec["loop_message"]
