from __future__ import annotations

from types import SimpleNamespace

from nexus_scalp.database import log_store, query_logging
from nexus_scalp.database.log_store import DatabaseLogEntry


def test_persistence_is_opt_in() -> None:
    settings = SimpleNamespace(get=lambda key: None)
    assert log_store.log_persistence_enabled(settings) is False
    settings.get = lambda key: SimpleNamespace(value=True)
    assert log_store.log_persistence_enabled(settings) is True


def test_query_funnels_persist_when_enabled(monkeypatch) -> None:
    records: list[DatabaseLogEntry] = []

    class FakeStore:
        cfg = SimpleNamespace(provider="sqlite")

        def record(self, entry: DatabaseLogEntry) -> bool:
            records.append(entry)
            return True

    monkeypatch.setattr(query_logging, "_persistence_enabled", lambda: True)
    monkeypatch.setattr(log_store, "DatabaseLogStore", FakeStore)
    query_logging.log_query_failure(
        operation="select_users",
        exc=RuntimeError("password=secret-value"),
        sql="SELECT * FROM users WHERE token='secret-value'",
        domain="audit",
    )
    monkeypatch.setattr(query_logging, "PG_SLOW_QUERY_THRESHOLD_MS", 1.0)
    query_logging.log_slow_query(operation="slow", duration_ms=2.0, domain="audit")
    query_logging.log_pool_failure(pool_name="pool", operation="checkout", exc=RuntimeError())

    assert [entry.level for entry in records] == ["ERROR", "WARNING", "ERROR"]
    assert "secret-value" not in records[0].masked_sql


def test_persistence_recursion_is_isolated(monkeypatch) -> None:
    calls = 0

    class RecursiveStore:
        cfg = SimpleNamespace(provider="sqlite")

        def record(self, entry: DatabaseLogEntry) -> bool:
            nonlocal calls
            calls += 1
            query_logging._persist_event("ERROR", {"operation": "nested"})
            return True

    monkeypatch.setattr(query_logging, "_persistence_enabled", lambda: True)
    monkeypatch.setattr(log_store, "DatabaseLogStore", RecursiveStore)
    query_logging._persist_event("ERROR", {"operation": "outer"})
    assert calls == 1
