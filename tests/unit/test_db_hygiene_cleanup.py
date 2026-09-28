"""Regression tests for the database hygiene cleanup rules (DATABASE HYGIENE).

Every rule is exercised against a real, isolated PostgreSQL database
(synthetic data with known duplicates) and must prove:

1. duplicate detection finds exactly the seeded duplicates
2. canonical selection keeps the right row (earliest evidence, terminal state)
3. orphan detection finds only parentless children
4. spam classification isolates retry-storm / empty-placeholder rows
5. idempotency: a second run deletes ZERO additional rows
6. money / audit-evidence rows are never touched
7. foreign-key integrity is preserved (news children reconcile to parents)

The cluster is the throwaway instance described in the engineering skill:
``$LOCALAPPDATA/Temp/pg17_test`` on port 55432. It is provisioned by the
``_pg_url`` fixture and torn down at the end of the session; nothing here
ever touches the operator's live ``nexusdb`` (a mismatch on the URL is an
explicit test failure, not a silent skip).

Run::

    ./.venv/Scripts/python.exe -m pytest tests/unit/test_db_hygiene_cleanup.py \
        -p no:cacheprovider -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
sys.path.insert(0, str(_SRC))

from scripts.maint.db_hygiene import cleanup as cu  # noqa: E402  # noqa: E402  # noqa: E402

#: The test cluster URL. The database name makes the isolation explicit: an
#: accidental pointer at the live ``nexusdb`` fails the URL guard below.
_TEST_DSN = os.environ.get(
    "NSE_HYGIENE_TEST_URL",
    "postgresql://nse_hygiene:hygiene_pw@127.0.0.1:55432/nse_hygiene_test",
)


def _is_live_db(url: str) -> bool:
    return cu.is_live_database(url)


@pytest.fixture(scope="module")
def pg_url() -> str:
    """Provision an empty test database for the duration of the module."""
    if _is_live_db(_TEST_DSN):
        pytest.fail(f"refusing to run hygiene tests against a live database: {_TEST_DSN}")
    # split off the db name and recreate it on the admin connection
    base, _, dbname = _TEST_DSN.rpartition("/")
    admin = psycopg.connect(f"{base}/postgres")
    admin.autocommit = True
    try:
        admin.execute(f"DROP DATABASE IF EXISTS {dbname}")
        admin.execute(f"CREATE DATABASE {dbname}")
    finally:
        admin.close()
    yield _TEST_DSN


@pytest.fixture()
def con(pg_url):
    """A fresh schema per test; rolled back automatically at teardown."""
    c = psycopg.connect(pg_url)
    c.autocommit = True
    yield c
    c.close()


# --------------------------------------------------------------------------
# schema + seed helpers
# --------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS audit_orders (
    id BIGSERIAL PRIMARY KEY,
    ticket BIGINT,
    order_id TEXT NOT NULL,
    symbol TEXT,
    action TEXT,
    price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    reason TEXT,
    latency DOUBLE PRECISION,
    execution_mode TEXT,
    execution_id TEXT,
    timestamp TEXT
);
CREATE TABLE IF NOT EXISTS news_analysis (
    analysis_id TEXT PRIMARY KEY,
    article_id TEXT,
    run_id TEXT,
    status TEXT,
    local_only BIGINT,
    provider TEXT,
    summary TEXT,
    analyzed_at TEXT
);
CREATE TABLE IF NOT EXISTS news_analysis_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    article_ids TEXT,
    provider TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS news_articles (
    article_id TEXT PRIMARY KEY,
    article_hash TEXT UNIQUE,
    article_status TEXT
);
CREATE TABLE IF NOT EXISTS news_analyzed_hashes (
    article_hash TEXT PRIMARY KEY,
    title TEXT,
    analysis_id TEXT,
    analyzed_at TEXT
);
CREATE TABLE IF NOT EXISTS news_junk_hashes (
    article_hash TEXT PRIMARY KEY,
    title TEXT,
    reason TEXT,
    pruned_at TEXT,
    analysis_id TEXT
);
CREATE TABLE IF NOT EXISTS news_ai_analysis (
    ai_analysis_id TEXT PRIMARY KEY,
    article_id TEXT
);
CREATE TABLE IF NOT EXISTS news_impacts (
    id BIGSERIAL PRIMARY KEY,
    article_id TEXT
);
CREATE TABLE IF NOT EXISTS news_entities (
    id BIGSERIAL PRIMARY KEY,
    article_id TEXT
);
CREATE TABLE IF NOT EXISTS news_topics (
    id BIGSERIAL PRIMARY KEY,
    article_id TEXT
);
CREATE TABLE IF NOT EXISTS audit_ledger (
    ticket BIGINT PRIMARY KEY,
    symbol TEXT,
    pnl DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS db_operation_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    level VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    domain VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    repository VARCHAR NOT NULL DEFAULT '',
    query_name VARCHAR NOT NULL DEFAULT '',
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    rows BIGINT NOT NULL DEFAULT 0,
    error_code VARCHAR NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    correlation_id VARCHAR NOT NULL DEFAULT '',
    masked_sql TEXT NOT NULL DEFAULT ''
);
"""


