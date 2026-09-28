"""P1 — audit_signals scan-amplification regression tests (TASK-P1-SCAN-AMP).

The forensic evidence (docs/forensic-docs/postgresql-forensic-audit-2026-09-28.md):
  audit_signals held ~9,108 live rows; pg_stat_user_tables reported
  533,577 sequential scans reading 4,927,831,046 tuples over 18.11 h
  (8.2 scans/sec, ~9,235 tuples/scan = the whole table per scan).

The defect was NOT the table size and NOT missing indexes. It was four
query shapes whose predicates selected ~100% of the table, so the planner
correctly chose a seq scan every time:

  Q1 ``WHERE generated_at >= ?`` + GROUP BY     — a 7-day window on a
    7-day-retention table selects ~100% of rows. The index exists and is
    unused because there is no selectivity to exploit.
  Q2 ``WHERE id IN (SELECT id ... LIMIT 20000)`` — the "bound" is LARGER
    than the table, so the window covers everything and the semi-join
    degrades to a full scan.
  Q3 ``WHERE action = 'NO_TRADE'``               — NO_TRADE is ~92% of rows;
    a majority predicate is unindexable by definition.
  Q4 ``WHERE ticket=? OR payload LIKE '%..%'``   — leading-wildcard LIKE is
    non-sargable, plus ``ticket`` is not a column on audit_signals at all.

The fix bounds each query at the SOURCE (a bounded ``ORDER BY id DESC
LIMIT n`` tail read is index-served and cannot grow into a scan) and adds a
high-water-mark memo so the SSE loop stops re-reading an unchanged ledger.

These tests pin the fix. Reverting any source change must make a test in
this file FAIL — a suite that passes with and without the fix pins nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ledger_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
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
            signal_dedup_key TEXT UNIQUE,
            preferred_direction TEXT,
            raw_prob_buy REAL,
            raw_prob_sell REAL,
            raw_prob_no_trade REAL,
            confidence_source TEXT,
            spread_usd REAL
        );
        CREATE INDEX idx_audit_signals_generated ON audit_signals (generated_at DESC);
        """
    )


def _seed(
    con: sqlite3.Connection,
    n_rows: int,
    *,
    no_trade_frac: float = 0.92,
    rows_per_minute: float = 1.0,
) -> dict[str, int]:
    """Seed the ledger with the production shape: id rises with time, NO_TRADE
    is the overwhelming majority, and the window is ~7 days."""
    import json
    import random

    rng = random.Random(20260928)
    base = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    counts = {"NO_TRADE": 0, "TRADE": 0}
    for i in range(1, n_rows + 1):
        ts = base - timedelta(minutes=(n_rows - i) / rows_per_minute)
        is_nt = rng.random() < no_trade_frac
        action = "NO_TRADE" if is_nt else "BUY_MARKET"
        counts["NO_TRADE" if is_nt else "TRADE"] += 1
        payload = json.dumps(
            {
                "model_action": action,
                "ai_buy_probability": 0.1,
                "ai_sell_probability": 0.2,
                "ai_no_trade_probability": 0.7,
                "execution_id": f"exec-{i}",
            }
        )
        con.execute(
            "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
            "proposed_entry, stop_loss, take_profit, regime, generated_at, payload, "
            "execution_mode, reason_code, decision_stage, blocked_by, signal_dedup_key, "
            "preferred_direction, raw_prob_buy, raw_prob_sell, raw_prob_no_trade, "
            "confidence_source, spread_usd) VALUES "
            "(?, 'XAUUSD', ?, 0.5, 2650.0, 2645.0, 2660.0, 'RANGING', ?, ?, "
            "'STANDARD', ?, ?, ?, ?, ?, 0.1, 0.2, 0.7, 'RAW_MODEL', 0.3)",
            (
                f"req-{i:07d}",
                action,
                ts.isoformat(),
                payload,
                "MODEL_SIGNAL" if not is_nt else "CONFIDENCE_GATE",
                "FINAL_DECISION" if not is_nt else "CONFIDENCE_GATE",
                None if not is_nt else "CONFIDENCE_GATE",
                f"ddk-{i:07d}",
                "" if is_nt else "BUY",
            ),
        )
    con.commit()
    return counts


