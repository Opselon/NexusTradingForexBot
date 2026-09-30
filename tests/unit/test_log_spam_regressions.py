"""Log-spam regression tests (live-cluster error/warning flood).

Each test pins one defect observed in the live PostgreSQL run's
``logs/error`` + ``logs/warning`` files (2026-09-29, one 90-second window):

  * ``get_decision_stats`` GROUP BY error — PostgreSQL refuses a bare
    ``generated_at`` in the outer projection while grouping by
    ``(action, second_key)`` (GroupingError x6).
  * ``queue_write_batch`` / ``ops_queue_write_batch`` passed ``(query, args)``
    to ``execute_batch``, which treats the second element as a sequence of
    ROWS — a single string argument exploded into its characters
    ("the query has 1 placeholders but 18 parameters" from
    ``shadow_worker._mark_interrupted_runs``).
  * ``_BorrowedRows`` fetchone() returned a plain dict, so legacy helpers
    using positional ``row[0]`` raised ``KeyError(0)`` — stringified to
    ``error=0`` and spammed 91 times by ``[STRATEGY_RESEARCH] evidence
    resolution failed``.
  * ``outcome_recovery_sweep._connect`` opened ``sqlite3.connect`` on a
    PostgreSQL box's ``_db_path`` ("unable to open database file") — the
    sweep must route through the fabric's read connection instead.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from nexus_scalp.adapters.database import provider_store
from nexus_scalp.adapters.database.provider_store import (
    _BorrowedRows,
    _PositionalRow,
)

# ---------------------------------------------------------------------------
# get_decision_stats: PostgreSQL GROUP BY contract
# ---------------------------------------------------------------------------


def _stats_sql() -> str:
    """The SQL ``AuditRepository.get_decision_stats`` builds (source order)."""
    second_key = "decision_stage"
    return (
        "SELECT action, "
        f"{second_key}, MAX(generated_at) AS generated_at, COUNT(*) AS n FROM ("
        " SELECT action, reason_code, decision_stage, generated_at FROM audit_signals"
        " ORDER BY id DESC LIMIT ?"
        ") AS recent WHERE generated_at >= ?"
        f" GROUP BY action, {second_key}"
    )


def test_decision_stats_projection_is_group_safe() -> None:
    """Every non-aggregated projected column must appear in GROUP BY.

    PostgreSQL raises GroupingError for a bare column that is neither grouped
    nor aggregated; SQLite silently allows it. The outer query groups by
    ``(action, second_key)`` and selects ``action``, ``second_key``,
    ``generated_at``, ``n`` — so ``generated_at`` MUST be aggregated (MAX).
    """
    sql = _stats_sql()
    assert "MAX(generated_at)" in sql
    # The window predicate still filters on the un-aggregated column inside
    # the subquery slice, which is legitimate (it is not the grouping level).
    assert "WHERE generated_at >= ?" in sql


def test_decision_stats_runs_on_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The fixed query must still execute and aggregate correctly on SQLite."""
    db = sqlite3.connect(tmp_path / "a.db")
    db.execute(
        "CREATE TABLE audit_signals (id INTEGER PRIMARY KEY, action TEXT, "
        "reason_code TEXT, decision_stage TEXT, generated_at TEXT)"
    )
    db.executemany(
        "INSERT INTO audit_signals (action, reason_code, decision_stage, generated_at) "
        "VALUES (?,?,?,?)",
        [
            ("NO_TRADE", "LOW_CONFIDENCE", "GATE", "2026-09-29T10:00:00+00:00"),
            ("NO_TRADE", "LOW_CONFIDENCE", "GATE", "2026-09-29T11:00:00+00:00"),
            ("BUY", "STRONG", "GATE", "2026-09-29T12:00:00+00:00"),
        ],
    )
    db.commit()
    rows = db.execute(_stats_sql(), (100, "2000-01-01T00:00:00+00:00")).fetchall()
    db.close()
    by = {(r[0], r[1]): r[3] for r in rows}
    assert by == {("NO_TRADE", "GATE"): 2, ("BUY", "GATE"): 1}


# ---------------------------------------------------------------------------
# execute_batch shape contract: (query, args) vs (query, rows)
# ---------------------------------------------------------------------------


class _RecordingWriteBackend:
    """Captures the statement list ``execute_batch`` receives."""

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, list[Any]]]] = []

    def execute_batch(self, statements: Any) -> None:
        self.batches.append([tuple(s) for s in statements])


