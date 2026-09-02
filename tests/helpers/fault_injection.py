"""TASK-QA-DEEP-ASSURANCE / CHG-0045: deterministic fault-injection helper.

Application-level chaos: inject failures at ONE boundary at a time and
verify the system classifies the failure, keeps state truthful, and never
corrupts unrelated subsystems. Fully deterministic (explicit trigger counts,
no random destruction). Offline by construction.

FaultPoint: a callable wrapper that raises/returns a injected failure for
the first N calls (or until armed-off), then passes through. Used by the
adversarial batteries to prove failure paths without monkeypatching
production modules ad hoc in every test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FaultPoint:
    """Deterministic boundary fault injector.

    behavior: 'raise' -> raise the given exception instance
              'return' -> return the given (failure-shaped) value
    after `fail_times` calls, calls pass through untouched.
    """

    def __init__(
        self,
        target: Callable[..., Any],
        *,
        fail_times: int = 1,
        exc: Exception | None = None,
        ret: Any = None,
    ) -> None:
        self._target = target
        self._remaining = fail_times
        self._exc = exc
        self._ret = ret
        self.calls = 0
        self.failures = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            self.failures += 1
            if self._exc is not None:
                raise self._exc
            return self._ret
        return self._target(*args, **kwargs)

    @property
    def exhausted(self) -> bool:
        return self._remaining <= 0
