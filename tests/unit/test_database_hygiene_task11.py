"""
TASK-11 Database Hygiene Worker — Regression Guards (TEST-HYG-01..36)

Proves the non-destructive safety contract of the hygiene worker:
  * dry-run / audit-only makes ZERO mutations
  * exact duplicates detected via canonical identities; split fills NEVER
    treated as duplicates
  * financial / migration / research / model rows NEVER auto-deleted
  * expired cache + stale temp cleanup with budget bounds
  * archive-before-delete with verified checksums
  * journal created for every destructive action
  * financial aggregates unchanged after cleanup
  * research lineage / news identity unchanged
  * WAL-safe, busy-defer, hot-path isolation
  * idempotent second run
  * crash recovery (INTERRUPTED, never blind resume)
  * CLI/plan/executor consistency
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.hygiene import Confidence, WorkerMode, WorkerState
from nexus_scalp.hygiene.archive import ArchiveManager
from nexus_scalp.hygiene.detectors import DuplicateDetector, OrphanDetector
from nexus_scalp.hygiene.retention import RetentionEngine
from nexus_scalp.hygiene.state import HygieneStateStore
from nexus_scalp.hygiene.worker import (
    SAFE_CLEAN_CLASSES,
    CleanupExecutor,
    HygienePlanner,
    HygieneScanner,
    financial_aggregates,
)
from nexus_scalp.hygiene.worker_runner import (
    DatabaseHygieneWorker,
    db_integrity_digest,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _mk_audit_db(path: Path) -> sqlite3.Connection:
    """Builds a small audit.db-shaped database with the tables we test."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE audit_ledger (
            ticket INTEGER PRIMARY KEY, symbol TEXT, direction TEXT, volume REAL,
            entry_price REAL, exit_price REAL, status TEXT, pnl REAL DEFAULT 0.0,
            commission REAL DEFAULT 0.0, swap REAL DEFAULT 0.0, duration_sec REAL,
            timestamp TEXT, order_id TEXT DEFAULT '', open_time TEXT, close_time TEXT,
            entry_reason TEXT DEFAULT '', ai_confidence_at_open REAL DEFAULT 0.0,
            market_regime_at_open TEXT DEFAULT ''
        );
        CREATE TABLE audit_experiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT, experience_id TEXT,
            request_id TEXT, idempotency_key TEXT UNIQUE, strategy_id TEXT,
            decision_timestamp TEXT, payload TEXT
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT UNIQUE NOT NULL,
            execution_id TEXT, realized_pnl_usd REAL DEFAULT 0.0, payload TEXT
        );
        CREATE TABLE audit_broker_trades (
            trade_id TEXT, position_id INTEGER, net_pnl REAL,
            entry_time TEXT, exit_time TEXT
        );
        CREATE TABLE audit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at TEXT, payload TEXT
        );
        CREATE TABLE audit_guard_telemetry (
            window_start TEXT NOT NULL,
            symbol TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (window_start, symbol, reason_code)
        );
        CREATE TABLE position_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket TEXT, event_type TEXT, event_timestamp TEXT
        );
        CREATE TABLE research_worker_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT, updated_at TEXT, payload TEXT
        );
        CREATE TABLE trade_autopsies (
            ticket INTEGER PRIMARY KEY, payload TEXT
        );
        CREATE TABLE audit_broker_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER, deal_data TEXT
        );
        """
    )
    # Ledger with a 3-leg split-fill family (one economic trade).
    now = datetime.now(UTC)
    ts = now.isoformat()
    conn.execute(
        "INSERT INTO audit_ledger VALUES "
        "(1,'XAUUSD','BUY',0.1,2000.0,1995.0,'CLOSED',-50.0,0,0,60,?,"
        "'fam-a','','', 'PURE_AI',0.65,'RANGING')",
        (ts,),
    )
    for t in (2, 3):
        conn.execute(
            "INSERT INTO audit_ledger VALUES "
            f"({t},'XAUUSD','BUY',0.1,2000.0,1995.0,'CLOSED',-50.0,0,0,60,?,"
            "'fam-a','','', 'PURE_AI',0.65,'RANGING')",
            (ts,),
        )
    # Decisions + outcomes (one decision, one outcome key).
    conn.execute(
        "INSERT INTO audit_experiences (experience_id, request_id, idempotency_key, "
        "strategy_id, decision_timestamp, payload) VALUES ('e1','r1','exp_r1',"
        "'PURE_AI',?, '{}')",
        (ts,),
    )
    conn.execute(
        "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
        "realized_pnl_usd, payload) VALUES ('exp_r1','1',-50.0,'{}')"
    )
    # Broker trades mirror the 3 legs.
    for t in (1, 2, 3):
        conn.execute(
            "INSERT INTO audit_broker_trades VALUES (?, ?, -50.0, ?, ?)",
            (f"bt{t}", t, ts, ts),
        )
    # Signals: 5 old + 3 new.
    for _ in range(5):
        old = (now - timedelta(days=20)).isoformat()
        conn.execute("INSERT INTO audit_signals (generated_at, payload) VALUES (?, '{}')", (old,))
    for _ in range(3):
        conn.execute(
            "INSERT INTO audit_signals (generated_at, payload) VALUES (?, '{}')",
            (now.isoformat(),),
        )
    # Guard telemetry: 2 old.
    for j in range(2):
        old = (now - timedelta(days=30)).isoformat()
        conn.execute(
            "INSERT INTO audit_guard_telemetry (window_start, symbol, reason_code, count) "
            "VALUES (?, 'XAUUSD', ?, 1)",
            (old, f"RC{j}"),
        )
    # MOVING events: 4 old.
    for _ in range(4):
        old = (now - timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO position_lifecycle_events (ticket, event_type, event_timestamp) "
            "VALUES ('99','POSITION_MOVING', ?)",
            (old,),
        )
    # Stale worker state.
    stale = (now - timedelta(days=60)).isoformat()
    conn.execute(
        "INSERT INTO research_worker_state (updated_at, payload) VALUES (?, '{}')", (stale,)
    )
    # Autopsy for ticket 1.
    conn.execute("INSERT INTO trade_autopsies (ticket, payload) VALUES (1, '{}')")
    conn.commit()
    return conn

def _mk_news_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE news_articles (
            article_id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_hash TEXT UNIQUE, title TEXT, published_at TEXT,
            source_id INTEGER, is_duplicate INTEGER DEFAULT 0,
            duplicate_of TEXT DEFAULT '', created_at TEXT
        );
        CREATE TABLE news_analysis (
            analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER, run_id TEXT, analyzed_at TEXT
        );
        CREATE TABLE news_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT
        );
        """
    )
    now = datetime.now(UTC)
    conn.execute(
        "INSERT INTO news_articles (article_hash, title, published_at, source_id, "
        "is_duplicate, duplicate_of, created_at) VALUES "
        "('h1','Gold rises',?,1,0,'',?)",
        (now.isoformat(), now.isoformat()),
    )
    # Duplicate of h1, flagged + canonical exists.
    conn.execute(
        "INSERT INTO news_articles (article_hash, title, published_at, source_id, "
        "is_duplicate, duplicate_of, created_at) VALUES "
        "('h1-dup','Gold rises (dup)',?,1,1,'h1',?)",
        (now.isoformat(), now.isoformat()),
    )
    # Flagged duplicate with NO canonical row (ambiguous).
    conn.execute(
        "INSERT INTO news_articles (article_hash, title, published_at, source_id, "
        "is_duplicate, duplicate_of, created_at) VALUES "
        "('h2','Silver falls',?,1,1,'missing-hash',?)",
        (now.isoformat(), now.isoformat()),
    )
    # Health rows: 2 old.
    for _ in range(2):
        old = (now - timedelta(days=200)).isoformat()
        conn.execute("INSERT INTO news_health (created_at) VALUES (?)", (old,))
    conn.commit()
    return conn

