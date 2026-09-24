"""PG-READ-PLANE-001 / Lane B — safety-state persistence on a pooled provider.

THE DEFECT (verified, CAPITAL PROTECTION):

    Under provider=postgresql ``AuditRepository._db_path == ""`` (set at
    construction: ``... if self._is_sqlite else ""``), so the safety-state
    methods' ``self._connect_sqlite(10.0)`` became ``sqlite3.connect("")`` —
    a throwaway TEMP database destroyed when the connection closed. Live
    engine logs, verbatim:

        [error] runtime_risk_state persist FAILED state=RUNNING
                error=no such table: runtime_risk_state
        [error] breaker anchor read FAILED (treated as absent)
                error=no such table: runtime_risk_state

    The schema DID exist in PostgreSQL (the migration logged
    ``applied=128 errors=0``), but the write never reached it: the live
    ``nexusdb`` had ``runtime_risk_state`` = 0 rows while the engine logged
    RUNNING. On restart the anchors were treated as absent, so the daily /
    weekly loss budgets re-anchored to CURRENT equity and a loss taken
    before the restart vanished from the budget accounting (the BUG-259
    regression, but only under PostgreSQL).

THE FIX (inside ``audit_repository.py`` only):

    Every safety-critical read/write gains a NEW ``if not self._is_sqlite``
    branch. The SQLite branch is untouched (byte-for-byte contract):
      * ``set_runtime_risk_state`` / ``save_breaker_anchors`` execute
        SYNCHRONOUSLY against the fabric's pooled WRITE backend — the bool
        they return is a safety gate, so it must reflect real durability,
        not a queue that may still be in flight;
      * ``get_breaker_anchors`` routes through the fabric READ plane via the
        existing ``_provider_read_guard`` seam, and keeps returning None for
        corrupt / absent anchors (fail closed);
      * ``_db_path`` exposes a real, non-empty location under a pooled
        provider so consumers that build a ``Path`` from it (debug_snapshot's
        schema probe) stop raising ``WindowsPath('.') has an empty name``.

WHAT IS PINNED HERE

    The unit tests mock the fabric planes (no live server) and assert the
    routing + fail-closed contracts; the PostgreSQL arm proves the real
    end-to-end round trip when ``NSE_PG_TEST_URL`` is set (the suite's
    established convention — ``tests/unit/test_rtf001_real_postgresql_schema.py``).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.adapters.database.audit_repository import (
    AuditRepository,
)

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_postgres = pytest.mark.skipif(
    not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)"
)


# =====================================================================
# Test doubles
# =====================================================================


class _FakeWriteBackend:
    """Records what a synchronous safety write sent to the pooled backend.

    ``execute_batch`` is the exact seam ``_provider_execute_write`` calls, so
    this pins the real call shape (statement list of (query, rows) pairs) and
    lets a test inject a failure to assert fail-closed.
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.batches: list[Sequence[tuple[str, Sequence[Sequence[Any]]]]] = []
        self.fail = fail

    def execute_batch(self, statements: Sequence[tuple[str, Sequence[Sequence[Any]]]]) -> None:
        self.batches.append(statements)
        if self.fail:
            raise RuntimeError("simulated pool failure")

    @property
    def executed_queries(self) -> list[str]:
        return [q for batch in self.batches for q, _ in batch]


