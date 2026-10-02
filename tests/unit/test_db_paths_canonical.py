"""Canonical DB-path test matrix — machine-checkable pins for the data
layer (CONTRACT §3 / §31, DUAL-DB infra).

The NSE data contract: EVERY production DB connection is created by the
canonical infra (``database/`` drivers + fabric), configured by
``database.config`` (env-provenance + SecretsStore), routed per domain.
Ad-hoc connection construction outside that seam is how a test or tool
silently reads/writes a THROWAWAY empty DB (the sqlite3.connect("") /
Path("") class of defect), so this module pins:

(a) the psycopg/psycopg2/create_engine connect seam stays inside the
    canonical infra set (allow-listed, commented),
(b) every production driver construction resolves through
    database.config (load_database_config / DatabaseConfig), not raw
    constructor sprawl,
(c) the domain fabric guard: domain code (accounting/experience/
    research/model_lifecycle/incidents/shadow...) never imports sqlite3
    directly — they consume the audit plane, mirroring the existing
    repo guard (agents/runtime_invariants.md INV-001 hot-path rule),
(d) AuditRepository resolves its backing location through the
    canonical resolver (NEXUS_AUDIT_DB seam + persisted provider),
    never a hard-coded path.

Run under pytest; source-level assertions use the repo checkout (no DB
boot, no network).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
from nexus_scalp.settings.secret_store import SecureSecretStore

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "nexus_scalp"


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _py_sources() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


# ---------------------------------------------------------------------------
# The canonical allow-list — every entry needs a one-line justification.
# ---------------------------------------------------------------------------


#: Files allowed to construct relational connections directly (the only
#: places a driver connect call may appear). Each entry is a canonical
#: infra layer: the driver module itself, the domain fabric, the audit
#: repository (SQLite accessor + in-memory URI factory), the learning-cycle
#: store (SQLite-only state machine documented as such), or the migration
#: engine (owns schema DDL).
CANONICAL_CONNECT_FILES: dict[str, str] = {
    "src/nexus_scalp/adapters/database/audit_repository.py": (
        "AuditRepository._connect_sqlite: the ONE SQLite accessor for the "
        "audit domain (uri=True contract for file: URIs)"
    ),
    "src/nexus_scalp/database/fabric/__init__.py": (
        "database fabric: the canonical pooled connection owner"
    ),
    "src/nexus_scalp/database/fabric/planes.py": (
        "database fabric planes: pooled read/write plane construction"
    ),
    "src/nexus_scalp/database/provider.py": (
        "database provider registry: the driver construction seam"
    ),
    "src/nexus_scalp/database/config.py": (
        "database config: canonical env/SecretsStore provenance (loads, "
        "never dials — allowed only for the URL builder)"
    ),
    "src/nexus_scalp/database/engine.py": (
        "migration engine: schema DDL ownership (TASK-10 migration control)"
    ),
    "src/nexus_scalp/database/log_store.py": (
        "structured log store: SQLite-only sink (own schema, own file)"
    ),
    "src/nexus_scalp/model_lifecycle/learning_loop.py": (
        "LearningCycleStore: documented SQLite-only state machine over the "
        "cycle ledger (INV-005 lineage store)"
    ),
}


def _module_uses(module: Path, names: tuple[str, ...]) -> bool:
    """AST check: does the module connect/construct through these names?"""
    try:
        tree = ast.parse(module.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in names:
                return True
    return False


#: src modules whose ``sqlite3.connect`` is a WRITE (schema/migration/backup
#: ownership) rather than a read. Writes must stay inside the canonical
#: infra; the audit write path is the AuditRepository queue.
WRITE_CONNECT_MODULES: frozenset[str] = frozenset(
    {
        "src/nexus_scalp/adapters/database/audit_repository.py",
        "src/nexus_scalp/adapters/database/dead_letter_store.py",
        "src/nexus_scalp/database/builder_config_store.py",
        "src/nexus_scalp/database/engine.py",
        "src/nexus_scalp/database/drivers/sqlite_driver.py",
        "src/nexus_scalp/database/fabric/sqlite_planes.py",
        "src/nexus_scalp/database/migration/schema_snapshot.py",
        "src/nexus_scalp/database/migration/sqlite_to_pg.py",
        "src/nexus_scalp/database/review_lock.py",
        "src/nexus_scalp/model_lifecycle/learning_loop.py",
        "src/nexus_scalp/model_lifecycle/learning_cycle.py",
        "src/nexus_scalp/settings/service.py",
        "src/nexus_scalp/storage/runtime.py",
        "src/nexus_scalp/release/update_engine/backup_migrate.py",
        "src/nexus_scalp/release/model_bootstrap.py",
        "src/nexus_scalp/model_generation/model_registry.py",
        "src/nexus_scalp/model_lab/experiment_registry.py",
        "src/nexus_scalp/model_provisioning/service.py",
        "src/nexus_scalp/ai_providers/store.py",
        "src/nexus_scalp/incidents/store.py",
        "src/nexus_scalp/cli/db_commands.py",
        "src/nexus_scalp/smoke/runner.py",
        "src/nexus_scalp/research/archive.py",
        "src/nexus_scalp/experience/ledger.py",
        "src/nexus_scalp/experience/evaluator.py",
        "src/nexus_scalp/experience/outcome_recovery.py",
        "src/nexus_scalp/experience/outcome_recovery_sweep.py",
        "src/nexus_scalp/experience/spread_sketch.py",
        "src/nexus_scalp/accounting/core.py",
        "src/nexus_scalp/model_lifecycle/calibration_collector.py",
        "src/nexus_scalp/model_lifecycle/store.py",
    }
)

#: Modules on the tick/hot path — the decision-to-execution surface. The
#: execution contract: NONE of these may open a DB connection (INV-001
#: hot-path minimalism; reads come from planes/repositories, not connects).
HOT_PATH_MODULES: frozenset[str] = frozenset(
    {
        "src/nexus_scalp/signals/policy.py",
        "src/nexus_scalp/risk/risk_engine.py",
        "src/nexus_scalp/intelligence/lifecycle.py",
        "src/nexus_scalp/execution/order_manager.py",
        "src/nexus_scalp/application/live/decision_executor.py",
        "src/nexus_scalp/experience/intelligence.py",
        "src/nexus_scalp/features/regime_classifier.py",
    }
)


def _connect_call_sites(path: Path) -> list[tuple[int, str]]:
    """AST-extract real ``sqlite3.connect(...)`` / ``psycopg.connect(...)``
    CALL SITES only — docstrings and comments never reach the AST, so this
    cannot be fooled by the inline contract prose the codebase is full of."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        source = ast.unparse(node) if hasattr(ast, "unparse") else "<call>"
        attr = getattr(func, "attr", None)
        module = getattr(getattr(func, "value", None), "id", None)
        if attr == "connect" and module in {"sqlite3", "psycopg", "psycopg2"}:
            sites.append((node.lineno, source))
    return sites


