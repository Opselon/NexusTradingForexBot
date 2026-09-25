"""BUG-285 (NSE-Swarm role 10, 2026-09-15): durable financial-overflow was WRITE-ONLY.

The criticality-aware enqueue keeps financial audit rows durable through queue
saturation by writing them to ``artifacts/audit_overflow/`` — and that was the
LAST line of defense AND a dead end: no reader existed anywhere in src/ (the
2026-09-14 perf wave, finding R2). A financial row that overflowed was
"durable" forever on disk but never returned to the ledger, and the directory
grew unbounded (one file per overflow event, no cap, no consumer).

Contract pinned here:
  * recovery: the audit worker's idle pass drains stranded files, oldest
    first, bounded batch, replaying the stored INSERT/REPLACE + args on the
    worker's own connection (single writer; idempotent producer SQL makes a
    duplicate replay a no-op, never a double count);
  * cadence: <=1 pass per OVERFLOW_RECOVERY_INTERVAL_SEC, None sentinel =
    first pass always due (the BUG-273 class);
  * retire: rename-then-delete under overflow_recovered/ (crash between
    replay and unlink can never lose the row twice);
  * poison: unreplayable payloads (non-INSERT, unparseable, __unserializable__
    envelopes) are durably DEAD-LETTERED, counted, and retired — never
    retried forever, never silently dropped;
  * bound: the writer refuses files past _FINANCIAL_OVERFLOW_MAX_FILES and
    routes the row to the bounded dead-letter table instead (counted loud);
  * observability: recovered/failed/pending surfaces on debug_snapshot;
  * wiring: the drain is actually called from _process_queue_worker (the
    #1 failure shape on this repo is a working class with no caller).
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


# The one deterministic monotonic-domain clock for the cadence tests.
#
# The production throttle reads ``time.monotonic()`` at the top of
# ``_drain_financial_overflow_due`` (audit_repository.py:3272) and compares it
# against the last stamp. That is correct production code — the drain is a
# real-time idle-pass concern — but the tests below used to read the SAME
# clock to simulate the passage of its interval (``time.monotonic()`` and
# ``time.monotonic() - (INTERVAL + 1)``), which made the suite's throttle
# coverage depend on the host's uptime monotonic instead of on the comparison:
# whether the second read landed above or below the stored stamp — and hence on
# which side of the boundary the test sat — was a property of the host clock
# domain rather than of the code under test. The comparison is purely against a
# monotonic *domain*, so a deterministic test-side clock in the same domain
# exercises the exact production arithmetic, including BOTH edges of the
# strict ``<`` boundary, without ever touching the host clock and without
# waiting out a real 60-second interval.
class _MonotonicClock:
    """Deterministic stand-in for the ``time.monotonic()`` domain.

    ``_drain_financial_overflow_due(conn, now=clock.read())`` takes the domain
    value as an explicit argument (the same optional-clock idiom the repo
    already uses in ``storage/runtime.py::is_due``), so a test advances time by
    calling ``advance()`` instead of waiting on the wall clock. Values only
    need to be monotonic and comparable to the last stamp the production code
    stored.
    """

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def reset(self) -> float:
        self._t = 0.0
        return self._t

    def read(self) -> float:
        return self._t

    def advance(self, dt: float) -> float:
        self._t += float(dt)
        return self._t


_MONO_CLOCK = _MonotonicClock()


@pytest.fixture
def drain_clock():
    """Resets the deterministic clock so each cadence test starts at 0.0.

    The clock is module-level (the production method takes the value as an
    argument), so without this an earlier test's advance would leak into the
    next one's boundary arithmetic.
    """
    _MONO_CLOCK.reset()
    yield _MONO_CLOCK


_ORDERS_SQL = """
            INSERT INTO audit_orders
            (ticket, order_id, symbol, action, price, stop_loss, take_profit, volume, reason, latency, execution_mode, execution_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) WHERE execution_id IS NOT NULL AND execution_id != ''
            DO NOTHING
        """


@pytest.fixture
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug278.db'}")
    yield r
    r.close()


@pytest.fixture
def ovf(tmp_path, monkeypatch, repo):
    """Overflow dir + worker-connection stand-in for a single test."""
    d = tmp_path / "ovf"
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_DIR", str(d))
    conn = sqlite3.connect(repo._db_path)
    yield repo, d, conn
    conn.close()


def _write_ovf_file(d: Path, name: str, payload: dict[str, Any]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ======================================================================
# 1. End-to-end recovery: a real overflow write is drained back to the DB
# ======================================================================


def test_overflow_row_written_by_producer_is_replayed_into_the_ledger(ovf) -> None:
    repo, d, conn = ovf
    args = (
        991,
        "req-278",
        "XAUUSD",
        "BUY",
        4001.5,
        3999.0,
        4005.0,
        0.01,
        "test",
        0.0,
        "STANDARD",
        "exec-278",
        "2026-09-15T00:00:00+00:00",
    )
    repo._write_financial_overflow(_ORDERS_SQL, args, error=None)
    assert repo.overflow_pending_count() == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM audit_orders WHERE execution_id='exec-278'").fetchone()[
            0
        ]
        == 0
    )

    repo._drain_financial_overflow_due(conn)

    assert (
        conn.execute("SELECT COUNT(*) FROM audit_orders WHERE execution_id='exec-278'").fetchone()[
            0
        ]
        == 1
    )
    assert repo.financial_overflow_recovered == 1
    assert repo.financial_overflow_failed == 0
    assert repo.overflow_pending_count() == 0
    # retired (rename-then-delete), file gone from the pending glob
    assert list(d.glob("overflow_*.json")) == []


def test_replay_of_a_row_that_already_landed_is_a_noop(ovf) -> None:
    """Producer SQL is ON CONFLICT DO NOTHING: the duplicate replay of a row
    that made it in through another path must never double-count."""
    repo, d, conn = ovf
    args = (
        7,
        "req-dup",
        "XAUUSD",
        "BUY",
        1.0,
        0.0,
        0.0,
        0.01,
        "r",
        0.0,
        "STANDARD",
        "exec-dup",
        "t",
    )
    conn.execute(_ORDERS_SQL, args)  # the row landed normally
    conn.commit()
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000000.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(args))},
    )
    repo._drain_financial_overflow_due(conn)
    n = conn.execute("SELECT COUNT(*) FROM audit_orders WHERE execution_id='exec-dup'").fetchone()[
        0
    ]
    assert n == 1
    assert repo.financial_overflow_recovered == 1
    assert repo.audit_dead_letter_rows == 0


# ======================================================================
# 2. Cadence: None sentinel first pass always due; then <=1 pass/interval
# ======================================================================


def test_first_drain_is_always_due_none_sentinel(ovf) -> None:
    """BUG-273 class regression: a 0.0 sentinel vs time.monotonic() would
    silently skip the first pass on hosts with uptime < interval."""
    repo, d, conn = ovf
    assert repo._last_overflow_drain is None
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000001.json",
        {
            "query": "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity, account_source) VALUES (?,?,?,?,?,?)",
            "args": json.dumps(["t1", 1.0, 1.0, 0.0, 1.0, "LIVE"]),
        },
    )
    repo._drain_financial_overflow_due(conn)
    assert repo.financial_overflow_recovered == 1


def test_drain_throttled_to_one_pass_per_interval(ovf, drain_clock) -> None:
    repo, d, conn = ovf
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000002.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(range(13)))},
    )
    repo._drain_financial_overflow_due(conn, now=drain_clock.read())  # consumes + stamps
    _write_ovf_file(
        d,
        "overflow_20260101_000001_00000003.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(range(20, 33)))},
    )
    # not one tick past the interval: must NOT touch the dir
    repo._drain_financial_overflow_due(conn, now=drain_clock.advance(0.0))
    assert repo.overflow_pending_count() == 1
    # one tick past the interval: due again, consumed
    repo._drain_financial_overflow_due(
        conn, now=drain_clock.advance(repo.OVERFLOW_RECOVERY_INTERVAL_SEC + 0.001)
    )
    assert repo.overflow_pending_count() == 0


def test_drain_throttle_arithmetic_is_the_production_comparison(ovf, drain_clock) -> None:
    """The throttle boundary is the production comparison, not a wall-clock
    magnitude: exactly at the interval the pass is already DUE (the comparison
    is strict ``<``, so equality does not throttle), while one tick short of it
    the pass is still throttled. Under the deterministic clock both edges are
    reproducible to the nanosecond, whereas a wall-clock leg could land on
    either side of the boundary depending on scheduler jitter alone."""
    repo, d, conn = ovf
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000012.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(range(13)))},
    )
    repo._drain_financial_overflow_due(conn, now=drain_clock.read())
    assert repo.financial_overflow_recovered == 1
    # one tick SHORT of the interval: strict < still holds, so still throttled
    _write_ovf_file(
        d,
        "overflow_20260101_000002_00000013.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(range(30, 43)))},
    )
    repo._drain_financial_overflow_due(
        conn, now=drain_clock.advance(repo.OVERFLOW_RECOVERY_INTERVAL_SEC - 0.001)
    )
    assert repo.overflow_pending_count() == 1, (
        "one tick short of the interval must NOT drain (strict <)"
    )
    # exactly AT the interval: equality is NOT < interval, so the pass is due
    repo._drain_financial_overflow_due(conn, now=drain_clock.advance(0.001))
    assert repo.overflow_pending_count() == 0
    assert repo.financial_overflow_recovered == 2


def test_drain_batch_is_bounded(ovf, monkeypatch) -> None:
    repo, d, conn = ovf
    monkeypatch.setattr(repo, "OVERFLOW_RECOVERY_BATCH", 3)
    for i in range(7):
        _write_ovf_file(
            d,
            f"overflow_2026010{i}_000000_{i:08d}.json",
            {
                "query": "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity, account_source) VALUES (?,?,?,?,?,?)",
                "args": json.dumps([f"t{i}", 1.0, 1.0, 0.0, 1.0, "LIVE"]),
            },
        )
    repo._drain_financial_overflow_due(conn)
    assert repo.financial_overflow_recovered == 3  # bounded to one batch
    assert repo.overflow_pending_count() == 4


# ======================================================================
# 3. Poison files: rejected, dead-lettered durably, retired — never looped
# ======================================================================


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "DELETE FROM audit_orders", "args": "[1]"},  # not an INSERT/REPLACE
        {"query": "UPDATE audit_ledger SET pnl=0", "args": "[]"},
        {"query": "", "args": "[]"},
        {"query": _ORDERS_SQL, "args": "{not json"},  # unparseable args
        {
            "query": _ORDERS_SQL,
            "args": [{"__unserializable__": True, "type": "bytes", "repr": "b'..'"}],
        },
    ],
    ids=["delete", "update", "empty-query", "bad-args-json", "unserializable-envelope"],
)
def test_unreplayable_overflow_is_dead_lettered_not_retried(ovf, payload) -> None:
    repo, d, conn = ovf
    p = _write_ovf_file(d, "overflow_20260101_000000_00000009.json", payload)
    repo._drain_financial_overflow_due(conn)
    assert repo.financial_overflow_failed == 1
    assert repo.financial_overflow_recovered == 0
    # durably recorded in the dead-letter table (the new terminal home)
    dl = repo.get_dead_letter_rows()
    assert any("BUG-285" in str(r.get("payload_note", "")) for r in dl), dl
    # retired: no longer pending, no longer in the dir (no forever-retry loop)
    assert not p.exists()
    assert repo.overflow_pending_count() == 0


def test_replay_sql_error_dead_letters_and_preserves_other_rows(ovf) -> None:
    repo, d, conn = ovf
    # file A references a table that cannot exist -> replay raises OperationalError
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000010.json",
        {"query": "INSERT INTO no_such_table (x) VALUES (?)", "args": "[1]"},
    )
    # file B is a good row that MUST still be recovered in the same pass
    _write_ovf_file(
        d,
        "overflow_20260101_000001_00000011.json",
        {
            "query": "INSERT INTO audit_account_snapshots (timestamp, balance, equity, margin_free, peak_equity, account_source) VALUES (?,?,?,?,?,?)",
            "args": json.dumps(["t-ok", 5.0, 5.0, 0.0, 5.0, "LIVE"]),
        },
    )
    repo._drain_financial_overflow_due(conn)
    assert repo.financial_overflow_failed == 1
    assert repo.financial_overflow_recovered == 1
    assert repo.overflow_pending_count() == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM audit_account_snapshots WHERE timestamp='t-ok'"
        ).fetchone()[0]
        == 1
    )


# ======================================================================
# 4. Writer bound: past the cap the row goes to dead-letter, not disk
# ======================================================================


def test_overflow_writer_cap_routes_row_to_dead_letter(repo, tmp_path, monkeypatch) -> None:
    d = tmp_path / "ovf-cap"
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_DIR", str(d))
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_MAX_FILES", 2)
    repo._write_financial_overflow(_ORDERS_SQL, tuple(range(13)), error=None)
    repo._write_financial_overflow(_ORDERS_SQL, tuple(range(100, 113)), error=None)
    assert repo.overflow_pending_count() == 2
    dl_before = repo.audit_dead_letter_rows
    # third write is refused a FILE and lands in the bounded dead-letter table
    repo._write_financial_overflow(_ORDERS_SQL, tuple(range(200, 213)), error=None)
    assert repo.overflow_pending_count() == 2  # no third file
    assert repo.financial_overflow_failed == 1
    assert repo.audit_dead_letter_rows == dl_before + 1
    dl = repo.get_dead_letter_rows()
    assert any("cap reached" in str(r.get("payload_note", "")) for r in dl), dl


# ======================================================================
# 5. Wiring + surfaces (the #1 failure shape: shipped but never called)
# ======================================================================


def test_drain_is_wired_into_the_audit_worker_idle_pass() -> None:
    src = inspect.getsource(AuditRepository._process_queue_worker)
    assert "_drain_financial_overflow_due(conn)" in src
    # on the IDLE branch only — a batched pass must not pay the directory scan
    idle_idx = src.find("if not batch:")
    drain_idx = src.find("_drain_financial_overflow_due(conn)")
    assert idle_idx != -1 and drain_idx > idle_idx


def test_writer_and_drainer_share_one_path_resolution() -> None:
    """Single _overflow_dir() resolution site: a second hand-built path would
    silently orphan either the writer or the reader (R2 shape)."""
    src = (REPO_ROOT / "src/nexus_scalp/adapters/database/audit_repository.py").read_text(
        encoding="utf-8"
    )
    assert src.count("Path(get_runtime_workspace()) / self._FINANCIAL_OVERFLOW_DIR") == 1
    # the writer must NOT keep its own copy of the path expression
    writer_src = inspect.getsource(AuditRepository._write_financial_overflow)
    assert "get_runtime_workspace" not in writer_src
    assert "self._overflow_dir()" in writer_src


def test_debug_snapshot_surfaces_recovery_counters() -> None:
    src = (REPO_ROOT / "src/nexus_scalp/web/debug_snapshot.py").read_text(encoding="utf-8")
    for key in (
        "financial_overflow_recovered",
        "financial_overflow_failed",
        "financial_overflow_pending",
    ):
        assert f'"{key}"' in src, key


def test_overflow_counter_semantics_are_exact(ovf) -> None:
    """recovered + failed + pending == what the pass saw: no silent third bucket."""
    repo, d, conn = ovf
    _write_ovf_file(
        d,
        "overflow_20260101_000000_00000020.json",
        {"query": _ORDERS_SQL, "args": json.dumps(list(range(13)))},
    )
    _write_ovf_file(
        d,
        "overflow_20260101_000001_00000021.json",
        {"query": "DROP TABLE audit_orders", "args": "[]"},
    )
    seen = repo.overflow_pending_count()
    repo._drain_financial_overflow_due(conn)
    assert repo.financial_overflow_recovered + repo.financial_overflow_failed == seen
    assert repo.overflow_pending_count() == 0
    # the rejected poison must NOT have dropped the good row's table
    assert conn.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0] == 1