class _FakeReadPlane:
    """A read-shaped plane (``query``/``query_one``/``scalar`` only).

    ``_registered_audit_read_plane`` refuses any backend exposing ``execute``
    (write-shaped), so this must NOT define it.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def open(self) -> None:
        """Registry planes are opened before registration (no-op here)."""

    def query(
        self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False
    ) -> list[dict[str, Any]]:
        self.queries.append((sql, tuple(args)))
        return list(self.rows)

    def query_one(
        self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False
    ) -> dict[str, Any] | None:
        self.queries.append((sql, tuple(args)))
        return self.rows[0] if self.rows else None

    def scalar(self, sql: str, args: Sequence[Any] = (), *, allow_replica: bool = False) -> Any:
        self.queries.append((sql, tuple(args)))
        return self.rows[0][next(iter(self.rows[0]))] if self.rows else None


@pytest.fixture()
def pg_registry(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Isolate the process-global fabric registry for this process.

    ``provision_domain`` / ``AuditRepository._build_pooled_write_backend``
    resolve the REAL registry lazily at call time, so the unit tests must
    reach the real ``get_domain_backend`` / ``register_domain_read_backend``
    code path — monkeypatching them away would test nothing. Instead the
    registry is CLEARED before and after each test so no cross-test state can
    leak, and every test registers its own fake planes.
    """
    from nexus_scalp.database.fabric import (
        _DOMAIN_BACKEND_LOCK,
        _DOMAIN_BACKENDS,
    )

    with _DOMAIN_BACKEND_LOCK:
        saved = dict(_DOMAIN_BACKENDS)
        _DOMAIN_BACKENDS.clear()
    yield
    with _DOMAIN_BACKEND_LOCK:
        _DOMAIN_BACKENDS.clear()
        _DOMAIN_BACKENDS.update(saved)


def _make_pg_repo(
    db_url: str = "postgresql://localhost:5432/nse_audit",
) -> AuditRepository:
    """A non-SQLite repository WITHOUT a live server.

    ``__new__`` is the established shape for provider-gated unit tests
    (``tests/unit/test_provider_read_guard_observability_chg0067.py`` and
    ``tests/unit/test_audit_flush_contract.py`` build exactly this): the
    safety methods resolve the fabric registry at call time, so a full
    ``__init__`` (which would provision real pools) is not needed to pin
    their contracts.
    """
    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = db_url
    repo._db_path = repo._provider_db_path()
    repo.provider_reads_routed = 0
    repo.provider_read_route_errors = 0
    repo.provider_read_degraded_total = 0
    repo.provider_read_degraded_ops = {}
    repo._provider_read_guard_state = {}
    return repo


def _wire_write_backend(backend: Any, domain: str = "audit") -> Any:
    """Register a fake WRITE backend on the real registry path."""
    from nexus_scalp.database.fabric import register_domain_backend

    register_domain_backend(domain, backend)
    return backend


def _wire_read_plane(plane: Any, domain: str = "audit") -> Any:
    """Register a fake READ plane on the real registry path."""
    from nexus_scalp.database.fabric import register_domain_read_backend

    register_domain_read_backend(domain, plane)
    return plane


# =====================================================================
# 1. SQLite is byte-for-byte unchanged
# =====================================================================


def test_sqlite_still_uses_the_direct_connection(tmp_path: Path) -> None:
    """The SQLite branch must never touch the provider write seam."""
    path = tmp_path / "safety.db"
    repo = AuditRepository(db_url=f"sqlite:///{path}")
    try:
        assert repo._is_sqlite is True
        assert repo._provider_write_backend() is None
        assert repo.set_runtime_risk_state(state="RUNNING") is True
        assert repo.save_breaker_anchors(
            day_anchor=100.0, day_utc="2026-09-24", week_anchor=100.0, week_iso="2026-W39"
        )
        anchors = repo.get_breaker_anchors()
    finally:
        repo.close()
    assert anchors == {
        "day_anchor": 100.0,
        "day_utc": "2026-09-24",
        "week_anchor": 100.0,
        "week_iso": "2026-W39",
    }


def test_sqlite_db_path_unchanged(tmp_path: Path) -> None:
    """D9 fix is additive: SQLite keeps its filesystem path verbatim."""
    path = tmp_path / "safety.db"
    repo = AuditRepository(db_url=f"sqlite:///{path}")
    try:
        assert repo._db_path.endswith("safety.db")
        assert repo._provider_db_path() == repo._db_path
    finally:
        repo.close()