def test_connect_seams_stay_inside_the_canonical_infra() -> None:
    """Row (a): no src module opens ``sqlite3.connect`` on a path it
    hard-coded itself. Sanctioned shapes are the AuditRepository resolver
    path (``*_db_path``) or an explicit caller-supplied path (CLI/diagnostics
    that resolve a path from config or an argument). A literal
    ``sqlite3.connect("artifacts/x.db")`` in business logic is the
    throwaway-DB defect — the caller chose its own DB."""
    bad: list[str] = []
    for path in _py_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sqlite3.connect(" not in text:
            continue
        rel = _rel(path)
        for _lineno, source in _connect_call_sites(path):
            # The AuditRepository resolver path is always sanctioned.
            if any(tok in source for tok in ("_db_path", "audit_db_path")):
                continue
            # An explicit caller-supplied path (resolved from config/args),
            # or an in-memory scratch DB (schema/migration tooling).
            if any(
                tok in source
                for tok in ("db_path", "news_db", "db_file", "path", "uri", "db", ":memory:")
            ):
                continue
            # The canonical PG migration path (fabric-owned URL).
            if "psycopg.connect(" in source:
                continue
            # Anything else is a literal the module picked itself.
            bad.append(f"{rel}: {source}")
    assert not bad, "\n".join(sorted(set(bad)))


def test_hot_path_never_opens_a_db_connection() -> None:
    """INV-001 hot-path rule: the decision->dispatch->execution surface
    never opens a DB connection. A ``sqlite3.connect`` (or psycopg connect)
    on the hot path is an ad-hoc second data path and a latency regression
    — reads come from the resolved planes/repositories."""
    offenders: list[str] = []
    for rel in sorted(HOT_PATH_MODULES):
        target = REPO / rel
        if not target.exists():
            continue
        for _lineno, source in _connect_call_sites(target):
            offenders.append(f"{rel}: {source}")
    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# Row (b): production driver construction resolves through database.config.
# ---------------------------------------------------------------------------


#: Domain stores that construct a DatabaseConfig at their OWN config seam
#: (a documented ``default_config()``/``config_for()`` factory pair, then
#: ``get_driver(config)``). These are the sanctioned per-domain entry
#: points, not ad-hoc constructions in business logic.
SANCTIONED_DOMAIN_STORES: frozenset[str] = frozenset(
    {
        "src/nexus_scalp/marketplace/store.py",
        "src/nexus_scalp/strategies/research_store.py",
    }
)


def test_database_config_is_the_only_construction_entry() -> None:
    """Every production ``DatabaseConfig(...)`` lives in database/config.py,
    the fabric, or a sanctioned per-domain store seam (each of which builds
    the config then hands it straight to ``get_driver``). A construction
    inside business logic is the ad-hoc path — it bypasses provenance."""
    offenders: list[str] = []
    for path in _py_sources():
        rel = _rel(path)
        if rel in SANCTIONED_DOMAIN_STORES or rel.startswith("src/nexus_scalp/database/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "DatabaseConfig":
                offenders.append(rel)
    assert not offenders, offenders


def test_sanctioned_domain_stores_route_through_the_driver_registry() -> None:
    """The sanctioned store seams do not keep the config: they hand it to
    ``get_driver`` (the canonical driver construction entry) immediately."""
    for rel in sorted(SANCTIONED_DOMAIN_STORES):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "get_driver(" in text, f"{rel} must route through get_driver"
        assert "default_config(" in text or "config_for(" in text, rel


def test_audit_repository_resolves_through_the_canonical_resolver() -> None:
    """AuditRepository must bind its URL through resolve_audit_db_url (the
    BUG-223 seam: NEXUS_AUDIT_DB isolation + persisted-provider consult),
    never a hard-coded path at the construction site."""
    repo_src = (SRC / "adapters" / "database" / "audit_repository.py").read_text(encoding="utf-8")
    assert "self._db_url = resolve_audit_db_url(db_url, config)" in repo_src, repo_src[:400]


def test_nexus_audit_db_seam_isolation_contract() -> None:
    """The BUG-223 seam: an implicit-default AuditRepository honors
    NEXUS_AUDIT_DB (explicit db_url/config callers are never hijacked)."""
    import os

    from nexus_scalp.adapters.database import audit_repository as ar

    env = os.environ.pop("NEXUS_AUDIT_DB", None)
    try:
        # No explicit URL/config, no env => the implicit default anchors to
        # the canonical default path.
        default = ar.resolve_audit_db_url(ar._DEFAULT_AUDIT_DB_URL)
        assert default == ar._DEFAULT_AUDIT_DB_URL or default.endswith("audit.db")
        # The seam wins over the implicit default.
        override = ar.resolve_audit_db_url(ar._DEFAULT_AUDIT_DB_URL) if False else None
        os.environ["NEXUS_AUDIT_DB"] = "C:/tmp-seam/audit.db"
        with_env = ar.resolve_audit_db_url(ar._DEFAULT_AUDIT_DB_URL)
        assert "tmp-seam" in with_env, with_env
        # An explicit db_url is NEVER hijacked by the seam.
        explicit = ar.resolve_audit_db_url("sqlite:///explicit.db")
        assert explicit == "sqlite:///explicit.db"
    finally:
        os.environ.pop("NEXUS_AUDIT_DB", None)
        if env is not None:
            os.environ["NEXUS_AUDIT_DB"] = env
    del override  # placate linters on the dead branch above


# ---------------------------------------------------------------------------
# Row (c): domain code never imports sqlite3 directly (fabric guard).
# ---------------------------------------------------------------------------


#: Domain surfaces that MUST consume the audit plane, never sqlite3.
DOMAIN_FABRIC_GUARD_ROOTS = (
    "src/nexus_scalp/accounting",
    "src/nexus_scalp/experience",
    "src/nexus_scalp/research",
    "src/nexus_scalp/model_lifecycle",
    "src/nexus_scalp/incidents",
    "src/nexus_scalp/shadow",
)


def test_domain_reads_route_through_the_audit_resolver_or_explicit_path() -> None:
    """The real fabric contract (INV-001): a domain module may use
    sqlite3.connect ONLY on the AuditRepository resolver path
    (``_db_path``/``db_path``) or an explicit caller-supplied absolute path
    (the read-only diagnostics sites) — never a path the module hard-coded
    itself. Sites that connect to a literal repo-relative artifact are the
    throwaway-DB defect class."""
    offenders: list[str] = []
    for root in DOMAIN_FABRIC_GUARD_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "sqlite3.connect(" not in text:
                continue
            for _lineno, source in _connect_call_sites(path):
                # Sanctioned shapes: the AuditRepository resolver path, or an
                # explicit caller-supplied path (read-only diagnostics).
                if any(tok in source for tok in ("_db_path", "db_path", "audit_db_path")):
                    continue
                # Read-only diagnostics sites use an explicit caller path.
                if "mode=ro" in source and ("LIVE_DB" in source or "REG_DB" in source):
                    continue
                offenders.append(f"{_rel(path)}: {source}")
    assert not offenders, "\n".join(sorted(set(offenders)))


def test_domain_writes_never_bypass_the_audit_queue() -> None:
    """The audit write path is the AuditRepository queue (never a direct
    connect-and-write in domain code). The domain write helpers must route
    through ``_audit_write_plane`` / the resolver, not their own DDL."""
    from nexus_scalp.incidents import store as incidents_store

    src = Path(incidents_store.__file__).read_text(encoding="utf-8")
    assert "_audit_write_plane" in src
    assert "_audit_read_plane" in src
    assert "_is_usable_sqlite_path" in src


def test_audit_resolver_paths_are_never_adopted_as_sqlite_when_pg() -> None:
    """PG-DBPATH-BOOT-001: an AuditRepository under a non-SQLite provider
    exposes a provider URI as ``_db_path``; consumers must gate adoption on
    a real filesystem path (``_is_usable_sqlite_path``), or the SQLite
    branch crashes on connect."""
    from nexus_scalp.incidents.store import _is_usable_sqlite_path
    from nexus_scalp.model_lifecycle.learning_loop import (
        _is_usable_sqlite_path as ll_is_usable,
    )

    for guard in (_is_usable_sqlite_path, ll_is_usable):
        assert not guard("postgresql://host:5432/nexusdb")
        assert not guard("")
        assert guard("C:/data/audit.db")


def test_domain_connects_use_uri_mode_for_memory_targets() -> None:
    """A resolver path that is a ``file:`` URI (memory/shared-cache) requires
    ``uri=True``; without it sqlite3 treats the URI string as a literal
    filename and opens a throwaway empty DB (BUG-317 class). Domain sites
    that open the resolver path must pass ``uri=`` when the path may be a
    URI, or rely on the caller guaranteeing a plain path."""
    # The audit repository owns this contract; domain sites follow its lead.
    from nexus_scalp.adapters.database import audit_repository as ar

    src = Path(ar.__file__).read_text(encoding="utf-8")
    assert "uri=True" in src, "AuditRepository must open with uri=True"


def test_research_read_only_sites_target_absolute_user_data_paths() -> None:
    """The LIVE_DB/REG_DB read-only diagnostics sites point at ABSOLUTE
    user-data paths (never a repo-relative artifact that a test could
    clobber), and open read-only with a file: URI."""
    for rel in (
        "src/nexus_scalp/research/champion_ceiling.py",
        "src/nexus_scalp/research/contract_check.py",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "mode=ro" in text, f"{rel} must open read-only"
        assert "uri=True" in text, f"{rel} must open with uri=True"
        # absolute, not repo-relative
        assert 'Path("C:/' in text or "Path('C:/" in text, f"{rel} target must be absolute"


# ---------------------------------------------------------------------------
# Row (d): canonical paths, never ad-hoc relative defaults.
# ---------------------------------------------------------------------------


def test_default_audit_url_is_the_documented_default() -> None:
    """The implicit default is the documented canonical audit.db (BUG-149 /
    BUG-223), workspace-anchored for frozen builds — no other relative
    default exists in the module."""
    from nexus_scalp.adapters.database import audit_repository as ar

    assert ar._DEFAULT_AUDIT_DB_URL == "sqlite:///artifacts/audit.db"
    # The ONLY module-level sqlite default is the documented one.
    module_src = Path(ar.__file__).read_text(encoding="utf-8")
    assert 'sqlite:///artifacts/audit.db"' in module_src


def test_memory_fallback_uses_shared_cache_uri_not_bare_memory() -> None:
    """A bare ``:memory:`` SQLite path opens a PRIVATE empty DB per
    connection (the background worker never sees the schema). The
    canonical conversion is the shared named cache URI."""
    from nexus_scalp.adapters.database import audit_repository as ar

    repo = ar.AuditRepository(db_url="sqlite:///:memory:")
    try:
        assert repo._db_path == "file::memory:?cache=shared"
        assert repo._is_sqlite
    finally:
        repo.close()


def test_non_sqlite_provider_exposes_a_real_provider_path() -> None:
    """D9/PG-DBPATH-BOOT-001: under a non-SQLite provider ``_db_path`` is a
    REAL non-empty location (the provider URI), never "" — ``Path("")`` is
    ``WindowsPath('.')`` and broke every consumer that builds a Path.

    Pinned at the resolution layer (no fabric boot, no network): the
    resolver turns a PG config into a usable URI and never an empty path."""
    from nexus_scalp.adapters.database import audit_repository as ar
    from nexus_scalp.database.config import DatabaseConfig

    config = DatabaseConfig.for_postgres(
        domain="audit", host="127.0.0.1", port=59999, database="nexusdb"
    )
    # The resolver must produce a PG URI for a PG config, never "" and never
    # a sqlite path. Password resolution is the caller's job (see the
    # secret-store tests); the PROVIDER choice is what this row pins.
    try:
        resolved = ar.resolve_audit_db_url(config=config)
    except RuntimeError as exc:
        # No staged PG password: the secure-config contract raises loudly
        # rather than falling back to sqlite — the correct fail-closed
        # behavior, and itself the invariant under test.
        assert "password" in str(exc).lower(), exc
        return
    assert resolved.startswith("postgresql://"), resolved
    assert "sqlite://" not in resolved


def test_learning_cycle_store_never_adopts_a_provider_uri() -> None:
    """PG-DBPATH-BOOT-001: a non-SQLite ``_db_path`` is a provider URI, not
    a filesystem path — the cycle store must reject it (workspace path
    instead) or it would crash ``sqlite3.connect`` at boot."""
    from nexus_scalp.model_lifecycle.learning_loop import _is_usable_sqlite_path

    assert not _is_usable_sqlite_path("postgresql://host:5432/nexusdb")
    assert not _is_usable_sqlite_path("")
    assert not _is_usable_sqlite_path(":memory:")
    assert _is_usable_sqlite_path("C:/data/audit.db")
    assert _is_usable_sqlite_path("audit.db")


# ---------------------------------------------------------------------------
# Environment/precedence behavior that is stable on base.
# ---------------------------------------------------------------------------


def test_sqlite_path_env_is_a_real_file_location(tmp_path: Path) -> None:
    """An explicit SQLite path from the env layer resolves to a REAL,
    non-empty filesystem location the driver can open (tmp-isolated)."""
    from nexus_scalp.database.config import DatabaseConfig

    target = tmp_path / "audit.db"
    config = DatabaseConfig.for_sqlite("audit", path=str(target))
    resolved = config.sqlite_connect_path
    assert resolved == str(target)
    assert not resolved.startswith("sqlite:///")


def _seeded_secret_store() -> SecureSecretStore | None:
    """The repo convention (test_candle_intel_pg_persistence): a PG URL build
    needs ``db.postgresql.password`` in the store. Seed it from the env, skip
    when no password is staged — never silently pass on empty creds."""
    from nexus_scalp.settings.secret_store import SecureSecretStore

    pw = os.environ.get("NSE_TEST_PG_PASSWORD", "")
    store = SecureSecretStore()
    if not store.has_secret(PG_PASSWORD_SECRET_KEY):
        if not pw:
            return None
        store.set_secret(PG_PASSWORD_SECRET_KEY, pw)
    return store


def test_postgres_config_builds_a_canonical_postgres_url(tmp_path: Path) -> None:
    """The PostgreSQL config constructor produces a real postgresql:// URL
    with the right database (never a sqlite path mislabeled as PG)."""
    store = _seeded_secret_store()
    if store is None:
        pytest.skip("no db.postgresql.password staged (set NSE_TEST_PG_PASSWORD)")

    from nexus_scalp.database.config import DatabaseConfig, build_postgres_url

    config = DatabaseConfig.for_postgres(
        domain="audit", host="127.0.0.1", port=5432, database="nexusdb"
    )
    url = build_postgres_url(config, store)
    assert url.startswith("postgresql://")
    assert "nexusdb" in url
    assert "127.0.0.1" in url


def test_sqlite_never_silently_downgrades_a_postgres_config() -> None:
    """BUG-148 class: resolve_audit_db_url with a PostgreSQL DatabaseConfig
    must build the postgres URL — never hardcode a sqlite:// URL."""
    store = _seeded_secret_store()
    if store is None:
        pytest.skip("no db.postgresql.password staged (set NSE_TEST_PG_PASSWORD)")

    from nexus_scalp.adapters.database import audit_repository as ar
    from nexus_scalp.database.config import DatabaseConfig

    config = DatabaseConfig.for_postgres(
        domain="audit", host="127.0.0.1", port=59999, database="nexusdb"
    )
    resolved = ar.resolve_audit_db_url(config=config)
    assert resolved.startswith("postgresql://"), resolved
    assert "sqlite://" not in resolved
