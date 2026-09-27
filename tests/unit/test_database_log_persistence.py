from __future__ import annotations

from types import SimpleNamespace

from nexus_scalp.database import log_store, query_logging
from nexus_scalp.database.log_store import (
    DatabaseLogEntry,
    log_persistence_enabled,
    log_persistence_snapshot,
    reset_log_persistence_counters,
)


def test_persistence_is_opt_in(monkeypatch) -> None:
    settings = SimpleNamespace(get=lambda key: None)
    assert log_persistence_enabled(settings) is False

    settings.get = lambda key: SimpleNamespace(value=True)
    assert log_persistence_enabled(settings) is True


def test_query_failure_is_sent_to_persistent_sink(monkeypatch) -> None:
    records: list[DatabaseLogEntry] = []

    class FakeStore:
        def __init__(self) -> None:
            pass

        def record(self, entry: DatabaseLogEntry) -> bool:
            records.append(entry)
            return True

    reset_log_persistence_counters()
    monkeypatch.setattr(query_logging, "_persistence_enabled", lambda: True)
    monkeypatch.setattr(log_store, "DatabaseLogStore", FakeStore)
    monkeypatch.setattr(query_logging, "DatabaseLogStore", FakeStore, raising=False)
    query_logging.log_query_failure(
        operation="select_users",
        exc=RuntimeError("password=secret-value"),
        sql="SELECT * FROM users WHERE token='secret-value'",
        domain="audit",
    )

    assert len(records) == 1
    assert records[0].level == "ERROR"
    assert "secret-value" not in records[0].masked_sql


def test_persistence_guard_drops_recursive_sink(monkeypatch) -> None:
    calls = 0

    class RecursiveStore:
        def record(self, entry: DatabaseLogEntry) -> bool:
            nonlocal calls
            calls += 1
            query_logging._persist_event("ERROR", {"operation": "nested"})
            return True

    reset_log_persistence_counters()
    monkeypatch.setattr(query_logging, "_persistence_enabled", lambda: True)
    monkeypatch.setattr(log_store, "DatabaseLogStore", RecursiveStore)
    monkeypatch.setattr(query_logging, "DatabaseLogStore", RecursiveStore, raising=False)
    query_logging._persist_event("ERROR", {"operation": "outer"})

    assert calls == 1
    assert log_persistence_snapshot()["last_persist_error"] is None
