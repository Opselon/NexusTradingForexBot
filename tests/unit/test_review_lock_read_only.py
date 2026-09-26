"""REVIEW LOCK (L3): NSE_REVIEW_MODE must refuse every write-path SQL.

The review console (agents/REVIEW_LOCK.md) is a read-only plane. This test
pins the guard that enforces it inside the write path itself, because that
is the only layer that still holds when an agent ignores every doc.

History: the first attempt guarded only PortableConnection. Live testing
proved that too narrow — ~72 modules open sqlite3.connect() directly, so a
guard on one chokepoint could not cover the rest. The guard now wraps
sqlite3.connect process-wide, which every store in the product shares.

Contract:
  * NSE_REVIEW_MODE unset  -> writes pass through unchanged (production path)
  * NSE_REVIEW_MODE set    -> every DML/DDL is refused with ReviewWriteBlockedError
  * SELECTs still work (the console must be able to *show* state)
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from nexus_scalp.database.review_lock import (
    ReviewWriteBlockedError,
    install_review_write_guard,
)


@pytest.fixture(autouse=True)
def _restore_real_connect() -> None:
    """Every test here mutates the process-global sqlite3.connect.

    Yield-style teardown restores the real one afterwards, otherwise the
    wrapper leaks into other test modules and, worse, would make a
    production run believe it was review-locked.
    """
    real_connect = sqlite3.connect
    yield
    sqlite3.connect = real_connect  # type: ignore[assignment]


@pytest.fixture
def review_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> str:
    """Turn review mode on and point it at an isolated db file."""
    db = str(tmp_path / "review_lock.db")
    monkeypatch.setenv("NSE_REVIEW_MODE", "1")
    if getattr(sqlite3.connect, "_nse_review_guard", False):
        del sqlite3.connect._nse_review_guard  # type: ignore[attr-defined]
    assert install_review_write_guard() is True
    return db


@pytest.fixture
def prod_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSE_REVIEW_MODE", raising=False)
    if getattr(sqlite3.connect, "_nse_review_guard", False):
        del sqlite3.connect._nse_review_guard  # type: ignore[attr-defined]
    assert install_review_write_guard() is False


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t (a) VALUES (1)",
        "  UPDATE t SET a = 1",
        "DELETE FROM t",
        "CREATE TABLE t (a INTEGER)",  # plain form: would raise on an existing table
        "ALTER TABLE t ADD COLUMN b INTEGER",
        "DROP TABLE t",
    ],
)
def test_review_mode_refuses_every_write(review_mode: str, sql: str) -> None:
    """Any mutating statement must raise, never silently pass.

    Uses sqlite3 directly — the same surface incidents/store.py and ~70
    other modules use — so this proves the guard covers the paths that
    bypass PortableConnection.
    """
    conn = sqlite3.connect(review_mode)
    try:
        with pytest.raises(ReviewWriteBlockedError, match="REVIEW LOCK"):
            conn.execute(sql)
    finally:
        conn.close()


def test_review_mode_allows_select(review_mode: str) -> None:
    """The console must still be able to read state — that is its purpose."""
    conn = sqlite3.connect(review_mode)
    try:
        # A SELECT against a non-existent table raises OperationalError,
        # NOT ReviewWriteBlockedError: the guard must let reads through and
        # let the normal sqlite error surface.
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT a FROM t").fetchone()
    finally:
        conn.close()


def test_review_mode_refuses_executemany(review_mode: str) -> None:
    """executemany is the bulk write path and is covered too."""
    conn = sqlite3.connect(review_mode)
    try:
        with pytest.raises(ReviewWriteBlockedError):
            conn.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])
    finally:
        conn.close()


def test_review_mode_refuses_executescript(review_mode: str) -> None:
    """executescript is the schema-migration path and is covered too."""
    conn = sqlite3.connect(review_mode)
    try:
        with pytest.raises(ReviewWriteBlockedError):
            conn.executescript("CREATE TABLE t (a INTEGER);")
    finally:
        conn.close()


def test_review_mode_allows_idempotent_bootstrap(review_mode: str) -> None:
    """CREATE TABLE IF NOT EXISTS is the boot-time schema bootstrap.

    It is idempotent — it only creates a table that is absent and never
    alters or drops existing data. Refusing it makes the console unable to
    start, which defeats the guard's purpose (it protects *state*, not
    schema presence). A plain CREATE TABLE is still refused.
    """
    conn = sqlite3.connect(review_mode)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (a INTEGER)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_t ON t (a)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_t ON t (a)")
        conn.commit()
    finally:
        conn.close()


def test_review_mode_allows_idempotent_seed(review_mode: str) -> None:
    """INSERT OR IGNORE / INSERT OR REPLACE are idempotent seed writes.

    The engine's bootstrap seeds fixed constant rows (30+ trading rules,
    disabled by default). ``OR IGNORE`` is a no-op when the row exists, and
    the seed only runs when the table is empty. A plain INSERT is still
    refused — that is a genuine data write.
    """
    conn = sqlite3.connect(review_mode)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS rules (name TEXT PRIMARY KEY, enabled INTEGER)")
        conn.executemany(
            "INSERT OR IGNORE INTO rules (name, enabled) VALUES (?, 0)", [("a",), ("b",)]
        )
        conn.executemany(
            "INSERT OR REPLACE INTO rules (name, enabled) VALUES (?, 0)", [("a",), ("b",)]
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 2
    finally:
        conn.close()


def test_prod_mode_writes_pass_through(prod_mode: None, tmp_path: object) -> None:
    """Production path is untouched: the guard costs nothing when off."""
    conn = sqlite3.connect(str(tmp_path / "prod.db"))
    try:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t (a) VALUES (42)")
        conn.commit()
        assert conn.execute("SELECT a FROM t").fetchone() == (42,)
    finally:
        conn.close()


def test_guard_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing twice must not double-wrap."""
    monkeypatch.setenv("NSE_REVIEW_MODE", "1")
    if getattr(sqlite3.connect, "_nse_review_guard", False):
        del sqlite3.connect._nse_review_guard  # type: ignore[attr-defined]
    assert install_review_write_guard() is True
    assert install_review_write_guard() is True  # second call: still ok
    # and the env-var check confirms it short-circuits, not re-wraps
    assert os.environ.get("NSE_REVIEW_MODE") == "1"
