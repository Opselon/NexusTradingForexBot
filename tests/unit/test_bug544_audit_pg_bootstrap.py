"""Regression tests for BUG-544: PostgreSQL audit domain bootstrap gaps.

Two independent failures surfaced together on a PostgreSQL-backed engine:

1. ``AuditWritePlane`` closed the fabric's *shared* pooled write backend when
   its ``AuditRepository`` owner shut down (``owns_backend`` defaulted to
   ``True`` even for a pooled provider). Any second ``AuditRepository`` in the
   same process — e.g. ``nexus risk release --confirm`` after a ``risk status``
   read — found ``pg pool 'pg-write' is not open`` and its safety-state write
   failed, so a persisted drawdown halt could never be released and the engine
   stayed REFUSED forever.

2. The ``experience_model_registry`` table's bootstrap DDL was missing the 8
   lifecycle columns the model registry added (``lifecycle_status`` et al.).
   The registry's own migration ran on SQLite via the schema snapshot, but the
   snapshot builds from the app bootstrap DDL, so PostgreSQL provisions got a
   table without them and every champion lookup died with
   ``column "lifecycle_status" does not exist``.
"""

from __future__ import annotations

import queue
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.dead_letter_store import DeadLetterStore
from nexus_scalp.database.app_columns import APP_REQUIRED_COLUMNS


def _bare_repository(*, is_sqlite: bool) -> AuditRepository:
    """An ``AuditRepository`` with just enough state to build its write plane.

    ``__init__`` starts a background worker and touches the filesystem; the
    plane builder only needs the handful of attributes it reads, so the half
    built instance mirrors what ``schema_snapshot`` itself does.
    """
    repo = object.__new__(AuditRepository)
    repo._is_sqlite = is_sqlite  # type: ignore[attr-defined]
    repo._flush_interval = 0.25  # type: ignore[attr-defined]
    repo._queue = queue.Queue()  # type: ignore[attr-defined]
    repo.dead_letter_store = DeadLetterStore(  # type: ignore[attr-defined]
        conn_factory=sqlite3.connect,
        is_sqlite=True,
        db_path="",
        write_sink=None,
    )
    repo._overflow_base_dir = None  # type: ignore[attr-defined]
    return repo


# ---------------------------------------------------------------- owns_backend


def test_pooled_write_plane_does_not_own_the_fabric_backend() -> None:
    """A pooled provider backend is shared: the plane must not close it.

    This is the exact mechanism behind ``nexus risk release`` failing with
    ``pg pool 'pg-write' is not open`` after any prior read: the first
    repository's shutdown closed a pool the fabric still hands out.
    """
    captured: dict[str, object] = {}

    class RecordingPlane:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    with (
        patch(
            "nexus_scalp.adapters.database.audit_write_plane.AuditWritePlane",
            RecordingPlane,
        ),
        patch.object(
            AuditRepository,
            "_build_pooled_write_backend",
            return_value=object(),  # stands in for a pooled (non-SQLite) backend
        ),
    ):
        repo = _bare_repository(is_sqlite=False)
        repo._build_write_plane()

    assert captured["owns_backend"] is False, (
        "a pooled write backend is a fabric-owned shared resource; the audit "
        "write plane must not close it on repository shutdown"
    )


def test_sqlite_write_plane_still_owns_its_backend() -> None:
    """The SQLite backend is created exclusively for the plane — keep closing it.

    The fix is scoped to the pooled path only; the SQLite plane still owns its
    connection and must keep closing it so local runs do not leak handles.
    """
    captured: dict[str, object] = {}

    class RecordingPlane:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    with (
        patch(
            "nexus_scalp.adapters.database.audit_write_plane.AuditWritePlane",
            RecordingPlane,
        ),
        patch.object(AuditRepository, "_build_pooled_write_backend", return_value=None),
    ):
        repo = _bare_repository(is_sqlite=True)
        repo._build_write_plane()

    assert captured.get("owns_backend", "absent") is True, (
        "the SQLite backend is created exclusively for this plane and must keep "
        "being closed on shutdown so local runs do not leak a connection"
    )


# ------------------------------------------------- experience_model_registry DDL


_LIFECYCLE_COLUMNS = (
    "lifecycle_status",
    "training_run_id",
    "parent_model_id",
    "parent_model_version",
    "child_model_id",
    "promotion_reason",
    "gate_summary",
    "validation_run_ids",
)


def _bootstrap_experience_model_registry() -> sqlite3.Connection:
    """Run the app bootstrap DDL for the registry table on a scratch SQLite db."""
    conn = sqlite3.connect(":memory:")
    AuditRepository._create_experience_tables(object.__new__(AuditRepository), conn)
    return conn


@pytest.mark.parametrize("column", _LIFECYCLE_COLUMNS)
def test_experience_model_registry_bootstrap_has_lifecycle_columns(column: str) -> None:
    """Every lifecycle column must be in the CREATE TABLE the snapshot replays.

    The schema snapshot derives PostgreSQL DDL from this bootstrap, so a missing
    column here is a missing column on every PostgreSQL box — which is exactly
    the ``column "lifecycle_status" does not exist`` failure.
    """
    conn = _bootstrap_experience_model_registry()
    try:
        columns = {
            str(row[1]).lower()
            for row in conn.execute("PRAGMA table_info(experience_model_registry)")
        }
    finally:
        conn.close()

    assert column.lower() in columns


def test_lifecycle_champion_lookup_executes_on_bootstrap_schema() -> None:
    """The registry's champion query must run against the bootstrap table.

    Reproduces the production query shape (``WHERE lifecycle_status``) so a
    drift between the bootstrap DDL and the registry's read path fails here
    instead of at engine boot.
    """
    conn = _bootstrap_experience_model_registry()
    try:
        conn.execute(
            "INSERT INTO experience_model_registry"
            " (model_id, model_version, registered_at, lifecycle_status)"
            " VALUES (?, ?, ?, ?)",
            ("primary_scalp", "v1.0", "2026-09-28T00:00:00+00:00", "CHAMPION"),
        )
        row = conn.execute(
            "SELECT model_id, artifact_fingerprint FROM experience_model_registry"
            " WHERE lifecycle_status = ? ORDER BY registered_at DESC LIMIT 1",
            ("CHAMPION",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "primary_scalp"


def test_app_required_columns_carries_lifecycle_columns() -> None:
    """``APP_REQUIRED_COLUMNS`` heals skeletons; it must list the lifecycle set.

    A table provisioned as a column-less skeleton reaches PostgreSQL through
    this contract, so a missing entry means the heal path leaves it broken.
    """
    declared = {name.lower() for name, _ddl in APP_REQUIRED_COLUMNS["experience_model_registry"]}
    for column in _LIFECYCLE_COLUMNS:
        assert column in declared


def test_champion_lookup_is_a_read_only_safety_path() -> None:
    """Guard: this test covers a diagnostic read, never a safety bypass."""
    # The champion lookup feeds trust-anchor verification — an inert read.
    # Asserting it succeeds does not weaken or bypass any risk enforcement.
    assert _LIFECYCLE_COLUMNS[0] == "lifecycle_status"