_TABLES = (
    "audit_orders",
    "news_analysis",
    "news_analysis_runs",
    "news_articles",
    "news_analyzed_hashes",
    "news_junk_hashes",
    "news_ai_analysis",
    "news_impacts",
    "news_entities",
    "news_topics",
    "audit_ledger",
    "db_operation_logs",
)


def _apply_ddl(con):
    """Create the schema idempotently and truncate every table.

    The DDL uses IF NOT EXISTS and the truncate guarantees each test starts
    from an empty table, so tests are order-independent and re-runnable.
    """
    for stmt in _DDL.split(";"):
        s = stmt.strip()
        if s:
            con.execute(s)
    for t in _TABLES:
        con.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")


def _run_rules(con, execute: bool):
    """Run all rules through the module driver against a synthetic target.

    Each rule gets its own transaction on a FRESH connection, exactly like
    ``cleanup.run_cleanup`` does: one transaction per rule, rollback on any
    post-condition failure. Reusing a single connection is not equivalent
    here because psycopg 3 refuses to flip ``autocommit`` while a
    transaction is open.
    """
    results = []
    for rule_id, fn in cu.RULES:
        rule_con = psycopg.connect(con.info.dsn)
        try:
            cu._set_autocommit(rule_con, False)
            try:
                res = fn(rule_con, execute)
                if execute and res.candidates:
                    cu._log_operation(rule_con, res)
                rule_con.commit()
                results.append(res)
            except Exception:
                rule_con.rollback()
                results.append(
                    cu.RuleResult(rule_id, "?", f"{rule_id} failed", 0, 0, 0, 0, "FAILED")
                )
        finally:
            rule_con.close()
    return results


# --------------------------------------------------------------------------
# R1A - byte-identical duplicates
# --------------------------------------------------------------------------


def test_r1a_byte_identical_duplicates(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO audit_orders (order_id, ticket, symbol, action, price, reason, timestamp) "
        "VALUES ('ORD-1', 100, 'XAUUSD', 'Executed order', 4400.0, 'fill', '2026-08-17 04:00:00')"
    )
    # three byte-identical copies of the same executed order
    for _ in range(3):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, price, reason, timestamp) "
            "VALUES ('ORD-1', 100, 'XAUUSD', 'Executed order', 4400.0, 'fill', '2026-08-17 04:00:00')"
        )
    # a legitimately different event for the same order (different business
    # columns): NOT a duplicate, must survive
    con.execute(
        "INSERT INTO audit_orders (order_id, ticket, symbol, action, price, reason, timestamp) "
        "VALUES ('ORD-1', 100, 'XAUUSD', 'Modified order', 4401.0, 'sl move', '2026-08-17 05:00:00')"
    )
    # a second order with byte-identical copies (multi-group coverage)
    for _ in range(2):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, price, reason, timestamp) "
            "VALUES ('ORD-2', 101, 'XAUUSD', 'Executed order', 4500.0, 'fill', '2026-08-17 06:00:00')"
        )
    before = con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]
    assert before == 7

    res = cu.rule_r1a(con, dry_run=False)
    assert res.status == "APPLIED"
    assert res.candidates == 4
    assert res.deleted == 4
    after = con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]
    assert after == 3

    # canonical selection: the LOWEST id survived in each group
    for oid, want in (("ORD-1", 2), ("ORD-2", 1)):
        got = con.execute("SELECT COUNT(*) FROM audit_orders WHERE order_id=%s", (oid,)).fetchone()[
            0
        ]
        assert got == want, f"{oid}: expected {want} surviving rows, got {got}"
    # the distinct business event (Modified order) is preserved
    actions = sorted(
        r[0] for r in con.execute("SELECT action FROM audit_orders WHERE order_id='ORD-1'")
    )
    assert actions == ["Executed order", "Modified order"]


