"""INV-001 V1 regression guards — tick-path closed-ledger enrichment.

Lane E CR-04/V1 defect (verified): the position-finalize tick path opened the
closed-ledger enrichment read with a RAW ``sqlite3.connect(
audit_repo._db_path, timeout=5.0)``. That call site bypassed
``AuditRepository._connect_sqlite`` — the repo's ONE connect site — and with it
the BUG-156 URI contract: a ``file:`` URI (the shared in-memory audit DB)
passed to a bare ``sqlite3.connect`` is treated as a LITERAL FILE NAME and
silently creates a junk ``file::memory:?cache=shared`` file in the process CWD.
Under PostgreSQL ``_db_path`` has no SQLite meaning at all (it is ``""``), so
the raw connect would open an unnamed temp DB instead of skipping an
optional read.

Contract guarded here:
  (a) on SQLite, finalize-position enrichment still reads the authoritative
      closed-ledger row and returns its real values;
  (b) ``sqlite3.connect`` is NEVER called directly by
      ``_read_closed_ledger`` — only the repository seam may connect;
  (c) a non-SQLite (PostgreSQL) repository never opens ANY SQLite connection
      and the enrichment returns the documented (0.0, "", 0.0) defaults fast.
"""

from __future__ import annotations

import sqlite3
import typing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.intelligence.lifecycle import PositionLifecycleTracker
from nexus_scalp.intelligence.models import (
    PositionPerformance,
    PositionSnapshot,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def audit_repo(tmp_path: Path) -> typing.Iterator[AuditRepository]:
    """A real SQLite-backed audit repository on a temp file."""
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'inv001.db'}")
    yield repo
    repo.close()


