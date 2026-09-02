"""TASK-DB-PLATFORM regression net (2026-09-02, Nexus-Main DB platform owner).

Pins the database-platform overhaul fixes to executable evidence:

BUG-194  manifest schema versions must equal registry-derived expected
         versions (news/candle had drifted to 1 while their registries
         carried one migration each -> two SSOTs disagreed).
BUG-195  /api/debug/state database section must report a real
         schema_version + migration_state (was hard-coded None -> the UI
         could never see the schema version; API was the first broken
         layer).
BUG-196  release.versioning.default_db_versions_provider must read the
         engine's ``migration_state`` key (``state`` never existed ->
         operator snapshot showed an empty state for every domain).
BUG-197  the migration gate's baseline skeletons must be compatible with
         the application bootstrap (gate-first fresh install used to
         crash AuditRepository._seed_trading_rules with "no column named
         rule_name").
BUG-198  the db_console router registration assertion follows the
         CHG-0032-A1 Step 3E extraction (debug_research_routes, not
         server.py).

Also certifies (§4-§6 of the DB platform brief): fresh-DB migration
completeness, idempotent re-migration, and legacy-row data preservation
through the full migration chain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.database.engine import DatabaseMigrationEngine
from nexus_scalp.database.manifest import MANIFESTS
from nexus_scalp.database.models import DatabaseDomain
from nexus_scalp.database.registry import expected_version_for_domain

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# BUG-194: manifest <-> registry SSOT agreement
# ---------------------------------------------------------------------------
class TestManifestRegistryAgreement:
    def test_all_domains_manifest_version_equals_registry_expected(self) -> None:
        for dom in DatabaseDomain:
            assert MANIFESTS[dom].schema_version == expected_version_for_domain(dom), (
                f"{dom.value}: manifest schema_version "
                f"{MANIFESTS[dom].schema_version} != registry expected "
                f"{expected_version_for_domain(dom)}"
            )

    def test_specific_pinned_versions(self) -> None:
        assert MANIFESTS[DatabaseDomain.AUDIT].schema_version == 7
        assert MANIFESTS[DatabaseDomain.NEWS].schema_version == 2
        assert MANIFESTS[DatabaseDomain.CANDLE_INTEL].schema_version == 2


# ---------------------------------------------------------------------------
# BUG-197: gate-first fresh install must not break the app bootstrap
# ---------------------------------------------------------------------------
class TestBaselineSkeletonHeal:
    def test_gate_first_fresh_install_allows_audit_bootstrap(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        res = eng.migrate()
        assert res["state"] == "DB_MIGRATION_SUCCEEDED"

        con = sqlite3.connect(db)
        cols = {r[1] for r in con.execute("PRAGMA table_info(trading_rules_config)")}
        ticket = next(
            (r for r in con.execute("PRAGMA table_info(audit_ledger)") if r[1] == "ticket"),
            None,
        )
        con.close()
        # Skeleton carries the columns the app bootstrap's seed pass needs.
        assert "rule_name" in cols
        assert "category" in cols
        # Ledger PK is INTEGER PRIMARY KEY, not the TEXT skeleton.
        assert ticket is not None
        assert str(ticket[2]).upper() == "INTEGER"
        assert ticket[5] == 1  # pk column

    def test_full_migration_chain_data_preserved(self, tmp_path: Path) -> None:
        """Legacy v1 fixture -> full migration -> rows intact, no invented values."""
        db = tmp_path / "legacy.db"
        con = sqlite3.connect(db)
        con.execute(
            """
            CREATE TABLE audit_ledger (
                ticket INTEGER PRIMARY KEY, symbol TEXT NOT NULL,
                direction TEXT NOT NULL, volume REAL NOT NULL,
                entry_price REAL NOT NULL, exit_price REAL, status TEXT NOT NULL,
                pnl REAL DEFAULT 0.0, commission REAL DEFAULT 0.0,
                swap REAL DEFAULT 0.0, duration_sec REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL
            )
            """
        )
        con.execute(
            "INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price,"
            " status, pnl, timestamp) VALUES (123001,'XAUUSD','BUY',0.01,1900.5,"
            "'CLOSED',12.34,'2026-08-01T10:30:00Z')"
        )
        con.commit()
        con.close()

        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        res = eng.migrate()
        assert res["state"] == "DB_MIGRATION_SUCCEEDED"

        con = sqlite3.connect(db)
        row = con.execute(
            "SELECT ticket, pnl, status, net_pnl_usd, exit_reason_source"
            " FROM audit_ledger WHERE ticket = 123001"
        ).fetchone()
        con.close()
        assert row[0] == 123001
        assert row[1] == pytest.approx(12.34)
        assert row[2] == "CLOSED"
        # New columns exist with defaults, never invented history.
        assert row[3] == pytest.approx(0.0)
        assert row[4] == ""


# ---------------------------------------------------------------------------
# Fresh-DB certification + idempotency (brief §4/§6)
# ---------------------------------------------------------------------------
class TestFreshDbCertification:
    @pytest.mark.parametrize("domain", list(DatabaseDomain))
    def test_fresh_db_reaches_expected_version_and_idempotent(
        self, tmp_path: Path, domain: DatabaseDomain
    ) -> None:
        db = tmp_path / f"{domain.value}.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=domain)
        first = eng.migrate()
        assert first["state"] == "DB_MIGRATION_SUCCEEDED"
        assert first["current_version"] == eng.expected_version()
        assert first["integrity"] == "ok"

        # Idempotent re-run: nothing applied, no error.
        second = eng.migrate()
        assert second["state"] == "DB_MIGRATION_NOT_REQUIRED"
        assert second.get("applied", []) == []

        con = sqlite3.connect(db)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        missing = eng.expected_tables() - tables
        assert not missing, f"missing after full migration: {sorted(missing)}"


# ---------------------------------------------------------------------------
# BUG-195: /api/debug/state carries real schema version (API layer truth)
# ---------------------------------------------------------------------------
class TestDebugSnapshotDatabaseSection:
    def test_schema_version_probed_not_none(self, tmp_path: Path) -> None:
        from nexus_scalp.web.debug_snapshot import _database_section

        db = tmp_path / "audit.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        assert eng.migrate()["state"] == "DB_MIGRATION_SUCCEEDED"

        class _FakeAudit:
            _db_path = str(db)

        engine = type("E", (), {"config": None, "audit": _FakeAudit(), "news_engine": None})()
        section = _database_section(engine)
        audit = section["databases"]["audit"]
        assert audit["health"] == "READY"
        assert audit["schema_version"] == eng.expected_version()
        assert audit["migration_state"] == "DB_MIGRATION_NOT_REQUIRED"

    def test_absent_db_reports_not_recorded(self, tmp_path: Path) -> None:
        from nexus_scalp.web.debug_snapshot import _database_section

        engine = type("E", (), {"config": None, "audit": None, "news_engine": None})()
        section = _database_section(engine)
        assert section["databases"]["audit"]["health"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# BUG-196: operator snapshot reads the real migration_state key
# ---------------------------------------------------------------------------
class TestDbVersionsProviderState:
    def test_provider_state_is_migration_state_not_empty(self, tmp_path: Path, monkeypatch) -> None:
        from nexus_scalp.release import versioning

        db = tmp_path / "audit.db"
        eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
        assert eng.migrate()["state"] == "DB_MIGRATION_SUCCEEDED"

        # Patch the engine module's path resolver as imported inside the provider.
        import nexus_scalp.database.engine as engine_mod

        monkeypatch.setattr(
            engine_mod,
            "db_path_for_domain",
            lambda domain, workspace=None: tmp_path / f"{domain}.db",
        )
        out = versioning.default_db_versions_provider()
        assert out["audit"]["current"] == eng.expected_version()
        assert out["audit"]["state"] == "DB_MIGRATION_NOT_REQUIRED"
        assert out["audit"]["state"] != ""


# ---------------------------------------------------------------------------
# BUG-198: db_console router registration follows the 3E extraction
# ---------------------------------------------------------------------------
class TestDbConsoleRegistrationSurface:
    def test_router_registered_in_debug_research_routes(self) -> None:
        text = (REPO_ROOT / "src" / "nexus_scalp" / "web" / "debug_research_routes.py").read_text(
            encoding="utf-8"
        )
        assert "db_console" in text
        assert "app.include_router(db_console_router)" in text


# ---------------------------------------------------------------------------
# DB -> API -> client field contract: pagination boundedness
# ---------------------------------------------------------------------------
class TestPaginationBoundedness:
    def test_incident_store_limit_is_bounded(self, tmp_path: Path) -> None:
        """Incident listing must clamp client-supplied limits (never unlimited)."""
        import inspect

        from nexus_scalp.incidents.store import IncidentStore

        src = inspect.getsource(IncidentStore)
        assert "min(int(limit)" in src or "min(limit" in src
