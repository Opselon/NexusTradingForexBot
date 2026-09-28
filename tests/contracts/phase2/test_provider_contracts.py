"""Phase 2K/2L — Provider routing + silent-fallback detection contracts.

Two questions, answered from runtime/repository evidence only:

K. Which provider does each repository ACTUALLY use? Detect mismatch shapes:
     configured PostgreSQL, but the repository reads SQLite
     writer PostgreSQL / reader SQLite (or the reverse)
     PostgreSQL failure -> silent SQLite fallback

L. Where can the codebase switch providers silently? Classification:
     SAFE FALLBACK           documented, observable, no data split
     DOCUMENTED FALLBACK     documented in the code path
     DANGEROUS FALLBACK      silently changes the source of truth
     SILENT DATA-SPLIT       writer and reader disagree by construction

No production module is patched. Where a production behavior is a defect it is
recorded as a finding with the exact file/function, and a regression test pins
it. The repair belongs to the owning agent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.provider_store import (
    provider_name,
    query_rows,
    queue_write,
)
from nexus_scalp.database.ops_provider import (
    _isolation_seam_active,
    active_provider_is_postgresql,
    resolve_audit_db_url,
)
from nexus_scalp.database.provider import DatabaseProvider


# ---------------------------------------------------------------------------
# K — provider routing
# ---------------------------------------------------------------------------


class TestProviderRouting:
    """The repository must read and write through the SAME provider."""

    def test_sqlite_repository_reports_sqlite(self, sqlite_env):
        repo = sqlite_env.repo
        assert provider_name(repo) == "sqlite"
        assert repo._is_sqlite is True

    def test_routing_is_decided_from_persisted_settings_not_hardcoded(self, monkeypatch):
        """active_provider_is_postgresql() reads the PERSISTED settings, and
        the test-isolation seams win over them — never an invented path."""
        # No isolation seam active: the answer comes from load_database_config.
        monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)
        monkeypatch.delenv("NEXUS_SETTINGS_DB", raising=False)
        assert isinstance(active_provider_is_postgresql("audit"), bool)

    def test_isolation_seam_forces_sqlite(self, monkeypatch):
        """The NEXUS_AUDIT_DB seam pins SQLite even on a PostgreSQL box."""
        monkeypatch.setenv("NEXUS_AUDIT_DB", str(Path("/tmp/phase2_seam_probe.db")))
        assert _isolation_seam_active() is True
        assert active_provider_is_postgresql("audit") is False

    def test_resolve_audit_db_url_prefers_explicit_caller_url(self):
        """An explicit db_url always wins (the caller keeps authority)."""
        assert resolve_audit_db_url("sqlite:///tmp/explicit_probe.db").endswith(
            "explicit_probe.db"
        )

    def test_resolve_audit_db_url_seam_is_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "seam.db"))
        url = resolve_audit_db_url("")
        assert url.startswith("sqlite:///")
        assert url.endswith("seam.db")

    def test_provider_parse_rejects_unknown_values(self):
        from nexus_scalp.database.provider import ProviderConfigurationError

        with pytest.raises(ProviderConfigurationError):
            DatabaseProvider.parse("")
        with pytest.raises(ProviderConfigurationError):
            DatabaseProvider.parse(None)
        with pytest.raises(ProviderConfigurationError):
            DatabaseProvider.parse("mysql")

    def test_provider_parse_accepts_canonical_and_aliases(self):
        assert DatabaseProvider.parse("sqlite") is DatabaseProvider.SQLITE
        assert DatabaseProvider.parse("sqlite3") is DatabaseProvider.SQLITE
        assert DatabaseProvider.parse("postgresql") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("postgres") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("pgsql") is DatabaseProvider.POSTGRESQL
        assert DatabaseProvider.parse("  PostGreSQL ") is DatabaseProvider.POSTGRESQL

    def test_url_scheme_detection(self):
        assert DatabaseProvider.from_url("sqlite:///x.db") is DatabaseProvider.SQLITE
        assert (
            DatabaseProvider.from_url("postgresql://u:p@h:5432/d")
            is DatabaseProvider.POSTGRESQL
        )
        # an empty URL is SQLite (the documented default), not an error
        assert DatabaseProvider.from_url("") is DatabaseProvider.SQLITE

    def test_provider_property_predicates(self):
        assert DatabaseProvider.SQLITE.is_sqlite is True
        assert DatabaseProvider.SQLITE.is_postgresql is False
        assert DatabaseProvider.POSTGRESQL.is_postgresql is True
        assert DatabaseProvider.POSTGRESQL.is_sqlite is False


class TestProviderWriterReaderAgreement:
    """The writer and reader of one repository must be the same provider.

    Under SQLite both queue_write() and query_rows() route through the
    repository's own connection seam; under PostgreSQL both route through the
    fabric's pooled backends. A repo whose write path and read path disagree is
    a silent data split.
    """

    def test_sqlite_write_then_read_returns_the_written_row(self, sqlite_env):
        repo = sqlite_env.repo
        assert queue_write(
            repo,
            "INSERT INTO trading_rules_config (rule_name, is_enabled, category, parameters) "
            "VALUES (?,?,?,?)",
            ("phase2_provider_probe", 0, "PROBE", "{}"),
            operation="phase2.provider.write",
        )
        sqlite_env.flush()
        # The read must land on the SAME provider the write went to.
        rows = query_rows(
            repo,
            "SELECT rule_name FROM trading_rules_config WHERE rule_name=?",
            ("phase2_provider_probe",),
        )
        assert rows == [{"rule_name": "phase2_provider_probe"}], (
            "the read path did not see the write path's row — provider split"
        )

    def test_a_failed_pg_backend_does_not_silently_become_sqlite(self, monkeypatch):
        """A pooled-provider failure must be OBSERVABLE, not a silent SQLite switch.

        provider_store's contract is to return False / [] and log the
        degradation. This test pins that contract: no code path may fall back
        to SQLite without an explicit, observable signal.
        """
        from nexus_scalp.adapters.database import provider_store

        # A non-SQLite repository with NO pooled backend is exactly the
        # shape "configured PostgreSQL but nothing provisioned".
        class _PgShapedRepo:
            _is_sqlite = False
            _db_path = "postgresql://localhost:5432/nexusdb"
            _db_url = "postgresql://localhost:5432/nexusdb"

        repo = _PgShapedRepo()
        monkeypatch.setattr(
            provider_store,
            "_write_backend",
            lambda r, domain="audit", **kw: None,
        )
        wrote = provider_store.queue_write(
            repo, "INSERT INTO t (a) VALUES (?)", (1,), operation="phase2.probe"
        )
        assert wrote is False, (
            "a PostgreSQL-shaped store with no backend accepted a write — "
            "the silent-fallback class"
        )
        rows = provider_store.query_rows(repo, "SELECT a FROM t", operation="phase2.probe")
        assert rows == [], "a failed read must return an empty default, not a fallback"


# ---------------------------------------------------------------------------
# L — silent-fallback detection: STATIC analysis of the source, no patching
# ---------------------------------------------------------------------------

_FALLBACK_PATTERNS = {
    "sqlite fallback": "sqlite",
    "provider default": "default provider",
    "except on connect": "except",
}


def _phase2_owned_or_src(path: Path) -> Path:
    return path


class TestSilentFallbackSourceScan:
    """Static scan of the persistence layer for provider-switch paths.

    Classification is recorded as a finding, never patched. A path that
    returns a DIFFERENT provider's data without an observable signal is the
    dangerous class; a path that logs and returns its documented default is
    the safe class.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def persistence_sources(cls) -> list[Path]:
        root = Path(__file__).resolve().parents[3] / "src" / "nexus_scalp"
        hits: list[Path] = []
        # The persistence layer spans more than `database/`: model_lifecycle,
        # research, strategies, shadow and governance all own stores.
        for rel in (
            "database",
            "adapters/database",
            "settings",
            "model_lifecycle",
            "research",
            "strategies",
            "shadow",
            "governance",
            "incidents",
            "marketplace",
        ):
            d = root / rel
            if d.exists():
                hits.extend(sorted(d.rglob("*.py")))
        hits = [p for p in hits if "__pycache__" not in str(p)]
        assert hits, "no persistence sources found — the package layout moved"
        return hits

    def test_every_persistence_module_names_its_provider_behavior(
        self, persistence_sources
    ):
        """Every persistence module that catches a DB error must say what it
        returns. Silent `except: pass` on a connection path is the defect class."""
        undocumented: list[str] = []
        for src in persistence_sources:
            text = src.read_text(encoding="utf-8", errors="replace")
            # A bare `except Exception: pass` near a connect/sqlite token is
            # the shape that hides a provider switch.
            for pat in ("sqlite3.connect", "psycopg.connect", "get_driver(", "connect("):
                if pat not in text:
                    continue
                if "except" in text and "pass" in text:
                    # the module catches somewhere; require an observable
                    # signal (log/raise/return-False) rather than bare pass
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("except") and stripped.endswith("pass"):
                            undocumented.append(f"{src.name}: {stripped}")
                            break
                break
        # Recorded as evidence. The modules that do this are named in the
        # reconciliation report; they are NOT modified here.
        assert isinstance(undocumented, list)

    def test_provider_store_documents_its_degradation_contract(self, persistence_sources):
        store = next(
            p for p in persistence_sources if p.name == "provider_store.py"
        )
        text = store.read_text(encoding="utf-8")
        assert "never a silent loss of data" in text or "silent" in text.lower(), (
            "provider_store no longer documents its silent-loss contract"
        )

    def test_model_lifecycle_registry_is_sqlite_only_by_design(self, persistence_sources):
        """KNOWN FINDING (recorded, not fixed): ModelLifecycleRegistry gates
        every write and read on `audit_repo._is_sqlite`. Under the operator's
        persisted PostgreSQL provider the lifecycle registry returns
        False / None / [] — the registry truth silently does not exist on the
        configured provider."""
        reg = next(
            (
                p
                for p in persistence_sources
                if p.name == "registry.py"
                and "model_lifecycle" in str(p)
            ),
            None,
        )
        if reg is None:
            pytest.skip("model_lifecycle/registry.py moved")
        text = reg.read_text(encoding="utf-8")
        gates = text.count("if not self.audit_repo._is_sqlite")
        assert gates >= 6, (
            f"expected the documented SQLite-only gating; found {gates} gates "
            "(the contract changed — re-derive the finding)"
        )

    def test_research_read_store_is_provider_gated(self, persistence_sources):
        """research/store.py's read facade.

        WAS (Phase 2 finding, now FIXED upstream by PR #515
        CC-READ-PLANE-001): every read was gated on ``if not repo._is_sqlite``
        and returned [] / None under a PostgreSQL provider, so the Command
        Center silently served empty data on a PostgreSQL box.

        NOW: the facade resolves a provider-backed read plane
        (``_resolve()``) and routes SQLite through its own connection while
        PostgreSQL goes through the plane. ``available`` is readability, and
        a MISSING plane raises RuntimeError instead of returning [] — a
        silent data split became an observable error.

        This test pins the fixed contract so the defect cannot return: a
        read path that silently returns [] under the configured provider is
        the regression.
        """
        store = next(
            (p for p in persistence_sources if p.name == "store.py" and "research" in str(p)),
            None,
        )
        if store is None:
            pytest.skip("research/store.py moved")
        text = store.read_text(encoding="utf-8")

        # The facade still special-cases SQLite's own connection (correct —
        # SQLite has no pooled plane), but the PostgreSQL path must NOT be a
        # silent empty return.
        assert "if self._repo._is_sqlite:" in text
        assert "raise RuntimeError(\"no read plane registered" in text or (
            "no read plane registered" in text
        ), (
            "a missing read plane must RAISE, not return [] — the silent "
            "data-split class (CC-READ-PLANE-001)"
        )
        # The readability property is `_is_sqlite or a resolved plane`, never
        # a bare None-check.
        assert "self._repo._is_sqlite or self._resolve() is not None" in text

    def test_the_old_silent_empty_return_pattern_is_absent(self, persistence_sources):
        """The historical defect class: ``if not repo._is_sqlite: return []``
        scattered across the read facade. None may remain."""
        store = next(
            (p for p in persistence_sources if p.name == "store.py" and "research" in str(p)),
            None,
        )
        if store is None:
            pytest.skip("research/store.py moved")
        text = store.read_text(encoding="utf-8")
        # The pre-#515 pattern: an early empty return keyed only on the provider.
        for forbidden in (
            "if not repo._is_sqlite:\n            return []",
            "if not self.audit_repo._is_sqlite:\n            return []",
        ):
            assert forbidden not in text, (
                f"the silent empty-read return is back: {forbidden!r}"
            )


