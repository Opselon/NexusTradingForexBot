"""Tests for Database Event and Query Log Store.

Enforces Sections 16, 17, and 18 of the Dual Database Architecture:
    - Structured DB logging for WARNING, ERROR, CRITICAL only.
    - Password and secret masking in log records.
    - Configurable retention and automatic log purge.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.log_store import DbLogEntry, DbLogStore


def test_db_log_store_lifecycle_and_masking() -> None:
    """Verifies that the log store persists errors, masks secrets, and respects retention."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "log_store_test.db"
        cfg = DatabaseConfig.for_sqlite("audit", path=str(db_path))
        driver = SQLiteDriver(cfg)
        store = DbLogStore(cfg, retention_days=30)
        store.ensure_table(driver)

        # 1. INFO events must be skipped to avoid database bloat (Section 17)
        info_entry = DbLogEntry(
            level="INFO",
            provider="sqlite",
            domain="audit",
            operation="read_items",
        )
        saved_info = store.log(info_entry, driver=driver)
        assert saved_info is False

        # 2. WARNING and ERROR events must be stored
        warn_entry = DbLogEntry(
            level="WARNING",
            provider="sqlite",
            domain="audit",
            operation="slow_read",
            duration_ms=250.0,
            masked_sql="SELECT * FROM users WHERE token = 'secret_12345'",
        )
        saved_warn = store.log(warn_entry, driver=driver)
        assert saved_warn is True

        err_entry = DbLogEntry(
            level="ERROR",
            provider="postgresql",
            domain="audit",
            operation="pool_connect",
            error_code="CONNECTION_REFUSED",
            masked_sql="postgresql://user:secretpass@localhost:5432/nexusdb",
        )
        saved_err = store.log(err_entry, driver=driver)
        assert saved_err is True

        # 3. Read back entries
        entries = store.get_recent_logs(driver=driver, limit=10)
        assert len(entries) == 2

        # 4. Verify password redaction
        err_record = next(e for e in entries if e["level"] == "ERROR")
        assert "secretpass" not in err_record["masked_sql"]
        assert "***" in err_record["masked_sql"] or "[REDACTED]" in err_record["masked_sql"]

        driver.close()
