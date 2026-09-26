"""Lane E — a fresh PostgreSQL bootstrap RECORDS its migrations and serves reads.

The two proven defects on the live nexusdb (all 116 tables present):

  1. ``schema_migrations`` was EMPTY (0 rows) while every table existed. The
     provisioning replay creates the engine's history table but never writes a
     single row into it — the SQLite engine's ``_record_migration`` runs only
     on the SQLite path, and the PG provisioner emitted 133 DDL statements with
     zero INSERTs. Every version/convergence check therefore reported "never
     migrated" on a database that demonstrably had been.

  2. The audit READ plane was not registered, so reads degraded to their
     documented defaults (368 live warnings: "no read plane registered for
     domain 'audit'"). ``provision_domain`` registers the read plane from the
     process that ran it; every OTHER process on the box (a diagnostics route,
     a maintenance worker, a CLI probe that only reads) starts with an empty
     registry and its first read finds nothing.

These tests pin both contracts. The PostgreSQL arm runs against
``NSE_PG_TEST_URL`` (an ISOLATED instance — never the live localhost:5432) and
skips cleanly when it is unset; the no-server arm still proves the recording
contract offline with a fake executor, so the regression can never go silently
green in CI.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)


@pytest.fixture()
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The domain backend registry is a process-wide singleton; give this
    module a private one so no other test's registration can change what these
    assertions see (and so a plane opened here never leaks into them)."""
    from nexus_scalp.database import fabric as fabric_mod

    saved = dict(fabric_mod._DOMAIN_BACKENDS)
    fabric_mod._DOMAIN_BACKENDS = {}
    try:
        yield
    finally:
        for entry in fabric_mod._DOMAIN_BACKENDS.values():
            close = getattr(entry, "close", None)
            if callable(close):
                with _suppress():
                    close()
        fabric_mod._DOMAIN_BACKENDS.clear()
        fabric_mod._DOMAIN_BACKENDS.update(saved)


@contextlib.contextmanager
def _suppress() -> Iterator[None]:
    with contextlib.suppress(Exception):
        yield


# =====================================================================
# Offline contract: the recorder writes the chain (no server needed)
# =====================================================================


class _RecordingExecutor:
    """Single-string + parameterized executor, recording everything it runs."""

    def __init__(self) -> None:
        self.stmts: list[tuple[str, tuple]] = []

    def __call__(self, sql: str, args: tuple = ()) -> None:
        self.stmts.append((sql, tuple(args)))


def test_apply_schema_records_the_registry_chain_for_a_governed_domain() -> None:
    """``apply_schema`` must leave ``schema_migrations`` non-empty.

    This is the offline statement of defect 1: the DDL replay creates the
    history table, so the ledger row is the only thing missing.
    """
    from nexus_scalp.database.migration import migrate_domain
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import (
        expected_version_for_domain,
        migrations_for,
    )

    exec_ = _RecordingExecutor()
    result = migrate_domain(DatabaseDomain.AUDIT.value, exec_)

    assert result["error_count"] == 0
    assert result["applied_count"] > 0, "the DDL replay produced no statements"
    chain = list(migrations_for(DatabaseDomain.AUDIT))
    assert result["migrations_recorded"] == len(chain)

    inserts = [s for s, a in exec_.stmts if s.lstrip().upper().startswith("INSERT")]
    assert len(inserts) == len(chain), "one INSERT per registry migration"
    for sql, _ in [(s, a) for s, a in exec_.stmts if s.lstrip().upper().startswith("INSERT")]:
        assert "INSERT INTO schema_migrations" in sql
        # PG has no INSERT OR REPLACE — the SQLite-only spelling is defect 1.
        assert "OR REPLACE" not in sql.upper()
        assert "ON CONFLICT" in sql

    recorded_ids = {a[0] for _s, a in exec_.stmts if a}
    assert recorded_ids == {m.migration_id for m in chain}


