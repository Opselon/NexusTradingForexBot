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
        ok = mgr.confirm_activation()
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

    def test_transition_state_is_shared_between_manager_instances(self, tmp_path: Path) -> None:
        """A new request manager must observe the persisted transition state."""
        settings_db = str(tmp_path / "settings.db")
        first = ProviderLifecycleManager(settings_db_path=settings_db)
        first.start_transition("postgresql")

        second = ProviderLifecycleManager(settings_db_path=settings_db)
        state = second.get_state()

        assert state.target_provider == "postgresql"
        assert state.phase == ProviderSwitchPhase.CONFIGURED

    def test_activation_route_contract_requires_ready_state(self, tmp_path: Path) -> None:
        """Persisted state cannot be activated until verification reaches READY."""
        settings_db = str(tmp_path / "settings.db")
        mgr = ProviderLifecycleManager(settings_db_path=settings_db)
        mgr.start_transition("postgresql")

        assert mgr.confirm_activation() is False
        assert (
            ProviderLifecycleManager(settings_db_path=settings_db).get_state().phase
            == ProviderSwitchPhase.CONFIGURED
        )

        mgr._state.last_test_passed = True
        mgr._state.last_migration_passed = True
        mgr.mark_verification(True)
        assert ProviderLifecycleManager(settings_db_path=settings_db).confirm_activation() is False
        assert (
            ProviderLifecycleManager(settings_db_path=settings_db).get_state().phase
            == ProviderSwitchPhase.READY
        )


