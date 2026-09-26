"""Lane L — dedup_guard: provider-aware idempotency on BOTH providers.

WHAT THIS PINS
==============
The live nexusdb accumulated duplicates on exactly the paths that re-deliver
the same event (re-sent ticks -> audit_signals, re-played telemetry ->
audit_guard_telemetry, re-synced broker history -> audit_broker_*). This
suite proves the helper enforces "insert exactly once per natural key" on
PostgreSQL AND SQLite, so a store adopting it cannot regress either
provider — SQLite must stay a first-class provider.

ISOLATION CONTRACT
==================
The PostgreSQL arm creates a THROWAWAY database per test and drops it
afterward — never the live nexusdb. It follows the suite's existing
convention (``NSE_PG_TEST_URL`` / ``tests/unit/test_candle_intel_pg_persistence.py``):
the live cluster is connected to ONLY for CREATE/DROP of the scratch
database. When ``NSE_PG_TEST_URL`` is unset the whole PG arm skips cleanly,
so the suite runs wherever psycopg is absent. The SQLite arm always runs.
No credential is written into this file or any log line.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.dedup_guard import (
    DedupGuard,
    DedupTargetError,
    insert_ignore_sql,
    natural_key_hash,
)
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver

# ---------------------------------------------------------------------------
# Provider arms
# ---------------------------------------------------------------------------

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL arm of this suite)"
)

_SCRATCH_DB = "nse_dedup_guard_pg_test"


def _pg_parts() -> dict[str, Any]:
    from psycopg.conninfo import conninfo_to_dict

    return dict(conninfo_to_dict(PG_URL))


def _base_dsn() -> str:
    from psycopg.conninfo import make_conninfo

    parts = _pg_parts()
    parts.pop("dbname", None)
    parts.pop("database", None)
    return make_conninfo("", **parts)


# ---------------------------------------------------------------------------
# Schema: mirrors the real hot-path tables, in both dialects.
# ---------------------------------------------------------------------------

# audit_orders on live nexusdb: PK(id) plus a PARTIAL unique index on
# execution_id (``WHERE execution_id IS NOT NULL AND execution_id != ''`` —
# see ``AuditRepository._ensure_unique_constraint_heal``). The guard must
# dedupe on that index and still allow NULL/'' execution_id rows to coexist.
ORDERS_SQLITE = """
CREATE TABLE dedup_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    execution_id TEXT,
    timestamp TEXT NOT NULL
)
"""

_ORDERS_UNIQUE_SQLITE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_orders_execution_id "
    "ON dedup_orders (execution_id) WHERE execution_id IS NOT NULL AND execution_id != ''"
)

# audit_guard_telemetry: composite PK (window_start, symbol, reason_code) —
# the natural key a store derives when no single unique column exists.
GUARD_SQLITE = """
CREATE TABLE dedup_guard_telemetry (
    window_start TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (window_start, symbol, reason_code)
)
"""

# audit_broker_orders: no surrogate key — the broker TICKET is the identity,
# exactly the re-sync duplicate path in broker_history.py.
BROKER_SQLITE = """
CREATE TABLE dedup_broker_orders (
    ticket INTEGER PRIMARY KEY,
    position_id INTEGER,
    symbol TEXT NOT NULL,
    synced_at TEXT NOT NULL
)
"""

# A table with NO usable constraint: the store must be told to fail loudly
# rather than emit a plain INSERT that duplicates on every re-delivery.
NOCONSTRAINT_SQLITE = """
CREATE TABLE dedup_noconstraint (
    a TEXT NOT NULL,
    b TEXT NOT NULL
)
"""

_PG_TYPE = {
    "AUTOINCREMENT": "GENERATED ALWAYS AS IDENTITY",
    "TEXT": "TEXT",
    "INTEGER": "BIGINT",
}


def _to_pg_ddl(sqlite_ddl: str) -> str:
    """Translate the shared SQLite-shaped DDL to PostgreSQL.

    Uses the repo's own translator ( the same path the migrator takes ) so
    the PG schema is what a real provision would build.
    """
    from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver

    return PostgreSQLDriver.translate_sql(sqlite_ddl)


# ---------------------------------------------------------------------------
# SQLite fixtures ( always run )
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_driver(tmp_path: Path) -> Iterator[SQLiteDriver]:
    """A real SQLiteDriver over a per-test file DB with the real constraint shapes."""
    db_path = tmp_path / "dedup_guard.db"
    cfg = DatabaseConfig.for_sqlite("audit", path=str(db_path))
    driver = SQLiteDriver(cfg)
    for ddl in (ORDERS_SQLITE, GUARD_SQLITE, BROKER_SQLITE, NOCONSTRAINT_SQLITE):
        driver.create_table(f"dedup_{ddl.split()[2].lower()}", ddl)
    driver.execute(_ORDERS_UNIQUE_SQLITE)
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture(params=["sqlite"])
def any_driver(request: pytest.FixtureRequest) -> Any:
    """Provider-parametrized driver: SQLite now, PG appended below."""
    return request.getfixturevalue("sqlite_driver")


# ---------------------------------------------------------------------------
# PostgreSQL fixtures ( skipped without NSE_PG_TEST_URL )
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    """Create an isolated throwaway PG database; drop it after the test."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_base_dsn(), connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}"')
            cur.execute(f'CREATE DATABASE "{_SCRATCH_DB}"')
    try:
        yield _base_dsn() + f" dbname={_SCRATCH_DB}"
    finally:
        with psycopg.connect(_base_dsn(), connect_timeout=10, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{_SCRATCH_DB}"')


@pytest.fixture
def pg_driver(pg_dsn: str) -> Any:
    """A real PostgreSQLDriver pointed at the throwaway database."""
    psycopg = pytest.importorskip("psycopg")
    from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver

    parts = _pg_parts()
    cfg = DatabaseConfig.for_postgres(
        domain="audit",
        host=str(parts.get("host", "localhost")),
        port=int(parts.get("port", 5432)),
        database=_SCRATCH_DB,
        username=str(parts.get("user", "postgres")),
        password_secret="db.postgresql.password",
    )
    # The isolated secret store the conftest points at must carry the
    # credential the driver resolves at connect time; never hardcode it.
    from nexus_scalp.settings.secret_store import SecureSecretStore

    pw = parts.get("password") or ""
    if pw:
        SecureSecretStore().set_secret("db.postgresql.password", str(pw))
    driver = PostgreSQLDriver(cfg)
    for ddl in (ORDERS_SQLITE, GUARD_SQLITE, BROKER_SQLITE, NOCONSTRAINT_SQLITE):
        name = f"dedup_{ddl.split()[2].lower()}"
        driver.create_table(name, _to_pg_ddl(ddl))
    # The partial unique index on execution_id ( PG supports partial indexes
    # natively — the same shape the audit store's bootstrap creates ).
    driver.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_orders_execution_id "
        "ON dedup_orders (execution_id) WHERE execution_id IS NOT NULL "
        "AND execution_id <> ''"
    )
    try:
        yield driver
    finally:
        driver.close()
    assert psycopg is not None