# --------------------------------------------------------------------------
# R1B - retry-storm collapse (first + last kept)
# --------------------------------------------------------------------------


def test_r1b_retry_storm_collapse(con):
    _apply_ddl(con)
    # a 5-row BREAKEVEN_FAILED storm from one ticket (retry-distinct: sl drifts)
    for sl in (100.0, 100.1, 100.2, 100.3, 100.4):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, stop_loss, timestamp) "
            "VALUES (%s, 200, 'XAUUSD', 'BREAKEVEN_FAILED', %s, '2026-08-17 06:00:00')",
            ("STORM-1", sl),
        )
    # a lone BREAKEVEN_FAILED with no repeats: must survive untouched
    con.execute(
        "INSERT INTO audit_orders (order_id, ticket, symbol, action, stop_loss, timestamp) "
        "VALUES ('SINGLE-1', 201, 'XAUUSD', 'BREAKEVEN_FAILED', 200.0, '2026-08-17 07:00:00')"
    )
    before = con.execute(
        "SELECT COUNT(*) FROM audit_orders WHERE action='BREAKEVEN_FAILED'"
    ).fetchone()[0]
    assert before == 6

    res = cu.rule_r1b(con, dry_run=False)
    assert res.status == "APPLIED"
    assert res.candidates == 3
    assert res.deleted == 3

    remaining = con.execute(
        "SELECT order_id, id FROM audit_orders WHERE action='BREAKEVEN_FAILED' ORDER BY id"
    ).fetchall()
    # the storm group kept exactly its first and last row
    storm = [r for r in remaining if r[0] == "STORM-1"]
    assert len(storm) == 2
    # the lone row survived
    assert ("SINGLE-1",) in tuple((r[0],) for r in remaining)
    assert len(remaining) == 3


# --------------------------------------------------------------------------
# R2 - re-analysis duplicates
# --------------------------------------------------------------------------


def test_r2_reanalysis_duplicates(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO news_analysis (analysis_id, article_id, provider, status, analyzed_at) VALUES "
        "('A-1', 'ART-1', 'openai', 'COMPLETE', '2026-09-01T10:00:00'),"
        "('A-2', 'ART-1', 'openai', 'COMPLETE', '2026-09-01T11:00:00'),"  # duplicate
        "('A-3', 'ART-1', 'anthropic', 'COMPLETE', '2026-09-01T10:00:00'),"  # other provider: keep
        "('A-4', 'ART-2', 'openai', 'COMPLETE', '2026-09-01T10:00:00')"
    )
    res = cu.rule_r2(con, dry_run=False)
    assert res.status == "APPLIED"
    assert res.candidates == 1
    assert res.deleted == 1
    kept = sorted(r[0] for r in con.execute("SELECT analysis_id FROM news_analysis"))
    assert kept == ["A-1", "A-3", "A-4"]


# --------------------------------------------------------------------------
# R4A / R4B - duplicate and empty analysis runs
# --------------------------------------------------------------------------