class TestPostgresToSqliteMigrator:
    """Verifies streaming batch migration from PostgreSQL to SQLite."""

    def test_reverse_schema_create_table_regex_matches_real_ddl(self) -> None:
        """The owner-detection regex must match the DDL it is written against.

        ``r\"\\s\"`` is a literal backslash-s and matches nothing, so an
        over-escaped pattern silently emptied the owner table set and skipped
        every auxiliary schema (DB-FABRIC reverse migration: missing
        ``shadow70_drift_alerts`` in the SQLite destination).
        """
        from nexus_scalp.database.migrate_reverse import _CREATE_TABLE_RE

        for ddl, expected in (
            ("CREATE TABLE IF NOT EXISTS shadow70_drift_alerts (id INTEGER)",
             "shadow70_drift_alerts"),
            ("CREATE TABLE model_governance_state (model_id TEXT)",
             "model_governance_state"),
            ('CREATE TABLE IF NOT EXISTS "learning_cycles" (id INTEGER)',
             "learning_cycles"),
        ):
            match = _CREATE_TABLE_RE.search(ddl)
            assert match is not None, f"pattern failed to match: {ddl}"
            assert match.group(1) == expected

    def test_strategy_factory_schema_statements_cover_research_meta(self) -> None:
        """The factory owner must provision its whole declared table set.

        The statements are the reverse-migration source of truth for the
        strategy_factory owner, so a table the store declares must appear in
        them: ``strategy_research_meta`` is declared by
        ``research_store.ALL_DDL`` but was missing from the older
        ``factory.store._SCHEMA`` transcription, so a PostgreSQL audit database
        that had it could not be reverse-migrated ("Table
        strategy_research_meta does not exist in SQLite destination").
        """
        from nexus_scalp.database.migrate_reverse import _CREATE_TABLE_RE
        from nexus_scalp.database.migration.schema_snapshot import (
            strategy_factory_schema_statements,
        )
        from nexus_scalp.strategies.research_store import ALL_DDL, TABLES

        statements = strategy_factory_schema_statements()
        created = {
            _CREATE_TABLE_RE.search(stmt).group(1).casefold()
            for stmt in statements
            if _CREATE_TABLE_RE.search(stmt)
        }
        # every table the owning store declares must be provisioned
        assert {table.casefold() for table in TABLES} <= created
        assert "strategy_research_meta" in created
        # the extractor must not double-emit a statement per table
        assert len(created) == len({table.casefold() for table in TABLES})

    def test_reverse_schema_replay_provisional_owner_when_source_has_table(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A source-present ops_shadow table must be provisioned downstream.

        Regression for the over-escaped owner-detection regex: the destination
        ended up without ``shadow70_drift_alerts`` and the copy aborted with
        "does not exist in SQLite destination".
        """
        src_db = tmp_path / "source.db"
        dst_db = tmp_path / "destination.db"
        src_cfg = DatabaseConfig.for_sqlite("audit", path=str(src_db))
        dst_cfg = DatabaseConfig.for_sqlite("audit", path=str(dst_db))

        source = SQLiteDriver(src_cfg)
        source.execute(
            "CREATE TABLE IF NOT EXISTS shadow70_drift_alerts ("
            "id INTEGER PRIMARY KEY, alert_id TEXT NOT NULL)"
        )
        source.execute(
            "INSERT INTO shadow70_drift_alerts (id, alert_id) VALUES (?, ?)",
            (1, "alert-1"),
        )
        source.close()

        destination = SQLiteDriver(dst_cfg)
        destination.close()

        from nexus_scalp.database.migration import schema_snapshot

        monkeypatch.setattr(
            schema_snapshot,
            "ops_shadow_schema_statements",
            lambda: (
                "CREATE TABLE IF NOT EXISTS shadow70_drift_alerts ("
                "id INTEGER PRIMARY KEY, alert_id TEXT NOT NULL)",
            ),
            raising=False,
        )

        report = PostgresToSqliteMigrator(
            src_cfg,
            dst_cfg,
            MigrationOptions(batch_size=10, validate_checksums=True),
        ).run()

        assert report.status == "SUCCESS"
        verify = SQLiteDriver(dst_cfg)
        try:
            assert verify.table_exists("shadow70_drift_alerts")
            assert (
                verify.scalar(
                    "SELECT alert_id FROM shadow70_drift_alerts WHERE id = ?",
                    (1,),
                )
                == "alert-1"
            )
        finally:
            verify.close()

    def test_reverse_schema_replay_skips_existing_add_column(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Existing additive columns must not break reverse migration bootstrap."""
        src_db = tmp_path / "source.db"
        dst_db = tmp_path / "destination.db"
        src_cfg = DatabaseConfig.for_sqlite("audit", path=str(src_db))
        dst_cfg = DatabaseConfig.for_sqlite("audit", path=str(dst_db))

        source = SQLiteDriver(src_cfg)
        source.execute(
            """
            CREATE TABLE factory_candidates (
                candidate_id TEXT PRIMARY KEY,
                context_matrices TEXT DEFAULT '{}'
            )
            """
        )
        source.execute(
            "INSERT INTO factory_candidates (candidate_id, context_matrices) VALUES (?, ?)",
            ("candidate-1", '{"session_matrix":{}}'),
        )
        source.close()

        destination = SQLiteDriver(dst_cfg)
        destination.execute(
            """
            CREATE TABLE factory_candidates (
                candidate_id TEXT PRIMARY KEY,
                context_matrices TEXT DEFAULT '{}'
            )
            """
        )
        destination.close()

        from nexus_scalp.database.migration import schema_snapshot

        monkeypatch.setattr(
            schema_snapshot,
            "replay_schema",
            lambda *, domain: (
                "CREATE TABLE IF NOT EXISTS factory_candidates ("
                "candidate_id TEXT PRIMARY KEY, context_matrices TEXT DEFAULT '{}'"
                ")",
                "ALTER TABLE factory_candidates ADD COLUMN context_matrices TEXT DEFAULT '{}';",
            ),
        )

        report = PostgresToSqliteMigrator(
            src_cfg,
            dst_cfg,
            MigrationOptions(batch_size=10, validate_checksums=True),
        ).run()

        assert report.status == "SUCCESS"
        assert report.provider_switch_ready is True
        assert report.rows_migrated == 1
        verify = SQLiteDriver(dst_cfg)
        try:
            assert verify.scalar("SELECT COUNT(*) FROM factory_candidates") == 1
            assert (
                verify.scalar(
                    "SELECT context_matrices FROM factory_candidates WHERE candidate_id = ?",
                    ("candidate-1",),
                )
                == '{"session_matrix":{}}'
            )
        finally:
            verify.close()

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
                    closed_at TEXT NOT NULL,
                    mae_usd REAL NOT NULL
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
                "INSERT INTO audit_ledger (id, symbol, pnl, closed_at, mae_usd) VALUES (?, ?, ?, ?, ?)",
                (1, "EURUSD", 125.50, "2026-09-27T10:00:00Z", -4.25),
            )
            src_drv.execute(
                "INSERT INTO audit_ledger (id, symbol, pnl, closed_at, mae_usd) VALUES (?, ?, ?, ?, ?)",
                (2, "GBPUSD", -25.50, "2026-09-27T10:05:00Z", -7.75),
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
                    closed_at TEXT NOT NULL,
                    MAE_usd REAL NOT NULL
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
            mae_sum = verify_drv.scalar('SELECT SUM("MAE_usd") FROM audit_ledger')
            assert abs(float(mae_sum or 0.0) - (-12.0)) < 1e-4
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
