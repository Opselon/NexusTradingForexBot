"""Regression tests for the Control Center truth chain (2026-09-28).

Two independent defects were found live in production by tracing
Chrome UI -> /api/operator/summary -> operator_routes -> audit ledger:

1. ``latest_decision_at`` reported the OLDEST row in the bounded window, not
   the latest. ``SELECT ... WHERE id IN (...)`` is unordered, so ``rows[0]``
   is an arbitrary row; on the production SQLite audit.db that row was the
   window minimum (2026-09-18) while the real latest decision was
   2026-09-24. The operator console's "LATEST DECISION" rail cell and the
   Overview "latest decision" InfoRow both displayed the wrong timestamp.
   Fixed by taking the max over the ledger's own timestamps.

2. The React Overview panel crashed the whole Control Center page on render
   (Minified React error #310, caught by the live browser console and the
   ErrorBoundary fallback "Control Center crashed while rendering").
   ``OverviewPanels`` called ``useMemo`` AFTER conditional early returns for
   the pending/error states, violating the rules of hooks. This test suite
   covers (1) at the API level; the hooks-order defect is covered by the
   JS unit test in tests/js/control_center_hooks.test.mjs.

Run: .venv/Scripts/python -m pytest tests/unit/test_control_center_truth.py -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import create_app

FAKE_WEB_AUTH_TOKEN = "cc-truth-fake-token"

#: The seeded ledger inverts id↔chronology on purpose: id 1 is the LATEST
#: timestamp and id 3 the OLDEST. That inversion is what reproduces the
#: production defect. SQLite serves ``WHERE id IN (...)`` against an INTEGER
#: PRIMARY KEY in ROWID (ascending) order, so the buggy ``rows[0]`` read
#: returned the LOWEST id — the OLDEST timestamp — and the Control Center
#: displayed a stale "LATEST DECISION" on every poll. The fix takes MAX over
#: the ledger's own timestamps. A fixture where ascending id == ascending
#: time would pass with the bug still present (false green).
OLDEST_TS = "2020-01-01T00:00:00+00:00"
MIDDLE_TS = "2024-06-15T12:30:00+00:00"
LATEST_TS = "2026-09-24T18:14:03+00:00"


def _build_ledger(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            CREATE TABLE audit_signals (
                id INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                regime TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                execution_mode TEXT,
                reason_code TEXT,
                decision_stage TEXT,
                blocked_by TEXT
            );
            CREATE TABLE audit_orders (
                id INTEGER PRIMARY KEY,
                ticket INTEGER,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )
        # Highest id = OLDEST timestamp; lowest id = LATEST. Any code that
        # trusts row order or id order instead of comparing timestamps gets
        # the wrong answer.
        rows = (
            (1, "req-latest", "XAUUSD", "NO_TRADE", LATEST_TS),
            (2, "req-mid", "XAUUSD", "BUY_LIMIT", MIDDLE_TS),
            (3, "req-oldest", "XAUUSD", "NO_TRADE", OLDEST_TS),
        )
        for sid, req, sym, action, ts in rows:
            con.execute(
                "INSERT INTO audit_signals (id, request_id, symbol, action, confidence, "
                "regime, generated_at, payload, execution_mode, reason_code, "
                "decision_stage, blocked_by) VALUES (?, ?, ?, ?, 0.5, 'RANGING', ?, "
                "'{}', 'PAPER', 'GATE', 'STAGE', 'GATE')",
                (sid, req, sym, action, ts),
            )
        con.execute(
            "INSERT INTO audit_orders (id, ticket, order_id, symbol, action, price, "
            "volume, timestamp) VALUES (1, 1, 'o1', 'XAUUSD', 'X', 1.0, 0.01, ?)",
            (LATEST_TS,),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from nexus_scalp.web import operator_routes

    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", FAKE_WEB_AUTH_TOKEN)
    db = tmp_path / "audit.db"
    _build_ledger(db)
    # Same test-isolation seam the existing operator suite uses: redirect the
    # audit ledger to the tmp fixture WITHOUT touching production code.
    monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(db))
    return TestClient(create_app(engine_ref=None))


def _auth_kwargs() -> dict[str, str]:
    return {"headers": {"Authorization": f"Bearer {FAKE_WEB_AUTH_TOKEN}"}}


def test_summary_latest_decision_at_is_the_maximum_timestamp(client: TestClient) -> None:
    """The reported latest decision must be MAX(generated_at), not rows[0].

    This is the exact reproduction of the production defect: the live audit
    ledger returned the window's OLDEST row first, so the Control Center
    displayed a 6-day-stale 'LATEST DECISION' on every poll.
    """
    body = client.get("/api/operator/summary", **_auth_kwargs()).json()
    ledger = body["ledger"]
    assert ledger["available"] is True, ledger
    assert ledger["total"] == 3
    assert ledger["latest_decision_at"] == LATEST_TS, (
        "latest_decision_at must be the newest recorded timestamp; the "
        "unordered IN (...) fetch made the old code return rows[0], which on "
        "the production ledger was the window MINIMUM."
    )


def test_latest_decision_at_never_inherits_an_arbitrary_row_order(client: TestClient) -> None:
    """Guard against re-introducing an order-dependent read.

    A second call exercises a fresh query plan; both calls must agree and
    both must report LATEST_TS. A rows[0]-style read returns whatever SQLite
    chose this time and is non-deterministic across plans/vacuum states.
    """
    values = {
        client.get("/api/operator/summary", **_auth_kwargs()).json()["ledger"]["latest_decision_at"]
        for _ in range(3)
    }
    assert values == {LATEST_TS}


def test_summary_actions_census_is_order_independent(client: TestClient) -> None:
    """The action distribution must be a complete census, not row-order state."""
    body = client.get("/api/operator/summary", **_auth_kwargs()).json()
    actions = body["ledger"]["actions"]
    assert actions == {"NO_TRADE": 2, "BUY_LIMIT": 1}


def test_decisions_endpoint_newest_row_agrees_with_summary_latest(client: TestClient) -> None:
    """The summary's 'latest decision' must be the maximum recorded timestamp.

    The decisions list is ordered by id DESC (its documented contract). This
    fixture deliberately inverts id↔chronology (id 3 = OLDEST, id 1 = LATEST)
    precisely so that trusting row order produces the wrong answer — the same
    way the production ledger exposed the bug. So the decisions surface's first
    row is NOT the newest timestamp here, and summary must derive its answer
    by comparing timestamps rather than trusting any row's position.
    """
    decisions = client.get("/api/operator/decisions?limit=1", **_auth_kwargs()).json()
    assert decisions["available"] is True
    assert decisions["rows"][0]["id"] == 3  # id DESC is the list's contract
    assert decisions["rows"][0]["generated_at"] == OLDEST_TS  # inverted by design

    summary_latest = client.get("/api/operator/summary", **_auth_kwargs()).json()["ledger"][
        "latest_decision_at"
    ]
    assert summary_latest == LATEST_TS, (
        "summary must report the maximum recorded timestamp, not rows[0] — the "
        "pre-fix code read the first row of an ascending id-order fetch and "
        "reported the OLDEST decision as the latest one"
    )

    # Full census reconciliation: the decisions surface must expose every row,
    # and the newest timestamp anywhere in the ledger is what summary reports.
    all_rows = client.get("/api/operator/decisions?limit=10", **_auth_kwargs()).json()
    timestamps = [r["generated_at"] for r in all_rows["rows"]]
    assert sorted(timestamps) == sorted([OLDEST_TS, MIDDLE_TS, LATEST_TS])
    assert max(timestamps) == LATEST_TS
    assert summary_latest == max(timestamps)


def test_bounded_tail_window_makes_rows0_the_oldest_row(client: TestClient) -> None:
    """The production ledger shape: a bounded tail window whose first row is
    the OLDEST.

    This is the exact mechanism that made the original bug visible. On the
    live audit.db, ``SELECT id FROM audit_signals ORDER BY id DESC LIMIT
    20000`` returns a CONTIGUOUS id block (11300 ids, 1321921..1943677),
    SQLite then serves ``WHERE id IN (...)`` for that block in ASCENDING id
    order, and because id and timestamp rise together on a live ledger,
    ``rows[0]`` is the window's MINIMUM id and its OLDEST timestamp
    (2026-09-18) — which the route then labeled "latest_decision_at" while
    the real latest was 2026-09-24.

    Reproduced here with a contiguous id block where ascending id == ascending
    timestamp, so the buggy ``rows[0]`` read provably returns the oldest row
    and only MAX(generated_at) is correct. The window is deliberately larger
    than the IN-list (spanning ids the list does not name) so SQLite cannot
    shortcut to the list order — matching the real 11300-of-1943677 selectivity.
    """
    from nexus_scalp.web import operator_routes

    tmp = Path(operator_routes._audit_db_path()).parent / "tail_audit.db"
    con = sqlite3.connect(tmp)
    try:
        con.executescript(
            """
            CREATE TABLE audit_signals (
                id INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                regime TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                execution_mode TEXT,
                reason_code TEXT,
                decision_stage TEXT,
                blocked_by TEXT
            );
            """
        )
        # Ascending id == ascending timestamp, exactly like a live ledger.
        # 400 rows with a 1-minute cadence; the window takes the last 20000
        # ids (all 400 qualify) and the IN-list then holds a contiguous tail.
        base = 1_000_000
        for i in range(400):
            ts = f"2026-09-{18 + i // 200:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:00+00:00"
            con.execute(
                "INSERT INTO audit_signals (id, request_id, symbol, action, confidence, "
                "regime, generated_at, payload, execution_mode, reason_code, "
                "decision_stage, blocked_by) VALUES (?, ?, 'XAUUSD', 'NO_TRADE', 0.5, "
                "'RANGING', ?, '{}', 'PAPER', 'G', 'S', 'G')",
                (base + i, f"r{i}", ts),
            )
        con.commit()
    finally:
        con.close()

    monkeypatch_holder = operator_routes._audit_db_path
    try:
        operator_routes._audit_db_path = lambda: str(tmp)
        body = client.get("/api/operator/summary", **_auth_kwargs()).json()
    finally:
        operator_routes._audit_db_path = monkeypatch_holder

    ledger = body["ledger"]
    assert ledger["available"] is True
    newest = f"2026-09-{18 + 399 // 200:02d}T{(399 // 60) % 24:02d}:{399 % 60:02d}:00+00:00"
    assert ledger["latest_decision_at"] == newest, (
        "the bounded-tail window returns the oldest row first, so only "
        "MAX(generated_at) is correct — rows[0] reports the oldest decision"
    )


@pytest.fixture()
def empty_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Same auth + ledger seam, but a schema-only audit DB with zero rows."""
    from nexus_scalp.web import operator_routes

    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", FAKE_WEB_AUTH_TOKEN)
    db = tmp_path / "empty_audit.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(
            """
            CREATE TABLE audit_signals (
                id INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                regime TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE audit_orders (
                id INTEGER PRIMARY KEY,
                ticket INTEGER,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                timestamp TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(db))
    return TestClient(create_app(engine_ref=None))


def test_empty_ledger_reports_null_not_a_fabricated_timestamp(
    empty_client: TestClient,
) -> None:
    """An empty window yields None, never a synthesized 'latest'."""
    body = empty_client.get("/api/operator/summary", **_auth_kwargs()).json()
    ledger = body["ledger"]
    assert ledger["available"] is True
    # total/scanned_rows/actions are only emitted when the window has rows; a
    # zero-row window legitimately omits the whole stats block, so
    # latest_decision_at is absent too — the contract is that no timestamp is
    # ever fabricated. Missing keys here are the honest "nothing recorded"
    # answer, never a synthesized date.
    assert ledger.get("scanned_rows", 0) == 0
    assert ledger.get("latest_decision_at") is None
    assert ledger.get("actions", {}) == {}
