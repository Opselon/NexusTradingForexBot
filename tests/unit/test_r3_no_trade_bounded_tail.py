"""R-3 regression: the /api/operator/no-trade tail read must actually bind.

FINDING R-3 (peer Phase-2 matrix, `PHASE2-RANKED-REMEDIATION-MATRIX.md`):
PR #561's bounded tail did not bind at `/api/operator/no-trade`. The route
built ONE bounded query but left the majority predicate INSIDE it::

    SELECT ... FROM audit_signals
     WHERE UPPER(action) = 'NO_TRADE'      <-- selects ~92-94.6% of rows
     ORDER BY id DESC LIMIT 2000           <-- therefore unreachable

A predicate matching a majority of the table has no index path, so the
planner must consider every row before the LIMIT can stop. Measured on a
scratch PostgreSQL 17.10 cluster (`EXPLAIN (ANALYZE, BUFFERS)`, 20K/200K
production-shaped rows):

    rows | PRE (filter inside slice)          | POST (bare pkey tail)
    -----+------------------------------------+-----------------------------
      20K| Seq Scan, 18,400 rows, 15.8 ms     | Index Scan Backward, 4,000
         | top-N heapsort of 1,128 kB         | rows, 1.12 ms
    200K | Seq Scan, 184,000 rows, 136.0 ms   | Index Scan Backward, 4,000
         | top-N heapsort of 1,128 kB         | rows, 0.88 ms

PRE is O(ledger) (18,400 -> 184,000 rows examined for a 10x ledger growth);
POST is O(1) (a constant 4,000-row tail at both points). The fix moves the
filter OUTSIDE the slice — the shape the sibling `get_decision_stats` already
uses and which L5 measured flat across 10K->10M rows on SQLite.

WHY THESE TESTS AND NOT A 3-ROW FIXTURE
---------------------------------------
A fixture smaller than the fetch bound proves nothing: with 7 rows both the
pre-fix and post-fix shapes return the identical payload, so the test would be
a false green (the wave contract calls this out explicitly). This module
therefore pins the defect THREE ways, each of which FAILS if the fix is
reverted:

1. `test_no_majority_predicate_reaches_sql` — the SQL the route hands the
   ledger must carry no `action` predicate at all. Reverting restores
   `where="UPPER(action) = 'NO_TRADE'"` and this fails.
2. `test_tail_read_is_bounded_when_the_tail_is_not_no_trade` — a fixture whose
   newest 5,000 rows are NOT NO_TRADE, followed by a deep NO_TRADE history.
   The pre-fix shape walks arbitrarily far back to fill its 2,000-row quota
   and reports a non-zero total; the bounded shape reports 0, because it only
   ever looks at the newest slice. This is the R-3 defect expressed
   behaviourally, and it needs a fixture LARGER than the bound (5,000+ rows).
3. `test_payload_shape_and_distributions_unchanged` — the guard on the fix:
   the endpoint's payload shape and every distribution must be byte-identical
   to what the pre-fix shape produced on a production-shaped ledger.

Run:
  env -u NSE_DATABASE__PROVIDER -u NSE_DATABASE__PG_HOST -u NSE_DATABASE__PG_PORT \\
      -u NSE_DATABASE__PG_USER -u NSE_DATABASE__PG_DATABASE PYTHONPATH=src \\
      .venv/Scripts/python.exe -m pytest \\
      tests/unit/test_r3_no_trade_bounded_tail.py -q -p no:cacheprovider
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web import operator_routes
from nexus_scalp.web.operator_routes import _NO_TRADE_SCAN_FACTOR, _NO_TRADE_WINDOW
from nexus_scalp.web.server import create_app

FAKE_WEB_AUTH_TOKEN = "r3-bounded-tail-fake-token"

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
    blocked_by TEXT
);
CREATE TABLE audit_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket INTEGER,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    volume REAL NOT NULL,
    reason TEXT,
    latency REAL,
    execution_mode TEXT,
    execution_id TEXT,
    timestamp TEXT NOT NULL
);
"""