# =====================================================================
# 2. The provider write path routes through the pooled WRITE backend
# =====================================================================


def test_set_runtime_risk_state_routes_to_the_write_backend(pg_registry) -> None:
    backend = _wire_write_backend(_FakeWriteBackend())
    repo = _make_pg_repo()
    assert (
        repo.set_runtime_risk_state(
            state="halted",
            reason="daily-loss-budget",
            source="breaker",
            balance=100_000.0,
            equity=97_000.0,
            peak_equity=100_000.0,
            consecutive_losses=3,
        )
        is True
    )

    assert len(backend.batches) == 1
    batch = backend.batches[0]
    # one statement, one row — execute_batch takes (query, rows) pairs
    assert len(batch) == 1
    query, rows = batch[0]
    assert len(rows) == 1
    assert "INSERT INTO runtime_risk_state" in query
    assert "ON CONFLICT(id) DO UPDATE" in query
    # no SQLite dialect may reach the provider
    assert "INSERT OR REPLACE" not in query
    # state is normalised + the numeric columns carry through unchanged
    args = tuple(rows[0])
    assert args[0] == 1  # version
    assert args[1] == "HALTED"  # state, normalised to the durable form
    assert args[2] == "daily-loss-budget"  # reason
    assert args[3] == "breaker"  # source
    assert args[6] == pytest.approx(97_000.0)  # equity
    assert args[8] == 1  # release_required
    assert args[9] == 3  # consecutive_losses


def test_set_runtime_risk_state_refuses_unknown_state(pg_registry) -> None:
    """The state validation runs BEFORE any backend call (fail closed)."""
    backend = _wire_write_backend(_FakeWriteBackend())
    repo = _make_pg_repo()
    assert repo.set_runtime_risk_state(state="BOGUS") is False
    assert backend.batches == []


def test_set_runtime_risk_state_fails_closed_without_a_backend(pg_registry) -> None:
    """No pooled write backend -> False, never a silent success."""
    repo = _make_pg_repo()
    assert repo.set_runtime_risk_state(state="RUNNING") is False
    assert repo.provider_read_degraded_total == 0  # a write failure is not a read degradation


def test_set_runtime_risk_state_fails_closed_on_backend_error(pg_registry) -> None:
    """A raised backend error propagates as False + a loud log line."""
    backend = _wire_write_backend(_FakeWriteBackend(fail=True))
    repo = _make_pg_repo()
    assert repo.set_runtime_risk_state(state="KILL_SWITCH") is False
    assert len(backend.batches) == 1  # the write was attempted


def test_save_breaker_anchors_routes_both_statements_in_one_transaction(
    pg_registry,
) -> None:
    """The canonical row is guaranteed before the anchor UPDATE, atomically."""
    backend = _wire_write_backend(_FakeWriteBackend())
    repo = _make_pg_repo()
    assert (
        repo.save_breaker_anchors(
            day_anchor=101_000.0,
            day_utc="2026-09-24",
            week_anchor=102_000.0,
            week_iso="2026-W39",
        )
        is True
    )
    assert len(backend.batches) == 1
    queries = backend.executed_queries
    assert len(queries) == 2
    assert "INSERT INTO runtime_risk_state" in queries[0]
    assert "ON CONFLICT (id) DO NOTHING" in queries[0]
    assert "INSERT OR IGNORE" not in queries[0]
    assert "UPDATE runtime_risk_state" in queries[1]
    assert "breaker_day_anchor" in queries[1]


def test_save_breaker_anchors_rejects_invalid_values(pg_registry) -> None:
    """The value contract is provider-independent (NaN/negative/zero)."""
    backend = _wire_write_backend(_FakeWriteBackend())
    repo = _make_pg_repo()
    assert (
        repo.save_breaker_anchors(
            day_anchor=float("nan"), day_utc="d", week_anchor=1.0, week_iso="w"
        )
        is False
    )
    assert (
        repo.save_breaker_anchors(day_anchor=1.0, day_utc="d", week_anchor=-5.0, week_iso="w")
        is False
    )
    assert (
        repo.save_breaker_anchors(day_anchor=0.0, day_utc="d", week_anchor=1.0, week_iso="w")
        is False
    )
    assert (
        repo.save_breaker_anchors(
            day_anchor="not-a-number", day_utc="d", week_anchor=1.0, week_iso="w"
        )
        is False
    )
    assert backend.batches == []