def _pg_repo_stub() -> Any:
    """A non-SQLite repository stub (``_is_sqlite`` falsy)."""

    class _Repo:
        _is_sqlite = False
        _db_url = "postgresql://user:pass@host/db"

    return _Repo()


def test_queue_write_batch_wraps_args_as_one_row(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``queue_write_batch`` must pass (query, [args]) not (query, args).

    A single-string argument passed through unwrapped is iterated by the
    backend's ``len(rows) == 1 -> tuple(rows[0])`` fast path into one
    parameter PER CHARACTER — the live "1 placeholders but 18 parameters".
    """
    backend = _RecordingWriteBackend()
    monkeypatch.setattr(provider_store, "_write_backend", lambda repo, **kw: backend)
    repo = _pg_repo_stub()
    run_id = "shadow_90d4a989ee6f"  # 19 chars, matching the live failure

    ok = provider_store.queue_write_batch(
        repo,
        [("UPDATE shadow_runs SET status='INCOMPLETE' WHERE run_id=?;", (run_id,))],
        operation="shadow_worker.mark_interrupted.write",
    )
    assert ok is True
    assert len(backend.batches) == 1
    (stmt,) = backend.batches[0]
    query, rows = stmt
    # Exactly ONE row carrying exactly ONE bind value — not 19 characters.
    assert list(rows) == [(run_id,)]
    assert query == "UPDATE shadow_runs SET status='INCOMPLETE' WHERE run_id=?;"


def test_ops_queue_write_batch_wraps_args_as_one_row(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The named-domain variant has the same contract (ops_shadow domain)."""
    backend = _RecordingWriteBackend()
    monkeypatch.setattr(provider_store, "_write_backend", lambda repo, **kw: backend)
    repo = _pg_repo_stub()

    ok = provider_store.ops_queue_write_batch(
        repo,
        "ops_shadow",
        [("UPDATE shadow_runs SET status='INCOMPLETE' WHERE run_id=?;", ("abc123",))],
        operation="shadow_worker.mark_interrupted.write",
    )
    assert ok is True
    (stmt,) = backend.batches[0]
    _query, rows = stmt
    assert list(rows) == [("abc123",)]


# ---------------------------------------------------------------------------
# _BorrowedRows / _PositionalRow: sqlite3.Row parity (error=0 spam)
# ---------------------------------------------------------------------------


def _borrowed() -> _BorrowedRows:
    return _BorrowedRows(
        [
            {"id": 11, "decision_stage": "TRADE_INTELLIGENCE_GATE", "ticket": 0},
            {"id": 12, "decision_stage": "STANDARD_EVAL", "ticket": 9001},
        ],
        ["id", "decision_stage", "ticket"],
    )


def test_borrowed_fetchone_supports_positional_access() -> None:
    """``row[0]`` / ``row[1]`` must work on rows returned by fetchone().

    ``resolve_decision_evidence`` reads positional columns; a plain dict
    raises ``KeyError(0)`` whose ``str()`` is ``"0"`` — the live ``error=0``.
    """
    row = _borrowed().fetchone()
    assert row[0] == 11
    assert row[1] == "TRADE_INTELLIGENCE_GATE"
    assert row["decision_stage"] == "TRADE_INTELLIGENCE_GATE"


def test_borrowed_fetchall_rows_are_dictable_and_positional() -> None:
    rows = _borrowed().fetchall()
    assert len(rows) == 2
    first = dict(rows[0])
    assert first["id"] == 11
    assert rows[1][2] == 9001  # positional third column


def test_positional_row_matches_sqlite_row_semantics() -> None:
    """The wrapper must agree with sqlite3.Row for name+positional+dict()."""
    cols = ["a", "b"]
    values = {"a": 1, "b": 2}
    wrapped = _PositionalRow(values, cols)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    native = conn.execute("SELECT 1 AS a, 2 AS b").fetchone()
    conn.close()

    for key in (0, 1, "a", "b"):
        assert wrapped[key] == native[key], f"mismatch at {key!r}"
    assert dict(wrapped) == dict(native)
    assert wrapped.keys() == list(native.keys())


# ---------------------------------------------------------------------------
# resolve_decision_evidence through a borrowed connection (end-to-end)
# ---------------------------------------------------------------------------


class _FakeReadBackend:
    """Pooled read backend speaking ``query(sql, args) -> list[dict]``."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def query(self, sql: str, args: Any = ()) -> list[dict[str, Any]]:
        return list(self._rows)


def test_resolve_decision_evidence_positional_through_borrowed_conn() -> None:
    """The shared resolver must not KeyError on a pooled read connection."""
    from nexus_scalp.experience.decision_evidence import resolve_decision_evidence

    backend = _FakeReadBackend([{"id": 42, "decision_stage": "TRADE_INTELLIGENCE_GATE"}])
    conn = provider_store._BorrowedConnection(backend)
    try:
        ev = resolve_decision_evidence(conn, "REQ-1")
    finally:
        conn.close()
    assert ev.evidence == "GATE_REJECTION"
    assert ev.pre_dispatch_gate == "TRADE_INTELLIGENCE_GATE"
    assert ev.evidence_ids == ("42",)


# ---------------------------------------------------------------------------
# outcome_recovery_sweep: no sqlite3.connect on a PostgreSQL box
# ---------------------------------------------------------------------------


def test_recovery_sweep_connect_uses_provider_read_connection(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``_connect`` must not open sqlite3 on a non-SQLite repository."""
    from nexus_scalp.experience.outcome_recovery_sweep import (
        HistoricalOutcomeRecoverySweep,
    )

    connections: list[Any] = []

    def _fake_read_connection(repo: Any, **kw: Any) -> Any:
        connections.append(object())
        return connections[-1]

    monkeypatch.setattr(provider_store, "read_connection", _fake_read_connection)

    class _Repo:
        _is_sqlite = False
        _db_path = ""  # empty under PostgreSQL — sqlite3.connect("") creates a junk file

    sweep = HistoricalOutcomeRecoverySweep(ledger=object(), audit_repo=_Repo())
    conn = sweep._connect()
    assert connections, "sweep must route through provider_store.read_connection"
    assert conn is connections[0], "sweep must return the fabric connection verbatim"


# ---------------------------------------------------------------------------
# research dataset builder: _iter_records must be ONE query, not an N+1
# (live cluster 2026-09-30: 74 [WORKER_KICK] TIMEOUT errors per hour, every
# research cycle 94-145s against a 45s budget, because _iter_records ran one
# get_experiences_for_strategy query PER strategy — 4,113 round trips)
# ---------------------------------------------------------------------------


def test_iter_records_is_a_single_query_not_an_n_plus_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``_iter_records`` must issue ONE bounded read, not one per strategy."""
    from nexus_scalp.experience.ledger import ExperienceLedger
    from nexus_scalp.research.dataset import ResearchDatasetBuilder

    queries: list[str] = []

    def _fake_list_all(self: Any, limit: int = 20000) -> list[Any]:
        queries.append("list_all_experiences")
        return []

    def _no_per_strategy(self: Any, sid: Any, limit: int = 0, **kw: Any) -> list[Any]:
        queries.append(f"get_experiences_for_strategy({sid})")
        return []

    monkeypatch.setattr(ExperienceLedger, "list_all_experiences", _fake_list_all)
    monkeypatch.setattr(ExperienceLedger, "get_experiences_for_strategy", _no_per_strategy)

    builder = ResearchDatasetBuilder(ledger=ExperienceLedger(audit_repo=object()))
    builder._iter_records()

    assert queries == ["list_all_experiences"], (
        f"_iter_records must use the single-query accessor, not an N+1; saw {queries}"
    )


def test_iter_records_dedupes_and_returns_merged_records() -> None:
    """The single-query path keeps the per-key dedupe the N+1 loop had.

    Dedupe lives in ``list_all_experiences`` (the accessor owns it, so every
    caller benefits); ``_iter_records`` returns what the accessor yields.
    """
    from nexus_scalp.research.dataset import ResearchDatasetBuilder

    class _Rec:
        def __init__(self, key: str) -> None:
            self.idempotency_key = key

    class _Ledger:
        def list_all_experiences(self, limit: int = 20000) -> list[Any]:
            # The accessor dedupes by key before returning (its own contract).
            return [_Rec("dup"), _Rec("unique")]

    builder = ResearchDatasetBuilder(ledger=_Ledger())
    records = builder._iter_records()
    assert [r.idempotency_key for r in records] == ["dup", "unique"]
