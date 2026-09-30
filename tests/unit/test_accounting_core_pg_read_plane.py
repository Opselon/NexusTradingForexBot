"""PG-ACCT-READ-001 — the Accounting tab reads a store it never queries.

SYMBOL: http://localhost:8091/account renders a self-consistent empty page —
"No closed trades recorded for this day yet.", "No accounting snapshots stored
yet.", SHARPE/SORTINO/CALMAR/SQN all "—" — while /api/account/trades on the
SAME server returns 25 real closed trades and /api/account/growth returns 9005
snapshots from the same tables.

The chain (verified end to end on the live box, 2026-09-30):

    React /account
      -> GET /api/account/performance | /equity-curve | /performance/DAY/series
      -> web/debug_research_routes._accounting()
      -> engine.accounting_core.load_trades / load_snapshots / period_report
      ->     if not self._enabled: return []            <-- the silent gate
      ->     @property _enabled: repo._is_sqlite         <-- False (PostgreSQL)
      ->     sqlite3.connect(repo._db_path, ...)         <-- never reached

``_enabled`` inverted the meaning of "readable": it returned True only when the
audit repository was SQLite. The engine migrated to PostgreSQL, so under the
live default every accounting read silently degraded to ``[]`` / a dataless
``PeriodReport`` — no exception, no failed request (HTTP 200 on every endpoint),
no log line. The frontend then rendered the honest-empty state faithfully,
which is exactly why the page looked broken-but-fine.

Three independent defects were found while fixing this one symbol:

1. The SQLite-only gate above (``_enabled``) plus the raw ``sqlite3.connect``
   in ``_connect()`` — the read path was structurally unable to ask the server
   the engine writes to. Fixed by routing every query through the domain's own
   registered READ plane (same resolver the audit routes already use), keeping
   the SQLite connection for SQLite.

2. ``load_snapshots`` compared ``timestamp`` (written by
   ``datetime.now(UTC).isoformat()``, i.e. ALWAYS ISO with a 'T') against a
   cutoff built with ``strftime('%Y-%m-%d %H:%M:%S')`` (space separator). On
   any sub-day bound the lexicographic comparison is wrong in BOTH directions
   because 'T' (0x54) > ' ' (0x20): '2026-09-30T08:00:00' >= '2026-09-30
   09:00:00' is TRUE, so the window admits the past; and
   '2026-09-30T11:00:00' < '2026-09-30 10:00:00' is TRUE, so it admits the
   future. Proven on the live server: 277 snapshots existed for 10:00-11:00
   while the raw predicate matched 0. ``load_trades`` had already been fixed
   this way (TASK-1); the snapshot path was missed.

3. ``CAST(ticket AS INTEGER)`` against ``100000000000`` (1e11) overflowed
   PostgreSQL's 32-bit INTEGER — MT5 position tickets are >= 1e11, so the cast
   raised ``NumericValueOutOfRange: integer out of range`` and the whole trade
   load fell back to "no rows" via the bare ``except``. SQLite's INTEGER is
   64-bit so it never surfaced there. BIGINT is 64-bit on PostgreSQL and plain
   INTEGER-affinity on SQLite, so one expression serves both.

This test pins all three: provider-aware reads through the repo's own declared
READ plane, normalized timestamp bounds that hold on ISO and legacy formats,
and a ticket cast that survives PostgreSQL's 32-bit INTEGER. It fails before
the fix (gate returns [] / sub-day windows match nothing / ticket cast raises)
and passes after.

The fake plane is shaped like ``PgReadPlane`` (query/query_one, no ``execute``
— a write-shaped backend is refused for reads), per the established shape in
``test_audit_pg_read_plane.py`` and ``test_research_read_plane_pg.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.accounting.core import AccountingCore
from nexus_scalp.accounting.models import TradeOutcome
from nexus_scalp.accounting.periods import PeriodKind


class _AcctReadPlane:
    """A read-plane-shaped fake standing in for a live PostgreSQL read pool.

    Answers the accounting surface's real SQL with shapes the SQLite path
    would have produced, so the assertions describe behaviour, not mock
    plumbing. Counts are unmistakably non-zero (a real empty table answers 0).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    # -- read surface only (no execute: reads must not share the write path) ---
    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append(("query", sql, tuple(args)))
        s = sql.upper()
        if "AUDIT_ACCOUNT_SNAPSHOTS" in s:
            # Emulate the server applying the WHERE window. The pre-fix raw
            # predicate matched NEITHER of these against a space-separated
            # cutoff; the normalized bound must keep exactly the in-window row.
            rows = [
                {
                    "timestamp": "2026-09-30T08:00:00+00:00",
                    "balance": 30000.0,
                    "equity": 30100.0,
                    "margin_free": 29000.0,
                    "peak_equity": 30100.0,
                },
                {
                    "timestamp": "2026-09-30T10:30:00+00:00",
                    "balance": 30100.0,
                    "equity": 30200.0,
                    "margin_free": 29100.0,
                    "peak_equity": 30200.0,
                },
            ]
            return self._apply_window(rows, args)
        if "AUDIT_EXPERIENCE_OUTCOMES" in s:
            # Two different queries hit this table: the identity join
            # (o.execution_id, e.experience_id, ...) and the quality detail
            # (o.strategy_quality, o.entry_quality, ...). Answer the one asked.
            if "STRATEGY_QUALITY" in s:
                return [
                    {
                        "strategy_quality": 0.62,
                        "entry_quality": 0.41,
                        "execution_quality": 0.77,
                        "management_quality": 0.30,
                        "exit_quality": 0.55,
                        "behavioral_flags": "",
                        "slippage_points": -0.15,
                        "execution_latency_ms": 12.3,
                        "exit_reason": "NSE_CLOSE",
                        "realized_r_multiple": 0.4,
                    }
                ]
            return [
                {
                    "execution_id": "152702994183",
                    "experience_id": "exp_1",
                    "strategy_id": "strat_1",
                    "strategy_version": "1.0.0",
                    "model_id": "m",
                    "model_version": "v1",
                    "feature_schema_id": "s",
                    "feature_dimension": 50,
                }
            ]
        if "AUDIT_LEDGER" in s:
            return [
                {
                    "ticket": 152702994183,
                    "symbol": "XAUUSD",
                    "direction": "BUY",
                    "volume": 0.17,
                    "entry_price": 4197.02,
                    "exit_price": 4197.30,
                    "status": "CLOSED",
                    "timestamp": "2026-09-30T07:20:27+00:00",
                    "close_time": "2026-09-30T07:22:27+00:00",
                    "net_pnl_usd": 4.76,
                    "account_source": "",
                }
            ]
        return []

    @staticmethod
    def _apply_window(rows: list[dict[str, Any]], args: tuple[Any, ...]) -> list[dict[str, Any]]:
        """Replays the SQL time bounds over the candidate rows.

        Cutoffs arrive as strftime strings ('YYYY-MM-DD HH:MM:SS') and row
        timestamps are ISO with a 'T' and a trailing offset; normalize both
        sides exactly the fixed expression does before comparing. This is what
        makes the sub-day window assertion meaningful at all — a stub that
        ignored the window would pass against the broken code.
        """
        cutoffs = [a for a in args if isinstance(a, str) and " " in a and ":" in a]
        if len(cutoffs) < 2:
            return list(rows)
        since, until = cutoffs[0], cutoffs[1]

        def norm(ts: str) -> str:
            return ts.replace("T", " ").replace("+00:00", "")

        kept = [r for r in rows if since <= norm(r["timestamp"]) < until]
        limit = next((a for a in args if isinstance(a, int) and a > 0), None)
        return kept[:limit] if limit is not None else kept

    def query_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self.calls.append(("query_one", sql, tuple(args)))
        rows = self.query(sql, args)
        return rows[0] if rows else None