_INSERT = (
    "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
    "proposed_entry, stop_loss, take_profit, regime, generated_at, payload, "
    "execution_mode, reason_code, decision_stage, blocked_by) VALUES "
    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

#: A generated_at 1 minute in the past, INSIDE every ``hours`` window this
#: module exercises. Computed from the clock so the fixture cannot go stale.
_FRESH_TS = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
#: A generated_at far outside any realistic window.
_ANCIENT_TS = "2020-01-01T00:00:00+00:00"


def _row(
    idx: int,
    *,
    action: str,
    reason: str,
    gate: str,
    regime: str,
    generated_at: str = _FRESH_TS,
) -> tuple[Any, ...]:
    return (
        f"r3-req-{idx}",
        "XAUUSD",
        action,
        0.5,
        3300.0,
        3267.0,
        3333.0,
        regime,
        generated_at,
        '{"model_action": "NO_TRADE", "ai_no_trade_probability": 0.9}',
        "PAPER",
        reason,
        "TERMINAL",
        gate,
    )


def _seed(db: Path, rows: list[tuple[Any, ...]]) -> None:
    con = sqlite3.connect(db)
    try:
        con.executescript(_DDL)
        con.executemany(_INSERT, rows)
        con.commit()
    finally:
        con.close()


def _production_shaped_ledger(n_no_trade: int, n_other: int) -> list[tuple[Any, ...]]:
    """A ledger at the measured NO_TRADE share (~92%), deterministic content.

    Reason/gate/regime cycle through a few values so every distribution has
    more than one bucket and the ordering of ``most_common()`` is exercised.
    """
    reasons = ("SPREAD_GATE", "CONFIDENCE_FLOOR", "REGIME_BLOCK", "NEWS_BLACKOUT")
    gates = ("SPREAD_GATE", "CONFIDENCE_FLOOR", "REGIME_BLOCK", "NEWS_BLACKOUT")
    regimes = ("RANGING_MEAN_REVERSION", "TRENDING_UP", "TRENDING_DOWN")
    out: list[tuple[Any, ...]] = []
    for i in range(n_no_trade):
        out.append(
            _row(
                i,
                action="NO_TRADE",
                reason=reasons[i % 4],
                gate=gates[i % 4],
                regime=regimes[i % 3],
            )
        )
    for j in range(n_other):
        out.append(_row(n_no_trade + j, action="BUY", reason="", gate="", regime=regimes[j % 3]))
    return out


@pytest.fixture
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", FAKE_WEB_AUTH_TOKEN)


def _client(
    tmp_path: Path, rows: list[tuple[Any, ...]], monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    db = tmp_path / "audit.db"
    _seed(db, rows)
    monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(db))
    app = create_app(engine_ref=None)
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {FAKE_WEB_AUTH_TOKEN}"})
    return c


# ---------------------------------------------------------------------------
# 1. Structural: no majority predicate may reach SQL
# ---------------------------------------------------------------------------


class TestNoMajorityPredicateReachesSql:
    """The defect is a *predicate placement*, so pin the placement directly.

    On SQLite the pre-fix shape is incidentally cheap (a 92%-dense table
    satisfies its quota after ~2,174 rows), so a timing or rows-visited
    assertion cannot see the defect on the platform CI runs. The predicate
    placement is the invariant that makes the PostgreSQL plan a Seq Scan, and
    it is observable here without a PostgreSQL server.
    """

    def test_no_majority_predicate_reaches_sql(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        calls: list[dict[str, Any]] = []
        real_select = operator_routes.LedgerReader.select

        def spy(self: Any, proj: str, table: str, limit: int, **kw: Any) -> Any:
            calls.append({"proj": proj, "table": table, "limit": limit, **kw})
            return real_select(self, proj, table, limit, **kw)

        monkeypatch.setattr(operator_routes.LedgerReader, "select", spy)
        client = _client(tmp_path, _production_shaped_ledger(600, 60), monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert body["available"] is True

        tail_calls = [c for c in calls if c["table"] == "audit_signals"]
        assert tail_calls, "the route must read the ledger"
        for call in tail_calls:
            where = str(call.get("where") or "")
            # Reverting the fix restores exactly this substring.
            assert "NO_TRADE" not in where.upper(), (
                "the NO_TRADE majority predicate is back inside the bounded "
                f"query — the LIMIT cannot bind (R-3). where={where!r}"
            )
            assert "action" not in where.lower(), (
                f"no action predicate may reach the planner (R-3). where={where!r}"
            )
        # The bare tail read must be the *oversampled* bound, not the bare
        # window: filtering after the read needs headroom to still fill
        # ``window`` NO_TRADE rows from a mixed tail.
        assert tail_calls[0]["limit"] == _NO_TRADE_WINDOW * _NO_TRADE_SCAN_FACTOR, tail_calls[0][
            "limit"
        ]
        assert tail_calls[0]["limit"] > _NO_TRADE_WINDOW

    def test_scanned_rows_never_exceeds_the_fetch_bound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        """``scanned_rows`` is the disclosed bound — it must be the REAL bound."""
        client = _client(tmp_path, _production_shaped_ledger(600, 60), monkeypatch)
        body = client.get("/api/operator/no-trade?limit=8").json()
        assert body["scanned_rows"] <= _NO_TRADE_WINDOW
        assert body["window"] == _NO_TRADE_WINDOW
        assert body["total"] == body["scanned_rows"]


# ---------------------------------------------------------------------------
# 2. Behavioural: the tail read is bounded even when the tail is not NO_TRADE
# ---------------------------------------------------------------------------


class TestTailReadIsBounded:
    """The discriminating fixture: LARGER than the fetch bound, and the newest
    rows are NOT the rows the filter wants.

    The pre-fix query ``WHERE action='NO_TRADE' ORDER BY id DESC LIMIT 2000``
    has no upper bound on how far back it reaches: it walks the ledger until
    it has collected 2,000 matching rows. With a deep NO_TRADE history behind
    a non-NO_TRADE tail it therefore reports a NON-ZERO total while examining
    an unbounded number of rows — precisely R-3. The bounded shape can only
    ever see its own slice, so the same ledger yields 0.
    """

    _HISTORY = 6_000  # deep NO_TRADE history, well past the fetch bound
    _TAIL = _NO_TRADE_WINDOW * _NO_TRADE_SCAN_FACTOR + 1_000  # newest rows

    @pytest.fixture
    def rows(self) -> list[tuple[Any, ...]]:
        # Insert order == id order: NO_TRADE history first, then a BUY tail
        # whose length exceeds ``window * factor``. So the newest
        # ``window * factor`` rows contain ZERO NO_TRADE rows.
        return [
            *_production_shaped_ledger(self._HISTORY, 0),
            *_production_shaped_ledger(0, self._TAIL),
        ]

    def test_tail_read_is_bounded_when_the_tail_is_not_no_trade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _auth_env: None,
        rows: list[tuple[Any, ...]],
    ) -> None:
        assert len(rows) > _NO_TRADE_WINDOW * _NO_TRADE_SCAN_FACTOR, (
            "fixture must be larger than the fetch bound or the pre-fix shape "
            "and the bounded shape are indistinguishable (false green)"
        )
        client = _client(tmp_path, rows, monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert body["available"] is True
        # Reverting the fix returns total=2000 here (the old shape walked back
        # through the whole BUY tail to fill its quota).
        assert body["total"] == 0, (
            "the newest window*factor rows contain no NO_TRADE row, so a "
            f"BOUNDED tail read must report 0 — got {body['total']}"
        )
        assert body["gates"] == []
        assert body["regimes"] == []
        assert body["reasons"] == []
        assert body["hourly_trend"] == []
        assert body["recent"] == []
        assert body["model_direction_unresolved"] == 0

    def test_a_no_trade_row_inside_the_slice_is_seen(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _auth_env: None,
        rows: list[tuple[Any, ...]],
    ) -> None:
        """One NO_TRADE row inside the slice is enough — the bound is honest."""
        rows = [*rows, *_production_shaped_ledger(1, 0)]
        client = _client(tmp_path, rows, monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert body["total"] == 1
        assert sum(g["count"] for g in body["gates"]) == 1


# ---------------------------------------------------------------------------
# 3. Guard: payload shape and every distribution are unchanged
# ---------------------------------------------------------------------------


class TestPayloadUnchanged:
    """Both the legacy ``Web/control_center.js`` console and the React
    ``NoTradeTab`` consume this payload; its key set is a contract."""

    _KEYS: ClassVar[set[str]] = {
        "available",
        "total",
        "window",
        "scanned_rows",
        "gates",
        "regimes",
        "reasons",
        "reasons_top_n",
        "hourly_trend",
        "model_direction_unresolved",
        "model_direction_unresolved_note",
        "recent",
    }

    def test_key_set_is_stable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        client = _client(tmp_path, _production_shaped_ledger(600, 60), monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert set(body) == self._KEYS, set(body) ^ self._KEYS

    def test_distributions_match_the_unbounded_reference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        """Every Counter equals an independent in-test computation over the
        newest ``window`` NO_TRADE rows — i.e. the pre-fix shape's answer.

        This is the guard that the bounded read did not change what the panel
        renders, on a ledger whose NO_TRADE share matches production.
        """
        import collections

        rows = _production_shaped_ledger(_NO_TRADE_WINDOW + 200, 100)
        client = _client(tmp_path, rows, monkeypatch)
        body = client.get("/api/operator/no-trade").json()

        # Reference: the SQL predicate the pre-fix code used, computed here
        # directly against the same seeded file so it cannot drift with the fix.
        db = tmp_path / "audit.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            ref = [
                dict(r)
                for r in con.execute(
                    "SELECT blocked_by, regime, reason_code, generated_at FROM audit_signals "
                    "WHERE UPPER(action) = 'NO_TRADE' ORDER BY id DESC LIMIT ?",
                    (_NO_TRADE_WINDOW,),
                ).fetchall()
            ]
        finally:
            con.close()
        assert len(ref) == _NO_TRADE_WINDOW
        assert body["total"] == len(ref)

        def as_pairs(items: list[dict[str, Any]], key: str) -> list[tuple[str, int]]:
            return [(str(i[key]), int(i["count"])) for i in items]

        assert (
            as_pairs(body["gates"], "gate")
            == collections.Counter((r["blocked_by"] or "NOT_BLOCKED") for r in ref).most_common()
        )
        assert (
            as_pairs(body["regimes"], "regime")
            == collections.Counter((r["regime"] or "NOT_RECORDED") for r in ref).most_common()
        )
        assert (
            as_pairs(body["reasons"], "reason")
            == collections.Counter((r["reason_code"] or "NOT_RECORDED") for r in ref).most_common()
        )
        buckets: collections.Counter[str] = collections.Counter()
        for r in ref:
            ts = r["generated_at"] or ""
            if len(ts) >= 13:
                buckets[ts[:13]] += 1
        assert (
            as_pairs(body["hourly_trend"], "hour")
            == sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)[:12]
        )
        # total reconciles against every distribution (the panel's own check).
        assert body["total"] == sum(g["count"] for g in body["gates"])
        assert body["total"] == sum(x["count"] for x in body["regimes"])
        assert body["total"] == sum(x["count"] for x in body["reasons"])

    def test_case_insensitive_action_filter_is_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        """The pre-fix predicate was ``UPPER(action) = 'NO_TRADE'``.

        A lower/mixed-case ``no_trade`` row was selected by the old SQL, so the
        Python filter must compare case-insensitively too.
        """
        rows = _production_shaped_ledger(300, 0)
        rows[10] = _row(
            10,
            action="no_trade",
            reason="SPREAD_GATE",
            gate="SPREAD_GATE",
            regime="RANGING_MEAN_REVERSION",
        )
        rows[11] = _row(
            11,
            action="No_Trade",
            reason="SPREAD_GATE",
            gate="SPREAD_GATE",
            regime="RANGING_MEAN_REVERSION",
        )
        client = _client(tmp_path, rows, monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert body["total"] == 300

    def test_hours_filter_still_applies_after_the_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        """The time filter stays a Python-side filter on the bounded rows.

        The pre-fix SQL had no time predicate either (the filter was applied in
        Python post-fetch, deliberately, so the window could never drag the
        planner into a seq scan). This pins that the fix kept it that way.
        """
        all_fresh = _production_shaped_ledger(400, 0)
        mixed = list(_production_shaped_ledger(400, 0))
        for i in range(200):
            mixed[i] = _row(
                i,
                action="NO_TRADE",
                reason="SPREAD_GATE",
                gate="SPREAD_GATE",
                regime="RANGING_MEAN_REVERSION",
                generated_at=_ANCIENT_TS,
            )

        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir()
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        fresh_db = fresh_dir / "audit.db"
        old_db = old_dir / "audit.db"
        _seed(fresh_db, all_fresh)
        _seed(old_db, mixed)
        app = create_app(engine_ref=None)
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {FAKE_WEB_AUTH_TOKEN}"})

        # Both clients read the SAME module-level path resolver, so the switch
        # has to happen between requests, not between client constructions.
        monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(fresh_db))
        f_full = c.get("/api/operator/no-trade").json()
        f_one = c.get("/api/operator/no-trade?hours=1").json()

        monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(old_db))
        m_full = c.get("/api/operator/no-trade").json()
        m_one = c.get("/api/operator/no-trade?hours=1").json()

        assert f_full["total"] == 400
        assert f_one["total"] == 400, "every fixture row is 1 minute old"
        assert m_full["total"] == 400
        assert m_one["total"] == 200, "200 of the 400 rows are from 2020"
        assert m_one["total"] <= m_full["total"]

    def test_hourly_trend_buckets_come_from_the_slice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_env: None
    ) -> None:
        """The trend is derived in memory — no second query, bounded buckets."""
        rows: list[tuple[Any, ...]] = []
        for hour in range(12):
            ts = (datetime.now(UTC) - timedelta(hours=hour)).isoformat()
            for k in range(3):
                rows.append(
                    _row(
                        hour * 10 + k,
                        action="NO_TRADE",
                        reason="SPREAD_GATE",
                        gate="SPREAD_GATE",
                        regime="RANGING_MEAN_REVERSION",
                        generated_at=ts,
                    )
                )
        client = _client(tmp_path, rows, monkeypatch)
        body = client.get("/api/operator/no-trade").json()
        assert body["total"] == len(rows)
        trend = body["hourly_trend"]
        assert len(trend) == 12, trend
        assert [h["count"] for h in trend] == [3] * 12
        # newest bucket first (the console reverses it back to chronological)
        assert [h["hour"] for h in trend] == sorted((h["hour"] for h in trend), reverse=True)
        assert all(len(h["hour"]) == 13 for h in trend)
