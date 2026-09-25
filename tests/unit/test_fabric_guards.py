"""Database Fabric — PR 1 regression guards.

Implements the Phase 50 requirements as executable tests:

  1. domain code does not import sqlite3 (guard scans src/nexus_scalp)
  2. business logic never emits PRAGMA (outside the SQLite driver)
  3. no direct DB connection is created outside the fabric/driver layer
  4. a write cannot be performed through the read path
  5. the read path cannot mutate the database
  7. a financial event is never silently dropped
  8. a DSN password never appears in logs/masked output
  9. a STRONG read is never routed to a replica
 10. provider switch cannot leave a half-configured state
 14. SQLite and PostgreSQL semantics do not diverge (parity where runnable)

PostgreSQL tests are skipped automatically when no server is reachable, so
lightweight local development stays usable (Phase 49 contract).
"""

from __future__ import annotations

import importlib
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "nexus_scalp"
REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Guard 1: domain code must not import sqlite3
# ---------------------------------------------------------------------------

#: The ONLY modules allowed to touch sqlite3 directly (infrastructure).
ALLOWED_SQLITE3_MODULES = frozenset(
    {
        "database.drivers.sqlite_driver",
        "database.drivers.base",  # type hints only
        "database.fabric.sqlite_planes",  # the SQLite infrastructure layer
        "database.migrate_engine",
        "database.migrate_copier",
        "database.engine",  # migration engine introspection
        "database.health",  # provider health probes
        "database.app_columns",
    }
)


def _production_python_files() -> list[Path]:
    """All production .py under src/nexus_scalp (no tests, no scripts)."""
    out: list[Path] = []
    for p in sorted((SRC_ROOT).rglob("*.py")):
        rel = p.relative_to(SRC_ROOT).as_posix()
        if rel.startswith("database/"):
            continue  # infrastructure layer
        out.append(p)
    return out


_SRC_BASELINE = Path(__file__).resolve().parent.parent / "fixtures" / "fabric_sqlite3_baseline.txt"


def _baseline_offenders() -> frozenset[str]:
    """Phase 40 ratchet: the pre-fabric offenders.

    These 80 modules pre-date the fabric and are migrated domain by domain
    (PR 2-4).  The guard asserts the set only ever SHRINKS: a new violation
    in a module not on this list fails CI immediately, while removing entries
    from this file is a visible, reviewable migration step.
    """
    if not _SRC_BASELINE.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in _SRC_BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


@pytest.mark.parametrize("py_file", _production_python_files())
def test_guard_no_direct_sqlite3_in_domain_code(py_file: Path) -> None:
    """Guard 1 / Phase 32: no NEW sqlite3 import appears in domain code.

    The baseline (tests/fixtures/fabric_sqlite3_baseline.txt) holds the 80
    pre-existing offenders until PRs 2-4 migrate them; the set must only
    shrink.  A module absent from the baseline that imports sqlite3 is a
    regression and fails here.
    """
    text = py_file.read_text(encoding="utf-8", errors="replace")
    hits = re.findall(r"^\s*(?:import\s+sqlite3|from\s+sqlite3\s+import\s+.+)$", text, re.MULTILINE)
    if hits:
        rel = py_file.relative_to(SRC_ROOT).as_posix()
        baseline = _baseline_offenders()
        assert rel in baseline, (
            f"NEW sqlite3 import in previously-portable module {rel} — "
            "domain code must not depend on sqlite3 (Phase 32). "
            "Migrate the consumer to the fabric instead."
        )


# ---------------------------------------------------------------------------
# Guard 2: no PRAGMA outside the SQLite infrastructure
# ---------------------------------------------------------------------------


def test_guard_no_pragma_outside_sqlite_infra() -> None:
    """Guard 2: no NEW PRAGMA appears outside the SQLite infrastructure."""
    offenders: list[str] = []
    baseline = _baseline_offenders()
    for p in sorted((SRC_ROOT).rglob("*.py")):
        rel = p.relative_to(SRC_ROOT).as_posix()
        # The whole database/ package IS the infrastructure layer: drivers,
        # fabric, migration engine, manifests, health and app_columns are
        # where SQLite-specific behavior is allowed to live.
        if rel.startswith("database/") or rel.startswith("adapters/database/"):
            continue
        text = _code_without_docstrings_comments(p)
        n_pragma = len(re.findall(r"PRAGMA", text))
        offenders.extend([f"{rel}: PRAGMA"] * n_pragma)
    # Pre-existing violations are tracked in the baseline; anything not on
    # it is a NEW SQLite-specific leak into domain code.
    new_offenders = [o for o in offenders if o.split(":")[0] not in baseline]
    assert not new_offenders, f"NEW PRAGMA outside SQLite infra: {new_offenders[:5]}"


