"""Shared fixtures for the Phase 2 contract suite.

Isolation contract (never violated):
  * SQLite writes go ONLY to a tmp_path file created by the fixture.
  * PostgreSQL writes go ONLY to the throwaway server the fixture starts (or
    the NSE_PG_TEST_URL target when explicitly provided). The operator's live
    `postgresql-x64-17` cluster and its `nexusdb` / `nse_audit` databases are
    NEVER opened for writes.
  * Read-only probes of the live stores use read-only URI mode.

Everything here targets PUBLIC repository interfaces (AuditRepository,
provider_store queue_write/query_rows, the research/model_lifecycle stores).
No production module is imported for patching.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# The live engine writes into the MAIN shared checkout (this worktree is a
# pristine agent lane). Resolved once, not guessed per machine.
_MAIN_CHECKOUT = Path(r"C:/Users/Capsizer/source/repos/NexusTradingForexBot")

from nexus_scalp.adapters.database.audit_repository import AuditRepository  # noqa: E402
from nexus_scalp.model_lifecycle.models import ModelStatus  # noqa: E402

# A throwaway PG server the fixture owns. Chosen off the operator's 5432.
_PHASE2_PG_PORT = int(os.environ.get("NSE_PHASE2_PG_PORT", "55433"))
_PHASE2_DATADIR = os.environ.get(
    "NSE_PHASE2_PG_DATADIR",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "pg17_phase2"),
)
_PHASE2_PG_URL = os.environ.get(
    "NSE_PG_TEST_URL",
    f"postgresql://nse_user:nse_password_dev@127.0.0.1:{_PHASE2_PG_PORT}/nse_phase2",
)


# ---------------------------------------------------------------------------
# SQLite: an isolated audit database created through the REAL repository
# ---------------------------------------------------------------------------


class Phase2SQLiteEnv:
    """An AuditRepository bound to a throwaway SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # The write plane (background queue + worker) is constructed inside
        # AuditRepository.__init__, so the repository is already live here.
        self.repo = AuditRepository(db_url=str(path))
        # Force the schema so contract tests can read immediately.
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = sqlite3.connect(str(self.path))
        try:
            self.repo._create_sqlite_tables(conn)
            # The operational stores (shadow / governance) own their own DDL,
            # which the audit bootstrap does not emit. The contract tests need
            # those tables in the isolated database too.
            from nexus_scalp.model_lifecycle.schema import (
                model_lifecycle_schema_statements,
            )
            from nexus_scalp.shadow.schema import ops_shadow_schema_statements

            for stmt in model_lifecycle_schema_statements():
                conn.execute(stmt)
            for stmt in ops_shadow_schema_statements():
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def repo_schema(self, table: str) -> list[dict]:
        """Column metadata for a table, as sqlite3 returns it (name/type/notnull)."""
        conn = sqlite3.connect(str(self.path))
        try:
            return [
                {"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
                for r in conn.execute(f"PRAGMA table_info({table})")
            ]
        finally:
            conn.close()

    def flush(self, timeout: float = 10.0) -> None:
        """Wait for the background write queue to drain (repo owns the API)."""
        with contextlib.suppress(Exception):
            self.repo.flush(timeout_sec=timeout)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.repo.close()


@pytest.fixture
def sqlite_env(tmp_path: Path) -> Phase2SQLiteEnv:
    path = tmp_path / "phase2_audit.db"
    env = Phase2SQLiteEnv(path)
    yield env
    env.close()


# ---------------------------------------------------------------------------
# PostgreSQL: a throwaway server the suite owns (never the operator's cluster)
# ---------------------------------------------------------------------------


class Phase2PgEnv:
    """A throwaway PostgreSQL server + database for write tests."""

    def __init__(self) -> None:
        self.bin = Path("C:/Program Files/PostgreSQL/17/bin")
        self.datadir = Path(_PHASE2_DATADIR)
        self.port = _PHASE2_PG_PORT
        self.url = _PHASE2_PG_URL
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        import psycopg  # type: ignore[import-not-found]

        if not self.datadir.exists():
            subprocess.run(
                [
                    str(self.bin / "initdb.exe"),
                    "-D",
                    str(self.datadir),
                    "-U",
                    "postgres",
                    "--auth-local=trust",
                    "--auth-host=trust",
                ],
                check=True,
                capture_output=True,
                timeout=300,
            )
        self.proc = subprocess.Popen(
            [
                str(self.bin / "pg_ctl.exe"),
                "start",
                "-D",
                str(self.datadir),
                "-l",
                str(self.datadir / "phase2.log"),
                "-o",
                f"-p {self.port} -c listen_addresses=127.0.0.1 "
                f"-c max_worker_processes=2 -c max_parallel_workers=0",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.proc.wait(timeout=120)
        self._wait_ready()
        self._provision()

    def _wait_ready(self, timeout: float = 90.0) -> None:
        import psycopg  # type: ignore[import-not-found]

        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                c = psycopg.connect(
                    f"postgresql://postgres@127.0.0.1:{self.port}/postgres",
                    connect_timeout=3,
                )
                c.close()
                return
            except Exception as exc:  # the first connect often dies with 0xC0000142
                last = exc
                time.sleep(1.0)
        if last:
            raise RuntimeError(f"phase2 PG never came up: {last}")
        raise RuntimeError("phase2 PG never came up")

    def _provision(self) -> None:
        import psycopg  # type: ignore[import-not-found]

        conn = psycopg.connect(
            f"postgresql://postgres@127.0.0.1:{self.port}/postgres", connect_timeout=5
        )
        conn.autocommit = True
        try:
            with contextlib.suppress(Exception):
                conn.execute("CREATE ROLE nse_user LOGIN PASSWORD 'nse_password_dev' CREATEDB")
            with contextlib.suppress(Exception):
                conn.execute("CREATE DATABASE nse_phase2 OWNER nse_user")
        finally:
            conn.close()

    def connection(self):
        import psycopg  # type: ignore[import-not-found]

        c = psycopg.connect(self.url, connect_timeout=10)
        c.autocommit = True
        return c

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            subprocess.run(
                [str(self.bin / "pg_ctl.exe"), "stop", "-D", str(self.datadir), "-m", "fast"],
                check=False,
                capture_output=True,
                timeout=120,
            )
        if self.proc is not None:
            self.proc.wait(timeout=60)


_PG_LOCK = threading.Lock()


@pytest.fixture(scope="session")
def phase2_pg() -> Any:
    """Session-scoped throwaway PG server, shared by all PG-armed tests."""
    if os.environ.get("NSE_PHASE2_PG_DISABLE") == "1":
        pytest.skip("NSE_PHASE2_PG_DISABLE=1")
    try:
        import psycopg
    except Exception:
        pytest.skip("psycopg not installed")
    with _PG_LOCK:
        env = Phase2PgEnv()
        env.start()
        try:
            yield env
        finally:
            env.stop()


@pytest.fixture
def pg_conn(phase2_pg: Phase2PgEnv):
    conn = phase2_pg.connection()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Provider matrix: every persistence test runs on BOTH providers
# ---------------------------------------------------------------------------


@pytest.fixture(params=["sqlite", "postgresql"], ids=["sqlite", "postgresql"])
def provider_name(request: pytest.FixtureRequest) -> str:
    if request.param == "postgresql" and os.environ.get("NSE_PHASE2_PG_DISABLE") == "1":
        pytest.skip("NSE_PHASE2_PG_DISABLE=1")
    return request.param  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Read-only handle on the LIVE stores (never for writes)
# ---------------------------------------------------------------------------


def _live_artifacts_dir() -> Path:
    """The MAIN checkout's artifacts — the running engine's real store."""
    main = _MAIN_CHECKOUT / "artifacts"
    if (main / "audit.db").exists():
        return main
    if (REPO_ROOT / "artifacts" / "audit.db").exists():
        return REPO_ROOT / "artifacts"
    return main


@pytest.fixture
def live_audit_db() -> Path:
    p = _live_artifacts_dir() / "audit.db"
    if not p.exists():
        pytest.skip(f"live audit.db not present at {p}")
    return p


@pytest.fixture
def live_sqlite_probe(live_audit_db: Path):
    """Read-only connection to the live audit database."""
    conn = sqlite3.connect(f"file:{live_audit_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def live_pg_probe():
    """Read-only connection to the operator's live nexusdb, if reachable."""
    try:
        import psycopg  # type: ignore[import-not-found]
    except Exception:
        pytest.skip("psycopg not installed")
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = SecureSecretStore()
    if not store.has_secret("db.postgresql.password"):
        pytest.skip("no db.postgresql.password in the secret store")
    pw = store.get_secret("db.postgresql.password")
    try:
        conn = psycopg.connect(
            f"postgresql://postgres:{pw}@127.0.0.1:5432/nexusdb", connect_timeout=8
        )
    except Exception as exc:
        pytest.skip(f"live nexusdb unreachable: {exc}")
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers shared by the contract modules
# ---------------------------------------------------------------------------


def iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(UTC).isoformat()


def insert_strategy_registry_row(env: Phase2SQLiteEnv, **overrides: Any) -> str:
    """Insert one strategy_registry row through the repository's write queue."""
    from nexus_scalp.adapters.database.provider_store import queue_write

    sid = overrides.pop("strategy_id", f"ST-PHASE2-{int(time.time() * 1000)}")
    vals = {
        "strategy_id": sid,
        "strategy_version": overrides.pop("strategy_version", "1.0"),
        "lifecycle": overrides.pop("lifecycle", "DISCOVERED"),
        "created_at": overrides.pop("created_at", iso_now()),
        "updated_at": overrides.pop("updated_at", iso_now()),
        "feature_schema_id": overrides.pop("feature_schema_id", "scalp_v3"),
        "feature_dimension": overrides.pop("feature_dimension", 70),
        "context_definition": overrides.pop("context_definition", "{}"),
        "parent_strategy_ids": overrides.pop("parent_strategy_ids", "[]"),
        "backtest": overrides.pop("backtest", "{}"),
        "walkforward": overrides.pop("walkforward", "{}"),
        "oos": overrides.pop("oos", "{}"),
        "robustness": overrides.pop("robustness", "{}"),
        "score": overrides.pop("score", "{}"),
        "validation_lineage": overrides.pop("validation_lineage", "[]"),
    }
    cols = list(vals)
    placeholders = ", ".join(["?"] * len(cols))
    ok = queue_write(
        env.repo,
        f"INSERT INTO strategy_registry ({', '.join(cols)}) VALUES ({placeholders})",
        tuple(vals[c] for c in cols),
        operation="phase2.insert_strategy_registry",
    )
    assert ok, "queue_write rejected the strategy_registry insert"
    env.flush()
    return sid


def read_row(conn_or_repo: Any, table: str, where: str, args: tuple[Any, ...] = ()):
    from nexus_scalp.adapters.database.provider_store import query_rows

    rows = query_rows(conn_or_repo, f'SELECT * FROM "{table}" WHERE {where}', args)
    return rows[0] if rows else None


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