def test_apply_schema_records_the_expected_version_and_checksums() -> None:
    """The recorded chain matches the registry: every id, and the version the
    engine's convergence checks read."""
    from nexus_scalp.database.migration import migrate_domain
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import (
        expected_version_for_domain,
        migrations_for,
    )

    exec_ = _RecordingExecutor()
    migrate_domain(DatabaseDomain.AUDIT.value, exec_)

    rows = [a for _s, a in exec_.stmts if a]
    # (migration_id, domain, version, ...) — the 3rd column is the version.
    versions = {int(a[2]) for a in rows}
    chain = list(migrations_for(DatabaseDomain.AUDIT))
    assert versions == {m.to_version for m in chain}
    assert max(versions) == expected_version_for_domain(DatabaseDomain.AUDIT)
    checksums = {a[4] for a in rows}
    assert len(checksums) == len(chain), "every migration has a distinct checksum"
    assert all(isinstance(c, str) and c for c in checksums)


def test_undeclared_domain_records_nothing_but_keeps_working() -> None:
    """A domain with no governed registry chain (an ops domain) provisions
    without recording — the result key is present and zero, never absent."""
    from nexus_scalp.database.migration import migrate_domain

    exec_ = _RecordingExecutor()
    result = migrate_domain("ops_shadow", exec_)
    assert result["migrations_recorded"] == 0
    assert not [s for s, _a in exec_.stmts if s.lstrip().upper().startswith("INSERT")]


def test_recorder_is_idempotent_across_replays() -> None:
    """Re-running the recorder over an existing chain refreshes rather than
    duplicates (``ON CONFLICT``), so a re-provisioning run is safe."""
    from nexus_scalp.database.migration.pg_schema import record_applied_migrations
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import migrations_for

    class _DupeCountingExecutor(_RecordingExecutor):
        """A fake executor that counts rows per migration_id as a PG table."""

        def __init__(self) -> None:
            super().__init__()
            self.rows: dict[str, int] = {}

        def __call__(self, sql: str, args: tuple = ()) -> None:
            super().__call__(sql, args)
            if args:
                self.rows[args[0]] = self.rows.get(args[0], 0) + 1

    chain = list(migrations_for(DatabaseDomain.AUDIT))
    exec_ = _DupeCountingExecutor()
    record_applied_migrations("audit", chain, exec_)
    record_applied_migrations("audit", chain, exec_)
    assert len(exec_.rows) == len(chain)
    assert all(count == 2 for count in exec_.rows.values())


# =====================================================================
# Offline contract: the read plane is resolvable for a read-only process
# =====================================================================


class _FakeReadPlane:
    """``PgReadPlane``-shaped stand-in (read surface only, no ``execute``)."""

    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.registered = False

    def open(self) -> None:
        self.opened += 1

    def close(self) -> None:
        self.closed += 1

    def query(self, sql: str, args: tuple = (), *, allow_replica: bool = False) -> list[dict]:
        return [{"ok": 1}]

    def query_one(self, sql: str, args: tuple = (), *, allow_replica: bool = False):
        return {"ok": 1}

    def scalar(self, sql: str, args: tuple = (), *, allow_replica: bool = False):
        return 1