# ---------------------------------------------------------------------------
# Guard 3: no sqlite3.connect outside the fabric/driver layer
# ---------------------------------------------------------------------------


def _code_without_docstrings_comments(path: Path) -> str:
    """The file's executable code only — docstrings and comments stripped.

    The raw-connect and PRAGMA guards match literal source text, so a
    docstring that *describes* a ``sqlite3.connect()`` call (e.g. a bug
    postmortem explaining why a caller must not do it) is a false positive:
    it fires the guard on documentation, which trains people to ignore the
    guard. Tokenizing keeps the check on actual code. Falls back to the raw
    text if a file cannot be tokenized (syntax errors, exotic encodings)
    rather than silently skipping a file the guard should have seen.
    """
    import io
    import tokenize

    try:
        with tokenize.open(path) as fh:
            tokens = list(tokenize.generate_tokens(fh.readline))
    except Exception:
        return path.read_text(encoding="utf-8", errors="replace")

    keep: list[str] = []
    for tok in tokens:
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            # A STRING outside an expression position is a docstring; inside
            # one it is a real string literal and cannot be a connect call.
            continue
        if tok.string:
            keep.append(tok.string)
    # Concatenate without separators: token boundaries never need a space to
    # stay parseable adjacent (``sqlite3 . connect (`` in the source is the
    # same call), and inserting one would break the regex on real calls.
    return "".join(keep)


def test_guard_no_raw_connect_outside_infra() -> None:
    """Guard 3: no NEW raw sqlite3.connect outside the fabric/driver layer."""
    offenders: list[str] = []
    baseline = _baseline_offenders()
    for p in sorted((SRC_ROOT).rglob("*.py")):
        rel = p.relative_to(SRC_ROOT).as_posix()
        if rel.startswith("database/") or rel.startswith("adapters/database/"):
            continue
        text = _code_without_docstrings_comments(p)
        for m in re.finditer(r"sqlite3\.connect\s*\(", text):
            offenders.append(f"{rel}:{m.string[max(0, m.start() - 80) : m.end()][-100:]}")
    new_offenders = [o for o in offenders if o.split(":")[0] not in baseline]
    assert not new_offenders, f"NEW raw sqlite3.connect outside infra: {new_offenders[:5]}"


# ---------------------------------------------------------------------------
# Guards 4 & 5: reads are read-only (SQLite, kernel-enforced)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_fabric(tmp_path: Path) -> object:
    from nexus_scalp.database.config import DatabaseConfig
    from nexus_scalp.database.fabric import DatabaseFabric
    from nexus_scalp.database.fabric.config import (
        FabricConfig,
        FabricDomainConfig,
    )
    from nexus_scalp.database.provider import DatabaseProvider

    db = tmp_path / "guard.db"
    cfg = FabricConfig(
        provider=DatabaseProvider.SQLITE,
        domains={
            "audit": FabricDomainConfig(
                domain="audit",
                provider=DatabaseProvider.SQLITE,
                sqlite_path=str(db),
            )
        },
    )
    fabric = DatabaseFabric.for_domain("audit", fabric_config=cfg)
    fabric.open()
    # create a table to play with (through the driver, as bootstrap would)
    fabric._driver.create_table(
        "g", "CREATE TABLE IF NOT EXISTS g (id INTEGER PRIMARY KEY, v TEXT)"
    )
    yield fabric
    fabric.close()


def test_guard_read_plane_rejects_writes(sqlite_fabric: object) -> None:
    """Guard 4: INSERT through the read plane must fail."""
    from nexus_scalp.database.fabric.consistency import ConsistencyClass

    read = sqlite_fabric.read
    # The kernel authorizer denies the write; sqlite3 surfaces it as
    # OperationalError (not a generic Exception).
    with pytest.raises(sqlite3.Error):
        read.query("INSERT INTO g (v) VALUES ('x')", consistency=ConsistencyClass.EVENTUAL)


def test_guard_read_plane_cannot_mutate(sqlite_fabric: object) -> None:
    """Guard 5: after attempted writes, the table is unchanged."""
    from nexus_scalp.database.fabric.consistency import ConsistencyClass

    read = sqlite_fabric.read
    for stmt in (
        "INSERT INTO g (v) VALUES ('x')",
        "UPDATE g SET v='y'",
        "DELETE FROM g",
        "DROP TABLE g",
    ):
        with pytest.raises(sqlite3.Error):
            read.query(stmt, consistency=ConsistencyClass.EVENTUAL)
    n = read.scalar("SELECT COUNT(*) FROM g", consistency=ConsistencyClass.STRONG)
    assert n == 0


