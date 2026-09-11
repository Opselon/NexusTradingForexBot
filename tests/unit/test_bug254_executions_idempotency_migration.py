"""BUG-254 regression battery: audit_executions UNIQUE-index startup crash.

Pins the complete fix contract for the ``idx_executions_order_status``
bootstrap (agent-17 idempotency index, release 2026-09-10):

  T1  Legacy DB with (order_id, status) duplicates  -> migrates, reconciles
      per policy (earliest row survives, superseded rows archived verbatim
      to audit_executions_reconciled), index exists, construction succeeds.
  T2  Idempotency: repair+create run 3x -> identical final state, no
      double-archive, no row churn.
  T3  Fresh DB init -> init again (pure no-op path).
  T4  Concurrent init (threads/process-shape race on one SQLite file) ->
      no corruption, no duplicate migration effects, exactly one survivor.
  T5  Interrupted/partial migration (archive inserted, delete lost) ->
      next startup recovers to the identical end state.
  T6  Data integrity: live+archived == seeded; archive rows carry the
      verbatim original payload + provenance; survivor is the earliest row.
  T7  Writer semantics: same-(order_id,status) redelivery collapses to one
      durable row (ON CONFLICT DO NOTHING); a distinct status still inserts
      its own row; no crash at the application layer.
  T8  E2E: a DB reproducing the original crash (raw CREATE UNIQUE INDEX
      raises sqlite3.IntegrityError) boots through AuditRepository.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.executions_idempotency import (
    EXECUTIONS_DEDUP_REPAIR_BATCH,
    EXECUTIONS_IDEMPOTENCY_INDEX,
    EXECUTIONS_RECONCILED_TABLE,
    ensure_executions_idempotency_index,
)
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import TradeOrder

_EXEC_DDL = """
CREATE TABLE audit_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    order_type TEXT NOT NULL,
    volume REAL NOT NULL,
    price REAL NOT NULL,
    status TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    payload TEXT NOT NULL
)
"""

_INSERT = (
    "INSERT INTO audit_executions "
    "(order_id, symbol, order_type, volume, price, status, executed_at, payload) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _seed_dirty_db(db_path, order_id: str = "legacy-req", n: int = 5) -> None:
    """Creates a pre-release-shaped DB with N exact-identity duplicate rows."""
    conn = sqlite3.connect(db_path)
    conn.execute(_EXEC_DDL)
    for i in range(n):
        conn.execute(
            _INSERT,
            (
                order_id,
                "XAUUSD",
                "BUY",
                0.1,
                2000.0,
                "REJECTED",
                f"2026-09-0{i + 1}T00:00:00Z",
                f"payload-{i}",
            ),
        )
    conn.commit()
    conn.close()


def _crash_repro(db_path) -> None:
    """The pre-fix construction path raises on a dirty DB (documents T8)."""
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "CREATE UNIQUE INDEX idx_executions_order_status ON audit_executions (order_id, status)"
        )
    conn.close()


def _live_archived(db_path) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    try:
        live = conn.execute("SELECT COUNT(*) FROM audit_executions").fetchone()[0]
        has_arch = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (EXECUTIONS_RECONCILED_TABLE,),
        ).fetchone()
        arch = (
            conn.execute(f"SELECT COUNT(*) FROM {EXECUTIONS_RECONCILED_TABLE}").fetchone()[0]
            if has_arch
            else 0
        )
        return int(live), int(arch)
    finally:
        conn.close()


def _survivor_payload(db_path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute("SELECT payload FROM audit_executions").fetchone()[0])
    finally:
        conn.close()


def test_t1_legacy_duplicates_migrate_and_construction_succeeds(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    _seed_dirty_db(db)
    _crash_repro(db)  # pre-fix behavior: raw DDL raises (T8 evidence)

    repo = AuditRepository(db_url=f"sqlite:///{db}")  # must NOT raise
    try:
        conn = sqlite3.connect(db)
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (EXECUTIONS_IDEMPOTENCY_INDEX,)
        ).fetchall()
        assert idx, "idempotency index must exist after construction"
        live, arch = _live_archived(db)
        assert (live, arch) == (1, 4)  # 5 seeded: 1 survivor + 4 archived
        assert _survivor_payload(db) == "payload-0", "earliest row must survive"
        # Archived rows keep the verbatim original payload + provenance
        rows = conn.execute(
            f"SELECT original_id, payload, reconciled_at, reconcile_reason "
            f"FROM {EXECUTIONS_RECONCILED_TABLE} ORDER BY original_id"
        ).fetchall()
        assert [r[0] for r in rows] == [2, 3, 4, 5]
        assert [r[1] for r in rows] == ["payload-1", "payload-2", "payload-3", "payload-4"]
        assert all(r[2] and r[3] for r in rows), "archive provenance required"
        conn.close()
    finally:
        repo.close()


def test_t2_repair_rerun_three_times_identical_final_state(tmp_path) -> None:
    db = tmp_path / "idem.db"
    _seed_dirty_db(db, n=4)
    conn = sqlite3.connect(db)
    try:
        first = ensure_executions_idempotency_index(conn)
        states = []
        for _ in range(3):
            again = ensure_executions_idempotency_index(conn)
            states.append(
                (
                    again["duplicates"],
                    again["repaired"],
                    _live_archived(db),
                    conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
                        (EXECUTIONS_IDEMPOTENCY_INDEX,),
                    ).fetchone()[0],
                )
            )
        conn.commit()
    finally:
        conn.close()
    assert first["repaired"] == 3
    # every re-run: no duplicates, no repair, same live/archived, index present
    assert all(s == (0, 0, (1, 3), 1) for s in states), states


def test_t3_fresh_db_init_twice_is_noop(tmp_path) -> None:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        repo2 = AuditRepository(db_url=f"sqlite:///{tmp_path / 'fresh.db'}")
        repo2.close()
        live, arch = _live_archived(tmp_path / "fresh.db")
        assert (live, arch) == (0, 0)
    finally:
        repo.close()


def test_t4_concurrent_init_no_corruption_no_double_effects(tmp_path) -> None:
    db = tmp_path / "race.db"
    _seed_dirty_db(db, order_id="race-req", n=12)
    errors: list[str] = []

    def _worker(n: int) -> None:
        try:
            # Mirror the production AuditRepository connect shape: one
            # connection with a busy timeout; the helper sets its own
            # PRAGMA busy_timeout. (A racing `PRAGMA journal_mode = WAL`
            # SETTER here fails with 'database is locked' — that is the
            # test's pragma racing itself, not the migration path; the
            # real bootstrap sets WAL once in _setup_storage before any
            # concurrent construction.)
            conn = sqlite3.connect(db, timeout=15.0)
            ensure_executions_idempotency_index(conn, reconcile_at=f"2026-09-11T00:00:{n:02d}Z")
            conn.close()
        except Exception as e:
            errors.append(f"{n}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    conn = sqlite3.connect(db)
    try:
        live = conn.execute("SELECT COUNT(*) FROM audit_executions").fetchone()[0]
        arch = conn.execute(f"SELECT COUNT(*) FROM {EXECUTIONS_RECONCILED_TABLE}").fetchone()[0]
        distinct = conn.execute(
            f"SELECT COUNT(DISTINCT original_id) FROM {EXECUTIONS_RECONCILED_TABLE}"
        ).fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (EXECUTIONS_IDEMPOTENCY_INDEX,)
        ).fetchall()
    finally:
        conn.close()
    assert integrity == "ok"
    assert idx, "index must exist after the race"
    assert live == 1, "exactly one survivor"
    assert live + arch == 12, "no legitimate audit row lost"
    assert distinct == arch, "no double-archived row"


def test_t5_interrupted_migration_recovers_next_startup(tmp_path) -> None:
    db = tmp_path / "partial.db"
    _seed_dirty_db(db, n=5)
    # Simulate an interrupted repair: one row archived, the live DELETE lost.
    conn = sqlite3.connect(db)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {EXECUTIONS_RECONCILED_TABLE} ("
        "id INTEGER PRIMARY KEY, order_id TEXT NOT NULL, symbol TEXT NOT NULL, "
        "order_type TEXT NOT NULL, volume REAL NOT NULL, price REAL NOT NULL, "
        "status TEXT NOT NULL, executed_at TEXT NOT NULL, payload TEXT NOT NULL, "
        "original_id INTEGER NOT NULL, reconciled_at TEXT NOT NULL, "
        "reconcile_reason TEXT NOT NULL)"
    )
    victim = conn.execute(
        "SELECT id, order_id, symbol, order_type, volume, price, status, "
        "executed_at, payload FROM audit_executions WHERE id = 2"
    ).fetchone()
    conn.execute(
        f"INSERT INTO {EXECUTIONS_RECONCILED_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (*victim, 2, "2026-09-10T00:00:00Z", "interrupted pass"),
    )
    conn.commit()
    conn.close()

    ensure_executions_idempotency_index(sqlite3.connect(db))
    live, arch = _live_archived(db)
    assert (live, arch) == (1, 4), (live, arch)  # 5 seeded: 1 survivor + 4 archived
    assert _survivor_payload(db) == "payload-0", "earliest row must survive recovery"


def test_t6_no_legitimate_row_lost_across_migration(tmp_path) -> None:
    db = tmp_path / "integrity.db"
    _seed_dirty_db(db, n=6)
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    try:
        live, arch = _live_archived(db)
        assert live + arch == 6
        conn = sqlite3.connect(db)
        try:
            # every original payload appears exactly once across live+archive
            live_payloads = [r[0] for r in conn.execute("SELECT payload FROM audit_executions")]
            arch_payloads = [
                r[0] for r in conn.execute(f"SELECT payload FROM {EXECUTIONS_RECONCILED_TABLE}")
            ]
            assert sorted(live_payloads + arch_payloads) == [f"payload-{i}" for i in range(6)]
        finally:
            conn.close()
    finally:
        repo.close()


def test_t7_writer_redelivery_collapses_distinct_status_persists(tmp_path) -> None:
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'writer.db'}")
    try:
        order = TradeOrder(
            order_id="req-w",
            symbol="XAUUSD",
            order_type=OrderType.BUY,
            volume=0.1,
            price=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            magic_number=1,
        )
        repo.log_execution(order, "REJECTED")
        repo.log_execution(order, "REJECTED")  # redelivery -> collapses
        repo.log_execution(order, "FILLED")  # distinct status -> own row
        assert repo.flush(timeout_sec=15.0) is True
        conn = sqlite3.connect(repo._db_path)
        rows = conn.execute(
            "SELECT order_id, status, COUNT(*) FROM audit_executions GROUP BY 1, 2"
        ).fetchall()
        conn.close()
        assert rows == [("req-w", "FILLED", 1), ("req-w", "REJECTED", 1)]
    finally:
        repo.close()


def test_t8_bounded_repair_never_unbounded_and_fails_loud_on_remainder(
    tmp_path, monkeypatch
) -> None:
    """Pathological volume: one pass repairs at most the configured batch;
    a remainder still failing the index build propagates (never silent)."""
    db = tmp_path / "burst.db"
    conn0 = sqlite3.connect(db)
    conn0.execute(_EXEC_DDL)
    # 6 duplicated identities; batch of 2 repairs only 2 per pass
    for k in range(6):
        for _ in range(3):
            conn0.execute(
                _INSERT,
                (
                    f"burst-{k}",
                    "XAUUSD",
                    "BUY",
                    0.1,
                    2000.0,
                    "REJECTED",
                    f"2026-09-01T00:00:{k:02d}Z",
                    "p",
                ),
            )
    conn0.commit()
    conn0.close()

    from nexus_scalp.adapters.database import executions_idempotency as m

    conn = sqlite3.connect(db)
    # batch=2: each attempt repairs 2 identities, and the helper has 2
    # attempts (initial + one retry) -> 4 identities repaired per call, the
    # remaining 2 STILL block the index -> loud IntegrityError (never silent).
    with pytest.raises(sqlite3.IntegrityError):
        m.ensure_executions_idempotency_index(conn, repair_batch=2, scan_limit=500)
    # the failed call ROLLED BACK its repairs (transactional guarantee), so
    # the next startup with a full batch repairs ALL 6 identities x 2 losers
    second = m.ensure_executions_idempotency_index(conn, repair_batch=500, scan_limit=500)
    assert second["repaired"] == 12, "all 6 identities x 2 losers archived"
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE name=?", (EXECUTIONS_IDEMPOTENCY_INDEX,)
    ).fetchall()
    conn.close()
    assert idx
    live, arch = _live_archived(db)
    assert (live, arch) == (6, 12)