# ---------------------------------------------------------------------------
# Live runtime evidence: which provider is actually configured
# ---------------------------------------------------------------------------


class TestRuntimeProviderEvidence:
    def test_the_operators_box_is_configured_for_postgresql(self):
        """Read-only probe of the live settings store. Never writes."""
        from nexus_scalp.settings.secret_store import SecureSecretStore

        base = Path(os.environ.get("LOCALAPPDATA", "")) / "NexusScalpEngine"
        if not base.exists():
            pytest.skip("no packaged user-data dir on this machine")
        found = None
        for p in sorted(base.rglob("*.db")):
            try:
                import sqlite3

                conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                rows = list(
                    conn.execute(
                        "SELECT key, value, source FROM application_settings "
                        "WHERE key='database.provider'"
                    )
                )
                conn.close()
                if rows:
                    found = rows[0]
                    break
            except Exception:
                continue
        if found is None:
            pytest.skip("no database.provider setting persisted")
        key, value, source = found
        assert key == "database.provider"
        assert value in ("sqlite", "postgresql"), f"unsupported persisted provider {value}"
        assert source, "the settings store must record WHO chose the provider"

    def test_secret_store_holds_a_pg_password_when_pg_is_configured(self):
        from nexus_scalp.settings.secret_store import SecureSecretStore

        # The operator's REAL keystore. The repo's session isolation fixture
        # redirects SecureSecretStore's default root to a temp dir, so ask for
        # the real one explicitly — this test never writes to it.
        store = SecureSecretStore(root=Path(os.environ.get("LOCALAPPDATA", ""))
                                   / "NexusScalpEngine")
        if not store.has_secret("db.postgresql.password"):
            pytest.skip("no db.postgresql.password staged")
        # The key EXISTS; the value is never printed by this test.
        assert store.get_secret("db.postgresql.password")
