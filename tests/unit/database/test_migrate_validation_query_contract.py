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
    # ``sql`` is the per-statement execution log (one entry per query), so the
    # casing contract is asserted per statement, not by list membership.
    assert any('SUM("MAE_usd")' in statement for statement in sqlite.sql), sqlite.sql
    assert any('SUM("mae_usd")' in statement for statement in postgres.sql), postgres.sql


def test_validate_honours_financial_column_override_over_the_default_watchlist() -> None:
    """MIG-VALID: a per-run override REPLACES the default watch-list entry.

    The default ``audit_ledger`` watch-list carries the columns the app's
    baseline-skeleton heal adds (pnl/commission/swap/gross_pnl_usd/
    net_pnl_usd/mae/mfe). A source that never ran that heal does not carry
    them, and probing them is a hard "SQLite source column missing" error -
    so ``financial_tables`` must win over the baseline instead of being
    silently discarded by it. This is the regression: the merge used to
    apply the defaults LAST, so every override was thrown away.
    """
    sqlite = _FakeDriver("sqlite", ["MAE_usd"])
    postgres = _FakeDriver("postgres", ["mae_usd"])

    assert _migrator(sqlite, postgres).validate() == "PASSED"
    # The seven heal-only columns must NOT be probed on this source: a single
    # missing-column warning for any of them fails validation again.
    probed = " ".join(sqlite.sql)
    for absent in ("pnl", "commission", "swap", "gross_pnl_usd", "net_pnl_usd"):
        assert f'SUM("{absent}")' not in probed, probed


def test_default_watchlist_still_covers_audit_ledger_financial_columns() -> None:
    """The production default (no override) still probes the full watch-list."""
    migrator = object.__new__(SqliteToPostgresMigrator)
    migrator.options = MigrationOptions()
    assert migrator._financial_cols()["audit_ledger"] == [
        "pnl",
        "commission",
        "swap",
        "gross_pnl_usd",
        "net_pnl_usd",
        "mae",
        "mfe",
        "MAE_usd",
        "MFE_usd",
        "account_balance_after",
        "account_equity_after",
        "drawdown_percent_after",
        "exit_reason_confidence",
        "ai_confidence_at_open",
    ]


def test_validate_rejects_financial_query_execution_failures() -> None:
    sqlite = _FakeDriver("sqlite", ["MAE_usd"])
    postgres = _FakeDriver("postgres", ["mae_usd"], fail_aggregate=True)

    assert _migrator(sqlite, postgres).validate() == "FAILED"