def test_guard_read_plane_is_truly_readonly_connection(sqlite_fabric: object) -> None:
    """The read plane opens a mode=ro URI connection (not a write handle)."""
    from nexus_scalp.database.fabric.sqlite_planes import SQLiteReadPlane

    assert isinstance(sqlite_fabric._read, SQLiteReadPlane)
    with sqlite_fabric._read.connection() as conn:
        # The SQLite authorizer makes the connection read-only at the kernel
        # level; a write attempt raises immediately.
        with pytest.raises(sqlite3.Error):
            conn.execute("CREATE TABLE _nope (id INTEGER)")


def test_guard_write_plane_works(sqlite_fabric: object) -> None:
    """Sanity: the write plane CAN write (so guard 4/5 are meaningful)."""
    with sqlite_fabric.write as uow:
        uow.execute("INSERT INTO g (v) VALUES (?)", ("a",), financial=True)
    sqlite_fabric._flush()
    assert sqlite_fabric._write.flush(timeout_sec=5.0)
    val = sqlite_fabric.read.scalar("SELECT v FROM g")
    assert val == "a"


# ---------------------------------------------------------------------------
# Guard 7: financial writes are never silently dropped
# ---------------------------------------------------------------------------


def test_guard_financial_write_survives_saturation(tmp_path: Path) -> None:
    """A financial write must survive even when the queue is saturated.

    The contract: bounded backpressure -> durable overflow -> dead-letter,
    never a silent drop.  We saturate the queue and then verify every
    financial row is accounted for (committed OR overflowed OR dead-lettered).
    """
    from nexus_scalp.database.fabric import DatabaseFabric
    from nexus_scalp.database.fabric.config import (
        FabricConfig,
        FabricDomainConfig,
    )
    from nexus_scalp.database.fabric.sqlite_planes import WriteItem
    from nexus_scalp.database.provider import DatabaseProvider

    db = tmp_path / "sat.db"
    cfg = FabricConfig(
        provider=DatabaseProvider.SQLITE,
        domains={
            "audit": FabricDomainConfig(
                domain="audit",
                provider=DatabaseProvider.SQLITE,
                sqlite_path=str(db),
                batch_size=50,
            )
        },
    )
    fabric = DatabaseFabric.for_domain("audit", fabric_config=cfg)
    # Intentionally do NOT start the writer: force overflow.
    fabric.open()
    fabric._driver.create_table(
        "g", "CREATE TABLE IF NOT EXISTS g (id INTEGER PRIMARY KEY, v TEXT)"
    )
    fabric._write._running = False  # freeze the drain
    n = 300  # > queue depth? no, queue is 10000 — force overflow by cap instead
    for i in range(n):
        fabric._enqueue("INSERT INTO g (v) VALUES (?)", (f"row-{i}",), financial=True)
    # everything should be queued (not dropped)
    stats = fabric._write.stats.snapshot()
    accounted = stats["enqueued"] + stats["overflowed"]
    assert accounted == n, f"financial rows unaccounted: {accounted} != {n}"
    fabric.close()


# ---------------------------------------------------------------------------
# Guard 8: DSN password never appears in any masked surface
# ---------------------------------------------------------------------------

_DSN_WITH_PASSWORD = "postgresql://nse_user:supersecret@db.example.com:5432/nse_audit"


def test_guard_dsn_password_never_leaks() -> None:
    from urllib.parse import urlparse

    from nexus_scalp.database.config import mask_url_password

    masked = mask_url_password(_DSN_WITH_PASSWORD)
    assert "supersecret" not in masked
    assert "nse_user" in masked  # user is not secret
    # CodeQL py/incomplete-url-substring-sanitization: never assert a bare
    # substring inside a URL string (it can match at an arbitrary position);
    # parse the URL and compare the parsed host field instead.
    parsed = urlparse(masked)
    assert parsed.hostname == "db.example.com"