@pytest.fixture()
def isolated_fabric_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private fabric registry: the module-level slot is process-global."""
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


@pytest.fixture()
def pg_repo(monkeypatch: pytest.MonkeyPatch, isolated_fabric_registry: None) -> Any:
    """A non-SQLite audit repo whose ``audit`` READ plane is registered.

    Mirrors the production wiring (verified on the live box:
    ``AuditRepository()._is_sqlite`` is False and the provider is
    ``postgresql://localhost:5432/nexusdb``): the plane is registered in the
    fabric under ``(domain, readonly=True)`` and resolved by the repository's
    own ``_registered_audit_read_plane()`` — no second connection path, and the
    accounting core reuses that same resolver.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database import fabric as fabric_mod

    fabric_mod.register_domain_read_backend("audit", _AcctReadPlane())

    repo = AuditRepository.__new__(AuditRepository)
    repo._is_sqlite = False
    repo._db_url = "postgresql://localhost:5432/nexusdb"
    repo._db_path = "postgresql://localhost:5432/nexusdb"
    return repo


class TestAccountingCoreReadsProviderStore:
    """The core must read the store the engine actually writes to."""

    def test_enabled_is_true_when_a_read_plane_is_registered(self, pg_repo: Any) -> None:
        """The pre-fix gate made _enabled False under PostgreSQL, killing every
        read before it ran. Readable now means: SQLite with a path, OR a
        reachable read plane."""
        core = AccountingCore(audit_repo=pg_repo)
        assert core._enabled is True
        assert core._read_plane is not None
        # A write-shaped backend must be refused for reads (the guard's rule).
        assert not hasattr(core._read_plane, "execute")

    def test_load_trades_reads_the_provider_not_an_empty_list(self, pg_repo: Any) -> None:
        """Before the fix this returned [] with the plane never consulted."""
        core = AccountingCore(audit_repo=pg_repo)
        trades = core.load_trades(limit=100)
        assert len(trades) == 1
        trade = trades[0]
        assert trade.ticket == 152702994183
        assert trade.symbol == "XAUUSD"
        assert trade.closed_at is not None
        # The identity join (ticket -> outcome -> experience) must resolve
        # through the same plane, not be skipped as unreadable.
        assert trade.strategy_id == "strat_1"

    def test_period_reports_report_real_data_under_postgresql(self, pg_repo: Any) -> None:
        """The Accounting tab's period cards said has_data=False / 0 trades
        while the ledger had rows. The report must now see them."""
        core = AccountingCore(audit_repo=pg_repo)
        report = core.period_report(PeriodKind.DAY)
        assert report.has_data is True
        assert report.total_trades >= 1

    def test_all_period_reports_covers_every_granularity(self, pg_repo: Any) -> None:
        """The tab renders DAY/WEEK/MONTH/YEAR cards; all four must be live."""
        core = AccountingCore(audit_repo=pg_repo)
        reports = core.all_period_reports()
        assert set(reports.keys()) == {k.value for k in PeriodKind}
        assert all(r.has_data for r in reports.values())

    def test_sub_day_snapshot_window_matches_on_iso_timestamps(self, pg_repo: Any) -> None:
        """The snapshot bound bug is provider-independent: ISO 'T' timestamps
        vs space-separated cutoffs broke every sub-day window. The normalized
        bound must keep the row inside the window and drop the one before it —
        in both providers."""
        from datetime import UTC, datetime

        core = AccountingCore(audit_repo=pg_repo)
        window = core.load_snapshots(
            since=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
            until=datetime(2026, 9, 30, 11, 0, tzinfo=UTC),
        )
        assert len(window) == 1
        assert window[0].balance == 30100.0

    def test_drawdown_and_equity_curve_are_populated(self, pg_repo: Any) -> None:
        """The tab's 'No accounting snapshots stored yet.' empty state and the
        all-'—' drawdown cards come from the same gated snapshot read."""
        core = AccountingCore(audit_repo=pg_repo)
        dd = core.drawdown_report()
        assert dd.has_data is True
        assert dd.sample_count == 2
        curve = core.equity_curve(lookback_days=None)
        assert len(curve) == 2
        assert all("drawdown_pct" in point for point in curve)

    def test_ticket_cast_is_64_bit_safe(self, pg_repo: Any) -> None:
        """The real MT5 ticket space is >= 1e11. PostgreSQL INTEGER is 32-bit
        and CAST(... AS INTEGER) raised NumericValueOutOfRange there, sinking
        the whole trade load through the bare except. The core must not raise
        and must return the row."""
        core = AccountingCore(audit_repo=pg_repo)
        trades = core.load_trades(limit=100)
        assert trades
        assert all(t.ticket >= 100000000000 for t in trades)

    def test_trade_trace_resolves_through_the_plane(self, pg_repo: Any) -> None:
        """Forensics used to report NON_SQLITE_BACKEND outright."""
        core = AccountingCore(audit_repo=pg_repo)
        trace = core.trade_trace(152702994183)
        assert trace.found is True
        assert "NON_SQLITE_BACKEND" not in trace.notes
        assert trace.trade["symbol"] == "XAUUSD"

    def test_sqlite_backend_is_unchanged(self, tmp_path: object) -> None:
        """SQLite keeps its local connection path: the fix must not reroute
        it or change a single row it returns."""
        import sqlite3
        from pathlib import Path

        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db_path = Path(str(tmp_path)) / "acct.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE audit_ledger ("
            "ticket TEXT, symbol TEXT, direction TEXT, volume REAL, "
            "entry_price REAL, exit_price REAL, status TEXT, timestamp TEXT, "
            "close_time TEXT, net_pnl_usd REAL, account_source TEXT)"
        )
        conn.execute(
            "INSERT INTO audit_ledger VALUES "
            "(?, 'XAUUSD', 'BUY', 0.1, 2650.0, 2651.0, 'CLOSED', "
            "'2026-09-20T10:00:00+00:00', '2026-09-20T10:05:00+00:00', 10.0, '')",
            ("152702994183",),
        )
        conn.commit()
        conn.close()

        repo = AuditRepository.__new__(AuditRepository)
        repo._is_sqlite = True
        repo._db_url = f"sqlite:///{db_path}"
        repo._db_path = str(db_path)

        core = AccountingCore(audit_repo=repo)
        assert core._enabled is True
        # No read plane is consulted for SQLite.
        assert core._read_plane is None
        trades = core.load_trades(limit=100)
        assert len(trades) == 1
        assert trades[0].net_pnl == pytest.approx(10.0)
        assert trades[0].outcome is TradeOutcome.WIN