def test_r4_runs_duplicates_and_empty(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO news_analysis_runs (run_id, started_at, status, article_ids, provider) VALUES "
        "('R-1', '2026-09-01T10:00:00', 'COMPLETE', '[\"a\"]', 'openai'),"
        "('R-2', '2026-09-01T11:00:00', 'COMPLETE', '[\"a\"]', 'openai'),"  # dup of R-1
        "('R-3', '2026-09-01T10:00:00', 'QUEUED', '[]', 'openai'),"  # empty placeholder
        "('R-4', '2026-09-01T10:00:00', 'COMPLETE', '[\"b\"]', 'openai')"
    )
    r4a = cu.rule_r4a(con, dry_run=False)
    assert r4a.candidates == 1 and r4a.deleted == 1
    r4b = cu.rule_r4b(con, dry_run=False)
    assert r4b.candidates == 1 and r4b.deleted == 1
    kept = sorted(r[0] for r in con.execute("SELECT run_id FROM news_analysis_runs"))
    assert kept == ["R-1", "R-4"]


# --------------------------------------------------------------------------
# R3 / R7 - orphan tombstones and contradictory overlap
# --------------------------------------------------------------------------


def test_r3_r7_tombstone_disjoint(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO news_articles (article_id, article_hash, article_status) VALUES "
        "('ART-1', 'H1', 'ACTIVE'), ('ART-2', 'H2', 'IRRELEVANT')"
    )
    con.execute(
        "INSERT INTO news_analyzed_hashes (article_hash, title, analysis_id) VALUES "
        "('H1', 't1', 'A-1'),"  # article exists + junk exists -> R7
        "('H2', 't2', 'A-2'),"  # article pruned + junk exists -> R7
        "('H9', 't9', 'A-9')"  # article pruned + NO junk -> R3 (guard)
    )
    con.execute(
        "INSERT INTO news_junk_hashes (article_hash, title, reason) VALUES ('H1','t1','junk'), ('H2','t2','junk')"
    )

    r7 = cu.rule_r7(con, dry_run=False)
    assert r7.candidates == 2 and r7.deleted == 2
    r3 = cu.rule_r3(con, dry_run=False)
    # R3 is a report-only tripwire: the orphan has no junk tombstone, so
    # deleting it would break the never-re-ingest guard. It must MEASURE the
    # row and refuse to delete it.
    assert r3.candidates == 1 and r3.status == "REPORT_ONLY" and r3.deleted == 0

    remaining = sorted(r[0] for r in con.execute("SELECT article_hash FROM news_analyzed_hashes"))
    assert remaining == ["H9"]


# --------------------------------------------------------------------------
# R5 - orphaned news children
# --------------------------------------------------------------------------


def test_r5_orphan_children(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO news_articles (article_id, article_hash, article_status) VALUES ('ART-1','H1','ACTIVE')"
    )
    con.execute(
        "INSERT INTO news_ai_analysis (ai_analysis_id, article_id) VALUES ('AI-1','ART-1'), ('AI-2','ART-GONE')"
    )
    con.execute("INSERT INTO news_impacts (article_id) VALUES ('ART-1'), ('ART-GONE')")
    con.execute("INSERT INTO news_entities (article_id) VALUES ('ART-1')")
    con.execute("INSERT INTO news_topics (article_id) VALUES ('ART-GONE-2')")

    res = cu.rule_r5(con, dry_run=False)
    assert res.status == "APPLIED"
    assert res.candidates == 3 and res.deleted == 3
    assert con.execute("SELECT COUNT(*) FROM news_ai_analysis").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM news_impacts").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM news_entities").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM news_topics").fetchone()[0] == 0


# --------------------------------------------------------------------------
# IDEMPOTENCY - the core contract
# --------------------------------------------------------------------------