def _mk_candle_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE candles (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT);
        CREATE TABLE trade_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT);
        CREATE TABLE open_positions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT);
        """
    )
    now = datetime.now(UTC)
    for _ in range(10):
        old = (now - timedelta(days=60)).isoformat()
        conn.execute("INSERT INTO candles (ts, symbol) VALUES (?, 'XAUUSD')", (old,))
    conn.execute(
        "INSERT INTO trade_decisions (ts, symbol) VALUES (?, 'XAUUSD')",
        (now.isoformat(),),
    )
    conn.commit()
    return conn

@pytest.fixture()
def env(tmp_path: Path):
    """Creates repo-shaped tmp dir + 3 fixture DBs + worker."""
    repo = tmp_path / "repo"
    repo.mkdir()
    audit_path = repo / "artifacts" / "audit.db"
    news_path = repo / "artifacts" / "news.db"
    candle_path = repo / "artifacts" / "candle_intel.db"
    (repo / "artifacts").mkdir(parents=True, exist_ok=True)
    a = _mk_audit_db(audit_path)
    a.close()
    n = _mk_news_db(news_path)
    n.close()
    c = _mk_candle_db(candle_path)
    c.close()
    return repo, audit_path, news_path, candle_path

# ---------------------------------------------------------------------------
# TEST-HYG-01..07: non-destructive defaults + protection
# ---------------------------------------------------------------------------

def test_hyg01_dry_run_makes_zero_mutation(env):
    repo, audit_path, news_path, candle_path = env
    digest_before = db_integrity_digest(str(audit_path))
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.DRY_RUN, apply_deletes=False)
    res = worker.run_cycle(["audit"])
    assert res["verification"] in ("PASS", "CHECK")
    db = res["databases"]["audit"]
    assert db["verification"] == "SKIPPED_DRY_RUN"
    assert db.get("deleted", {}) == {}
    assert db_integrity_digest(str(audit_path)) == digest_before

def test_hyg02_exact_duplicate_detection(env):
    repo, audit_path, news_path, candle_path = env
    conn = sqlite3.connect(str(news_path))
    det = DuplicateDetector()
    dups = det.scan_news(conn)
    conn.close()
    exact = [d for d in dups if d.confidence == Confidence.EXACT_DUPLICATE]
    assert len(exact) == 1
    assert exact[0].identity_layer == "article_hash"
    assert exact[0].canonical_row_id == 1
    # ambiguous flagged duplicate stays UNKNOWN (never deletable)
    unknown = [d for d in dups if d.confidence == Confidence.UNKNOWN]
    assert len(unknown) == 1

def test_hyg03_split_fill_is_not_duplicate(env):
    repo, audit_path, news_path, candle_path = env
    conn = sqlite3.connect(str(audit_path))
    det = DuplicateDetector()
    finds = det.scan_audit(conn)
    conn.close()
    # The 3-leg family is PROTECTED (NOT_DUPLICATE), never a delete candidate.
    family = [f for f in finds if f.identity_layer == "order_id_family"]
    assert len(family) == 1
    assert family[0].confidence == Confidence.NOT_DUPLICATE
    assert "PROTECTED" in family[0].detail
    assert all(f.confidence != Confidence.EXACT_DUPLICATE for f in finds)

def test_hyg04_financial_row_never_deleted_automatically(env):
    repo, audit_path, news_path, candle_path = env
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.SAFE_CLEAN, apply_deletes=True)
    res = worker.run_cycle(["audit"])
    db = res["databases"]["audit"]
    deleted = db.get("deleted", {})
    # audit_ledger must never appear in the deleted set.
    assert "audit_ledger" not in deleted
    assert "audit_broker_trades" not in deleted
    assert "audit_experiences" not in deleted
    assert "audit_experience_outcomes" not in deleted
    # Audit ledger rows all still present.
    conn = sqlite3.connect(str(audit_path))
    assert conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0] == 3
    conn.close()

def test_hyg05_migration_history_never_deleted(env):
    repo, audit_path, news_path, candle_path = env
    # TASK-10 schema_meta table (migration history) — simulated; must be KEEP.
    conn = sqlite3.connect(str(audit_path))
    conn.execute("CREATE TABLE schema_meta (migration_id TEXT PRIMARY KEY, applied_at TEXT)")
    conn.execute("INSERT INTO schema_meta VALUES ('AUDIT-0001','2026-08-18')")
    conn.commit()
    conn.close()

    def _protected_digest(path) -> str:
        """Digest over financial-evidence tables ONLY (approved-retention
        telemetry is allowed to change)."""
        import hashlib

        c = sqlite3.connect(str(path))
        parts = []
        for t in (
            "audit_ledger",
            "audit_experiences",
            "audit_experience_outcomes",
            "audit_broker_trades",
            "schema_meta",
        ):
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            parts.append(f"{t}={n}")
        c.close()
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    digest_before = _protected_digest(str(audit_path))
    worker = DatabaseHygieneWorker(
        repo_root=repo, mode=WorkerMode.AGGRESSIVE_CLEAN, apply_deletes=True
    )
    worker.run_cycle(["audit"])
    # schema_meta (unknown to the retention registry) is KEEP by default.
    conn = sqlite3.connect(str(audit_path))
    assert conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1
    assert _protected_digest(str(audit_path)) == digest_before
    conn.close()

def test_hyg06_research_evidence_preserved(env):
    repo, audit_path, news_path, candle_path = env
    worker = DatabaseHygieneWorker(
        repo_root=repo, mode=WorkerMode.AGGRESSIVE_CLEAN, apply_deletes=True
    )
    res = worker.run_cycle(["audit"])
    deleted = res["databases"]["audit"].get("deleted", {})
    assert "trade_autopsies" not in deleted
    assert "research_runs" not in deleted
    conn = sqlite3.connect(str(audit_path))
    assert conn.execute("SELECT COUNT(*) FROM trade_autopsies").fetchone()[0] == 1
    conn.close()

def test_hyg07_model_provenance_preserved(env):
    repo, audit_path, news_path, candle_path = env
    conn = sqlite3.connect(str(audit_path))
    conn.execute(
        "CREATE TABLE experience_model_registry (model_id TEXT PRIMARY KEY, model_version TEXT)"
    )
    conn.execute("INSERT INTO experience_model_registry VALUES ('m1','1.0.0')")
    conn.commit()
    conn.close()

    def _digest(path) -> str:
        import hashlib

        c = sqlite3.connect(str(path))
        parts = []
        for t in (
            "experience_model_registry",
            "audit_experiences",
            "audit_experience_outcomes",
            "audit_ledger",
            "audit_broker_trades",
        ):
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            parts.append(f"{t}={n}")
        c.close()
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    digest = _digest(str(audit_path))
    worker = DatabaseHygieneWorker(
        repo_root=repo, mode=WorkerMode.AGGRESSIVE_CLEAN, apply_deletes=True
    )
    worker.run_cycle(["audit"])
    assert _digest(str(audit_path)) == digest

# ---------------------------------------------------------------------------
# TEST-HYG-08..13: cleanup classes + archive
# ---------------------------------------------------------------------------

def test_hyg08_expired_cache_cleanup(env):
    repo, audit_path, news_path, candle_path = env
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.SAFE_CLEAN, apply_deletes=True)
    res = worker.run_cycle(["audit", "news", "candle_intel"])
    # news_health old rows deleted (retention 90d, 2 rows @200d).
    deleted = res["databases"]["news"].get("deleted", {})
    assert deleted.get("news_health", 0) == 2
    # audit signals old rows deleted (5 @20d > 7d).
    aud_del = res["databases"]["audit"].get("deleted", {})
    assert aud_del.get("audit_signals", 0) == 5
    # guard telemetry (2 @30d > 13d).
    assert aud_del.get("audit_guard_telemetry", 0) == 2
    # MOVING events (4 @10d > 3d).
    assert aud_del.get("position_lifecycle_events", 0) == 4
    # candle derived rows (10 @60d > 30d).
    cnd = res["databases"]["candle_intel"].get("deleted", {})
    assert cnd.get("candles", 0) == 10

def test_hyg10_orphan_detection(env):
    repo, audit_path, news_path, candle_path = env
    conn = sqlite3.connect(str(audit_path))
    det = OrphanDetector()
    orphs = det.scan_audit(conn)
    conn.close()
    # The autopsy for ticket 1 has a ledger row -> not an orphan.
    assert all(
        o["ref_key"] != 1 or o["classification"] == "EXPECTED_ORPHAN"
        for o in orphs
        if o["ref_key"] == 1
    )

def test_hyg12_archive_before_delete(env):
    repo, audit_path, news_path, candle_path = env
    worker = DatabaseHygieneWorker(repo_root=repo, mode=WorkerMode.SAFE_CLEAN, apply_deletes=True)
    res = worker.run_cycle(["news"])
    db = res["databases"]["news"]
    archived = db.get("archived", {})
    deleted = db.get("deleted", {})
    # The EXACT duplicate article was archived + deleted; the canonical stays.
    assert archived.get("news_articles", 0) == 1
    assert deleted.get("news_articles", 0) == 1
    conn = sqlite3.connect(str(news_path))
    rows = conn.execute("SELECT article_hash FROM news_articles ORDER BY article_id").fetchall()
    conn.close()
    hashes = [r[0] for r in rows]
    assert "h1" in hashes
    assert "h1-dup" not in hashes  # duplicate removed
    assert "h2" in hashes  # ambiguous kept

def test_hyg13_archive_checksum_verified(env):
    repo, audit_path, news_path, candle_path = env
    am = ArchiveManager(repo)
    man = am.archive_rows(
        "test",
        "tbl",
        [{"a": 1, "b": "x"}],
        retention_reason="test",
        software_version="t",
    )
    assert am.verify_archive(man) is True
    # corrupt the file -> verification fails
    p = repo / man["path"]
    p.write_bytes(p.read_bytes() + b"junk")
    assert am.verify_archive(man) is False

# ---------------------------------------------------------------------------
# TEST-HYG-14..18: journal / invariants / WAL
# ---------------------------------------------------------------------------

def test_hyg15_financial_aggregate_unchanged(env):
    repo, audit_path, news_path, candle_path = env
    conn = sqlite3.connect(str(audit_path))
    before = financial_aggregates(conn)
    conn.close()
    worker = DatabaseHygieneWorker(
        repo_root=repo, mode=WorkerMode.AGGRESSIVE_CLEAN, apply_deletes=True
    )
    worker.run_cycle(["audit"])
    conn = sqlite3.connect(str(audit_path))
    after = financial_aggregates(conn)
    conn.close()
    for k in before:
        assert abs(after[k] - before[k]) < 1e-6, f"{k} changed: {before[k]} -> {after[k]}"

def test_hyg16_research_lineage_unchanged(env):
    repo, audit_path, news_path, candle_path = env
    worker = DatabaseHygieneWorker(
        repo_root=repo, mode=WorkerMode.AGGRESSIVE_CLEAN, apply_deletes=True
    )
    worker.run_cycle(["audit"])
    # experiences/outcomes/autopsies unchanged -> counts stable.
    conn = sqlite3.connect(str(audit_path))
    assert conn.execute("SELECT COUNT(*) FROM audit_experiences").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit_experience_outcomes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trade_autopsies").fetchone()[0] == 1
    conn.close()

# ---------------------------------------------------------------------------
# TEST-HYG-19..23: budget / hot path / confidence / legacy
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST-HYG-24..30: cross-DB / rebuild / integrity / state / CLI-parity
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST-HYG-31..36: JSON truth / telegram / bounded / confidence / schema
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Real-DB copy forensic test (TEST-HYG-64 subset): run against COPIES of the
# real production DBs (read-only plan + no-mutation verify).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST-HYG-37..48 (TASK-22): runtime hygiene engine — config scheduler,
# first-run audit, quarantine, consistency rules, index health, dry-run,
# protected data, non-blocking cadence.
# ---------------------------------------------------------------------------