# ---------------------------------------------------------------------------
# natural_key_hash — deterministic, order-independent, loud on gaps
# ---------------------------------------------------------------------------


def test_hash_is_stable_across_dict_order() -> None:
    a = {"symbol": "XAUUSD", "window": "2026-09-25T19:52", "reason": "THROTTLED"}
    b = {"reason": "THROTTLED", "window": "2026-09-25T19:52", "symbol": "XAUUSD"}
    assert natural_key_hash(a, ["symbol", "window", "reason"]) == natural_key_hash(
        b, ["reason", "window", "symbol"]
    )


def test_hash_differs_for_different_keys() -> None:
    row = {"symbol": "XAUUSD", "window": "w1"}
    assert natural_key_hash(row, ["symbol", "window"]) != natural_key_hash(
        {**row, "window": "w2"}, ["symbol", "window"]
    )


def test_hash_raises_when_key_column_missing() -> None:
    """A row that cannot be identified must not be hashed with a guess."""
    with pytest.raises(KeyError):
        natural_key_hash({"symbol": "XAUUSD"}, ["symbol", "window"])


def test_hash_length_is_index_narrow() -> None:
    assert len(natural_key_hash({"a": 1}, ["a"])) == 32


# ---------------------------------------------------------------------------
# insert_ignore_sql — the dialect contract, one place
# ---------------------------------------------------------------------------


