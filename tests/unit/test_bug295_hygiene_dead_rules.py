"""
BUG-295 — Hygiene dead-rule regression net (lane-04 DBF-004).

Production forensics (docs/audit/wave_20260914/04_db_forensics.md §3.1/§4/§7):
417 hygiene cycles ran with deleted=0/archived=0/bytes_freed=0 in EVERY row
because four SAFE_RETENTION_DELETES rules named columns that do not exist in
the production DDL and one filtered on an event_type the ledger never stores —
and the planner skipped all of it silently (`ts_col not in col_names:
continue`).

Pins here build EXACT production-shaped DDL (columns copied from
adapters/database/audit_repository.py + news/db_schema.py — NOT the old fake
`research_worker_state(id, updated_at, payload)` fixture shape) and prove:
  * the healed rules FIND candidates (RED-before: silently skipped);
  * a rule naming an absent column is now LOUD (blocked entry + warning);
  * news_health ts_cols semantics: both-stale = candidate, either-fresh =
    survives, no-timestamp-evidence (NULL and '' shapes) = survives;
  * position_lifecycle_events sweeps the real emitted vocabulary while the
    legacy POSITION_MOVING key still matches, and CREATED/OPENED/EXITED rows
    never do;
  * news_analysis_runs (TEXT run_id PK) plans + deletes end-to-end;
  * single-key path (audit_signals / candles) is behaviorally unchanged;
  * end-to-end SAFE_CLEAN apply_deletes through DatabaseHygieneWorker.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.hygiene import WorkerMode
from nexus_scalp.hygiene.worker import (
    SAFE_RETENTION_DELETES,
    HygienePlanner,
    HygieneScanner,
    _retention_where,
)
from nexus_scalp.hygiene.worker_runner import DatabaseHygieneWorker

NOW = datetime.now(UTC)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# production-shaped DDL (column names copied verbatim from the real creators)
# ---------------------------------------------------------------------------


def _mk_audit_db(path: Path) -> None:
    """audit.db subset with the REAL audit_repository.py shapes."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        -- DuplicateDetector.scan_audit hard-requires these two (they exist in
        -- every production audit.db); minimal shapes, no test rows.
        CREATE TABLE audit_ledger (
            ticket INTEGER PRIMARY KEY, order_id TEXT DEFAULT '',
            status TEXT, pnl REAL DEFAULT 0.0
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT UNIQUE NOT NULL,
            execution_id TEXT, realized_pnl_usd REAL DEFAULT 0.0, payload TEXT
        );
        -- audit_repository._create_table_research_worker_state /
        -- _create_table_intelligence_worker_state (scope TEXT PRIMARY KEY,
        -- age column last_cycle_at; NO updated_at, NO id).
        CREATE TABLE research_worker_state (
            scope TEXT PRIMARY KEY,
            last_checkpoint TEXT DEFAULT '',
            last_cycle_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            cycle_count INTEGER DEFAULT 0
        );
        CREATE TABLE intelligence_worker_state (
            scope TEXT PRIMARY KEY,
            last_checkpoint TEXT DEFAULT '',
            last_cycle_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            cycle_count INTEGER DEFAULT 0
        );
        -- audit_repository._create_table_position_lifecycle_events (id PK +
        -- event_type/event_timestamp; vocabulary from intelligence/models.py).
        CREATE TABLE position_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE NOT NULL,
            ticket TEXT NOT NULL,
            trade_id TEXT DEFAULT '',
            experience_id TEXT DEFAULT '',
            symbol TEXT NOT NULL,
            timeframe TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            sequence INTEGER DEFAULT 0,
            event_timestamp TEXT NOT NULL,
            market_context TEXT DEFAULT '{}',
            position_snapshot TEXT DEFAULT '{}',
            payload TEXT DEFAULT '{}'
        );
        CREATE TABLE audit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT, payload TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def _mk_news_db(path: Path) -> None:
    """news.db subset with the REAL news/db_schema.py shapes (news_health has
    source_id PK + last_success_at/last_failure_at; news_worker_state has
    scope PK + last_cycle_at; news_analysis_runs has run_id TEXT PK +
    started_at)."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE news_health (
            source_id TEXT PRIMARY KEY,
            last_success_at TEXT DEFAULT '',
            last_failure_at TEXT DEFAULT '',
            last_status INTEGER,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            rate_limited INTEGER NOT NULL DEFAULT 0,
            retry_after_sec REAL NOT NULL DEFAULT 0.0,
            backoff_until TEXT DEFAULT '',
            healthy INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE news_worker_state (
            scope TEXT PRIMARY KEY,
            cycle_count INTEGER NOT NULL DEFAULT 0,
            last_cycle_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            last_checkpoint TEXT DEFAULT ''
        );
        CREATE TABLE news_analysis_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'QUEUED',
            article_ids TEXT DEFAULT '[]',
            provider TEXT DEFAULT '',
            error TEXT DEFAULT ''
        );
        """
    )
    conn.commit()
    conn.close()