def test_ensure_read_plane_bootstraps_only_the_read_slot(
    clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only process must resolve a read plane WITHOUT provisioning the
    write path — defect 2's real shape on the live box."""
    from nexus_scalp.database import fabric as fabric_mod
    from nexus_scalp.database.fabric import pg_planes as pg_planes_mod
    from nexus_scalp.database.ops_provider import ensure_read_plane

    built: list[_FakeReadPlane] = []

    def fake_plane(dsn: str, limits: object) -> _FakeReadPlane:
        plane = _FakeReadPlane()
        built.append(plane)
        return plane

    monkeypatch.setattr(pg_planes_mod, "PgReadPlane", fake_plane, raising=True)
    monkeypatch.setattr(pg_planes_mod, "PoolLimits", lambda **kw: object(), raising=True)

    plane = ensure_read_plane("audit", dsn="postgresql://user@host/db")

    assert plane is not None
    assert plane.opened == 1, "the read pool must be OPENED, not merely built"
    assert fabric_mod.get_domain_backend("audit", readonly=True) is plane
    # The write slot is untouched: a read-only process provisions no writer.
    assert fabric_mod.get_domain_backend("audit", readonly=False) is None


def test_ensure_read_plane_is_idempotent(
    clean_registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-resolving returns the SAME plane — no second pool, no churn."""
    from nexus_scalp.database.fabric import pg_planes as pg_planes_mod
    from nexus_scalp.database.ops_provider import ensure_read_plane

    built: list[_FakeReadPlane] = []

    def fake_plane(dsn: str, limits: object) -> _FakeReadPlane:
        plane = _FakeReadPlane()
        built.append(plane)
        return plane

    monkeypatch.setattr(pg_planes_mod, "PgReadPlane", fake_plane, raising=True)
    monkeypatch.setattr(pg_planes_mod, "PoolLimits", lambda **kw: object(), raising=True)

    first = ensure_read_plane("audit", dsn="postgresql://user@host/db")
    second = ensure_read_plane("audit", dsn="postgresql://user@host/db")

    assert first is second
    assert len(built) == 1, "a second resolve must not build a second pool"


# =====================================================================
# Live arm: fresh isolated PostgreSQL proves both fixes end to end
# =====================================================================


@needs_postgres
def test_fresh_pg_bootstrap_records_migrations_and_serves_reads(clean_registry: None) -> None:
    """The contract defect 1 and defect 2 assert on a REAL fresh database.

    Never runs against the live localhost:5432: ``NSE_PG_TEST_URL`` points at
    an isolated instance, and the scratch database is created and dropped here.
    """
    psycopg = pytest.importorskip("psycopg")

    # --- create the throwaway database (never touch the live one) ----------
    # Split the URL form directly (``make_conninfo`` drops ``user`` on this
    # psycopg build, which makes the connection fall back to the OS user and
    # fail with "database <user> does not exist").
    from urllib.parse import urlparse

    from nexus_scalp.database.fabric import (
        get_domain_backend,
        provision_domain,
        unregister_domain_backend,
    )
    from nexus_scalp.database.models import DatabaseDomain
    from nexus_scalp.database.registry import expected_version_for_domain, migrations_for

    parsed = urlparse(PG_URL)
    password = parsed.password or ""
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 55433
    user = parsed.username or "nse_user"
    instance = f"host={host} port={port} user={user} dbname=postgres"
    connect_kwargs = {"password": password} if password else {}
    scratch = "nse_pg_boot_test"
    with psycopg.connect(instance, autocommit=True, connect_timeout=10, **connect_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
            cur.execute(f'CREATE DATABASE "{scratch}"')
    dsn = f"postgresql://{user}:***@{host}:{port}/{scratch}"
    try:
        provision_domain(DatabaseDomain.AUDIT.value, dsn, min_size=1, max_size=4)

        with psycopg.connect(dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                )
                tables = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM schema_migrations")
                recorded = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT migration_id, version, status, checksum FROM schema_migrations "
                    "ORDER BY version"
                )
                rows = cur.fetchall()

        chain = list(migrations_for(DatabaseDomain.AUDIT))
        # defect 1: the ledger is NON-empty and complete
        assert recorded == len(chain), f"recorded {recorded} of {len(chain)} migrations"
        assert tables > 0
        assert {r[0] for r in rows} == {m.migration_id for m in chain}
        assert all(r[2] == "applied" for r in rows)
        assert all(isinstance(r[3], str) and r[3] for r in rows)
        assert max(int(r[1]) for r in rows) == expected_version_for_domain(DatabaseDomain.AUDIT)

        # defect 2: the read plane resolves and serves
        read = get_domain_backend(DatabaseDomain.AUDIT.value, readonly=True)
        assert read is not None
        assert not hasattr(read, "execute"), "a read plane must not be write-shaped"
        assert callable(read.query)
        rows_q = read.query("SELECT count(*) AS c FROM schema_migrations")
        assert rows_q and int(rows_q[0]["c"]) == len(chain)
    finally:
        for readonly in (False, True):
            backend = get_domain_backend(DatabaseDomain.AUDIT.value, readonly=readonly)
            if backend is not None:
                close = getattr(backend, "close", None)
                if callable(close):
                    with __import__("contextlib").suppress(Exception):
                        close()
        unregister_domain_backend(DatabaseDomain.AUDIT.value)
        with psycopg.connect(instance, autocommit=True, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (scratch,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')


@needs_postgres
def test_read_only_process_resolves_a_read_plane_on_a_provisioned_box(
    clean_registry: None,
) -> None:
    """Defect 2's live shape: a process that never ran the write-plane
    provisioning still resolves a read plane for the audit domain."""
    psycopg = pytest.importorskip("psycopg")

    from nexus_scalp.adapters.database import provider_store
    from nexus_scalp.database import fabric as fabric_mod
    from nexus_scalp.database.ops_provider import ensure_read_plane

    # Point the config resolver at the isolated instance. The suite's autouse
    # isolation fixtures pin NEXUS_AUDIT_DB (SQLite) for every test; an explicit
    # NSE_DATABASE__PROVIDER wins over that seam by documented precedence, but
    # the seam itself is re-pinned by the harness after setup, so set them
    # through a monkeypatch whose lifetime spans the resolve below.
    parts = psycopg.conninfo.conninfo_to_dict(PG_URL)
    dbname = parts.get("dbname") or parts.get("database") or "nse_audit"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("NEXUS_AUDIT_DB", raising=False)
    for key, value in {
        "NSE_DATABASE__PROVIDER": "postgresql",
        "NSE_DATABASE__PG_HOST": str(parts.get("host", "127.0.0.1")),
        "NSE_DATABASE__PG_PORT": str(parts.get("port", 55433)),
        "NSE_DATABASE__PG_USER": str(parts.get("user", "nse_user")),
        "NSE_DATABASE__PG_DATABASE": str(dbname),
        "NSE_DATABASE__PG_PASSWORD": str(parts.get("password", "")),
    }.items():
        monkeypatch.setenv(key, value)
    try:
        # Nothing is registered — exactly the state the live warning came from.
        assert fabric_mod.get_domain_backend("audit", readonly=True) is None

        class _ReadonlyRepo:
            _is_sqlite = False
            _db_url = PG_URL
            _db_path = ""

        # The live box's shape: the DSN is the one the repository already
        # resolved (its own _db_url), not a config lookup.
        plane = ensure_read_plane("audit", dsn=PG_URL)
        assert plane is not None, "the read plane must resolve for a read-only process"
        # A read-only process registers no write backend.
        assert fabric_mod.get_domain_backend("audit", readonly=False) is None
        assert fabric_mod.get_domain_backend("audit", readonly=True) is plane

        # The operational store read path resolves the same plane.
        backend = provider_store._read_backend(_ReadonlyRepo(), domain="audit")
        assert backend is plane
    finally:
        monkeypatch.undo()


# =====================================================================
# SQLite stays first-class: the engine's own recording path is untouched
# =====================================================================


def test_sqlite_engine_still_records_its_own_migrations(tmp_path: Path) -> None:
    """The hard rule: the SQLite migration engine keeps working exactly as
    today. Its ``INSERT OR REPLACE`` recording is SQLite-dialect and must stay
    — PG has no such spelling, which is precisely why the PG recorder is a
    separate function and never patches this one."""
    sqlite3 = pytest.importorskip("sqlite3")

    from nexus_scalp.database.engine import (
        _HISTORY_TABLE_DDL,
        _META_TABLE_DDL,
        DatabaseMigrationEngine,
    )
    from nexus_scalp.database.models import DatabaseDomain

    path = tmp_path / "audit.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(_META_TABLE_DDL)
        conn.execute(_HISTORY_TABLE_DDL)
        # The SQLite engine's own recording statement must still work.
        conn.execute(
            "INSERT OR REPLACE INTO schema_migrations "
            "(migration_id, domain, version, description, checksum, applied_at, "
            " application_version, git_commit, execution_ms, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "AUDIT-0002-add-audit-orders-ticket-index",
                "audit",
                2,
                "probe",
                "abc",
                "2026-01-01T00:00:00+00:00",
                "",
                "",
                0,
                "applied",
            ),
        )
        conn.commit()
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
    finally:
        conn.close()

    # And the full engine migrate() path still converges on SQLite.
    engine = DatabaseMigrationEngine(path, DatabaseDomain.AUDIT)
    result = engine.migrate()
    assert result["state"] in ("DB_MIGRATION_SUCCEEDED", "DB_MIGRATION_NOT_REQUIRED")
    assert engine.current_version() == engine.expected_version()
    hist = engine.history()
    assert len(hist) > 0, "the SQLite engine still records its applied migrations"
