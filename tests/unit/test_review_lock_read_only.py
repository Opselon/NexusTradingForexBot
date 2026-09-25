"""REVIEW LOCK (L3): NSE_REVIEW_MODE must refuse every write-path SQL.

The review console (agents/REVIEW_LOCK.md) is a read-only plane. This test
pins the guard that enforces it inside the write path itself, because that
is the only layer that still holds when an agent ignores every doc.

Contract:
  * NSE_REVIEW_MODE unset  -> writes pass through unchanged (production path)
  * NSE_REVIEW_MODE set    -> every DML/DDL is refused with ReviewWriteBlockedError
  * SELECTs still work (the console must be able to *show* state)
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.database.drivers.proxy import (
    PortableConnection,
    ReviewWriteBlockedError,
)
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver


def _connection(tmp_path: object) -> PortableConnection:
    """Build a PortableConnection over a real on-disk sqlite file.

    `DatabaseConfig.for_sqlite` is the public constructor; the file backend
    is used (not in-memory shared cache) so each test owns an isolated db.
    """
    from nexus_scalp.database.config import DatabaseConfig

    cfg = DatabaseConfig.for_sqlite(domain="review_lock", path=str(tmp_path / "review_lock.db"))
    driver = SQLiteDriver(cfg)
    return PortableConnection(driver)


@pytest.fixture
def review_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSE_REVIEW_MODE", "1")


@pytest.fixture
def prod_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSE_REVIEW_MODE", raising=False)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t (a) VALUES (1)",
        "  UPDATE t SET a = 1",
        "DELETE FROM t",
        "CREATE TABLE t (a INTEGER)",
        "ALTER TABLE t ADD COLUMN b INTEGER",
        "DROP TABLE t",
        "REPLACE INTO t (a) VALUES (1)",
    ],
)
def test_review_mode_refuses_every_write(tmp_path: object, review_mode: None, sql: str) -> None:
    """Any mutating statement must raise, never silently pass."""
    conn = _connection(tmp_path)
    with pytest.raises(ReviewWriteBlockedError, match="REVIEW LOCK"):
        conn.execute(sql)


def test_review_mode_allows_select(tmp_path: object, review_mode: None) -> None:
    """The console must still be able to read state — that is its purpose.

    The fixture data is inserted via a raw sqlite3 connection so the write
    never goes through the guarded path; only the read does.
    """
    conn = _connection(tmp_path)
    raw = sqlite3.connect(str(tmp_path / "review_lock.db"))
    raw.execute("CREATE TABLE t (a INTEGER)")
    raw.execute("INSERT INTO t (a) VALUES (1)")
    raw.commit()
    raw.close()
    row = conn.execute("SELECT a FROM t").fetchone()
    assert tuple(row) == (1,)


def test_review_mode_refuses_executemany(tmp_path: object, review_mode: None) -> None:
    """executemany is the bulk write path and is covered too."""
    conn = _connection(tmp_path)
    with pytest.raises(ReviewWriteBlockedError):
        conn.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])


def test_prod_mode_writes_pass_through(tmp_path: object, prod_mode: None) -> None:
    """Production path is untouched: the guard costs nothing when off."""
    conn = _connection(tmp_path)
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t (a) VALUES (42)")
    conn.commit()
    assert tuple(conn.execute("SELECT a FROM t").fetchone()) == (42,)
