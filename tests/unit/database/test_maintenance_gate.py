from types import SimpleNamespace

import pytest

from nexus_scalp.database.maintenance_gate import (
    engine_is_running,
    engine_start_guard,
    maintenance_active,
    migration_guard,
)


def _app(running: bool = False) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(_running=running)))


def test_migration_guard_rejects_active_engine() -> None:
    app = _app(running=True)

    with pytest.raises(RuntimeError, match="DB_ENGINE_MUST_BE_STOPPED"):
        with migration_guard(app):
            pass

    assert not maintenance_active(app)


def test_engine_start_guard_blocks_start_during_migration() -> None:
    app = _app(running=False)

    with migration_guard(app):
        assert maintenance_active(app)
        with pytest.raises(RuntimeError, match="DB_MAINTENANCE_IN_PROGRESS"):
            with engine_start_guard(app):
                pass

    assert not maintenance_active(app)


def test_migration_guard_serializes_engine_start_and_clears_state() -> None:
    app = _app(running=False)

    with migration_guard(app):
        assert not engine_is_running(app)
        assert maintenance_active(app)

    assert not maintenance_active(app)
    with engine_start_guard(app):
        app.state.engine._running = True

    assert engine_is_running(app)