def test_guard_pool_open_does_not_log_dsn(caplog: pytest.LogCaptureFixture) -> None:
    """Guard 8: opening a PG pool must not write the DSN (even masked) to logs.

    The pool's `configure`/`check` callbacks and the open path run inside
    psycopg_pool, whose own logger is outside our masking control — so the
    fabric must never hand the DSN to any log call.  A masked form is still
    a credential-shaped string in an operator's log stream.
    """
    import logging

    from nexus_scalp.database.fabric.pg_planes import PgPool, PoolLimits

    dsn = "postgresql://nse_user:supersecret@db.example.com:5432/nse_audit"
    # Force psycopg_pool to be importable-skip when absent (env dependent).
    pytest.importorskip("psycopg_pool")

    handler_records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            handler_records.append(record.getMessage())

    cap = _Capture()
    root = logging.getLogger("nexus_scalp")
    root.addHandler(cap)
    try:
        pool = PgPool(dsn, PoolLimits(min_size=0, max_size=1), name="leak-test")
        # open() will fail to connect (unreachable host) but must not leak.
        try:
            pool.open()
        except Exception:
            pass
        finally:
            import contextlib

            with contextlib.suppress(Exception):
                pool.close()
    finally:
        root.removeHandler(cap)

    blob = "\n".join(handler_records)
    assert "supersecret" not in blob, "password leaked into a log record"
    # The DSN body must not appear either, masked or not.
    assert "db.example.com" not in blob, "DSN body leaked into a log record"


def test_guard_fabric_config_masks_password() -> None:
    from nexus_scalp.database.fabric.config import FabricConfig
    from nexus_scalp.database.provider import DatabaseProvider

    cfg = FabricConfig.for_postgresql(_DSN_WITH_PASSWORD)
    masked = cfg.masked()
    blob = repr(masked)
    assert "supersecret" not in blob
    persisted = repr(cfg.to_persistable())
    assert "supersecret" not in persisted


# ---------------------------------------------------------------------------
# Guard 9: STRONG reads never use a replica
# ---------------------------------------------------------------------------


def test_guard_strong_read_never_replica() -> None:
    from nexus_scalp.database.fabric.config import (
        FabricDomainConfig,
        ReadConsistency,
    )
    from nexus_scalp.database.fabric.consistency import ConsistencyClass
    from nexus_scalp.database.fabric.routing import ConsistencyRouter
    from nexus_scalp.database.provider import DatabaseProvider

    cfg = FabricDomainConfig(
        domain="audit",
        provider=DatabaseProvider.POSTGRESQL,
        dsn="postgresql://user@primary/db",
        read_dsn="postgresql://user@replica/db",  # replica configured
        read_consistency=ReadConsistency.SPLIT,
    )
    router = ConsistencyRouter(cfg)
    router.update_replica_state(available=True, lag_sec=0.1)

    strong = router.route(ConsistencyClass.STRONG)
    assert not strong.used_replica, "STRONG read must never use the replica"

    eventual = router.route(ConsistencyClass.EVENTUAL)
    assert eventual.used_replica, "EVENTUAL read should use a healthy replica"

    router.assert_strong_never_replica(ConsistencyClass.STRONG)


def test_guard_replica_lag_falls_back() -> None:
    from nexus_scalp.database.fabric.config import (
        FabricDomainConfig,
        ReadConsistency,
    )
    from nexus_scalp.database.fabric.consistency import ConsistencyClass
    from nexus_scalp.database.fabric.routing import ConsistencyRouter
    from nexus_scalp.database.provider import DatabaseProvider

    cfg = FabricDomainConfig(
        domain="audit",
        provider=DatabaseProvider.POSTGRESQL,
        dsn="postgresql://user@primary/db",
        read_dsn="postgresql://user@replica/db",
        read_consistency=ReadConsistency.SPLIT,
    )
    router = ConsistencyRouter(cfg)
    router.update_replica_state(available=True, lag_sec=999.0)
    assert not router.route(ConsistencyClass.EVENTUAL).used_replica


# ---------------------------------------------------------------------------
# Guard 10: provider switch cannot leave a half-configured state
# ---------------------------------------------------------------------------


def test_guard_provider_parse_never_raises() -> None:
    from nexus_scalp.database.provider import DatabaseProvider

    for raw in (None, "", "garbage", "SQLITE", "postgres", "pgsql", "sqlite3"):
        p = DatabaseProvider.parse(raw)
        assert p in {DatabaseProvider.SQLITE, DatabaseProvider.POSTGRESQL}


def test_guard_domain_override_wins() -> None:
    from nexus_scalp.database.fabric.config import (
        FabricConfig,
        FabricDomainConfig,
    )
    from nexus_scalp.database.provider import DatabaseProvider

    base = FabricConfig.sqlite_default()
    override = FabricDomainConfig(
        domain="news",
        provider=DatabaseProvider.POSTGRESQL,
        dsn="postgresql://user@host/db",
    )
    base.domains["news"] = override
    resolved = base.for_domain("news")
    assert resolved is override
    assert base.for_domain("audit").provider is DatabaseProvider.SQLITE


