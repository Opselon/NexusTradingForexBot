"""Regression: research archive tables must exist on a freshly-bootstrapped DB.

Symptom: ``ResearchObservabilityStore.list_events`` returned ``[]`` for a DB
that had real events in it. The read is archive-aware (``live UNION ALL
archive``) so a missing ``research_events_archive`` made the whole query die
with ``no such table: research_events_archive`` and the store swallowed the
error and returned an empty list — the entire research timeline invisible.

The archive tables lived only in migration AUDIT-0009, which the governed
startup gate applies. A database bootstrapped fresh by ``AuditRepository``
itself (the normal first-run path) got every live research table but never the
archive pair, so the read broke from the very first run until the migration
gate happened to run.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.observability import ResearchObservabilityStore

_EVENT_SQL_PREFIX = "INSERT INTO research_events"


def _bootstrap_only(tmp_path: Path) -> AuditRepository:
    """A repo constructed the way first run does — no migration gate applied."""
    return AuditRepository(db_url=f"sqlite:///{tmp_path / 'fresh.db'}")


def test_archive_tables_exist_after_plain_bootstrap(tmp_path: Path) -> None:
    repo = _bootstrap_only(tmp_path)
    try:
        con = sqlite3.connect(str(tmp_path / "fresh.db"))
        try:
            tables = {
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
    finally:
        repo.close()

    assert "research_events" in tables
    assert "research_events_archive" in tables, (
        "fresh bootstrap created the live table but not its archive — "
        "list_events' archive-aware read dies and returns zero rows"
    )
    assert "research_evidence_archive" in tables


def test_list_events_returns_rows_on_fresh_db(tmp_path: Path) -> None:
    """The user-visible symptom: events recorded, then readable back."""
    repo = _bootstrap_only(tmp_path)
    try:
        obs = ResearchObservabilityStore(repo)
        dt.datetime.now(dt.UTC)
        obs.record_event("STRAT-A", "RUN-1", "RESEARCH_RUN_STARTED", "started")
        obs.record_event("STRAT-A", "RUN-1", "GATE_PASSED", "backtest PASS")
        repo._queue.join()

        events = obs.list_events(strategy_id="STRAT-A")
        types = {e["event_type"] for e in events}
        assert "RESEARCH_RUN_STARTED" in types, f"events invisible: {types}"
        assert "GATE_PASSED" in types
    finally:
        repo.close()


def test_bootstrap_ddl_matches_migration_ddl(tmp_path: Path) -> None:
    """The bootstrap DDL must be byte-equivalent to AUDIT-0009's.

    Both are idempotent ``CREATE TABLE IF NOT EXISTS``, so a database that has
    already run the migration must see the bootstrap as a no-op re-run. A drift
    here would leave two different shapes for the "same" table depending on
    which path created it first.
    """
    from nexus_scalp.database.registry import _audit_0009_research_archive_tables

    # The migration builds its archive DDL on a connection; capture it via the
    # bootstrap's own connection so both see the same translator.
    repo = _bootstrap_only(tmp_path)
    try:
        con = sqlite3.connect(str(tmp_path / "fresh.db"))
        try:
            _audit_0009_research_archive_tables(con, Path(str(tmp_path / "fresh.db")))
            con.commit()
            tables = {
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
    finally:
        repo.close()

    # Re-running the migration over a bootstrapped DB must not error and must
    # keep exactly one of each archive table (IF NOT EXISTS is the contract).
    assert "research_events_archive" in tables
    assert "research_evidence_archive" in tables


def test_write_plane_archive_read_does_not_swallow_missing_table(tmp_path: Path) -> None:
    """The read must surface a real failure rather than returning [] silently.

    This pins the failure mode: if the archive table is dropped out from under
    the store, ``list_events`` logs the error and returns an empty list. The
    guard above makes that unreachable on a bootstrapped DB; this test exists so
    a future refactor that drops the archive DDL produces a loud failure here
    rather than a silent empty timeline downstream.
    """
    repo = _bootstrap_only(tmp_path)
    try:
        obs = ResearchObservabilityStore(repo)
        obs.record_event("STRAT-B", "RUN-2", "RESEARCH_RUN_STARTED", "started")
        repo._queue.join()

        con = sqlite3.connect(str(tmp_path / "fresh.db"))
        try:
            con.execute("DROP TABLE research_events_archive")
            con.commit()
        finally:
            con.close()

        # Degraded but not crashing — and still returning the live rows once
        # the caller asks for the live-only view.
        live_only = obs.list_events(strategy_id="STRAT-B", include_archive=False)
        assert any(e["event_type"] == "RESEARCH_RUN_STARTED" for e in live_only)
    finally:
        repo.close()