def test_save_breaker_anchors_fails_closed_without_a_backend(pg_registry) -> None:
    repo = _make_pg_repo()
    assert (
        repo.save_breaker_anchors(
            day_anchor=100.0, day_utc="2026-09-24", week_anchor=100.0, week_iso="2026-W39"
        )
        is False
    )


# =====================================================================
# 3. The provider read path routes through the READ plane, fail-closed
# =====================================================================


def test_get_breaker_anchors_reads_the_registered_read_plane(pg_registry) -> None:
    plane = _wire_read_plane(
        _FakeReadPlane(
            rows=[
                {
                    "breaker_day_anchor": 101_000.0,
                    "breaker_day_utc": "2026-09-24",
                    "breaker_week_anchor": 102_000.0,
                    "breaker_week_iso": "2026-W39",
                }
            ]
        )
    )
    repo = _make_pg_repo()
    anchors = repo.get_breaker_anchors()
    assert anchors is not None
    assert anchors == {
        "day_anchor": 101_000.0,
        "day_utc": "2026-09-24",
        "week_anchor": 102_000.0,
        "week_iso": "2026-W39",
    }
    assert repo.provider_reads_routed == 1
    assert repo.provider_read_degraded_total == 0
    assert "runtime_risk_state" in plane.queries[0][0]


def test_get_breaker_anchors_absent_row_still_returns_none(pg_registry) -> None:
    """A healthy read that proves the row is unset -> None (not fabricated)."""
    repo = _make_pg_repo()
    _wire_read_plane(_FakeReadPlane(rows=[]))
    assert repo.get_breaker_anchors() is None
    assert repo.provider_reads_routed == 1


def test_get_breaker_anchors_fails_closed_when_no_read_plane_is_registered(
    pg_registry,
) -> None:
    """No read plane -> the read degrades OBSERVABLY to the documented None.

    This is the contract that made the pre-fix defect visible: absent data
    and UNAVAILABLE data must never look the same to the caller, so the
    degradation is counted + warned, then the fail-closed None is returned.
    """
    repo = _make_pg_repo()
    assert repo.get_breaker_anchors() is None
    assert repo.provider_read_degraded_total == 1
    assert repo.provider_read_degraded_ops.get("get_breaker_anchors") == 1


@pytest.mark.parametrize(
    ("row", "label"),
    [
        (
            {
                "breaker_day_anchor": None,
                "breaker_day_utc": "d",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "w",
            },
            "null anchors",
        ),
        (
            {
                "breaker_day_anchor": -1.0,
                "breaker_day_utc": "d",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "w",
            },
            "negative anchor",
        ),
        (
            {
                "breaker_day_anchor": float("nan"),
                "breaker_day_utc": "d",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "w",
            },
            "non-finite anchor",
        ),
        (
            {
                "breaker_day_anchor": 1.0,
                "breaker_day_utc": "",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "w",
            },
            "missing day identity",
        ),
        (
            {
                "breaker_day_anchor": 1.0,
                "breaker_day_utc": "d",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "",
            },
            "missing week identity",
        ),
        (
            {
                "breaker_day_anchor": "oops",
                "breaker_day_utc": "d",
                "breaker_week_anchor": 1.0,
                "breaker_week_iso": "w",
            },
            "type garbage",
        ),
    ],
)
def test_get_breaker_anchors_corrupt_rows_fail_closed(
    pg_registry, row: dict[str, Any], label: str
) -> None:
    """A well-formed but UNTRUSTWORTHY anchor reads back as None."""
    _wire_read_plane(_FakeReadPlane(rows=[row]))
    repo = _make_pg_repo()
    assert repo.get_breaker_anchors() is None, f"corrupt row must fail closed: {label}"


