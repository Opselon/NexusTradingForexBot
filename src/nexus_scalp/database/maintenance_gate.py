"""Process-local maintenance gate for destructive database transitions.

Database migrations are maintenance operations: the trading engine must be fully
quiesced for their entire duration, and a concurrent engine start must not be
allowed to race the migration. The gate is deliberately small and provider
agnostic; it coordinates the web lifecycle only.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

_MAINTENANCE_LOCK = threading.RLock()


def engine_is_running(app: Any) -> bool:
    engine = getattr(getattr(app, "state", None), "engine", None)
    return bool(engine is not None and getattr(engine, "_running", False))


def maintenance_active(app: Any) -> bool:
    return bool(getattr(getattr(app, "state", None), "db_maintenance_active", False))


@contextmanager
def migration_guard(app: Any) -> Iterator[None]:
    """Serialize DB migration with engine start and mark maintenance active."""
    with _MAINTENANCE_LOCK:
        if engine_is_running(app):
            raise RuntimeError("DB_ENGINE_MUST_BE_STOPPED")
        app.state.db_maintenance_active = True
        try:
            yield
        finally:
            app.state.db_maintenance_active = False


@contextmanager
def engine_start_guard(app: Any) -> Iterator[None]:
    """Serialize engine start against an active DB maintenance operation."""
    with _MAINTENANCE_LOCK:
        if maintenance_active(app):
            raise RuntimeError("DB_MAINTENANCE_IN_PROGRESS")
        yield
