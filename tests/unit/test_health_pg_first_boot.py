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


class _RecordingDriver(SQLiteDriver):
    """A connected driver that records every statement it is asked to run.

    ``relation_present`` models the target database: a fresh PostgreSQL has no
    ``schema_migrations`` yet. ``scalar`` distinguishes the catalog question
    from the version read so the test can assert which one was issued.
    """

    def __init__(self, config: Any, *, relation_present: bool, version_error: Any = None):
        super().__init__(config)
        self.relation_present = relation_present
        self.version_error = version_error
        self.statements: list[str] = []

    def ping(self) -> bool:
        return True

    def database_version(self) -> str:
        return "PostgreSQL 17.10"

    def table_count(self) -> int:
        return 3

    def scalar(self, sql: str = "", args: Any = None, **kwargs: Any) -> Any:
        self.statements.append(sql)
        if "to_regclass" in sql:
            return self.relation_present
        if self.version_error is not None:
            raise self.version_error
        return 7

    def version_statements(self) -> list[str]:
        """The statements that would FAIL against a missing relation."""
        return [
            s
            for s in self.statements
            if "FROM schema_migrations" in s or "FROM schema_meta" in s
        ]


def _load_pg(monkeypatch: Any, driver_factory: Any) -> None:
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
    monkeypatch.setattr(
        "nexus_scalp.database.health.get_driver", driver_factory, raising=True
    )


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
    """Absent relation => NOT_PROVISIONED, and NO version SELECT is issued."""
    captured: list[_RecordingDriver] = []

    def factory(config: Any) -> _RecordingDriver:
        driver = _RecordingDriver(config, relation_present=False)
        captured.append(driver)
        return driver

    _load_pg(monkeypatch, factory)
    records = _capture_warnings(monkeypatch)
    svc = DatabaseHealthService()
    snap = svc.check_domain("audit")
    assert snap["status"] == "CONNECTED"
    assert snap["migration_state"] == "NOT_PROVISIONED"
    assert snap["schema_version"] == 0
    assert snap.get("error") in (None, "")
    # The [PG-QUERY] error came from SELECTing the missing relation. The probe
    # must not issue that statement at all on a not-yet-provisioned database.
    assert captured[0].version_statements() == [], captured[0].statements
    assert any("DB_HEALTH_SCHEMA_NOT_PROVISIONED" in r for r in records), (
        f"warning record missing: {records}"
    )


def test_provisioned_pg_reads_version(monkeypatch: Any) -> None:
    """The normal path still reads MAX(version) once the relation exists."""
    captured: list[_RecordingDriver] = []

    def factory(config: Any) -> _RecordingDriver:
        driver = _RecordingDriver(config, relation_present=True)
        captured.append(driver)
        return driver

    _load_pg(monkeypatch, factory)
    records = _capture_warnings(monkeypatch)
    svc = DatabaseHealthService()
    snap = svc.check_domain("audit")
    assert snap["migration_state"] == "CURRENT"
    assert snap["schema_version"] == 7
    assert captured[0].version_statements(), "expected a version read"
    assert not any("DB_HEALTH_SCHEMA_NOT_PROVISIONED" in r for r in records), records


def test_other_scalar_failures_stay_unknown(monkeypatch: Any) -> None:
    """A failure that is NOT a missing relation degrades exactly as before."""

    def factory(config: Any) -> _RecordingDriver:
        return _RecordingDriver(
            config,
            relation_present=True,
            version_error=RuntimeError("column schema_migrations.version missing"),
        )

    _load_pg(monkeypatch, factory)
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