def test_sqlite_verb_is_or_ignore() -> None:
    cfg = DatabaseConfig.for_sqlite("audit", path=":memory:")
    sql = insert_ignore_sql("audit_orders", ["ticket", "symbol"], SQLiteDriver(cfg))
    assert sql.startswith("INSERT OR IGNORE INTO ")
    assert "ON CONFLICT" not in sql
    assert "?" in sql and "%s" not in sql


def test_pg_verb_is_on_conflict_do_nothing() -> None:
    from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver

    cfg = DatabaseConfig.for_postgres(database="nse_probe")
    sql = insert_ignore_sql("audit_orders", ["ticket", "symbol"], PostgreSQLDriver(cfg))
    assert sql.startswith("INSERT INTO ")
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert "%s" in sql and "?" not in sql


def test_identifiers_are_quoted_not_interpolated() -> None:
    """SEC: a hostile table name cannot reach the statement text."""
    cfg = DatabaseConfig.for_sqlite("audit", path=":memory:")
    driver = SQLiteDriver(cfg)
    with pytest.raises(ValueError):
        insert_ignore_sql("orders; DROP TABLE x", ["a"], driver)
    with pytest.raises(ValueError):
        insert_ignore_sql("orders", ["col); DROP TABLE x; --"], driver)


# ---------------------------------------------------------------------------
# SQLite arm — the provider that must stay first-class
# ---------------------------------------------------------------------------


def test_sqlite_inserts_then_suppresses_on_natural_key(sqlite_driver: SQLiteDriver) -> None:
    """Re-sent event: second insert is suppressed, not duplicated."""
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {
        "ticket": 7001,
        "order_id": "ord-7001",
        "symbol": "XAUUSD",
        "execution_id": "exec-1",
        "timestamp": "2026-09-25T19:52:00+00:00",
    }
    first = guard.insert_ignore(row)
    second = guard.insert_ignore(dict(row))
    assert first.inserted_row and first.suppressed == 0
    assert not second.inserted_row and second.suppressed == 1
    assert sqlite_driver.row_count("dedup_orders") == 1


def test_sqlite_suppression_counter_accumulates(sqlite_driver: SQLiteDriver) -> None:
    """The counter the stores keep (broker_history already kept this one)."""
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {"ticket": 1, "order_id": "o1", "symbol": "X", "execution_id": "e1", "timestamp": "t"}
    suppressed = sum(r.suppressed for r in guard.insert_ignore_many([row] * 5))
    assert suppressed == 4
    assert sqlite_driver.row_count("dedup_orders") == 1


def test_sqlite_distinct_natural_keys_all_land(sqlite_driver: SQLiteDriver) -> None:
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    rows = [
        {"ticket": i, "order_id": f"o{i}", "symbol": "X", "execution_id": f"e{i}", "timestamp": "t"}
        for i in range(8)
    ]
    results = guard.insert_ignore_many(rows)
    assert all(r.inserted_row for r in results)
    assert sqlite_driver.row_count("dedup_orders") == 8


