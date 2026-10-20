from __future__ import annotations

from types import SimpleNamespace

from nexus_scalp.database import log_store, query_logging
from nexus_scalp.database.log_store import DatabaseLogEntry


def _settings(value):
    """A settings seam whose row holds ``value`` (``None`` = no row at all)."""
    return SimpleNamespace(get=lambda key: None if value is None else SimpleNamespace(value=value))


def test_persistence_is_on_by_default_and_explicitly_switchable() -> None:
    """The sink ships ON; only an explicit operator row turns it off.

    The requirement is "errors and warnings from the WHOLE app, in the DB", so
    an absent/unreadable row must never read as "off" — that is precisely the
    failure mode that made the sink look dead. The operator switch itself is
    still honoured in both directions.
    """
    assert log_store.log_persistence_enabled(_settings(None)) is True
    assert log_store.log_persistence_enabled(_settings(True)) is True
    assert log_store.log_persistence_enabled(_settings(False)) is False


def test_persistence_switch_reads_operator_falsy_spellings() -> None:
    """An "0"/"false"/"off" row must DISABLE the switch.

    ``bool(value)`` reads every non-empty string as True, so a settings form
    that stored "0" left the sink running while the operator believed it was
    off — the switch looked wired and was not.
    """
    for falsy in ("0", "false", "False", "no", "off", "disabled"):
        assert log_store.log_persistence_enabled(_settings(falsy)) is False, falsy
    for truthy in ("1", "true", "on", "yes", "enabled"):
        assert log_store.log_persistence_enabled(_settings(truthy)) is True, truthy


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