@pytest.fixture()
def ledger_db(tmp_path: Path) -> Path:
    db = tmp_path / "audit_p1.db"
    with sqlite3.connect(db) as con:
        _ledger_schema(con)
        _seed(con, 600)
    return db


@pytest.fixture()
def repo(ledger_db: Path) -> Any:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    r = AuditRepository(db_url=f"sqlite:///{ledger_db}")
    return r


# ---------------------------------------------------------------------------
# Q2 — the vacuous LIMIT bound (operator summary / funnel)
# ---------------------------------------------------------------------------


class TestVacuousLimitBoundEliminated:
    """The ``WHERE id IN (LIMIT 20000)`` semi-join is gone."""

    def test_summary_window_is_smaller_than_a_real_ledger(self) -> None:
        """The bound must be materially smaller than a production ledger.

        On the live 9,108-row table the pre-fix 20000-row window covered the
        ENTIRE table, so the "bound" was vacuous and the semi-join degraded
        to a full scan. This test fails the moment the window is raised back
        above a realistic ledger size.
        """
        from nexus_scalp.web import operator_routes

        assert operator_routes._SUMMARY_WINDOW <= 2000, (
            "the summary window must stay materially below a production "
            "ledger (~9108 rows) or the bound becomes vacuous again"
        )
        assert operator_routes._FUNNEL_WINDOW <= 2000
        assert operator_routes._NO_TRADE_WINDOW <= 2000
        # 7 days at ~1 decision/minute = 10080 rows; the windows must not
        # cover that.
        seven_days = 7 * 24 * 60
        assert operator_routes._SUMMARY_WINDOW < seven_days
        assert operator_routes._FUNNEL_WINDOW < seven_days
        assert operator_routes._NO_TRADE_WINDOW < seven_days

    def test_summary_census_is_one_bounded_tail_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The summary reads the tail once — no id list, no IN semi-join."""
        from nexus_scalp.web import operator_routes

        statements: list[str] = []
        ledger_db_for(monkeypatch, operator_routes)

        class SpyCon(sqlite3.Connection):
            def execute(self, sql: str, *args, **kwargs):  # type: ignore[override]
                statements.append(sql)
                return super().execute(sql, *args, **kwargs)

        orig = operator_routes._connect_ro

        def spy() -> sqlite3.Connection | None:
            path = operator_routes._audit_db_path()
            con = SpyCon(f"file:{path}?mode=ro", uri=True, timeout=5.0)
            con.row_factory = sqlite3.Row
            return con

        monkeypatch.setattr(operator_routes, "_connect_ro", spy)
        _ = orig  # keep the original reference honest
        # exercise via a minimal app shell
        from fastapi import FastAPI

        from nexus_scalp.web.auth import require_web_auth

        app = FastAPI()

        def _err(code: str, **kw):  # type: ignore[no-untyped-def]
            return {"available": False, "error": {"code": code}}

        app.state.__dict__.clear()
        operator_routes.register_operator_routes(
            app,
            lambda: {},
            _err,
            lambda *a, **k: None,
            lambda x: x,
        )
        # bypass auth for the test
        app.dependency_overrides[require_web_auth] = lambda: None
        from starlette.testclient import TestClient

        body = TestClient(app).get("/api/operator/summary").json()
        audit_sql = [s for s in statements if "audit_signals" in s and "SELECT" in s.upper()]
        assert audit_sql, "the summary must read the ledger"
        assert len(audit_sql) == 1, f"one bounded read, got: {audit_sql}"
        sql = audit_sql[0]
        assert "ORDER BY id DESC" in sql, sql
        assert "LIMIT ?" in sql, sql
        # The semi-join shape is what caused the scan; it must be gone.
        assert "IN (" not in sql.upper().replace("IN (SELECT", "IN(SELECT"), sql
        assert body["ledger"]["available"] is True
        assert body["ledger"]["scanned_rows"] <= operator_routes._SUMMARY_WINDOW


def ledger_db_for(monkeypatch: pytest.MonkeyPatch, operator_routes: Any) -> Path:
    """Point the operator routes at a temp ledger and return its path."""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "audit_p1.db"
    with sqlite3.connect(tmp) as con:
        _ledger_schema(con)
        _seed(con, 600)
    monkeypatch.setattr(operator_routes, "_audit_db_path", lambda: str(tmp))
    return tmp


# ---------------------------------------------------------------------------
# Q1 — the bounded decision distribution
# ---------------------------------------------------------------------------


class TestBoundedDecisionStats:
    def test_stats_come_from_the_repository_helper(self, repo: Any) -> None:
        """/decisions/stats now reads a bounded slice, not a full GROUP BY."""
        stats = repo.get_decision_stats(hours_back=168.0)
        assert stats["total"] > 0
        assert "NO_TRADE" in stats["by_action"]
        assert stats["sampled_rows"] == 2000  # the cap
        # 600 seeded rows < the 2000-row slice -> the slice covered the whole
        # window, so the distribution IS exhaustive and says so.
        assert stats["exhaustive"] is True

    def test_stats_grouped_by_reason_when_asked(self, repo: Any) -> None:
        by_stage = repo.get_decision_stats()
        by_reason = repo.get_decision_stats(group_by_reason=True)
        assert by_stage["group_key"] == "decision_stage"
        assert by_reason["group_key"] == "reason_code"
        # the same underlying rows, so the totals agree
        assert by_stage["total"] == by_reason["total"]
        assert by_stage["total"] > 0
        # NOT_RECORDED must never appear for the seeded data (every row has a
        # reason_code) — the empty-string fallback is the honest marker.
        assert "NOT_RECORDED" not in by_reason["by_group"]

    def test_a_caller_cannot_restore_the_full_scan(self, repo: Any) -> None:
        """The sample cap is enforced, not advisory."""
        stats = repo.get_decision_stats(sample_rows=10**9)
        assert stats["sampled_rows"] == 2000

    def test_the_distribution_is_representative(self, repo: Any) -> None:
        """The bounded slice preserves the NO_TRADE majority ratio."""
        stats = repo.get_decision_stats()
        total = stats["total"]
        nt = stats["by_action"].get("NO_TRADE", 0)
        # seeded at 92%; allow slack but require the majority to be preserved
        assert nt / total > 0.85, (nt, total)

    def test_count_decisions_bounds_the_majority_predicate(self, repo: Any) -> None:
        """COUNT(WHERE action='NO_TRADE') no longer scans the table."""
        n = repo.count_decisions(action="NO_TRADE")
        assert n > 0
        # a bounded slice cannot return more than the cap
        assert n <= 2000
        # the unfiltered count is bounded too (the cap, not the table size)
        assert repo.count_decisions() <= 2000

    def test_count_respects_the_time_window(self, repo: Any) -> None:
        full = repo.count_decisions(action="NO_TRADE")
        recent = repo.count_decisions(action="NO_TRADE", hours_back=1.0)
        assert recent <= full
        assert recent >= 0


# ---------------------------------------------------------------------------
# The high-water-mark memo (the SSE hot path)
# ---------------------------------------------------------------------------


class TestHighWaterMarkMemo:
    def test_high_water_mark_is_the_max_id(self, repo: Any) -> None:
        hw = repo.ledger_high_water_mark()
        assert hw is not None
        with sqlite3.connect(ledger_of(repo)) as con:
            assert con.execute("SELECT MAX(id) FROM audit_signals").fetchone()[0] == hw

    def test_high_water_mark_is_none_on_an_empty_ledger(self, tmp_path: Path) -> None:
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        db = tmp_path / "empty.db"
        with sqlite3.connect(db) as con:
            _ledger_schema(con)
        r = AuditRepository(db_url=f"sqlite:///{db}")
        assert r.ledger_high_water_mark() is None

    def test_memo_skips_the_tail_read_when_the_ledger_did_not_move(
        self, repo: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SSE path must not re-read the tail on every 200ms cycle."""
        calls = {"n": 0}
        orig = repo.get_recent_predictions

        def counted(limit: int = 50):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return orig(limit)

        monkeypatch.setattr(repo, "get_recent_predictions", counted)
        from types import SimpleNamespace

        app = SimpleNamespace(state=SimpleNamespace())
        from nexus_scalp.web.server import _recent_predictions_cached

        # First call: no memo -> reads the tail + the high-water mark.
        first = _recent_predictions_cached(app, SimpleNamespace(audit=repo))
        tail_reads_after_first = calls["n"]
        assert tail_reads_after_first == 1
        # The memo is now seeded with the ledger's high-water mark.
        # Subsequent calls with an unchanged ledger must NOT read the tail.
        for _ in range(10):
            again = _recent_predictions_cached(app, SimpleNamespace(audit=repo))
            assert again == first
        assert calls["n"] == tail_reads_after_first, (
            "the tail read must be skipped while the ledger's high-water mark "
            "is unchanged — that is the whole P1 fix for the SSE hot path"
        )

    def test_memo_refreshes_when_a_new_row_lands(
        self, repo: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from nexus_scalp.web.server import _recent_predictions_cached

        app = SimpleNamespace(state=SimpleNamespace())
        before = _recent_predictions_cached(app, SimpleNamespace(audit=repo))
        n_before = len(before)
        oldest_before = before[-1]["request_id"]
        # Append one new decision row, bumping the high-water mark.
        _append_row(ledger_of(repo))
        after = _recent_predictions_cached(app, SimpleNamespace(audit=repo))
        # The window is a fixed newest-N, so the length does not grow —
        # what must change is the CONTENT: the new row enters and the
        # oldest row falls out of the window.
        assert len(after) == n_before
        assert after[0]["request_id"] == "req-new-0001", "the memo must refresh on a new row"
        assert after[-1]["request_id"] != oldest_before, (
            "the refreshed window must not be a stale copy"
        )

    def test_memo_refresh_is_observed_by_the_tail_read_counter(
        self, repo: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new row must actually trigger a fresh tail read (the P1
        invariant: the tail read happens ~once per new row, not once per
        poll)."""
        from types import SimpleNamespace

        from nexus_scalp.web.server import _recent_predictions_cached

        calls = {"n": 0}
        orig = repo.get_recent_predictions

        def counted(limit: int = 50):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return orig(limit)

        monkeypatch.setattr(repo, "get_recent_predictions", counted)
        app = SimpleNamespace(state=SimpleNamespace())
        engine = SimpleNamespace(audit=repo)
        for _ in range(5):
            _recent_predictions_cached(app, engine)
        assert calls["n"] == 1, "unchanged ledger -> one tail read"
        _append_row(ledger_of(repo))
        for _ in range(5):
            _recent_predictions_cached(app, engine)
        assert calls["n"] == 2, "one new row -> exactly one more tail read"

    def test_memo_cannot_serve_a_stale_tail(self, repo: Any) -> None:
        """id is monotonic and the ledger is append-only, so the memo key
        cannot regress — verify the invariant the memo relies on."""
        hw1 = repo.ledger_high_water_mark()
        _append_row(ledger_of(repo))
        hw2 = repo.ledger_high_water_mark()
        assert hw2 is not None and hw1 is not None and hw2 > hw1


def ledger_of(repo: Any) -> str:
    return str(repo._db_path).replace("file:", "")


def _append_row(db_path: str) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO audit_signals (request_id, symbol, action, confidence, "
            "proposed_entry, stop_loss, take_profit, regime, generated_at, payload, "
            "execution_mode, reason_code, decision_stage, blocked_by, signal_dedup_key) "
            "VALUES ('req-new-0001', 'XAUUSD', 'BUY_MARKET', 0.9, 2650.0, 2645.0, 2660.0, "
            "'TRENDING', ?, '{}', 'STANDARD', 'MODEL_SIGNAL', 'FINAL_DECISION', NULL, "
            "'ddk-new-0001')",
            ((datetime.now(UTC)).isoformat(),),
        )
        con.commit()


# ---------------------------------------------------------------------------
# Q4 — the non-sargable payload LIKE
# ---------------------------------------------------------------------------


class TestPayloadLikeIsLastResort:
    def test_why_blocked_prefers_the_sargable_key(self, ledger_db: Path) -> None:
        from nexus_scalp.incidents.trace import why_blocked

        out = why_blocked(str(ledger_db), "req-0000001")
        # the request_id equality path finds the row
        assert out["signal_rows"] >= 1

    def test_why_blocked_falls_back_to_substring_for_legacy_rows(self, ledger_db: Path) -> None:
        from nexus_scalp.incidents.trace import why_blocked

        # No request_id/execution_id matches this needle, so the substring
        # fallback runs (the seeded payload embeds "exec-1").
        out = why_blocked(str(ledger_db), "exec-1")
        assert isinstance(out, dict)
        assert out["signal_rows"] >= 1

    def test_the_substring_fallback_is_bounded(self, ledger_db: Path) -> None:
        """The LIKE fallback reads at most 20 rows, not the whole table."""
        from nexus_scalp.incidents import trace

        calls: list[str] = []
        orig = trace._safe_rows

        def spy(conn, sql, args=()):  # type: ignore[no-untyped-def]
            calls.append(sql)
            return orig(conn, sql, args)

        trace._safe_rows = spy
        try:
            trace.why_blocked(str(ledger_db), "exec-1")
        finally:
            trace._safe_rows = orig
        # every executed statement is bounded
        assert all("LIMIT 20" in s for s in calls if "audit_signals" in s), calls
        # the substring path is the last one executed, never the first
        assert any("payload LIKE" in s for s in calls)
        assert "payload LIKE" not in calls[0], (
            "a sargable equality must be tried before the substring scan"
        )

    def test_the_or_shape_is_gone(self) -> None:
        """The ``WHERE ticket=? OR payload LIKE ?`` shape is non-sargable by
        construction and must no longer exist."""
        from nexus_scalp.incidents import trace

        src = Path(trace.__file__).read_text(encoding="utf-8")
        # Strip comments/docstrings: the fix's own rationale mentions the old
        # shape, which is not a regression.
        import re

        code_only = re.sub(r'""".*?"""', '""', src, flags=re.S)
        code_only = re.sub(r"#.*", "", code_only)
        code_only = re.sub(r"^\s*:.*$", "", code_only, flags=re.M)
        assert "OR payload LIKE" not in code_only, (
            "the OR of a non-column equality and a leading-wildcard LIKE is "
            "the exact non-sargable shape P1 removes"
        )
        # and the fallback remains bounded + explicitly last-resort
        assert "payload LIKE ?" in code_only
        assert "LIMIT 20" in code_only
        lookups = trace._SARGABLE_SIGNAL_LOOKUPS
        assert len(lookups) == 1
        assert lookups[0].startswith("SELECT * FROM audit_signals WHERE request_id = ?")
        # the equality path must be tried BEFORE the substring path
        assert "payload LIKE" not in lookups[0]


# ---------------------------------------------------------------------------
# Query-shape contract: no hot query may be unbounded
# ---------------------------------------------------------------------------


class TestNoHotQueryIsUnbounded:
    def test_repository_stats_sql_reads_a_bounded_tail(self, repo: Any) -> None:
        import nexus_scalp.adapters.database.audit_repository as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        # The two helpers must exist...
        for name in ("get_decision_stats", "count_decisions"):
            assert f"def {name}(" in src, name
        # ...and neither may carry an unbounded whole-table GROUP BY/COUNT.
        # ``ORDER BY id DESC LIMIT ?`` is the bounded shape that keeps the
        # planner on the primary-key index; if either helper loses it, a
        # 7-day GROUP BY silently becomes a whole-table scan again.
        import re

        code_only = re.sub(r'""".*?"""', '""', src, flags=re.S)
        code_only = re.sub(r"#.*", "", code_only)
        for name in ("get_decision_stats", "count_decisions"):
            start = code_only.find(f"def {name}(")
            assert start != -1, name
            nxt = code_only.find("\n    def ", start + 10)
            body = code_only[start:nxt]
            assert "ORDER BY id DESC" in body, f"{name} lost its bounded tail"
            assert "LIMIT ?" in body, f"{name} lost its row cap"

    def test_operator_routes_have_no_id_in_semi_join(self) -> None:
        from nexus_scalp.web import operator_routes

        src = Path(operator_routes.__file__).read_text(encoding="utf-8")
        # The fix's own docstrings reference the vacuous old shape; strip
        # comments/docstrings so the assertion targets real code only.
        import re

        code_only = re.sub(r'""".*?"""', '""', src, flags=re.S)
        code_only = re.sub(r"#.*", "", code_only)
        assert "WHERE id IN" not in code_only, "the vacuous id-IN semi-join must not come back"
        # and the bounded tail shape is present in its place
        assert "ORDER BY id DESC LIMIT ?" in code_only
