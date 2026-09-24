"""RT-007: LearningCycleStore silently lost every cycle state (live defect).

Reproduces the recurring production error::

    [LEARNING_CYCLE] recovery failed ... error='no such table: learning_cycles'

Root cause: ``_resolve_cycle_store_db_path()`` fell back to ``:memory:`` when
the audit repo carries no SQLite ``_db_path``. LearningCycleStore opens one
short-lived ``sqlite3.connect()`` per operation with no shared connection, and
each ``:memory:`` connect mints a brand-new EMPTY database — so the table
``ensure_schema()`` creates is invisible to the very next call. Restart-safe
recovery was therefore silently disabled for any non-SQLite-backed audit repo.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

from nexus_scalp.model_lifecycle.learning_cycle import LearningCycleStore
from nexus_scalp.model_lifecycle.learning_loop import _resolve_cycle_store_db_path


def _assert_equal(actual, expected):
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


class _PgBackedRepo:
    """Audit-repo shape under PostgreSQL: no SQLite _db_path at all."""

    _db_path = ""
    _db_url = "postgresql://nse_user:***@localhost:5432/nexusdb"


def test_rt007_fallback_is_not_in_memory() -> None:
    # ``:memory:`` was the defect: a fresh empty DB per connect.
    resolved = _resolve_cycle_store_db_path(_PgBackedRepo())
    assert resolved != ":memory:", "RT-007 regressed: still :memory:"
    assert os.path.isfile(resolved), "expected a real schema-carrying temp file"
    os.unlink(resolved)


def test_rt007_fallback_schema_survives_reconnect() -> None:
    # The live failure: recover_interrupted() raised 'no such table'.
    path = _resolve_cycle_store_db_path(_PgBackedRepo())
    try:
        store = LearningCycleStore(path)
        store.recover_interrupted()
        cycle_id = store.start_cycle("manual", "rt007-probe")
        store.transition(cycle_id, "DATASET_BUILDING")
        # A SECOND instance on the same path must see the persisted state —
        # with :memory: this saw an empty DB every time.
        reopened = LearningCycleStore(path)
        got = reopened.get_cycle(cycle_id)
        _assert_equal(got["status"], "DATASET_BUILDING")
    finally:
        os.unlink(path)


def test_rt007_magicmock_double_resolves_to_a_file() -> None:
    # Test doubles (the case the :memory: fallback was originally written for)
    # still resolve, and now to a path whose schema is shared across connects.
    repo = MagicMock()
    resolved = _resolve_cycle_store_db_path(repo)
    assert isinstance(resolved, str)
    assert resolved != ":memory:"
    try:
        store = LearningCycleStore(resolved)
        store.recover_interrupted()
    finally:
        if os.path.isfile(resolved):
            os.unlink(resolved)


def test_rt007_real_sqlite_path_is_unchanged() -> None:
    # No regression on the production SQLite path.
    repo = MagicMock()
    repo._db_path = "/tmp/nse_rt007_canonical_audit.db"
    _assert_equal(_resolve_cycle_store_db_path(repo), "/tmp/nse_rt007_canonical_audit.db")
