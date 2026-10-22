"""DB-HEALTH-NOTPROV: a fresh PostgreSQL database reports NOT_PROVISIONED.

The defect: on a brand-new PostgreSQL database the health probe asked for
``schema_migrations`` before the domain migration had created it. The failure
was reported by the generic query-failure path as a raw ``[PG-QUERY] error``
record:

    UndefinedTable: relation "schema_migrations" does not exist

which made a healthy first boot look broken (the CI full-runtime gate treats
any ``[error]`` line as a soak failure). "No migration ledger yet" is a
legitimate pre-provisioning state, not a failed query.

This test pins:
1. a driver in the live-configured shape whose ``scalar`` raises
   psycopg's UndefinedTable reports ``migration_state=NOT_PROVISIONED``,
   ``schema_version=0``, and NO error string;
2. the warning record carries the DB_HEALTH_SCHEMA_NOT_PROVISIONED event,
   so the state is observable, not silently swallowed;
3. any OTHER scalar failure (a real query bug, a dead connection) still
   degrades to ``UNKNOWN`` and is never mislabeled as NOT_PROVISIONED.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver
from nexus_scalp.database.health import DatabaseHealthService
from nexus_scalp.database.provider import DatabaseProvider


class UndefinedTable(Exception):  # noqa: N818 - mirrors psycopg.errors.UndefinedTable
    """psycopg.errors.UndefinedTable's name, without importing psycopg.

    The production check keys on the exception TYPE NAME, so the stub must
    carry psycopg's exact name (no ``Error`` suffix) to prove the real path.
    """


class _FreshPgDriver(SQLiteDriver):
    """SQLite-shaped driver that reports a fresh database's PG behavior.

    ``ping()`` succeeds (the connection is fine); ``scalar`` raises the
    UndefinedTable the real psycopg driver raises against an empty cluster.
    Subclassing SQLiteDriver keeps the driver contract without requiring a
    live psycopg connection in a unit test.
    """

    def ping(self) -> bool:
        return True

    def database_version(self) -> str:
        return "PostgreSQL 17.10"

    def table_count(self) -> int:
        return 3

    def scalar(self, *args: Any, **kwargs: Any) -> Any:
        raise UndefinedTable('relation "schema_migrations" does not exist\nLINE 1: ...')


class _OtherFailureDriver(_FreshPgDriver):
    """scalar raises an UNRELATED failure: must stay UNKNOWN, not NOT_PROV."""

    def scalar(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("column schema_migrations.version does not exist")


def _load_pg(monkeypatch: Any, driver_cls: type) -> None:
    cfg = DatabaseConfig(
        provider=DatabaseProvider.POSTGRESQL,
        domain="audit",
        host="127.0.0.1",
        port=5499,
        database="nse_fresh",
        username="postgres",
    )
    DatabaseHealthService.invalidate_domain_cache()
    monkeypatch.setattr(
        "nexus_scalp.database.health.load_database_config",
        lambda domain, env=None, **kwargs: cfg,
        raising=True,
    )
    monkeypatch.setattr("nexus_scalp.database.health.get_driver", driver_cls, raising=True)


def _capture_warnings(monkeypatch: Any) -> list[str]:
    records: list[str] = []

    class _Capture:
        @staticmethod
        def warning(message: str, *args: Any, **kwargs: Any) -> None:
            records.append(message % args if args else message)

        @staticmethod
        def error(message: str, *args: Any, **kwargs: Any) -> None:
            records.append(message % args if args else message)

    monkeypatch.setattr("nexus_scalp.database.health.logger", _Capture())
    return records


def test_fresh_pg_reports_not_provisioned(monkeypatch: Any) -> None:
    """UndefinedTable on a connected driver => NOT_PROVISIONED, no error text."""
    _load_pg(monkeypatch, _FreshPgDriver)
    records = _capture_warnings(monkeypatch)
    svc = DatabaseHealthService()
    snap = svc.check_domain("audit")
    assert snap["status"] == "CONNECTED"
    assert snap["migration_state"] == "NOT_PROVISIONED"
    assert snap["schema_version"] == 0
    assert snap.get("error") in (None, "")
    assert any("DB_HEALTH_SCHEMA_NOT_PROVISIONED" in r for r in records), (
        f"warning record missing: {records}"
    )


def test_other_scalar_failures_stay_unknown(monkeypatch: Any) -> None:
    """A failure that is NOT a missing relation degrades exactly as before."""
    _load_pg(monkeypatch, _OtherFailureDriver)
    records = _capture_warnings(monkeypatch)
    svc = DatabaseHealthService()
    snap = svc.check_domain("audit")
    assert snap["migration_state"] == "UNKNOWN"
    assert snap["schema_version"] is None
    assert not any("DB_HEALTH_SCHEMA_NOT_PROVISIONED" in r for r in records), (
        "mislabeled a genuine failure as NOT_PROVISIONED"
    )


def test_sqlite_path_is_untouched(monkeypatch: Any, tmp_path: Any) -> None:
    """The engine-status branch keeps its contract under SQLite."""
    cfg = DatabaseConfig(
        provider=DatabaseProvider.SQLITE,
        domain="audit",
        database=str(tmp_path / "audit.db"),
        sqlite_path=str(tmp_path / "audit.db"),
    )
    DatabaseHealthService.invalidate_domain_cache()
    monkeypatch.setattr(
        "nexus_scalp.database.health.load_database_config",
        lambda domain, env=None, **kwargs: cfg,
        raising=True,
    )
    svc = DatabaseHealthService()
    snap = svc.check_domain("audit")
    # The SQLite engine provisions on status(); either CURRENT (it created the
    # tables) or a documented engine state — but never NOT_PROVISIONED.
    assert snap["migration_state"] != "NOT_PROVISIONED"
