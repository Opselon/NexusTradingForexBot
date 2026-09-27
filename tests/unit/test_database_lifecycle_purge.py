"""Tests for Database Data Lifecycle, Purging, and Maintenance Subsystem.

Enforces Sections 19-25 of the Dual Database Architecture:
    - Data tier classification: HOT, WARM, COLD, PURGEABLE, IMMUTABLE.
    - Strict immutable protection on financial and audit records.
    - Domain-aware batched purging to eliminate bloat and lock contention.
    - Safe routine database maintenance (VACUUM / ANALYZE / integrity_check).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.lifecycle import (
    DatabaseLifecycleManager,
    DataTier,
    ImmutableDataProtectionError,
    PurgePolicy,
)


@pytest.fixture
def lifecycle_sqlite_driver() -> Iterator[SQLiteDriver]:
    """Create an isolated SQLite database with sample purgeable and immutable tables."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "lifecycle_test.db"
        cfg = DatabaseConfig.for_sqlite("audit", path=str(db_path))
        drv = SQLiteDriver(cfg)

        # Create purgeable table: audit_signals
        drv.execute(
            """
            CREATE TABLE audit_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                generated_at TEXT NOT NULL
            )
            """
        )
        # Create immutable table: audit_ledger
        drv.execute(
            """
            CREATE TABLE audit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                pnl REAL NOT NULL,
                closed_at TEXT NOT NULL
            )
            """
        )
        # Insert old and recent rows into audit_signals
        old_time = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        recent_time = datetime.now(UTC).isoformat()

        for _ in range(25):
            drv.execute(
                "INSERT INTO audit_signals (symbol, action, generated_at) VALUES (?, ?, ?)",
                ("EURUSD", "BUY", old_time),
            )
        for _ in range(10):
            drv.execute(
                "INSERT INTO audit_signals (symbol, action, generated_at) VALUES (?, ?, ?)",
                ("EURUSD", "SELL", recent_time),
            )

        # Insert records into audit_ledger
        for _ in range(5):
            drv.execute(
                "INSERT INTO audit_ledger (symbol, pnl, closed_at) VALUES (?, ?, ?)",
                ("EURUSD", 50.0, old_time),
            )

        yield drv
        drv.close()


class TestDataLifecycleTiersAndPolicies:
    """Verifies tier classifications and policies."""

    def test_data_tier_enums(self) -> None:
        """Data tiers must define operational lifecycle classifications."""
        assert DataTier.HOT.value == "HOT"
        assert DataTier.WARM.value == "WARM"
        assert DataTier.COLD.value == "COLD"
        assert DataTier.PURGEABLE.value == "PURGEABLE"
        assert DataTier.IMMUTABLE.value == "IMMUTABLE"

    def test_immutable_table_protection(self, lifecycle_sqlite_driver: SQLiteDriver) -> None:
        """Deleting from an immutable table must raise ImmutableDataProtectionError."""
        mgr = DatabaseLifecycleManager()
        pol = PurgePolicy(
            table_name="audit_ledger",
            retention_days=30,
            timestamp_column="closed_at",
            tier=DataTier.IMMUTABLE,
            description="Audit ledger",
        )
        with pytest.raises(
            ImmutableDataProtectionError, match="Financial and audit ledger records are immutable"
        ):
            mgr._purge_table_batched(
                lifecycle_sqlite_driver, pol.table_name, pol, datetime.now(UTC), batch_size=100
            )


class TestPurgeExecutionAndBatching:
    """Verifies safe batched deletion and preview calculation."""

    def test_purge_preview_and_batched_execution(
        self, lifecycle_sqlite_driver: SQLiteDriver
    ) -> None:
        """Preview must correctly identify 25 expired rows and delete them safely."""
        mgr = DatabaseLifecycleManager()
        pol = PurgePolicy(
            table_name="audit_signals",
            retention_days=30,
            timestamp_column="generated_at",
            tier=DataTier.PURGEABLE,
            description="Audit signals",
        )
        # Verify initial counts
        total_before = lifecycle_sqlite_driver.scalar("SELECT COUNT(*) FROM audit_signals")
        assert total_before == 35

        # Execute batched purge with small batch size (10)
        deleted = mgr._purge_table_batched(
            lifecycle_sqlite_driver, pol.table_name, pol, datetime.now(UTC), batch_size=10
        )
        assert deleted == 25

        # Verify only recent rows remain
        remaining = lifecycle_sqlite_driver.scalar("SELECT COUNT(*) FROM audit_signals")
        assert remaining == 10

        # Ledger records must remain completely untouched
        ledger_count = lifecycle_sqlite_driver.scalar("SELECT COUNT(*) FROM audit_ledger")
        assert ledger_count == 5

    def test_safe_maintenance_execution(self, lifecycle_sqlite_driver: SQLiteDriver) -> None:
        """Maintenance routine must run ANALYZE, VACUUM, and integrity checks cleanly."""
        mgr = DatabaseLifecycleManager()
        report = mgr.run_maintenance(driver=lifecycle_sqlite_driver)
        assert report["provider"] == "sqlite"
        assert report["status"] == "SUCCESS"
        assert report["integrity_check"] == "ok"

    def test_retention_policy_persists_across_manager_instances(self, tmp_path: Path) -> None:
        """Retention changes survive construction of a fresh manager."""
        settings_path = tmp_path / "settings.db"
        cfg = DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "audit.db"))
        first = DatabaseLifecycleManager(config=cfg, settings_path=settings_path)
        assert first.update_policy("audit_signals", 91)
        second = DatabaseLifecycleManager(config=cfg, settings_path=settings_path)
        assert second.policies["audit_signals"].retention_days == 91

    def test_update_immutable_policy_is_rejected(self, tmp_path: Path) -> None:
        """Immutable tables cannot be made configurable."""
        manager = DatabaseLifecycleManager(
            config=DatabaseConfig.for_sqlite("audit", path=str(tmp_path / "audit.db")),
            settings_path=tmp_path / "settings.db",
        )
        with pytest.raises(ImmutableDataProtectionError):
            manager.update_policy("audit_ledger", 1)

    def test_retention_policy_persists_between_managers(self, tmp_path: Path) -> None:
        """Retention updates survive construction of a fresh manager."""
        settings_path = tmp_path / "settings.db"
        first = DatabaseLifecycleManager(settings_path=settings_path)
        assert first.update_policy("audit_signals", 91)

        second = DatabaseLifecycleManager(settings_path=settings_path)
        assert second.policies["audit_signals"].retention_days == 91

    def test_immutable_policy_cannot_be_changed(self, tmp_path: Path) -> None:
        """Immutable tables remain protected even through policy updates."""
        manager = DatabaseLifecycleManager(settings_path=tmp_path / "settings.db")
        with pytest.raises(ImmutableDataProtectionError):
            manager.update_policy("audit_ledger", 1)