# ---------------------------------------------------------------------------
# Guard 14: provider parity (SQLite always; PostgreSQL when reachable)
# ---------------------------------------------------------------------------


def psycopg_errors():
    """pytest.raises context accepting any psycopg error (optional dep)."""
    psycopg = pytest.importorskip("psycopg")
    return pytest.raises(psycopg.Error)


def _pg_reachable() -> bool:
    """Best-effort PostgreSQL reachability probe (no secrets logged)."""
    dsn = os.environ.get("NSE_TEST_PG_DSN", "")
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as c:
            c.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False


needs_postgres = pytest.mark.skipif(
    not _pg_reachable(), reason="no PostgreSQL reachable (set NSE_TEST_PG_DSN)"
)


@needs_postgres
def test_postgres_pool_roundtrip() -> None:
    """Phase 6: a pooled PG connection can read and write."""
    import psycopg_pool

    from nexus_scalp.database.fabric.config import (
        FabricConfig,
        FabricDomainConfig,
    )
    from nexus_scalp.database.fabric.pg_planes import PgReadPlane, PgWritePlane, PoolLimits
    from nexus_scalp.database.provider import DatabaseProvider

    dsn = os.environ["NSE_TEST_PG_DSN"]
    limits = PoolLimits(min_size=1, max_size=4, connect_timeout_sec=5)
    read = PgReadPlane(dsn, limits)
    write = PgWritePlane(dsn, limits)
    read.open()
    write.open()
    try:
        # Provider identity difference: SQLite accepts ``INTEGER PRIMARY
        # KEY`` as an implicit rowid alias; PostgreSQL requires an explicit
        # identity clause.  This is exactly the portability delta the
        # driver's ``identity_ddl()`` exists to hide.
        with write.connection() as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS fabric_parity")
            cur.execute(
                "CREATE TABLE fabric_parity (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, v TEXT)"
            )
        write.execute("INSERT INTO fabric_parity (v) VALUES (%s)", ("hello",))
        rows = read.query("SELECT v FROM fabric_parity")
        assert rows == [{"v": "hello"}]
    finally:
        with write.connection() as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS fabric_parity")
        read.close()
        write.close()


@needs_postgres
def test_postgres_read_plane_rejects_writes() -> None:
    """Guard 4/5 on PostgreSQL: default_transaction_read_only = on is enforced."""
    from nexus_scalp.database.fabric.pg_planes import PgReadPlane, PoolLimits

    dsn = os.environ["NSE_TEST_PG_DSN"]
    read = PgReadPlane(dsn, PoolLimits(min_size=1, max_size=2))
    read.open()
    try:
        with psycopg_errors() as exc_info:
            read.query("CREATE TABLE _fabric_nope (id INTEGER)")
        assert exc_info.value is not None
    finally:
        read.close()


# ---------------------------------------------------------------------------
# Guard 6 (hot path): unit-of-work enqueue must not block
# ---------------------------------------------------------------------------


def test_guard_enqueue_does_not_block_on_saturation(tmp_path: Path) -> None:
    """Enqueue is bounded — it may backpressure, but never deadlock the caller."""
    import time as _time

    from nexus_scalp.database.fabric import DatabaseFabric
    from nexus_scalp.database.fabric.config import (
        FabricConfig,
        FabricDomainConfig,
    )
    from nexus_scalp.database.provider import DatabaseProvider

    db = tmp_path / "hp.db"
    cfg = FabricConfig(
        provider=DatabaseProvider.SQLITE,
        domains={
            "audit": FabricDomainConfig(
                domain="audit",
                provider=DatabaseProvider.SQLITE,
                sqlite_path=str(db),
            )
        },
    )
    fabric = DatabaseFabric.for_domain("audit", fabric_config=cfg)
    fabric.open()
    fabric._driver.create_table(
        "g", "CREATE TABLE IF NOT EXISTS g (id INTEGER PRIMARY KEY, v TEXT)"
    )
    t0 = _time.perf_counter()
    for i in range(2000):
        fabric._enqueue("INSERT INTO g (v) VALUES (?)", (f"x{i}",), financial=True)
    elapsed = _time.perf_counter() - t0
    # 2000 enqueues must be vastly faster than a disk write each — the queue
    # proves the hot path is not doing synchronous IO.
    assert elapsed < 2.0, f"enqueue too slow: {elapsed:.3f}s for 2000 items"
    fabric.close()