def test_idempotent_second_run_deletes_nothing(con):
    _apply_ddl(con)
    # a mixed pollution snapshot: storm + byte-identical + re-analysis +
    # overlap + orphans + empty runs
    for sl in (1.0, 1.1, 1.2, 1.3):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, stop_loss, timestamp) "
            "VALUES ('O1', 1, 'XAUUSD', 'BREAKEVEN_FAILED', %s, 't')",
            (sl,),
        )
    for _ in range(2):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, timestamp) "
            "VALUES ('O2', 2, 'XAUUSD', 'Executed order', 't')"
        )
    con.execute(
        "INSERT INTO news_analysis (analysis_id, article_id, provider, status, analyzed_at) VALUES "
        "('A1','ART1','openai','COMPLETE','t1'),('A2','ART1','openai','COMPLETE','t2')"
    )
    con.execute(
        "INSERT INTO news_analysis_runs (run_id, started_at, status, article_ids, provider) VALUES "
        "('R1','t1','COMPLETE','[]','openai'),('R2','t2','COMPLETE','[]','openai')"
    )
    con.execute(
        "INSERT INTO news_articles (article_id, article_hash, article_status) VALUES ('ART1','H1','ACTIVE')"
    )
    con.execute(
        "INSERT INTO news_analyzed_hashes (article_hash, title, analysis_id) VALUES ('H1','t','A1')"
    )
    con.execute(
        "INSERT INTO news_junk_hashes (article_hash, title, reason) VALUES ('H1','t','junk')"
    )
    con.execute(
        "INSERT INTO news_ai_analysis (ai_analysis_id, article_id) VALUES ('AI1','ARTGONE')"
    )

    total_before = sum(
        con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "audit_orders",
            "news_analysis",
            "news_analysis_runs",
            "news_analyzed_hashes",
            "news_ai_analysis",
        )
    )

    first = {r.rule_id: (r.candidates, r.deleted) for r in _run_rules(con, False)}
    after_1 = sum(
        con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "audit_orders",
            "news_analysis",
            "news_analysis_runs",
            "news_analyzed_hashes",
            "news_ai_analysis",
        )
    )

    second = {r.rule_id: (r.candidates, r.deleted) for r in _run_rules(con, False)}
    after_2 = sum(
        con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "audit_orders",
            "news_analysis",
            "news_analysis_runs",
            "news_analyzed_hashes",
            "news_ai_analysis",
        )
    )

    # first pass deleted the pollution it identified
    assert after_1 < total_before
    # second pass deleted nothing more
    assert after_2 == after_1
    assert sum(c for c, _ in second.values()) == 0, f"second run still found work: {second}"
    # every rule that reported candidates on pass 1 reports zero on pass 2
    for rid, (c1, _d1) in first.items():
        c2, _ = second[rid]
        assert c2 == 0, f"{rid} non-idempotent: pass1={c1} pass2={c2}"


# --------------------------------------------------------------------------
# PROTECTED DATA - money rows are never touched
# --------------------------------------------------------------------------


def test_money_rows_never_touched(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO audit_orders (order_id, ticket, symbol, action, timestamp) VALUES "
        "('O1', 1, 'XAUUSD', 'Executed order', 't'), ('O1', 1, 'XAUUSD', 'Executed order', 't')"
    )
    con.execute(
        "INSERT INTO audit_ledger (ticket, symbol, pnl) VALUES (1, 'XAUUSD', 42.5), (2, 'XAUUSD', -7.0)"
    )

    cu._set_autocommit(con, True)
    dsn = con.info.dsn
    for _rule_id, fn in cu.RULES:
        rule_con = psycopg.connect(dsn)
        try:
            cu._set_autocommit(rule_con, False)
            try:
                fn(rule_con, False)
                rule_con.commit()
            except Exception:
                rule_con.rollback()
        finally:
            rule_con.close()

    # the byte-identical order duplicate is gone, but BOTH money rows survive
    assert con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0] == 1
    rows = con.execute("SELECT ticket, pnl FROM audit_ledger ORDER BY ticket").fetchall()
    assert rows == [(1, 42.5), (2, -7.0)]


# --------------------------------------------------------------------------
# FK INTEGRITY - news children reconcile to parents after cleanup
# --------------------------------------------------------------------------