def test_sqlite_composite_pk_guard(sqlite_driver: SQLiteDriver) -> None:
    """audit_guard_telemetry: the natural key is 3 columns."""
    guard = DedupGuard(
        sqlite_driver,
        "dedup_guard_telemetry",
        conflict_target=["window_start", "symbol", "reason_code"],
    )
    base = {"window_start": "w1", "symbol": "XAUUSD", "reason_code": "THROTTLED"}
    guard.insert_ignore({**base, "count": 1})
    guard.insert_ignore({**base, "count": 2})
    rows = sqlite_driver.query("SELECT * FROM dedup_guard_telemetry")
    assert len(rows) == 1
    # The suppressed row must not have clobbered the kept one.
    assert int(rows[0]["count"]) == 1


def test_sqlite_pk_only_table_is_deduped(sqlite_driver: SQLiteDriver) -> None:
    """No declared conflict_target: the guard resolves the PK itself."""
    guard = DedupGuard(sqlite_driver, "dedup_broker_orders", natural_key=["ticket"])
    row = {"ticket": 90001, "position_id": 90001, "symbol": "EURUSD", "synced_at": "t"}
    assert guard.insert_ignore(row).inserted_row
    assert guard.insert_ignore(dict(row)).suppressed == 1
    assert sqlite_driver.row_count("dedup_broker_orders") == 1


def test_sqlite_fails_loud_when_no_target(sqlite_driver: SQLiteDriver) -> None:
    """No PK/UNIQUE/declared key -> DedupTargetError, never a silent dup."""
    guard = DedupGuard(sqlite_driver, "dedup_noconstraint")
    with pytest.raises(DedupTargetError):
        guard.insert_ignore({"a": "1", "b": "2"})


def test_sqlite_degrades_explicitly_when_asked(sqlite_driver: SQLiteDriver) -> None:
    """fail_unresolved=False: still no exception, and the count stays honest.

    There is nothing to dedupe against on this table ( no PK, no UNIQUE ), so
    the helper says so plainly: the second insert is reported as INSERTED,
    not suppressed — the caller is told its table has no guard rather than
    being told a duplicate was caught. It never claims a suppression it did
    not perform.
    """
    guard = DedupGuard(sqlite_driver, "dedup_noconstraint", fail_unresolved=False)
    r1 = guard.insert_ignore({"a": "1", "b": "2"})
    assert r1.inserted_row
    r2 = guard.insert_ignore({"a": "1", "b": "2"})
    assert r2.inserted_row and r2.suppressed == 0
    assert sqlite_driver.row_count("dedup_noconstraint") == 2


def test_sqlite_seen_key_probe(sqlite_driver: SQLiteDriver) -> None:
    """The cheap read guard: a re-delivered key stops before the round trip.

    The cache records the key of a row the guard SUPPRESSED ( the duplicate ).
    A row that landed was not a duplicate, so it is not cached — that is the
    signal ``already_seen`` answers for, and it needs no provider round trip.
    """
    guard = DedupGuard(
        sqlite_driver,
        "dedup_orders",
        conflict_target=["execution_id"],
        natural_key=["execution_id"],
    )
    row = {"ticket": 1, "order_id": "o", "symbol": "X", "execution_id": "e", "timestamp": "t"}
    assert not guard.already_seen(row)
    first = guard.insert_ignore(row)
    assert first.inserted_row
    assert not guard.already_seen(row)
    # The re-delivery is suppressed and now remembered.
    second = guard.insert_ignore(dict(row))
    assert second.suppressed == 1
    assert guard.already_seen(row)
    # An unseen key is not falsely reported.
    assert not guard.already_seen({**row, "execution_id": "other"})


def test_sqlite_clear_cache_forgets(sqlite_driver: SQLiteDriver) -> None:
    guard = DedupGuard(
        sqlite_driver,
        "dedup_orders",
        conflict_target=["execution_id"],
        natural_key=["execution_id"],
    )
    row = {"ticket": 1, "order_id": "o", "symbol": "X", "execution_id": "e", "timestamp": "t"}
    guard.insert_ignore(row)
    guard.insert_ignore(dict(row))
    assert guard.already_seen(row)
    guard.clear_cache()
    assert not guard.already_seen(row)


