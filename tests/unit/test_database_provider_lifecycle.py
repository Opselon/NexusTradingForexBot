"""Tests for Database Provider Lifecycle, Switching State Machine, and Reverse Migration.

Enforces Sections 13 and 14 of the Dual Database Architecture:
    - Provider switching is a formal multi-phase lifecycle state machine.
    - Reverse migration streams operational tables safely from PostgreSQL to SQLite.
    - Divergence checks warn if operational data exists in PostgreSQL not in SQLite.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import SQLiteDriver
from nexus_scalp.database.migrate_engine import MigrationOptions
from nexus_scalp.database.migrate_reverse import PostgresToSqliteMigrator
from nexus_scalp.database.provider_lifecycle import (
    ProviderLifecycleManager,
    ProviderSwitchPhase,
)


class TestProviderLifecycleStateMachine:
    """Verifies state machine transitions and safety guards."""

    def test_initial_state_detection(self) -> None:
        """Manager must detect active provider from environment/settings."""
        mgr = ProviderLifecycleManager()
        st = mgr.get_state()
        assert st.active_provider in {"sqlite", "postgresql"}
        assert st.target_provider == st.active_provider
        assert st.phase in {ProviderSwitchPhase.CONFIGURED, ProviderSwitchPhase.ACTIVE}

    def test_transition_start_and_phases(self) -> None:
        """Starting transition moves phase to CONFIGURED toward target."""
        mgr = ProviderLifecycleManager()
        st = mgr.start_transition("postgresql")
        assert st.target_provider == "postgresql"
        assert st.phase == ProviderSwitchPhase.CONFIGURED

    def test_activation_rejected_if_not_ready(self) -> None:
        """Confirming activation must fail if phase has not reached READY."""
        mgr = ProviderLifecycleManager()
        mgr.start_transition("postgresql")
        # In CONFIGURED phase, activation without force must be rejected
        ok = mgr.confirm_activation(force=False)
        assert ok is False
        assert mgr.get_state().phase != ProviderSwitchPhase.ACTIVE

    def test_activation_bypass_is_rejected(self) -> None:
        """Activation cannot bypass target testing, migration, and verification."""
        mgr = ProviderLifecycleManager()
        mgr.start_transition("sqlite")
        assert mgr.confirm_activation() is False
        assert mgr.get_state().phase != ProviderSwitchPhase.ACTIVE

    def test_activation_requires_non_divergent_evidence(self) -> None:
        mgr = ProviderLifecycleManager()
        mgr.start_transition("sqlite")
        mgr._state.last_test_passed = True
        mgr._state.last_migration_passed = True
        mgr._state.last_verification_passed = True
        mgr.mark_verification(True)
        mgr._state.divergence = None
        assert mgr.confirm_activation() is False


class TestPostgresToSqliteMigrator:
    """Verifies streaming batch migration from PostgreSQL to SQLite."""

    def test_reverse_migration_table_copy_and_verification(self) -> None:
        """Migrator must copy tables, rows, and verify financial totals."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src_db = Path(tmpdir) / "source_mock_pg.db"
            dst_db = Path(tmpdir) / "dest_sqlite.db"

            # Create mock source tables using SQLite driver to emulate PostgreSQL
            src_cfg = DatabaseConfig.for_sqlite("audit", path=str(src_db))
            src_drv = SQLiteDriver(src_cfg)
            src_drv.execute(
                """
                CREATE TABLE audit_signals (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL NOT NULL
                )
                """
            )
            src_drv.execute(
                """
                CREATE TABLE audit_ledger (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    pnl REAL NOT NULL,
                    closed_at TEXT NOT NULL
                )
                """
            )
            # Insert test records
            for i in range(15):
                src_drv.execute(
                    "INSERT INTO audit_signals (id, symbol, action, confidence) VALUES (?, ?, ?, ?)",
                    (i + 1, "EURUSD", "BUY", 0.85),
                )
            src_drv.execute(
                "INSERT INTO audit_ledger (id, symbol, pnl, closed_at) VALUES (?, ?, ?, ?)",
                (1, "EURUSD", 125.50, "2026-09-27T10:00:00Z"),
            )
            src_drv.execute(
                "INSERT INTO audit_ledger (id, symbol, pnl, closed_at) VALUES (?, ?, ?, ?)",
                (2, "GBPUSD", -25.50, "2026-09-27T10:05:00Z"),
            )
            src_drv.close()

            # Create destination database
            dst_cfg = DatabaseConfig.for_sqlite("audit", path=str(dst_db))
            dst_drv = SQLiteDriver(dst_cfg)
            # Schema bootstrap
            dst_drv.execute(
                """
                CREATE TABLE audit_signals (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL NOT NULL
                )
                """
            )
            dst_drv.execute(
                """
                CREATE TABLE audit_ledger (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    pnl REAL NOT NULL,
                    closed_at TEXT NOT NULL
                )
                """
            )
            dst_drv.close()

            opts = MigrationOptions(batch_size=5, validate_checksums=True)
            migrator = PostgresToSqliteMigrator(src_cfg, dst_cfg, opts)
            report = migrator.run()

            assert report.status == "SUCCESS"
            assert report.tables_migrated >= 2
            assert report.rows_migrated >= 17

            # Verify rows in destination
            verify_drv = SQLiteDriver(dst_cfg)
            sig_cnt = verify_drv.scalar("SELECT COUNT(*) FROM audit_signals")
            assert sig_cnt == 15
            pnl_sum = verify_drv.scalar("SELECT SUM(pnl) FROM audit_ledger")
            assert abs(float(pnl_sum or 0.0) - 100.0) < 1e-4
            verify_drv.close()


class TestDivergenceDetection:
    """Verifies operational divergence checks when returning to SQLite."""

    def test_divergence_warning_when_pg_has_extra_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Divergence check warns if operational rows exist in source not in target."""
        mgr = ProviderLifecycleManager()
        # Mock drivers
        mock_src = MagicMock()
        mock_dst = MagicMock()

        mock_src.table_exists.return_value = True
        mock_dst.table_exists.return_value = True

        mock_src.scalar.return_value = 100
        mock_dst.scalar.return_value = 50

        # Inject mock drivers via monkeypatch
        monkeypatch.setattr(
            mgr, "_get_driver", lambda cfg: mock_src if cfg.is_postgresql else mock_dst
        )

        div = mgr.check_divergence(
            src_cfg=DatabaseConfig.for_postgres("audit"),
            dst_cfg=DatabaseConfig.for_sqlite("audit"),
        )
        assert div.diverged is True
        assert div.unmigrated_rows_estimate > 0
        assert "warning" in div.warning.lower()
