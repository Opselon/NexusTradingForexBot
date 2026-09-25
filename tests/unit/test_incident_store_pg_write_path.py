"""Regression tests for the PG/pooled write path of IncidentStore.

Context: PR #470 (PG parity wave) opened a pooled write-backend branch in
``IncidentStore.save``. That branch used bare ``INSERT`` statements for
``incident_events`` and ``incident_value_traces``, while the SQLite branch had
always deduped:

* events  -> ``INSERT OR REPLACE`` keyed on (incident_id, event_timestamp, event_type)
* traces  -> ``INSERT OR IGNORE``  keyed on (incident_id, field, source)

The bare inserts re-inserted the whole timeline + trace set on every save(),
silently duplicating rows on the PG path. These tests pin the upsert behaviour
on both providers by capturing the SQL the pooled backend actually runs.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from nexus_scalp.incidents.models import (
    EventSource,
    Incident,
    IncidentCategory,
    IncidentSeverity,
    IncidentStatus,
    TimelineEvent,
    ValueTrace,
)
from nexus_scalp.incidents.store import IncidentStore


def _make_incident(now: dt.datetime, incident_id: str = "INC-1") -> Incident:
    inc = Incident(
        incident_id=incident_id,
        detected_at=now,
        fingerprint="fp-" + incident_id,
        category=IncidentCategory.DATA,
        severity=IncidentSeverity.LOW,
        status=IncidentStatus.OPEN,
    )
    inc.timeline.append(
        TimelineEvent(timestamp=now, event_type="CREATED", source=EventSource.RUNTIME)
    )
    inc.value_traces.append(ValueTrace(field="balance", source="mt5", source_timestamp=now))
    return inc


class _FakeBackend:
    """Captures the SQL the pooled/PG write path runs, statements verbatim."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, values: object) -> int:
        self.statements.append(sql.strip())
        return 0

    def executemany(self, sql: str, seq: object) -> int:
        self.statements.append(sql.strip())
        return 0

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture()
def captured_pg_path(tmp_path: pathlib.Path) -> tuple[IncidentStore, list[str]]:
    """An IncidentStore whose write backend is a capture-only fake."""
    store = IncidentStore(db_path=str(tmp_path / "incidents.db"))
    fake = _FakeBackend()
    store._write_backend = fake
    store._write_backend_run = fake.execute
    return store, fake.statements


def _events_sql(statements: list[str]) -> list[str]:
    return [s for s in statements if s.startswith("INSERT INTO incident_events")]


def _traces_sql(statements: list[str]) -> list[str]:
    return [s for s in statements if s.startswith("INSERT INTO incident_value_traces")]


def test_incident_events_uses_on_conflict_upsert(captured_pg_path) -> None:
    store, statements = captured_pg_path
    now = dt.datetime.now(dt.UTC)
    store.save(_make_incident(now))

    sql = _events_sql(statements)
    assert len(sql) == 1, f"expected one events insert, got {len(sql)}"
    assert "ON CONFLICT(incident_id, event_timestamp, event_type)" in sql[0]
    assert "DO UPDATE SET" in sql[0]


def test_incident_value_traces_uses_on_conflict_upsert(captured_pg_path) -> None:
    store, statements = captured_pg_path
    now = dt.datetime.now(dt.UTC)
    store.save(_make_incident(now))

    sql = _traces_sql(statements)
    assert len(sql) == 1, f"expected one traces insert, got {len(sql)}"
    assert "ON CONFLICT(incident_id, field, source)" in sql[0]
    assert "DO UPDATE SET" in sql[0]


def test_repeated_save_emits_upsert_not_bare_insert(captured_pg_path) -> None:
    """The bug: every save() re-inserted the whole timeline on the PG path.

    The fix is the upsert clause. What a bare ``INSERT`` changed across saves
    was row count at rest, not statement count here (the fake backend never
    applies the SQL), so the meaningful assertion is that every statement
    emitted is an upsert — never a bare insert.
    """
    store, statements = captured_pg_path
    now = dt.datetime.now(dt.UTC)
    inc = _make_incident(now)

    store.save(inc)
    store.save(inc)

    all_events = _events_sql(statements)
    assert all_events, "no events SQL captured"
    assert all("ON CONFLICT(incident_id, event_timestamp, event_type)" in s for s in all_events)
    assert not any(s.endswith("VALUES (?, ?, ?, ?, ?, ?)") for s in all_events)


def test_sqlite_path_dedupes_events_across_saves(tmp_path: pathlib.Path) -> None:
    """End-to-end on the real SQLite path: row count must not grow."""
    store = IncidentStore(db_path=str(tmp_path / "incidents.db"))
    now = dt.datetime.now(dt.UTC)
    inc = _make_incident(now)

    store.save(inc)
    store.save(inc)
    store.save(inc)

    con = store._connect()
    try:
        events = con.execute("SELECT COUNT(*) FROM incident_events").fetchone()[0]
        traces = con.execute("SELECT COUNT(*) FROM incident_value_traces").fetchone()[0]
    finally:
        con.close()

    assert events == 1, f"timeline duplicated across saves: {events} rows"
    assert traces == 1, f"value traces duplicated across saves: {traces} rows"


def test_events_table_carries_unique_constraint(tmp_path: pathlib.Path) -> None:
    """The upsert target must exist as a live UNIQUE index (BUG-276 guard)."""
    store = IncidentStore(db_path=str(tmp_path / "incidents.db"))
    con = store._connect()
    try:
        uniques = {
            tuple(r[2].lower() for r in con.execute(f"PRAGMA index_info({i[1]})").fetchall())
            for i in con.execute("PRAGMA index_list(incident_events)").fetchall()
            if i[2]
        }
    finally:
        con.close()

    assert ("incident_id", "event_timestamp", "event_type") in uniques


def test_value_traces_table_carries_unique_constraint(tmp_path: pathlib.Path) -> None:
    store = IncidentStore(db_path=str(tmp_path / "incidents.db"))
    con = store._connect()
    try:
        uniques = {
            tuple(r[2].lower() for r in con.execute(f"PRAGMA index_info({i[1]})").fetchall())
            for i in con.execute("PRAGMA index_list(incident_value_traces)").fetchall()
            if i[2]
        }
    finally:
        con.close()

    assert ("incident_id", "field", "source") in uniques