def _plan(db_key: str, db_path: Path, mode: WorkerMode = WorkerMode.SAFE_CLEAN) -> Any:
    conn = sqlite3.connect(str(db_path))
    try:
        return HygienePlanner(mode=mode).build_plan(db_key, conn, HygieneScanner())
    finally:
        conn.close()


def _retention_row(plan: Any, table: str) -> dict[str, Any] | None:
    for cand in plan.retention_candidates:
        if cand["table"] == table:
            return cand
    return None


# ---------------------------------------------------------------------------
# 1) healed worker-state rules FIND candidates (RED-before: silent skip)
# ---------------------------------------------------------------------------


def test_worker_state_rules_find_stale_rows(tmp_path: Path) -> None:
    """RED-BEFORE pin: with the old ts_col="updated_at"/"created_at" the
    planner silently skipped these tables (0 candidates, 0 blocked). With the
    healed last_cycle_at rules, stale checkpoint rows become candidates and
    fresh ones never do."""
    audit = tmp_path / "audit.db"
    _mk_audit_db(audit)
    conn = sqlite3.connect(str(audit))
    conn.execute(
        "INSERT INTO research_worker_state (scope, last_cycle_at) VALUES ('research', ?)",
        (_iso(60),),
    )
    conn.execute(
        "INSERT INTO intelligence_worker_state (scope, last_cycle_at) VALUES ('intelligence', ?)",
        (_iso(45),),
    )
    conn.commit()
    conn.close()

    plan = _plan("audit", audit)
    rc = _retention_row(plan, "research_worker_state")
    ic = _retention_row(plan, "intelligence_worker_state")
    assert rc is not None and rc["candidate_rows"] == 1
    assert ic is not None and ic["candidate_rows"] == 1
    assert rc["ts_col"] == "last_cycle_at"

    news = tmp_path / "news.db"
    _mk_news_db(news)
    conn = sqlite3.connect(str(news))
    conn.execute(
        "INSERT INTO news_worker_state (scope, last_cycle_at) VALUES ('news', ?)", (_iso(31),)
    )
    conn.commit()
    conn.close()
    plan = _plan("news", news)
    nc = _retention_row(plan, "news_worker_state")
    assert nc is not None and nc["candidate_rows"] == 1


def test_worker_state_fresh_and_empty_never_candidates(tmp_path: Path) -> None:
    """A checkpoint active this week (fresh last_cycle_at) or one carrying a
    NULL age must NOT become a candidate. A '' age (worker never cycled)
    DOES age out on the single-key path — SQLite text order puts '' before
    any cutoff; that is the pre-existing bit-identical single-ts_col
    semantic and it is safe here: scope-PK upsert checkpoints are recreated
    by the producer's ON CONFLICT write on the next cycle (TIER_7)."""
    audit = tmp_path / "audit.db"
    _mk_audit_db(audit)
    conn = sqlite3.connect(str(audit))
    conn.execute(
        "INSERT INTO research_worker_state (scope, last_cycle_at) VALUES ('research', ?)",
        (_iso(2),),
    )
    conn.execute(
        "INSERT INTO intelligence_worker_state (scope, last_cycle_at) VALUES ('null', NULL)"
    )
    conn.commit()
    conn.close()
    plan = _plan("audit", audit)
    assert _retention_row(plan, "research_worker_state") is None
    assert _retention_row(plan, "intelligence_worker_state") is None


