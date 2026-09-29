"""R-3 for the v1 decision-analytics plane: the count must come from the slice.

L5's request to L3 (`wave-dir/requests.md`) measured `count_decisions` as a
Seq Scan on PostgreSQL: "the shipped bound caps the *returned* answer at
2,000, but the planner chose a full `Seq Scan` + top-N sort". The cause is the
same defect class as R-3 — the majority predicate sits INSIDE the bounded
subquery::

    SELECT COUNT(*) FROM (
      SELECT id FROM audit_signals
       WHERE UPPER(action) = UPPER(?) AND generated_at >= ?
       ORDER BY id DESC LIMIT 2000
    ) AS recent

Its sibling `get_decision_stats` puts its filter OUTSIDE the slice and was
measured bounded. MEASURED on a scratch PostgreSQL 17.10 cluster,
production-shaped 92%-NO_TRADE corpus, `EXPLAIN (ANALYZE, BUFFERS)`:

    rows | count_decisions (filter INSIDE)   | get_decision_stats (OUTSIDE)
    -----+-----------------------------------+------------------------------
     20K | Seq Scan 18,400 rows, 13.8 ms     | Index Scan Backward, 1.79 ms
    200K | Seq Scan 184,000 rows, 158.2 ms   | Index Scan Backward, 1.12 ms

11.5x per 10x of ledger growth (O(ledger)) versus a flat 2,000-row tail
(O(1)); 141x apart at 200K. The recent-cutoff arm is equally affected
(140.9 ms at 200K) because the predicate, not the window, is what defeats the
index.

`/api/v1/decisions/no-trade` now reads the SAME bounded slice as its sibling
endpoints instead of calling `count_decisions`. These tests pin that:

1. the route never calls the unbounded-shape helper (fails on revert);
2. its numbers equal a slice-based reference computed independently here;
3. the disclosed bound is present, so a client can tell a sampled answer from
   an exhaustive one.

The fixture is deliberately larger than the slice bound: with a handful of
rows the old and new shapes return the same number and the test would be a
false green.

Run:
  env -u NSE_DATABASE__PROVIDER -u NSE_DATABASE__PG_HOST -u NSE_DATABASE__PG_PORT \\
      -u NSE_DATABASE__PG_USER -u NSE_DATABASE__PG_DATABASE PYTHONPATH=src \\
      .venv/Scripts/python.exe -m pytest \\
      tests/unit/test_r3_v1_decision_count_bounded.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.web.api_v1_wiring import create_v1_app


def _repo(db: Path) -> AuditRepository:
    """A repository bound to the seeded SQLite file (the shipped test idiom)."""
    return AuditRepository(config=DatabaseConfig(provider="sqlite", sqlite_path=str(db)))


#: Must match the repository's own `_DECISION_STATS_MAX_SAMPLE` — asserted
#: below against the observable behaviour rather than imported privately.
_SLICE = 2000

_DDL = """
CREATE TABLE audit_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    proposed_entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    regime TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    execution_mode TEXT,
    reason_code TEXT,
    decision_stage TEXT,
    blocked_by TEXT,
    htf_score REAL,
    smc_score REAL,
    confidence_before_filters REAL,
    confidence_after_filters REAL
);
"""

_INSERT = (
    "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
    "proposed_entry, stop_loss, take_profit, regime, generated_at, payload, "
    "execution_mode, reason_code, decision_stage, blocked_by) VALUES "
    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_TS = "2026-09-29T{hour:02d}:{minute:02d}:00+00:00"

#: A generated_at inside the repository's default analytics window
#: (``get_decision_stats(hours_back=168.0)``) and outside it. Computed from
#: the clock so the fixture cannot go stale.
_INSIDE_WINDOW = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
_OUTSIDE_WINDOW = "2020-01-01T00:00:00+00:00"

_REASONS = ("SPREAD_GATE", "CONFIDENCE_FLOOR", "REGIME_BLOCK", "NEWS_BLACKOUT")


def _mix(n_no_trade: int, n_other: int) -> list[bool]:
    """Interleave the two actions so EVERY window of the ledger is ~92% NT.

    Inserting all NO_TRADE rows first (lowest ids) would leave the newest
    slice - the only rows a bounded read sees - entirely composed of the
    non-NO_TRADE rows, which is not the production shape and makes the
    assertion test the fixture rather than the fix.
    """
    total = n_no_trade + n_other
    share = n_no_trade / total
    flags: list[bool] = []
    emitted_nt = 0
    for i in range(total):
        want_nt = (emitted_nt + 1) / (i + 1) <= share
        if want_nt and emitted_nt < n_no_trade:
            flags.append(True)
            emitted_nt += 1
        else:
            flags.append(False)
    # Any remainder (rounding) is topped up from the end.
    for i in range(total):
        if emitted_nt >= n_no_trade:
            break
        if not flags[i]:
            flags[i] = True
            emitted_nt += 1
    assert emitted_nt == n_no_trade, (emitted_nt, n_no_trade)
    assert flags.count(False) == n_other, (flags.count(False), n_other)
    return flags


def _seed(db: Path, n_no_trade: int, n_other: int) -> None:
    """Production-shaped ledger: NO_TRADE is the ~92% majority throughout."""
    con = sqlite3.connect(db)
    try:
        con.executescript(_DDL)
        rows: list[tuple[Any, ...]] = []
        for i, is_nt in enumerate(_mix(n_no_trade, n_other)):
            rows.append(
                (
                    f"v1-r3-req-{i}",
                    "XAUUSD",
                    "NO_TRADE" if is_nt else "BUY",
                    0.5,
                    3300.0,
                    3267.0,
                    3333.0,
                    "RANGING_MEAN_REVERSION",
                    _INSIDE_WINDOW,
                    json.dumps({"model_action": "NO_TRADE"}),
                    "PAPER",
                    _REASONS[i % 4] if is_nt else None,
                    "TERMINAL",
                    _REASONS[i % 4] if is_nt else None,
                )
            )
        con.executemany(_INSERT, rows)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "audit.db"
    _seed(db, n_no_trade=_SLICE + 500, n_other=150)
    app = create_v1_app()
    app.state.audit_v1_repo = _repo(db)
    return TestClient(app)


def _data(resp: Any) -> Any:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"data", "meta"}
    return body["data"]


class TestV1DecisionCountIsSliceBounded:
    def test_route_does_not_call_the_filter_inside_slice_helper(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect is *which helper* the route calls. Reverting the fix
        restores ``count_decisions(action='NO_TRADE')`` and this fails."""
        calls: list[Any] = []
        real = AuditRepository.count_decisions

        def spy(self: Any, *a: Any, **kw: Any) -> int:
            calls.append((a, kw))
            return real(self, *a, **kw)

        monkeypatch.setattr(AuditRepository, "count_decisions", spy)
        data = _data(client.get("/api/v1/decisions/no-trade"))
        assert data["total"] > 0
        assert calls == [], (
            "the route is back on count_decisions(), whose predicate sits "
            f"inside the bounded subquery (R-3, O(ledger)). calls={calls!r}"
        )

    def test_total_and_distribution_match_a_slice_based_reference(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Every number equals an independent slice computation.

        The reference is the ``get_decision_stats`` shape (filter OUTSIDE the
        slice, then the window predicate applied to the slice), computed here
        in this test so it cannot drift with the fix.
        """
        data = _data(client.get("/api/v1/decisions/no-trade"))
        # The window predicate is applied AFTER the slice (the repository's
        # own documented semantics and the reason the shape is bounded), so
        # the reference must apply it to the slice too.
        cutoff = (datetime.now(UTC) - timedelta(hours=168)).isoformat()
        db = tmp_path / "audit.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            ref = [
                dict(r)
                for r in con.execute(
                    "SELECT action, reason_code, generated_at FROM audit_signals "
                    "ORDER BY id DESC LIMIT ?",
                    (_SLICE,),
                ).fetchall()
            ]
        finally:
            con.close()
        ref = [r for r in ref if (r["generated_at"] or "") >= cutoff]
        assert ref, "the slice must contain rows inside the window"

        # The repository groups the WHOLE slice by (action, reason_code) and
        # reports ``by_action``/``by_group`` separately; the route takes the
        # NO_TRADE action bucket as its count and the whole-slice reason
        # breakdown as its distribution. Both are reproduced here against the
        # same slice so neither can drift with the fix.
        ref_total = sum(1 for r in ref if str(r["action"]).upper() == "NO_TRADE")
        assert data["total"] == ref_total
        assert data["total"] > 0

        ref_reasons: dict[str, int] = {}
        for r in ref:
            key = r["reason_code"] or "NOT_RECORDED"
            ref_reasons[key] = ref_reasons.get(key, 0) + 1
        assert data["rejection_reasons"] == ref_reasons, data["rejection_reasons"]
        # ``total`` counts only the NO_TRADE rows; the distribution covers the
        # whole slice (including the other actions' NOT_RECORDED bucket), so
        # total <= the distribution sum, and equals the sum of the reason
        # buckets a NO_TRADE row can carry.
        assert data["total"] <= sum(data["rejection_reasons"].values())
        assert data["total"] == sum(v for k, v in ref_reasons.items() if k in _REASONS)

    def test_the_disclosed_bound_is_present_and_honest(self, client: TestClient) -> None:
        """A sampled answer must say so — the panel labels it approximate."""
        data = _data(client.get("/api/v1/decisions/no-trade"))
        assert data["sampled_rows"] == _SLICE
        assert data["exhaustive"] is False, (
            "the ledger is larger than the slice, so the answer is NOT exhaustive"
        )
        assert data["group_key"] == "reason_code"
        # the `latest` half of the payload is unchanged
        assert data["latest"] is not None
        assert data["latest"]["action"] == "NO_TRADE"

    def test_an_exhaustive_answer_is_labelled_exhaustive(self, tmp_path: Path) -> None:
        """When the ledger fits inside the slice the answer IS exhaustive.

        Guards against the fix always disclosing a bound it did not need, and
        against ``exhaustive`` being hard-coded either way.
        """
        db = tmp_path / "small.db"
        # 50 NO_TRADE rows + 10 BUY rows: 60 < _SLICE, so the slice sees the
        # whole ledger and the window keeps every row.
        _seed(db, n_no_trade=50, n_other=10)
        app = create_v1_app()
        app.state.audit_v1_repo = _repo(db)
        data = _data(TestClient(app).get("/api/v1/decisions/no-trade"))
        assert data["exhaustive"] is True, "the whole ledger fits in the slice"
        assert data["total"] == 50
        # the distribution covers the whole slice (60 rows), by design
        assert sum(data["rejection_reasons"].values()) == 60

    def test_the_count_is_not_the_whole_ledger(self, client: TestClient) -> None:
        """The pre-fix helper answered the whole ledger; the slice must not.

        The ledger holds ``_SLICE + 500`` NO_TRADE rows and the slice holds
        ``_SLICE``, so a whole-ledger count is distinguishable from a bounded
        one - with a tiny fixture it would not be.
        """
        data = _data(client.get("/api/v1/decisions/no-trade"))
        # The slice is ``_SLICE`` rows at the measured ~92.6% NO_TRADE share,
        # so the count is the NO_TRADE share of the slice, never the ledger's
        # ``_SLICE + 500`` NO_TRADE rows.
        assert data["total"] < _SLICE + 500
        assert data["total"] <= _SLICE
        assert data["total"] > 0