def test_get_runtime_risk_state_still_routes_through_the_read_guard(pg_registry) -> None:
    """Lane B must not regress the existing read routing (contract guard)."""
    _wire_read_plane(_FakeReadPlane(rows=[{"id": 1, "state": "HALTED"}]))
    repo = _make_pg_repo()
    row = repo.get_runtime_risk_state()
    assert row is not None
    assert row["state"] == "HALTED"
    assert repo.provider_reads_routed == 1
    assert repo.provider_read_degraded_total == 0


# =====================================================================
# 4. D9: _db_path is a real, non-empty location under a pooled provider
# =====================================================================


def test_provider_db_path_is_never_empty() -> None:
    """``Path(_db_path)`` must never be ``WindowsPath('.')`` (D9)."""
    repo = _make_pg_repo()
    assert repo._db_path != ""
    assert Path(repo._db_path).name != ""
    assert repo._db_path.startswith("postgresql://")


def test_provider_db_path_names_the_fabric_database() -> None:
    repo = _make_pg_repo(db_url="postgresql://db.example.com:6432/nexusdb")
    assert repo._db_path == "postgresql://db.example.com:6432/nexusdb"


def test_provider_db_path_falls_back_to_the_workspace_for_a_bare_dsn() -> None:
    """An unparseable DSN still yields a usable, non-empty location.

    ``_pg_dsn_parts`` recognises URL and libpq key=value forms; a bare name
    has neither, so the workspace fallback keeps ``Path(_db_path)`` valid
    (the D9 contract is "never the empty path", never "always a URL").
    """
    repo = _make_pg_repo(db_url="nse_audit")
    assert repo._db_path != ""
    assert Path(repo._db_path).name != ""


# =====================================================================
# 5. Real PostgreSQL arm (NSE_PG_TEST_URL) — the end-to-end contract
# =====================================================================


@pytest.fixture()
def pg_repo() -> Generator[AuditRepository, None, None]:
    """A repository on a scratch database, isolated from the live one.

    The connection URL comes from ``NSE_PG_TEST_URL`` and the password from
    the OS-backed secret store (never from this file or any log line) — the
    suite's established PostgreSQL arm convention
    (``tests/unit/test_rtf001_real_postgresql_schema.py``).
    """
    assert PG_URL, "NSE_PG_TEST_URL must be set"
    scratch_name = "nse_laneb_test"
    if "://" in PG_URL:
        url = f"{PG_URL.rsplit('/', 1)[0]}/{scratch_name}"
    else:  # libpq key=value form: rewrite just the dbname keyword
        url = " ".join(
            f"dbname={scratch_name}" if pair.startswith("dbname=") else pair
            for pair in PG_URL.split()
        )

    import psycopg  # the importorskip at collection time guarantees this

    def _admin(sql: str, params: tuple[Any, ...] = ()) -> None:
        with psycopg.connect(PG_URL, connect_timeout=10, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)

    def _reset_scratch() -> None:
        # ``AuditRepository`` provisions its own pools over the scratch
        # database; a bare DROP fails with ObjectInUse while those sessions
        # are still open. Terminate them first so the fixture is reusable.
        _admin(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (scratch_name,),
        )
        _admin(f'DROP DATABASE IF EXISTS "{scratch_name}"')
        _admin(f'CREATE DATABASE "{scratch_name}"')

    _reset_scratch()
    repo = AuditRepository(db_url=url)
    try:
        yield repo
    finally:
        repo.close()
        _reset_scratch()