# ---------------------------------------------------------------------------
# 2) loud skip: a dead rule may never be silently inert again (DBF-004 root)
# ---------------------------------------------------------------------------


def test_dead_rule_is_loud_blocked_plus_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Inject a bogus ts_col through a monkeypatched SAFE_RETENTION_DELETES
    copy and pin BOTH loud channels: plan.blocked carries the dead-rule
    entry, and a structured WARNING names table + missing column."""
    from nexus_scalp.hygiene import worker as worker_mod

    patched: dict[str, dict[str, dict[str, Any]]] = {
        "audit": {
            "research_worker_state": {"ts_col": "no_such_column", "days": 30.0, "pk_col": "scope"}
        }
    }
    monkeypatch.setattr(worker_mod, "SAFE_RETENTION_DELETES", patched)

    audit = tmp_path / "audit.db"
    _mk_audit_db(audit)
    conn = sqlite3.connect(str(audit))
    conn.execute(
        "INSERT INTO research_worker_state (scope, last_cycle_at) VALUES ('research', ?)",
        (_iso(60),),
    )
    conn.commit()
    conn.close()

    # Rebind the structlog pipeline AFTER caplog installed its root
    # LogCaptureHandler (BUG-140 lesson, per test_operational_log_hygiene).
    from nexus_scalp.observability.logging import configure_logging

    configure_logging(log_level="INFO", json_format=False, log_to_file=False)
    caplog.set_level(logging.WARNING, logger="nexus_scalp.hygiene.worker")
    caplog.clear()
    live_conn = sqlite3.connect(str(audit))
    try:
        plan = worker_mod.HygienePlanner(mode=WorkerMode.SAFE_CLEAN).build_plan(
            "audit", live_conn, HygieneScanner()
        )
    finally:
        live_conn.close()
    dead = [b for b in plan.blocked if "dead rule" in str(b.get("reason", ""))]
    assert len(dead) == 1
    assert dead[0]["table"] == "research_worker_state"
    assert "no_such_column" in dead[0]["reason"]
    # the dead rule must NOT also pose as a candidate
    assert _retention_row(plan, "research_worker_state") is None
    warn = [r for r in caplog.records if "HYGIENE_DEAD_RETENTION_RULE" in r.getMessage()]
    assert warn, f"expected structured warning, got: {[r.getMessage() for r in caplog.records]}"
    assert "research_worker_state" in caplog.text


def test_healed_shipped_rules_have_no_dead_columns(tmp_path: Path) -> None:
    """Class guard: against EXACT production-shaped fixtures, zero shipped
    SAFE_RETENTION_DELETES rules land in `blocked` — every configured
    ts_col/ts_cols exists in its real table (the pre-fix shipped state
    produced 4 dead-rule blocks here)."""
    audit = tmp_path / "audit.db"
    news = tmp_path / "news.db"
    _mk_audit_db(audit)
    _mk_news_db(news)
    # intelligence_worker_state + position_lifecycle_events + audit_signals
    # exist in the audit fixture; the remaining audit rules (guard
    # telemetry) simply aren't present -> `if not cfg: continue`, no block.
    for db_key, path in (("audit", audit), ("news", news)):
        plan = _plan(db_key, path)
        dead = [b for b in plan.blocked if "dead rule" in str(b.get("reason", ""))]
        assert dead == [], f"{db_key}: shipped rule went dead again: {dead}"


# ---------------------------------------------------------------------------
# 3) news_health ts_cols semantics
# ---------------------------------------------------------------------------


def test_news_health_dual_timestamp_semantics(tmp_path: Path) -> None:
    """Candidate ONLY when every timestamp carrying evidence is older than the
    window AND at least one carries evidence. Either-fresh survives; both-
    NULL and both-'' (no evidence) survive."""
    news = tmp_path / "news.db"
    _mk_news_db(news)
    conn = sqlite3.connect(str(news))
    rows = [
        # (source_id, last_success_at, last_failure_at, candidate?)
        ("both-stale", _iso(200), _iso(150), True),
        ("success-fresh", _iso(2), _iso(150), False),
        ("failure-fresh", _iso(200), _iso(1), False),
        ("success-only-stale", _iso(200), "", True),
        ("failure-only-stale", "", _iso(200), True),
        ("no-evidence-null", None, None, False),
        ("no-evidence-empty", "", "", False),
    ]
    for sid, succ, fail, _ in rows:
        conn.execute(
            "INSERT INTO news_health (source_id, last_success_at, last_failure_at) "
            "VALUES (?, ?, ?)",
            (sid, succ, fail),
        )
    conn.commit()
    conn.close()

    plan = _plan("news", news)
    rc = _retention_row(plan, "news_health")
    assert rc is not None
    assert rc["candidate_rows"] == sum(1 for *_x, c in rows if c) == 3
    # verify the exact qualifying set via the same WHERE on the live rows
    where, args = _retention_where(SAFE_RETENTION_DELETES["news"]["news_health"], _iso(90))
    conn = sqlite3.connect(str(news))
    got = {r[0] for r in conn.execute(f"SELECT source_id FROM news_health{where}", args).fetchall()}
    conn.close()
    assert got == {"both-stale", "success-only-stale", "failure-only-stale"}


# ---------------------------------------------------------------------------
# 4) position_lifecycle_events: real vocabulary + legacy MOVING, never CREATED
# ---------------------------------------------------------------------------


def test_lifecycle_event_vocab_purge(tmp_path: Path) -> None:
    audit = tmp_path / "audit.db"
    _mk_audit_db(audit)
    conn = sqlite3.connect(str(audit))
    old = _iso(10)
    fresh = _iso(1)

    def _ins(event_type: str, ts: str, key: str) -> None:
        conn.execute(
            "INSERT INTO position_lifecycle_events (event_key, ticket, symbol, "
            "event_type, event_timestamp) VALUES (?, '99', 'XAUUSD', ?, ?)",
            (key, event_type, ts),
        )

    # swept classes (old): MFE/GIVEBACK/DEGRADING/RECOVERY + legacy MOVING
    _ins("POSITION_MFE_REACHED", old, "a1")
    _ins("POSITION_PROFIT_GIVEBACK", old, "a2")
    _ins("POSITION_DEGRADING", old, "a3")
    _ins("POSITION_RECOVERY_ATTEMPT", old, "a4")
    _ins("POSITION_MOVING", old, "a5")
    # never swept (old): the durable timeline classes
    _ins("POSITION_CREATED", old, "b1")
    _ins("POSITION_OPENED", old, "b2")
    _ins("POSITION_EXITED", old, "b3")
    # swept class but fresh -> survives the 3d window
    _ins("POSITION_MFE_REACHED", fresh, "c1")
    conn.commit()
    conn.close()

    plan = _plan("audit", audit)
    rc = _retention_row(plan, "position_lifecycle_events")
    assert rc is not None
    assert rc["candidate_rows"] == 5  # a1..a5; b1..b3 never, c1 fresh

    conn = sqlite3.connect(str(audit))
    where, args = _retention_where(
        SAFE_RETENTION_DELETES["audit"]["position_lifecycle_events"], _iso(3)
    )
    got = {
        r[0] for r in conn.execute(f"SELECT event_key FROM position_lifecycle_events{where}", args)
    }
    conn.close()
    assert got == {"a1", "a2", "a3", "a4", "a5"}


# ---------------------------------------------------------------------------
# 5) news_analysis_runs: TEXT run_id PK plans + deletes end-to-end
# ---------------------------------------------------------------------------


def test_analysis_runs_planner_and_text_pk_delete(tmp_path: Path) -> None:
    news = tmp_path / "news.db"
    _mk_news_db(news)
    conn = sqlite3.connect(str(news))
    for i in range(5):
        conn.execute(
            "INSERT INTO news_analysis_runs (run_id, started_at, status) VALUES (?, ?, 'COMPLETE')",
            (f"run-old-{i}", _iso(40)),
        )
    conn.execute(
        "INSERT INTO news_analysis_runs (run_id, started_at, status) VALUES (?, ?, 'COMPLETE')",
        ("run-fresh", _iso(3)),
    )
    conn.commit()
    conn.close()

    plan = _plan("news", news)
    rc = _retention_row(plan, "news_analysis_runs")
    assert rc is not None
    assert rc["candidate_rows"] == 5

    # executor path: TEXT pk IN-subselect deletes exactly the stale rows
    from nexus_scalp.hygiene.worker import CleanupExecutor

    ex = CleanupExecutor(archive_root=tmp_path / "repo", mode=WorkerMode.SAFE_CLEAN)
    res = ex.apply_plan("news", str(news), plan, "run-bug295-1", apply_deletes=True)
    assert res["deleted"].get("news_analysis_runs") == 5
    conn = sqlite3.connect(str(news))
    remaining = [r[0] for r in conn.execute("SELECT run_id FROM news_analysis_runs")]
    conn.close()
    assert remaining == ["run-fresh"]


# ---------------------------------------------------------------------------
# 6) single-key path regression: existing tables behave bit-identically
# ---------------------------------------------------------------------------


def test_single_key_path_regression(tmp_path: Path) -> None:
    """audit_signals (7d) + candles (30d): same counts as pre-fix; the healed
    WHERE for single-ts_col rules is exactly ` WHERE col < ?`."""
    where, args = _retention_where({"ts_col": "generated_at", "days": 7.0}, "CUT")
    assert where == " WHERE generated_at < ?"
    assert args == ("CUT",)

    audit = tmp_path / "audit.db"
    _mk_audit_db(audit)
    conn = sqlite3.connect(str(audit))
    for _ in range(5):
        conn.execute(
            "INSERT INTO audit_signals (generated_at, payload) VALUES (?, '{}')", (_iso(20),)
        )
    for _ in range(3):
        conn.execute(
            "INSERT INTO audit_signals (generated_at, payload) VALUES (?, '{}')", (_iso(1),)
        )
    conn.commit()
    conn.close()
    plan = _plan("audit", audit)
    rc = _retention_row(plan, "audit_signals")
    assert rc is not None
    assert rc["candidate_rows"] == 5
    assert rc["ts_col"] == "generated_at"

    candle = tmp_path / "candle_intel.db"
    cconn = sqlite3.connect(str(candle))
    cconn.execute(
        "CREATE TABLE candles (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT)"
    )
    for _ in range(10):
        cconn.execute("INSERT INTO candles (ts, symbol) VALUES (?, 'XAUUSD')", (_iso(60),))
    cconn.commit()
    cconn.close()
    plan = _plan("candle_intel", candle)
    rc = _retention_row(plan, "candles")
    assert rc is not None
    assert rc["candidate_rows"] == 10


# ---------------------------------------------------------------------------
# 7) end-to-end SAFE_CLEAN via DatabaseHygieneWorker (task11 env pattern)
# ---------------------------------------------------------------------------


def _worker_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    art = repo / "artifacts"
    art.mkdir(parents=True)
    audit = art / "audit.db"
    news = art / "news.db"
    _mk_audit_db(audit)
    _mk_news_db(news)
    conn = sqlite3.connect(str(audit))
    conn.execute(
        "INSERT INTO research_worker_state (scope, last_cycle_at) VALUES ('research', ?)",
        (_iso(60),),
    )
    conn.execute(
        "INSERT INTO research_worker_state (scope, last_cycle_at) VALUES ('keeper', ?)", (_iso(2),)
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(news))
    conn.execute(
        "INSERT INTO news_worker_state (scope, last_cycle_at) VALUES ('news', ?)", (_iso(45),)
    )
    conn.execute(
        "INSERT INTO news_worker_state (scope, last_cycle_at) VALUES ('live', ?)", (_iso(5),)
    )
    conn.execute(
        "INSERT INTO news_health (source_id, last_success_at, last_failure_at) VALUES (?, ?, ?)",
        ("dead-src", _iso(200), _iso(150)),
    )
    conn.execute(
        "INSERT INTO news_health (source_id, last_success_at, last_failure_at) VALUES (?, ?, ?)",
        ("live-src", _iso(200), _iso(1)),
    )
    conn.execute(
        "INSERT INTO news_analysis_runs (run_id, started_at, status) VALUES (?, ?, 'COMPLETE')",
        ("old-run", _iso(40)),
    )
    conn.commit()
    conn.close()
    return repo, audit, news


def test_end_to_end_safe_clean_deletes_healed_classes(tmp_path: Path) -> None:
    repo, audit, news = _worker_env(tmp_path)
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.SAFE_CLEAN, apply_deletes=True)
    res = worker.run_cycle(["audit", "news"])
    ad = res["databases"]["audit"]["deleted"]
    nd = res["databases"]["news"]["deleted"]
    assert ad.get("research_worker_state") == 1
    assert nd.get("news_worker_state") == 1
    assert nd.get("news_health") == 1
    assert nd.get("news_analysis_runs") == 1
    assert res["databases"]["audit"]["verification"] == "PASS"
    assert res["databases"]["news"]["verification"] == "PASS"

    conn = sqlite3.connect(str(audit))
    assert [r[0] for r in conn.execute("SELECT scope FROM research_worker_state")] == ["keeper"]
    conn.close()
    conn = sqlite3.connect(str(news))
    assert [r[0] for r in conn.execute("SELECT scope FROM news_worker_state")] == ["live"]
    assert [r[0] for r in conn.execute("SELECT source_id FROM news_health")] == ["live-src"]
    assert conn.execute("SELECT COUNT(*) FROM news_analysis_runs").fetchone()[0] == 0
    conn.close()


def test_dry_run_never_mutates_healed_rules(tmp_path: Path) -> None:
    """The healed rules must obey the same non-destructive default: DRY_RUN
    finds the candidates and touches nothing."""
    repo, audit, _news = _worker_env(tmp_path)
    before = (
        sqlite3.connect(str(audit))
        .execute("SELECT COUNT(*) FROM research_worker_state")
        .fetchone()[0]
    )
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.DRY_RUN, apply_deletes=False)
    res = worker.run_cycle(["audit"])
    plan_summary = res["databases"]["audit"]["plan_summary"]
    assert plan_summary["retention_candidates"] >= 1
    # DRY_RUN blocks candidates by mode (pre-existing contract) but none of
    # them may be dead-rule entries — every shipped rule is live now.
    audit_db = res["databases"]["audit"]
    plan = HygienePlanner(mode=WorkerMode.DRY_RUN)
    conn = sqlite3.connect(str(audit))
    try:
        built = plan.build_plan("audit", conn, HygieneScanner())
    finally:
        conn.close()
    assert [b for b in built.blocked if "dead rule" in str(b.get("reason", ""))] == []
    assert audit_db["deleted"] == {}
    after = (
        sqlite3.connect(str(audit))
        .execute("SELECT COUNT(*) FROM research_worker_state")
        .fetchone()[0]
    )
    assert after == before
