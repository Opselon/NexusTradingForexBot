"""Named, failure-evident asyncio tasks (OBS-011).

The 2026-08-31 audit measured the failure mode: spawned asyncio tasks carry no
task id and no parent correlation, so a task that dies surfaces only as the
bare stdlib line ``Task exception was never retrieved`` — no component, no
operation, no cause, and no link to the request or boot that spawned it.

This module gives every ``create_task`` site ONE call that:

1. **Names the task** so `asyncio.Task.get_name()` identifies the component and
   operation in a traceback or a `Task` repr (this is what the stdlib "Task
   exception was never retrieved" message prints).
2. **Retrieves the exception** and logs it as a structured WARNING/ERROR with
   the task name, so the death is attributable WITHOUT relying on the garbage
   collector to print it, and without the caller having to remember to await
   the task.
3. **Never swallows CancelledError** — cancellation is cooperative control
   flow, not a failure, and must propagate untouched.

WHY A DONE-CALLBACK AND NOT A WRAPPER CORO
------------------------------------------
A wrapper coroutine must be awaited to observe the exception, which defeats the
purpose for fire-and-forget tasks. ``Task.add_done_callback`` runs when the
task finishes regardless of who is waiting, and ``task.exception()`` on a
finished task never raises. Retrieving the exception in the callback ALSO marks
it retrieved, so the interpreter's "was never retrieved" warning stays quiet —
the operator sees OUR structured line instead, which carries strictly more
information.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from typing import Any, TypeVar

import structlog

logger = structlog.get_logger("nexus_scalp.observability.task_spawner")

_T = TypeVar("_T")

#: Prefix so a named task is recognisable in a traceback orrepr at a glance.
_TASK_NAME_PREFIX = "nse."


def spawn_named_task(
    coro: Awaitable[_T],
    *,
    name: str,
    component: str | None = None,
    parent_id: str | None = None,
) -> asyncio.Task[_T]:
    """Create an asyncio.Task that is attributable when it fails (OBS-011).

    Args:
        coro: The coroutine to run.
        name: Short operation identity, e.g. ``"engine.run_loop"`` or
            ``"worker.accounting"``. Becomes the asyncio task name.
        component: Optional subsystem for the structured log record.
        parent_id: Optional correlation id (request_id / boot_id / execution
            id) of the spawner, so the task's failure can be joined back to
            the operation that started it.

    Returns:
        The created task. Callers may still ``await`` it; the done-callback
        only observes and logs, it never consumes the result.
    """
    task_name = f"{_TASK_NAME_PREFIX}{name}"
    task = asyncio.create_task(_run_named(coro, task_name), name=task_name)

    def _on_done(done: asyncio.Task[_T]) -> None:
        if done.cancelled():
            # Cancellation is control flow, not a failure. Do not log it as
            # one — a noisy "task failed" on every graceful stop would train
            # operators to ignore the line.
            return
        exc: BaseException | None = None
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover - defensive
            return
        if exc is None:
            return
        # Retrieving the exception marks it retrieved: the interpreter will
        # not emit its own unstructured "was never retrieved" line, so the
        # operator sees THIS record instead — which carries the component,
        # the operation and the parent correlation.
        context: dict[str, Any] = {
            "task_name": task_name,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
        if component:
            context["component"] = component
        if parent_id:
            context["parent_id"] = parent_id
        logger.warning(
            "[TASK_FAILED] a background task raised and was retrieved; "
            "the engine is unaffected unless this component owns state",
            **context,
        )

    task.add_done_callback(_on_done)
    return task


async def _run_named(coro: Awaitable[_T], task_name: str) -> _T:
    """Run the payload, tagging any exception with the task name.

    The done-callback cannot annotate the exception object, so the name is
    attached here — it then appears in the traceback the callback logs.
    """
    try:
        return await coro  # type: ignore[arg-type]
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        # Stamp the identity onto the exception so the structured record and
        # any downstream traceback both name the operation.
        with contextlib.suppress(AttributeError, TypeError):
            setattr(exc, "nse_task_name", task_name)  # noqa: B010
        raise


def current_task_name() -> str | None:
    """Return the asyncio task name of the running task (OBS-011).

    Exists so call sites and tests can assert attribution without reaching
    into private asyncio internals.
    """
    task = asyncio.current_task()
    if task is None:
        return None
    try:
        return task.get_name()
    except Exception:  # pragma: no cover - defensive
        return None


def install_named_task_exception_handler(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Log unhandled loop exceptions as one structured record (OBS-011).

    ``loop.call_exception_handler`` is where asyncio dumps everything it
    cannot attribute: exceptions raised in callbacks, in ``__del__`` driven
    "Task exception was never retrieved" reports, and in transport callbacks.
    By default these are printed to stderr as free text, which is invisible to
    the log pipeline and unsamplable. Routing them through structlog keeps
    them structured, redacted, and queryable.

    The default handler is NOT chained: chaining would print every record a
    second time as unstructured stderr text, which is the behaviour being
    replaced.
    """

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        fields: dict[str, Any] = {
            "loop_message": str(context.get("message", "unhandled loop exception"))[:500],
        }
        if exc is not None:
            fields["error_type"] = type(exc).__name__
            fields["error"] = str(exc)[:500]
            task_name = getattr(exc, "nse_task_name", None)
            if task_name:
                fields["task_name"] = task_name
        task = context.get("task")
        if task is not None:
            fields.setdefault("task_name", getattr(task, "get_name", lambda: None)())
        future = context.get("future")
        if future is not None and "task_name" not in fields:
            fields["future_repr"] = repr(future)[:200]
        logger.error(
            "[LOOP_EXCEPTION] unhandled asyncio loop error; this is a defect "
            "path, not a normal shutdown",
            **fields,
        )

    loop.set_exception_handler(_handler)


__all__ = [
    "current_task_name",
    "install_named_task_exception_handler",
    "spawn_named_task",
]
