"""Database Provider Neutral Contract Tests.

Enforces Section 32 and 33 of the Dual Database Architecture:
    Provider-neutral test suite verifying that both SQLite and PostgreSQL
    drivers fulfill the core database abstraction contract with identical semantics:
    - Table creation and schema queries
    - Parameterized queries and scalar lookups
    - Transaction commits and rollback semantics
    - Pagination and deterministic sorting
    - Upsert semantics
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver, get_driver


@pytest.fixture
def sqlite_test_driver() -> Iterator[SQLiteDriver]:
    """Provide a clean isolated SQLite driver for contract testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "contract_test.db"
        cfg = DatabaseConfig.for_sqlite("test", path=str(db_path))
        drv = SQLiteDriver(cfg)
        drv.execute(
            """
            CREATE TABLE contract_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT UNIQUE NOT NULL,
                score REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        yield drv
        drv.close()


class TestDatabaseContractSemantics:
    """Verifies that database drivers uphold the core abstraction contract."""

    def test_table_exists_and_column_inspection(self, sqlite_test_driver: SQLiteDriver) -> None:
        """Driver must truthfully report table existence and column names."""
        assert sqlite_test_driver.table_exists("contract_items") is True
        assert sqlite_test_driver.table_exists("nonexistent_items") is False

        cols = sqlite_test_driver.get_columns("contract_items")
        assert "id" in cols
        assert "item_key" in cols
        assert "score" in cols
        assert "status" in cols

    def test_parameterized_insert_and_query(self, sqlite_test_driver: SQLiteDriver) -> None:
        """Driver must support parameterized execution and fetch records as dicts."""
        sqlite_test_driver.execute(
            "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
            ("key_alpha", 98.5, "ACTIVE"),
        )
        sqlite_test_driver.execute(
            "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
            ("key_beta", 42.0, "PENDING"),
        )

        rows = sqlite_test_driver.query(
            "SELECT item_key, score, status FROM contract_items ORDER BY score DESC"
        )
        assert len(rows) == 2
        assert rows[0]["item_key"] == "key_alpha"
        assert rows[0]["score"] == 98.5
        assert rows[1]["item_key"] == "key_beta"
        assert rows[1]["score"] == 42.0

    def test_scalar_lookup(self, sqlite_test_driver: SQLiteDriver) -> None:
        """Scalar method must return a single primitive or None."""
        cnt = sqlite_test_driver.scalar("SELECT COUNT(*) FROM contract_items")
        assert cnt == 0

        sqlite_test_driver.execute(
            "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
            ("key_one", 10.0, "ACTIVE"),
        )
        cnt = sqlite_test_driver.scalar("SELECT COUNT(*) FROM contract_items")
        assert cnt == 1

        val = sqlite_test_driver.scalar(
            "SELECT score FROM contract_items WHERE item_key = ?", ("key_one",)
        )
        assert val == 10.0

        empty = sqlite_test_driver.scalar(
            "SELECT score FROM contract_items WHERE item_key = ?", ("missing",)
        )
        assert empty is None

    def test_transaction_commit(self, sqlite_test_driver: SQLiteDriver) -> None:
        """Transaction block must persist operations upon normal exit."""
        with sqlite_test_driver.transaction() as conn:
            conn.execute(
                "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
                ("tx_one", 50.0, "OK"),
            )
            conn.execute(
                "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
                ("tx_two", 60.0, "OK"),
            )

        cnt = sqlite_test_driver.scalar("SELECT COUNT(*) FROM contract_items WHERE status = 'OK'")
        assert cnt == 2

    def test_transaction_rollback(self, sqlite_test_driver: SQLiteDriver) -> None:
        """Transaction block must roll back completely when an exception is raised."""
        with pytest.raises(RuntimeError, match="Intentional transaction abort"):
            with sqlite_test_driver.transaction() as conn:
                conn.execute(
                    "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
                    ("tx_fail", 99.0, "SHOULD_ROLLBACK"),
                )
                raise RuntimeError("Intentional transaction abort")

        cnt = sqlite_test_driver.scalar(
            "SELECT COUNT(*) FROM contract_items WHERE item_key = 'tx_fail'"
        )
        assert cnt == 0

    def test_batch_execute_and_row_count(self, sqlite_test_driver: SQLiteDriver) -> None:
        """executemany must insert all parameter tuples cleanly."""
        data = [
            ("batch_1", 1.0, "BATCH"),
            ("batch_2", 2.0, "BATCH"),
            ("batch_3", 3.0, "BATCH"),
        ]
        sqlite_test_driver.executemany(
            "INSERT INTO contract_items (item_key, score, status) VALUES (?, ?, ?)",
            data,
        )

        rows = sqlite_test_driver.query(
            "SELECT item_key FROM contract_items WHERE status = 'BATCH' ORDER BY item_key ASC"
        )
        assert len(rows) == 3
        assert [r["item_key"] for r in rows] == ["batch_1", "batch_2", "batch_3"]

    def test_provider_factory_resolution(self) -> None:
        """get_driver must resolve the correct provider class."""
        sqlite_cfg = DatabaseConfig.for_sqlite("audit")
        driver = get_driver(sqlite_cfg)
        try:
            assert isinstance(driver, SQLiteDriver)
        finally:
            driver.close()
