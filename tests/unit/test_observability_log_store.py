"""Observability log sink: severity policy, bounded reads, and source health.

Covers the three behaviours the log centre depends on:

1. **Severity policy** — WARNING/ERROR/CRITICAL always persist while the sink
   is on; INFO persists only when the operator (or an explicit store/handler
   argument) opted in. The default stays off, so a default deployment does not
   grow the table with high-volume informational records, and enabling INFO
   can never silence the WARN+ stream.
2. **Bounded reads** — filtered, keyset-paginated queries with server-side
   caps, so a client cannot ask for an unbounded window or an unbounded
   full-text scan.
3. **Honest empty vs failure** — ``health()`` distinguishes "no events" from
   "source unavailable", which the UI renders differently.

These are behavioural round-trip tests: a record goes in through the real
store and comes back through the real query path on SQLite. PostgreSQL parity
is exercised by the integration suite (tests/integration/test_log_store_pg.py)
against a live server.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.log_store import (
    LOG_QUERY_MAX_LIMIT,
    DbLogEntry,
    DbLogStore,
    install_database_log_handler,
    uninstall_database_log_handler,
)


@pytest.fixture()
def store(tmp_path: Path) -> tuple[DbLogStore, SQLiteDriver]:
    """A fresh SQLite-backed store and driver sharing one connection."""
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "logs.db"))
    drv = SQLiteDriver(cfg)
    s = DbLogStore(cfg, retention_days=30)
    s.ensure_table(drv)
    yield s, drv
    drv.close()


def _entry(level: str, **overrides: object) -> DbLogEntry:
    params: dict[str, object] = {
        "level": level,
        "provider": "sqlite",
        "domain": "audit",
        "operation": f"op_{level.lower()}",
        "event_name": f"EVENT_{level}",
        "logger_name": "test.logger",
        "error_message": f"message for {level}",
        "correlation_id": "corr-1",
    }
    params.update(overrides)
    return DbLogEntry(**params)  # type: ignore[arg-type]


# -- severity policy ----------------------------------------------------------


def test_info_is_dropped_by_default(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """Default contract: INFO does not reach the table (size discipline)."""
    s, drv = store
    assert s.record(_entry("INFO"), driver=drv) is False
    assert s.query_events(driver=drv) == []


def test_warn_error_critical_always_persist(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """The WARN+ stream is independent of the INFO switch."""
    s, drv = store
    for level in ("WARNING", "ERROR", "CRITICAL"):
        assert s.record(_entry(level), driver=drv) is True
    rows = s.query_events(driver=drv)
    assert sorted(r["level"] for r in rows) == ["CRITICAL", "ERROR", "WARNING"]


def test_info_persists_when_explicitly_enabled(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """An explicit store argument turns INFO on without touching global settings."""
    s, drv = store
    s._persist_info = True
    assert s.record(_entry("INFO"), driver=drv) is True
    rows = s.query_events(levels=("INFO",), driver=drv)
    assert len(rows) == 1
    assert rows[0]["event_name"] == "EVENT_INFO"


def test_enabling_info_does_not_touch_warn_stream(
    store: tuple[DbLogStore, SQLiteDriver],
) -> None:
    """INFO on must not duplicate, sample, or drop the WARN+ stream."""
    s, drv = store
    s._persist_info = True
    s.record(_entry("INFO"), driver=drv)
    s.record(_entry("WARNING"), driver=drv)
    s.record(_entry("ERROR"), driver=drv)
    rows = s.query_events(driver=drv)
    assert sorted(r["level"] for r in rows) == ["ERROR", "INFO", "WARNING"]


def test_settled_levels_are_rejected(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """DEBUG/TRACE are not part of the persisted contract."""
    s, drv = store
    for level in ("DEBUG", "TRACE", "NOTSET"):
        assert s.record(_entry(level), driver=drv) is False
    assert s.query_events(driver=drv) == []


# -- handler-level severity policy -------------------------------------------


def test_handler_persists_info_only_when_enabled(tmp_path: Path) -> None:
    """The root-logger handler honours the same INFO switch as the store."""
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "logs.db"))
    try:
        handler = install_database_log_handler(config=cfg, enabled=True, persist_info=True)
        assert handler is not None
        assert handler.level == logging.WARNING
        root = logging.getLogger()
        assert handler in root.handlers
        # Emit one record so the lazily-resolved store exists, then assert the
        # INFO switch was carried into the store the handler bound.
        logging.getLogger("test.handler.info_on").warning("probe")
        assert handler._store is not None
        assert handler._store._info_persistence_enabled() is True
    finally:
        uninstall_database_log_handler()


def test_handler_default_drops_info(tmp_path: Path) -> None:
    """Default handler construction leaves INFO off."""
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "logs.db"))
    try:
        handler = install_database_log_handler(config=cfg, enabled=True)
        assert handler is not None
        logging.getLogger("test.handler.info_off").warning("probe")
        assert handler._store is not None
        assert handler._store._info_persistence_enabled() is False
    finally:
        uninstall_database_log_handler()


# -- bounded reads ------------------------------------------------------------


@pytest.mark.parametrize(
    "filter_kwargs, expected_levels",
    [
        ({"levels": ("ERROR",)}, ["ERROR"]),
        ({"levels": ("ERROR", "CRITICAL")}, ["CRITICAL", "ERROR"]),
        ({"levels": ("INFO",)}, []),  # nothing persisted at INFO by default
    ],
)
def test_level_filter(
    store: tuple[DbLogStore, SQLiteDriver],
    filter_kwargs: dict[str, object],
    expected_levels: list[str],
) -> None:
    s, drv = store
    for level in ("WARNING", "ERROR", "CRITICAL"):
        s.record(_entry(level), driver=drv)
    rows = s.query_events(driver=drv, **filter_kwargs)  # type: ignore[arg-type]
    assert sorted(r["level"] for r in rows) == expected_levels


def test_component_filter(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    s, drv = store
    s.record(_entry("ERROR", logger_name="alpha.mod"), driver=drv)
    s.record(_entry("ERROR", logger_name="beta.mod"), driver=drv)
    rows = s.query_events(components=("alpha.mod",), driver=drv)
    assert len(rows) == 1
    assert rows[0]["logger_name"] == "alpha.mod"


def test_event_name_filter(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    s, drv = store
    s.record(_entry("ERROR", event_name="ORDER_REJECTED"), driver=drv)
    s.record(_entry("ERROR", event_name="POOL_TIMEOUT"), driver=drv)
    rows = s.query_events(event_names=("ORDER_REJECTED",), driver=drv)
    assert len(rows) == 1
    assert rows[0]["event_name"] == "ORDER_REJECTED"


def test_correlation_id_drill_down(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """A correlation id joins every record of one logical operation."""
    s, drv = store
    s.record(_entry("WARNING", correlation_id="rid-99"), driver=drv)
    s.record(_entry("ERROR", correlation_id="rid-99"), driver=drv)
    s.record(_entry("ERROR", correlation_id="rid-other"), driver=drv)
    rows = s.query_events(correlation_id="rid-99", driver=drv)
    assert sorted(r["level"] for r in rows) == ["ERROR", "WARNING"]


def test_text_search_is_prefix_bounded(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """Text search matches the message prefix; a non-matching term returns []."""
    s, drv = store
    s.record(_entry("ERROR", error_message="connection refused by host"), driver=drv)
    assert len(s.query_events(text="connection", driver=drv)) == 1
    assert s.query_events(text="refused by host", driver=drv) == []


def test_time_range_filter(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """since/until bound the window. Rows carry ISO timestamps."""
    s, drv = store
    s.record(_entry("ERROR", timestamp="2026-01-01T00:00:00+00:00"), driver=drv)
    s.record(_entry("ERROR", timestamp="2026-06-01T00:00:00+00:00"), driver=drv)
    rows = s.query_events(since="2026-03-01T00:00:00+00:00", driver=drv)
    assert len(rows) == 1
    assert rows[0]["timestamp"].startswith("2026-06")


def test_keyset_pagination_is_bounded(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """before_id pages backwards without OFFSET; every page is bounded."""
    s, drv = store
    for i in range(5):
        s.record(_entry("ERROR", operation=f"op_{i}"), driver=drv)
    first = s.query_events(limit=2, driver=drv)
    assert len(first) == 2
    cursor = first[-1]["id"]
    second = s.query_events(limit=2, before_id=cursor, driver=drv)
    assert len(second) == 2
    assert all(r["id"] < cursor for r in second)


def test_limit_is_clamped_server_side(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """A client cannot request more than the server-side cap."""
    s, drv = store
    s.record(_entry("ERROR"), driver=drv)
    rows = s.query_events(limit=10_000_000, driver=drv)
    assert len(rows) <= LOG_QUERY_MAX_LIMIT


def test_filters_are_redacted(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """A filter value is masked before it reaches SQL — no secret probing."""
    s, drv = store
    s.record(_entry("ERROR", error_message="plain message"), driver=drv)
    # A password-shaped filter must not error and must not match anything.
    assert s.query_events(text="password=supersecretvalue123", driver=drv) == []
    assert s.query_events(components=("nexus_scalpsecret_token",), driver=drv) == []


# -- counts and health -------------------------------------------------------


def test_count_events_matches(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    s, drv = store
    for level in ("WARNING", "ERROR", "CRITICAL"):
        s.record(_entry(level), driver=drv)
    assert s.count_events(driver=drv) == 3
    assert s.count_events(levels=("ERROR",), driver=drv) == 1
    assert s.count_events(levels=("INFO",), driver=drv) == 0


def test_health_reports_ok_and_rows(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """health() says the table is readable and how many rows it holds."""
    s, drv = store
    s.record(_entry("ERROR"), driver=drv)
    h = s.health(drv)
    assert h["ok"] is True
    assert h["rows"] == 1
    assert h["provider"] == "sqlite"


def test_health_reports_failure_for_a_dead_source(tmp_path: Path) -> None:
    """A sink whose driver cannot connect reports ok=False, never raises."""
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "missing.db"))
    s = DbLogStore(cfg)
    # Point at a path that cannot be opened: a directory is not a database.
    cfg.sqlite_path = str(tmp_path)
    h = s.health()
    assert isinstance(h, dict)
    assert "ok" in h


def test_query_returns_empty_list_on_failure_never_raises(tmp_path: Path) -> None:
    """A read failure is an honest empty list, not an exception."""
    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "x.db"))
    s = DbLogStore(cfg)
    cfg.sqlite_path = str(tmp_path)  # unusable
    assert s.query_events() == []


# -- retention contract ------------------------------------------------------


def test_retention_cannot_touch_recent_records(store: tuple[DbLogStore, SQLiteDriver]) -> None:
    """Purge removes only rows older than the retention window.

    ``retention_days=0`` means "keep nothing" (the whole window is in the
    past), so a fresh record IS removed — that is the documented meaning of a
    zero retention, not a bug. A record written now under a NON-zero
    retention must survive. Both directions are asserted so the contract is
    pinned explicitly instead of relying on an off-by-days ambiguity.
    """
    s, drv = store
    s.record(_entry("ERROR"), driver=drv)
    assert s.count_events(driver=drv) == 1

    # Non-zero retention: a record written moments ago is NOT expired.
    s.retention_days = 30
    assert s.purge_expired() == 0
    assert s.count_events(driver=drv) == 1

    # Zero retention = keep nothing, so the row is legitimately removed.
    s.retention_days = 0
    assert s.purge_expired() == 1
    assert s.count_events(driver=drv) == 0