@needs_postgres
def test_postgresql_safety_state_round_trip(pg_repo: AuditRepository) -> None:
    """D2: the RUNNING row must actually land in PostgreSQL and read back."""
    assert pg_repo._is_sqlite is False
    assert (
        pg_repo.set_runtime_risk_state(
            state="running",
            reason="lane-b verification",
            source="test_pg_safety_persistence",
            balance=100_000.0,
            equity=97_500.0,
            peak_equity=100_000.0,
        )
        is True
    )
    row = pg_repo.get_runtime_risk_state()
    assert row is not None
    assert row["state"] == "RUNNING"
    assert float(row["equity"]) == pytest.approx(97_500.0)


@needs_postgres
def test_postgresql_breaker_anchors_survive_a_restart(pg_repo: AuditRepository) -> None:
    """D3/BUG-259: anchors written by 'process 1' must be readable on boot.

    This is the capital-protection invariant: a loss taken before a restart
    must stay inside the budget, which requires the persisted anchor to be
    trustworthy after the process ends.
    """
    assert (
        pg_repo.save_breaker_anchors(
            day_anchor=101_000.0,
            day_utc="2026-09-24",
            week_anchor=102_000.0,
            week_iso="2026-W39",
        )
        is True
    )
    # A second repository over the SAME database stands in for a restart:
    # no in-memory cache can hide a row that never reached PostgreSQL.
    import psycopg

    with psycopg.connect(PG_URL, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT breaker_day_anchor, breaker_day_utc, breaker_week_anchor, "
            "breaker_week_iso FROM runtime_risk_state WHERE id = 1"
        )
        persisted = cur.fetchone()
    assert persisted is not None
    assert float(persisted[0]) == pytest.approx(101_000.0)
    assert persisted[1] == "2026-09-24"
    assert float(persisted[2]) == pytest.approx(102_000.0)
    assert persisted[3] == "2026-W39"


@needs_postgres
def test_postgresql_anchor_row_bootstrap_is_idempotent(pg_repo: AuditRepository) -> None:
    """save_breaker_anchors on a fresh PG store creates its own parent row."""
    assert pg_repo.get_runtime_risk_state() is None  # fresh scratch database
    assert (
        pg_repo.save_breaker_anchors(
            day_anchor=100.0, day_utc="2026-09-24", week_anchor=100.0, week_iso="2026-W39"
        )
        is True
    )
    row = pg_repo.get_runtime_risk_state()
    assert row is not None
    assert row["state"] == "RUNNING"  # the INSERT OR IGNORE equivalent


@needs_postgres
def test_postgresql_db_path_is_not_empty(pg_repo: AuditRepository) -> None:
    """D9: the snapshot schema probe can build a Path from _db_path."""
    assert pg_repo._db_path != ""
    assert Path(pg_repo._db_path).name != ""


# =====================================================================
# 6. SQLite regression: the existing SQLite suite still passes
# =====================================================================


def test_sqlite_anchor_fail_closed_contract_is_unchanged(tmp_path: Path) -> None:
    """The SQLite validation moved into _validate_breaker_anchors unchanged.

    Both providers now share one validator, so the SQLite contract pinned by
    ``tests/unit/test_bug259_breaker_anchor_restart.py`` must hold exactly.
    """
    path = tmp_path / "safety.db"
    repo = AuditRepository(db_url=f"sqlite:///{path}")
    try:
        assert repo.get_breaker_anchors() is None
        assert repo.save_breaker_anchors(
            day_anchor=100.0, day_utc="2026-09-24", week_anchor=100.0, week_iso="2026-W39"
        )
        # Tamper directly, exactly like the existing SQLite suite does.
        with sqlite3.connect(str(repo._db_path)) as conn:
            conn.execute("UPDATE runtime_risk_state SET breaker_day_anchor = 'oops'")
            conn.commit()
        assert repo.get_breaker_anchors() is None
        with sqlite3.connect(str(repo._db_path)) as conn:
            conn.execute("UPDATE runtime_risk_state SET breaker_day_anchor = -1.0")
            conn.commit()
        assert repo.get_breaker_anchors() is None
    finally:
        repo.close()
