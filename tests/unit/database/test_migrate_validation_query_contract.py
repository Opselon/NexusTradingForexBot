from __future__ import annotations

from typing import Any

from nexus_scalp.database.migrate_engine import MigrationOptions, SqliteToPostgresMigrator


class _FakeDriver:
    def __init__(self, provider: str, columns: list[str], fail_aggregate: bool = False) -> None:
        self.name = provider
        self.provider = provider
        self.columns = columns
        self.fail_aggregate = fail_aggregate
        self.sql: list[str] = []

    def row_count(self, table: str) -> int:
        return 1

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        return [{"name": name, "pk": False} for name in self.columns]

    def list_tables(self) -> list[str]:
        return ["audit_ledger"]

    @staticmethod
    def quote_ident(value: str) -> str:
        return f'"{value}"'

    def scalar(self, sql: str, args: tuple[Any, ...] = ()) -> float:
        self.sql.append(sql)
        if self.fail_aggregate and "SUM" in sql:
            raise RuntimeError("synthetic aggregate query failure")
        if self.provider == "postgres" and '"MAE_usd"' in sql:
            raise AssertionError("source SQLite casing must not be emitted for PostgreSQL")
        return 123.0


def _migrator(sqlite: _FakeDriver, postgres: _FakeDriver) -> SqliteToPostgresMigrator:
    migrator = object.__new__(SqliteToPostgresMigrator)
    migrator.options = MigrationOptions(financial_tables={"audit_ledger": ["MAE_usd"]})
    migrator._src_driver = sqlite
    migrator._pg_driver = postgres
    return migrator


def test_validate_resolves_sqlite_financial_column_to_postgres_catalog_case() -> None:
    sqlite = _FakeDriver("sqlite", ["MAE_usd"])
    postgres = _FakeDriver("postgres", ["mae_usd"])

    assert _migrator(sqlite, postgres).validate() == "PASSED"
    assert 'SUM("MAE_usd")' in sqlite.sql
    assert 'SUM("mae_usd")' in postgres.sql


def test_validate_rejects_financial_query_execution_failures() -> None:
    sqlite = _FakeDriver("sqlite", ["MAE_usd"])
    postgres = _FakeDriver("postgres", ["mae_usd"], fail_aggregate=True)

    assert _migrator(sqlite, postgres).validate() == "FAILED"