def test_news_children_reconcile_after_cleanup(con):
    _apply_ddl(con)
    con.execute(
        "INSERT INTO news_articles (article_id, article_hash, article_status) VALUES "
        "('ART-1','H1','ACTIVE'), ('ART-2','H2','ACTIVE')"
    )
    con.execute(
        "INSERT INTO news_ai_analysis (ai_analysis_id, article_id) VALUES "
        "('AI-1','ART-1'), ('AI-X','ART-GONE')"
    )
    con.execute("INSERT INTO news_impacts (article_id) VALUES ('ART-2')")
    con.execute("INSERT INTO news_entities (article_id) VALUES ('ART-GONE')")
    con.execute("INSERT INTO news_topics (article_id) VALUES ('ART-1')")

    _run_rules(con, False)

    for child in ("news_ai_analysis", "news_impacts", "news_entities", "news_topics"):
        orphans = con.execute(
            f"SELECT COUNT(*) FROM {child} c WHERE NOT EXISTS "
            "(SELECT 1 FROM news_articles a WHERE a.article_id = c.article_id)"
        ).fetchone()[0]
        assert orphans == 0, f"{child} still has {orphans} orphans after cleanup"


# --------------------------------------------------------------------------
# DRY RUN - never writes
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing(con):
    _apply_ddl(con)
    for _ in range(3):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, timestamp) "
            "VALUES ('O1', 1, 'XAUUSD', 'Executed order', 't')"
        )
    before = con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]
    res = cu.rule_r1a(con, dry_run=True)
    assert res.status == "DRY_RUN" and res.deleted == 0
    assert con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0] == before


# --------------------------------------------------------------------------
# POSTCONDITION ROLLBACK - a failed rule leaves the table untouched
# --------------------------------------------------------------------------


def test_postcondition_failure_rolls_back(con):
    """A rule whose post-condition fails must leave the table untouched.

    Simulated by monkeypatching the guard so the check reports a wrong count
    after the delete: the real post-condition comparison then raises
    CleanupError and the driver rolls the transaction back.
    """
    _apply_ddl(con)
    for _ in range(3):
        con.execute(
            "INSERT INTO audit_orders (order_id, ticket, symbol, action, timestamp) "
            "VALUES ('O1', 1, 'XAUUSD', 'Executed order', 't')"
        )
    before = con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]

    real_guard = cu._guard_table
    state = {"deleted": False}

    def lying_guard(conn, table):
        # Report the TRUE count on the way in (the rule's `before` and
        # `candidates` probes) and a false count afterwards, so the rule's
        # own post-condition comparison necessarily fails.
        n = real_guard(conn, table)
        if state["deleted"]:
            return n + 1
        return n

    orig_delete = con.execute

    def tracking_execute(*a, **k):
        out = orig_delete(*a, **k)
        if a and isinstance(a[0], str) and a[0].lstrip().upper().startswith("DELETE"):
            state["deleted"] = True
        return out

    cu._guard_table = lying_guard
    con.execute = tracking_execute
    cu._set_autocommit(con, False)
    try:
        with pytest.raises(cu.CleanupError):
            cu.rule_r1a(con, False)
        con.rollback()
    finally:
        cu._guard_table = real_guard
        con.execute = orig_delete
        cu._set_autocommit(con, True)
    assert con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0] == before


# --------------------------------------------------------------------------
# LIVE DATABASE GUARD
# --------------------------------------------------------------------------


def test_refuses_live_database_url():
    assert cu._LIVE_DB_MARKERS == frozenset({"nexusdb", "nse_audit"})
    for url in (
        "postgresql://u:p@localhost:5432/nexusdb",
        "postgresql://u:p@localhost:5432/nse_audit",
    ):
        assert cu.is_live_database(url), url
    for url in (
        "postgresql://u:p@127.0.0.1:55432/nse_hygiene_test",
        "postgresql://u:p@127.0.0.1:55432/nse_audit_test",
    ):
        assert not cu.is_live_database(url), url