def _seed_closed_ledger_row(repo: AuditRepository, ticket: int) -> None:
    """Writes one authoritative CLOSED audit_ledger row for the enrichment."""
    conn = repo._connect_sqlite(5.0)
    try:
        conn.execute(
            "INSERT INTO audit_ledger "
            "(ticket, symbol, direction, volume, entry_price, exit_price, status, "
            " net_pnl_usd, gross_pnl_usd, exit_mechanism, timestamp, MFE_usd, MAE_usd) "
            "VALUES (?, 'XAUUSD', 'BUY', 0.1, 2000.0, 2010.0, 'CLOSED', "
            " ?, ?, 'TAKE_PROFIT', ?, 3.0, 0.5)",
            (
                ticket,
                -37.50,
                -40.00,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) SQLite: the enrichment still reads the closed-ledger row
# ---------------------------------------------------------------------------


def test_a_sqlite_enrichment_reads_real_ledger_row(audit_repo: AuditRepository) -> None:
    """On SQLite the enrichment returns the ledger's own net PnL + mechanism."""
    _seed_closed_ledger_row(audit_repo, 4242)
    tracker = PositionLifecycleTracker(audit_repo=audit_repo)
    perf = PositionPerformance(mfe=2.5, mae=0.4)

    net, mechanism, r_mult = tracker._read_closed_ledger("4242", perf)

    assert net == pytest.approx(-37.50), "enrichment must return the ledger net_pnl_usd"
    assert mechanism == "TAKE_PROFIT", "enrichment must return the ledger exit_mechanism"
    # R fallback: MFE branch (mfe > mae) — approximated from performance.
    assert r_mult == pytest.approx(2.5)


def test_a_sqlite_enrichment_missing_row_returns_defaults(audit_repo: AuditRepository) -> None:
    """A ticket with no ledger row degrades to the documented defaults."""
    tracker = PositionLifecycleTracker(audit_repo=audit_repo)
    net, mechanism, r_mult = tracker._read_closed_ledger("777", PositionPerformance())
    assert (net, mechanism, r_mult) == (0.0, "", 0.0)


def test_a_finalize_exit_uses_ledger_enrichment_when_caller_supplies_nothing(
    audit_repo: AuditRepository,
) -> None:
    """The full finalize path enriches POSITION_EXITED from the ledger row.

    The caller passes realized_pnl_usd == 0.0 / realized_r == 0.0, so
    finalize_exit must consult the ledger and carry its numbers onto the
    emitted event.
    """
    _seed_closed_ledger_row(audit_repo, 8801)
    tracker = PositionLifecycleTracker(audit_repo=audit_repo)
    # Establish the ticket timeline so finalize emits a POSITION_EXITED.
    tracker.observe_position(
        ticket=8801,
        snapshot=PositionSnapshot(entry_price=2000.0, current_price=2010.0, volume=0.1),
        trade_id="req_inv001",
        at=datetime.now(UTC),
    )
    tracker.finalize_exit(
        ticket=8801,
        snapshot=PositionSnapshot(entry_price=2000.0, current_price=2010.0, volume=0.1),
        performance=PositionPerformance(mfe=2.5, mae=0.4),
        at=datetime.now(UTC),
    )
    audit_repo._queue.join()

    events = tracker.list_events_for_ticket(8801)
    exited = [e for e in events if e.event_type.value == "POSITION_EXITED"]
    assert len(exited) == 1, "finalize_exit must emit exactly one POSITION_EXITED"
    detail = exited[0].detail
    assert "TAKE_PROFIT" in detail, "enriched exit mechanism must reach the event detail"
    assert "-37.50" in detail or "-37.5" in detail, (
        "enriched realized net PnL must reach the event detail"
    )


# ---------------------------------------------------------------------------
# (b) sqlite3.connect is never called directly by _read_closed_ledger
# ---------------------------------------------------------------------------


def test_b_no_raw_sqlite_connect_on_sqlite_path(
    audit_repo: AuditRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enrichment routes through the seam; the seam is entered exactly once.

    Spying ``AuditRepository._connect_sqlite`` is the sharp assertion: if the
    tick path bypassed the repository seam (the INV-001 defect) the spy would
    record ZERO seam calls while the read still succeeded via a raw
    ``sqlite3.connect`` — the BUG-156 URI-contract violation class.
    """
    _seed_closed_ledger_row(audit_repo, 5050)
    seam_calls: list[float] = []
    original = AuditRepository._connect_sqlite

    def _spy(self: AuditRepository, timeout: float) -> sqlite3.Connection:
        seam_calls.append(float(timeout))
        return original(self, timeout)

    monkeypatch.setattr(AuditRepository, "_connect_sqlite", _spy)

    tracker = PositionLifecycleTracker(audit_repo=audit_repo)
    net, mechanism, _r = tracker._read_closed_ledger("5050", PositionPerformance())

    assert net == pytest.approx(-37.50)
    assert mechanism == "TAKE_PROFIT"
    assert seam_calls == [pytest.approx(5.0)], (
        "the enrichment must enter the repository's ONE connect site exactly "
        "once, at the bounded 5.0s busy timeout (pre-INV-001 semantics)"
    )


def test_b_no_raw_sqlite_connect_source_on_tick_path() -> None:
    """The lifecycle module holds no direct sqlite3.connect call site.

    Source-level guard (INV-001 V1): the tick path's enrichment must reach the
    DB exclusively through the repository seam. A reintroduced raw connect in
    ``intelligence/lifecycle.py`` would reopen the BUG-156 disk-leak class.
    """
    module = __import__(PositionLifecycleTracker.__module__, fromlist=["*"])
    file_path = Path(getattr(module, "__file__", ""))
    text = file_path.read_text(encoding="utf-8")
    assert "sqlite3.connect(" not in text, (
        "intelligence/lifecycle.py must not call sqlite3.connect directly "
        "(route through AuditRepository._connect_sqlite)"
    )
    assert "_connect_sqlite" in text, "the seam accessor must be used"


def test_b_ledger_reads_route_through_the_seam_too(
    audit_repo: AuditRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same defect class in experience/ledger.py uses the seam as well.

    ``ExperienceLedger._connect`` was the raw-connect twin flagged by lane E;
    it now delegates to the repository accessor too.
    """
    from nexus_scalp.experience.ledger import ExperienceLedger

    seam_calls: list[float] = []
    original = AuditRepository._connect_sqlite

    def _spy(self: AuditRepository, timeout: float) -> sqlite3.Connection:
        seam_calls.append(float(timeout))
        return original(self, timeout)

    monkeypatch.setattr(AuditRepository, "_connect_sqlite", _spy)

    ledger = ExperienceLedger(audit_repo=audit_repo)
    assert ledger.get_experiences_for_strategy("strat_inv001") == []
    assert seam_calls == [pytest.approx(5.0)], (
        "the ledger read path must enter the repository seam, not a raw "
        "sqlite3.connect on the repository path"
    )


# ---------------------------------------------------------------------------
# (c) Non-SQLite repository: zero SQLite connections, fast defaults
# ---------------------------------------------------------------------------


class _NonSqliteRepo(AuditRepository):
    """A PostgreSQL-shaped repository with NO SQLite side effects.

    Real ``AuditRepository("postgresql://...")`` would try to provision a
    pooled PG backend (network, out of scope for a unit test). The INV-001
    contract under test is narrower and sharper: the OPTIONAL enrichment must
    gate on the provider BEFORE touching any connection, so a duck-typed
    repository stub of the provider-gated surface is the honest probe.
    """

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self._is_sqlite = False
        self._db_url = "postgresql://stub.invalid:5432/nse_audit"
        self._db_path = ""

    # Deliberately NOT provided: no _connect_sqlite, no SQLite machinery. If
    # the enrichment ever tried to open a connection here, AttributeError /
    # sqlite3.connect would be the failure mode the test catches.


def test_c_non_sqlite_repo_skips_enrichment_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PostgreSQL repository returns the defaults fast and opens nothing."""
    real_connect = sqlite3.connect
    calls: list[tuple[object, ...]] = []

    def _spy_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        calls.append((args, kwargs))
        return real_connect(*args, **kwargs)  # type: ignore[arg-type,call-overload]

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    repo = _NonSqliteRepo()
    tracker = PositionLifecycleTracker(audit_repo=repo)
    result = tracker._read_closed_ledger("9999", PositionPerformance())

    assert result == (0.0, "", 0.0), (
        "non-SQLite repositories must return the documented defaults so the "
        "optional enrichment never adds PG latency to the tick path"
    )
    assert calls == [], (
        "non-SQLite repositories must open ZERO sqlite3 connections on the "
        "tick path (INV-001 provider gate)"
    )


def test_c_non_sqlite_repo_ledger_reads_are_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same defect class in experience/ledger.py is gated identically.

    Lane E flagged the raw-connect twin at ``ledger.py:455``; its callers all
    gate on ``_is_sqlite`` before ``_connect``, so a non-SQLite repository
    never reaches the SQLite accessor.
    """
    from nexus_scalp.experience.ledger import ExperienceLedger

    repo = _NonSqliteRepo()
    ledger = ExperienceLedger(audit_repo=repo)

    real_connect = sqlite3.connect
    calls: list[tuple[object, ...]] = []

    def _spy_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        calls.append((args, kwargs))
        return real_connect(*args, **kwargs)  # type: ignore[arg-type,call-overload]

    monkeypatch.setattr(sqlite3, "connect", _spy_connect)

    assert ledger.get_experiences_for_strategy("strat_stub") == []
    assert calls == [], "non-SQLite ledger reads must not open SQLite connections"


def test_c_provider_gate_returns_before_any_connection_attempt(
    audit_repo: AuditRepository,
) -> None:
    """The gate is evaluated BEFORE the connection, not after a failed one.

    Proof: flipping a SQLite repo to non-SQLite after construction still
    short-circuits, and the seam is never entered (asserted by the fact that
    the closed row the repo DOES hold is not returned).
    """
    _seed_closed_ledger_row(audit_repo, 7070)
    audit_repo._is_sqlite = False
    tracker = PositionLifecycleTracker(audit_repo=audit_repo)

    net, mechanism, r_mult = tracker._read_closed_ledger("7070", PositionPerformance())

    assert (net, mechanism, r_mult) == (0.0, "", 0.0), (
        "the gate must return defaults without reading the SQLite row"
    )