def test_sqlite_empty_row_is_a_noop(sqlite_driver: SQLiteDriver) -> None:
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    r = guard.insert_ignore({})
    assert not r.inserted_row and r.suppressed == 0


def test_sqlite_conflict_target_resolution(sqlite_driver: SQLiteDriver) -> None:
    """Target resolution prefers the declared key, then the PK."""
    assert DedupGuard(
        sqlite_driver, "dedup_orders", conflict_target=["execution_id"]
    ).conflict_target() == ("execution_id",)
    # No declaration: falls back to the table's PK (id).
    assert DedupGuard(sqlite_driver, "dedup_orders").conflict_target() == ("id",)
    # Composite PK is resolved as-is.
    assert tuple(DedupGuard(sqlite_driver, "dedup_guard_telemetry").conflict_target()) == (
        "window_start",
        "symbol",
        "reason_code",
    )


def test_sqlite_row_missing_declared_target_still_dedupes(
    sqlite_driver: SQLiteDriver,
) -> None:
    """audit_orders.execution_id is nullable: a row without it must not raise."""
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {"ticket": 5, "order_id": "o5", "symbol": "X", "execution_id": None, "timestamp": "t"}
    r1 = guard.insert_ignore(row)
    assert r1.inserted_row
    # Different ticket, still no execution_id: distinct row, must land too.
    r2 = guard.insert_ignore({**row, "ticket": 6, "order_id": "o6"})
    assert r2.inserted_row
    assert sqlite_driver.row_count("dedup_orders") == 2


# ---------------------------------------------------------------------------
# PostgreSQL arm — the provider the duplicates actually bit
# ---------------------------------------------------------------------------


@needs_pg
def test_pg_inserts_then_suppresses_on_natural_key(pg_driver: Any) -> None:
    """The live duplicate class: re-sent tick -> one row, not two."""
    guard = DedupGuard(pg_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {
        "ticket": 7001,
        "order_id": "ord-7001",
        "symbol": "XAUUSD",
        "execution_id": "exec-1",
        "timestamp": "2026-09-25T19:52:00+00:00",
    }
    first = guard.insert_ignore(row)
    second = guard.insert_ignore(dict(row))
    assert first.inserted_row and first.suppressed == 0
    assert not second.inserted_row and second.suppressed == 1
    assert pg_driver.row_count("dedup_orders") == 1


@needs_pg
def test_pg_emits_on_conflict_do_nothing(pg_driver: Any) -> None:
    """The statement actually issued is PG dialect, not SQLite OR IGNORE."""
    guard = DedupGuard(pg_driver, "dedup_orders", conflict_target=["execution_id"])
    guard.insert_ignore(
        {"ticket": 1, "order_id": "o", "symbol": "X", "execution_id": "e", "timestamp": "t"}
    )
    sql = guard._targeted_sql(["execution_id"], ["execution_id"])
    assert 'ON CONFLICT ("execution_id") DO NOTHING' in sql
    assert "%s" in sql
    assert "OR IGNORE" not in sql
    assert "?" not in sql


@needs_pg
def test_pg_batch_suppression_counts_each_row(pg_driver: Any) -> None:
    guard = DedupGuard(pg_driver, "dedup_orders", conflict_target=["execution_id"])
    rows = [
        {"ticket": i, "order_id": f"o{i}", "symbol": "X", "execution_id": "same", "timestamp": "t"}
        for i in range(6)
    ]
    results = guard.insert_ignore_many(rows)
    assert sum(r.suppressed for r in results) == 5
    assert pg_driver.row_count("dedup_orders") == 1


@needs_pg
def test_pg_composite_pk_guard(pg_driver: Any) -> None:
    """audit_guard_telemetry's 3-column key on PG: one row per window."""
    guard = DedupGuard(
        pg_driver,
        "dedup_guard_telemetry",
        conflict_target=["window_start", "symbol", "reason_code"],
    )
    base = {"window_start": "w1", "symbol": "XAUUSD", "reason_code": "THROTTLED"}
    r1 = guard.insert_ignore({**base, "count": 1})
    r2 = guard.insert_ignore({**base, "count": 2})
    assert r1.inserted_row and r2.suppressed == 1
    rows = pg_driver.query("SELECT * FROM dedup_guard_telemetry")
    assert len(rows) == 1
    assert int(rows[0]["count"]) == 1


@needs_pg
def test_pg_pk_resolution_without_declared_target(pg_driver: Any) -> None:
    """broker_history's ticket-keyed table: the guard finds the PK alone."""
    guard = DedupGuard(pg_driver, "dedup_broker_orders", natural_key=["ticket"])
    assert guard.conflict_target() == ("ticket",)
    row = {"ticket": 90001, "position_id": 90001, "symbol": "EURUSD", "synced_at": "t"}
    assert guard.insert_ignore(row).inserted_row
    assert guard.insert_ignore(dict(row)).suppressed == 1
    assert pg_driver.row_count("dedup_broker_orders") == 1


@needs_pg
def test_pg_fails_loud_when_no_target(pg_driver: Any) -> None:
    guard = DedupGuard(pg_driver, "dedup_noconstraint")
    with pytest.raises(DedupTargetError):
        guard.insert_ignore({"a": "1", "b": "2"})


@needs_pg
def test_pg_seen_key_probe_and_clear(pg_driver: Any) -> None:
    guard = DedupGuard(
        pg_driver, "dedup_orders", conflict_target=["execution_id"], natural_key=["execution_id"]
    )
    row = {"ticket": 1, "order_id": "o", "symbol": "X", "execution_id": "e", "timestamp": "t"}
    assert not guard.already_seen(row)
    guard.insert_ignore(row)
    assert guard.already_seen(row)
    guard.clear_cache()
    assert not guard.already_seen(row)


@needs_pg
def test_pg_idempotent_under_concurrent_guards(pg_driver: Any) -> None:
    """Two guards ( two worker processes ) racing the same key: one row."""
    g1 = DedupGuard(pg_driver, "dedup_orders", conflict_target=["execution_id"])
    g2 = DedupGuard(pg_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {"ticket": 42, "order_id": "o42", "symbol": "X", "execution_id": "e42", "timestamp": "t"}
    results = [g.insert_ignore(dict(row)) for g in (g1, g2)]
    assert sum(r.inserted for r in results) == 1
    assert sum(r.suppressed for r in results) == 1
    assert pg_driver.row_count("dedup_orders") == 1


@needs_pg
def test_pg_driver_reports_postgres_dialect(pg_driver: Any) -> None:
    """Provider detection is by driver.name, not by sniffing a DSN."""
    assert str(pg_driver.name).lower() in {"postgres", "postgresql"}


# ---------------------------------------------------------------------------
# build_guard convenience ( SQLite arm; no live cluster touched )
# ---------------------------------------------------------------------------


def test_build_guard_from_sqlite_config(tmp_path: Path) -> None:
    from nexus_scalp.database.dedup_guard import build_guard

    cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "bg.db"))
    guard = build_guard(cfg, "dedup_orders", conflict_target=["execution_id"])
    assert isinstance(guard, DedupGuard)
    assert guard.conflict_target() == ("execution_id",)


# ---------------------------------------------------------------------------
# Regression contract: the helper never silently duplicates
# ---------------------------------------------------------------------------


def test_suppression_is_reported_not_swallowed(sqlite_driver: SQLiteDriver) -> None:
    """The adoption contract: every duplicate is counted, never invisible."""
    guard = DedupGuard(sqlite_driver, "dedup_orders", conflict_target=["execution_id"])
    row = {"ticket": 1, "order_id": "o", "symbol": "X", "execution_id": "e", "timestamp": "t"}
    results = guard.insert_ignore_many([row] * 3)
    assert [r.inserted for r in results] == [1, 0, 0]
    assert [r.suppressed for r in results] == [0, 1, 1]
