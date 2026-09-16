"""
Tests for BYTE_IDENTICAL_DUPLICATE hygiene detection in news_articles.

BUG-302 (NSE-Swarm role 5, Data/Ingestion): the existing DuplicateDetector
was blind to the dominant news duplication mode — byte-identical re-ingest
churn from hash instability (26,161 redundant rows, ~80 MB in production
news.db). The new BYTE_IDENTICAL_DUPLICATE class detects rows with identical
(canonical_url, title, body, summary) tuples, picking the earliest rowid as
the canonical row, and is registered in SAFE_CLEAN_CLASSES for operator-gated
archive-then-delete execution.

Covers: detection, SAFE_CLEAN archival+delete, canonical-missing safety,
graceful degradation on missing columns, and body-difference exclusion.
"""

import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.hygiene import Confidence, WorkerMode
from nexus_scalp.hygiene.detectors import DuplicateCandidate, DuplicateDetector
from nexus_scalp.hygiene.worker import SAFE_CLEAN_CLASSES
from nexus_scalp.hygiene.worker_runner import DatabaseHygieneWorker


@pytest.fixture
def hygiene_env(tmp_path: Path):
    """Sets up a test environment with a news database populated with byte-identical duplicates."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    news_db = artifacts / "news.db"
    
    conn = sqlite3.connect(str(news_db))
    conn.executescript(
        """
        CREATE TABLE news_articles (
            article_id TEXT PRIMARY KEY,
            article_hash TEXT UNIQUE NOT NULL,
            canonical_url TEXT DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            body TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            duplicate_of TEXT DEFAULT ''
        );
        """
    )
    
    # 1. Canonical story (min rowid for the Gold Price group)
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('A', 'hashA', 'http://example.com/1', 'Gold Price', 'Summary1', 'Body1', 'SRC1')"
    )
    # 2. Byte-identical duplicate of A
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('B', 'hashB', 'http://example.com/1', 'Gold Price', 'Summary1', 'Body1', 'SRC1')"
    )
    # 3. Another byte-identical duplicate of A
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('C', 'hashC', 'http://example.com/1', 'Gold Price', 'Summary1', 'Body1', 'SRC1')"
    )
    # 4. Same URL and title, but DIFFERENT body (NOT a byte-identical duplicate!)
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('D', 'hashD', 'http://example.com/1', 'Gold Price', 'Summary1', 'Different Body!', 'SRC1')"
    )
    # 5. Empty URL and title (should NOT be matched)
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('E1', 'hashE1', '', '', '', '', 'SRC2')"
    )
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id) "
        "VALUES ('E2', 'hashE2', '', '', '', '', 'SRC2')"
    )
    # 6. Legacy DUPLICATE_WITH_CANONICAL (flagged)
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id, is_duplicate, duplicate_of) "
        "VALUES ('F1', 'hashF1', 'http://example.com/2', 'Silver Price', 'Sum2', 'Body2', 'SRC3', 0, '')"
    )
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, canonical_url, title, summary, body, source_id, is_duplicate, duplicate_of) "
        "VALUES ('F2', 'hashF2', 'http://example.com/3', 'Silver Price (dup)', 'Sum2', 'Body2', 'SRC3', 1, 'hashF1')"
    )
    
    conn.commit()
    conn.close()
    return tmp_path, news_db


# -----------------------------------------------------------------------
# Unit-level detector tests
# -----------------------------------------------------------------------
def test_detector_finds_byte_identical_duplicates(hygiene_env):
    """The detector must find B and C as byte-identical duplicates of A."""
    _, news_db = hygiene_env
    conn = sqlite3.connect(str(news_db))
    det = DuplicateDetector()
    found = det.scan_news(conn)
    conn.close()

    by_id = {c.row_id: c for c in found}
    # B and C are byte-identical to A
    assert "B" in by_id
    assert "C" in by_id
    assert by_id["B"].canonical_row_id == "A"
    assert by_id["C"].canonical_row_id == "A"
    assert by_id["B"].cleanup_class == "BYTE_IDENTICAL_DUPLICATE"
    assert by_id["C"].confidence == Confidence.EXACT_DUPLICATE
    # D is NOT a duplicate (different body)
    assert "D" not in by_id
    # E2 is NOT a duplicate (empty url/title excluded)
    assert "E2" not in by_id
    # F2 is flagged DUPLICATE_WITH_CANONICAL
    assert "F2" in by_id
    assert by_id["F2"].cleanup_class == "DUPLICATE_WITH_CANONICAL"


def test_byte_identical_class_registered_safe():
    """BYTE_IDENTICAL_DUPLICATE must be in the SAFE_CLEAN_CLASSES allowlist."""
    assert "BYTE_IDENTICAL_DUPLICATE" in SAFE_CLEAN_CLASSES


# -----------------------------------------------------------------------
# Integration tests via DatabaseHygieneWorker
# -----------------------------------------------------------------------
def test_dry_run_detects_but_does_not_delete(hygiene_env):
    """DRY_RUN reports duplicate count but performs no mutations."""
    tmp_path, news_db = hygiene_env
    worker = DatabaseHygieneWorker(repo_root=tmp_path, mode=WorkerMode.DRY_RUN)
    res = worker.run_cycle(["news"])
    db_res = res["databases"]["news"]
    # B, C, F2 = 3 duplicates
    assert db_res["duplicates_found"] == 3
    # Nothing deleted in DRY_RUN
    assert db_res.get("deleted", {}) == {}
    # DB still has all 8 rows
    conn = sqlite3.connect(str(news_db))
    count = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
    conn.close()
    assert count == 8


def test_safe_clean_archives_and_deletes(hygiene_env):
    """SAFE_CLEAN archives duplicate rows then deletes them."""
    tmp_path, news_db = hygiene_env
    worker = DatabaseHygieneWorker(
        repo_root=tmp_path,
        mode=WorkerMode.SAFE_CLEAN,
        apply_deletes=True
    )
    res = worker.run_cycle(["news"])
    db_res = res["databases"]["news"]
    
    assert db_res["archived"].get("news_articles", 0) == 3
    assert db_res["deleted"].get("news_articles", 0) == 3
    
    conn = sqlite3.connect(str(news_db))
    remaining = {r[0] for r in conn.execute("SELECT article_id FROM news_articles").fetchall()}
    conn.close()
    
    assert "A" in remaining   # canonical kept
    assert "B" not in remaining  # byte-identical dup deleted
    assert "C" not in remaining  # byte-identical dup deleted
    assert "D" in remaining   # different body kept
    assert "E1" in remaining  # empty kept
    assert "E2" in remaining  # empty kept
    assert "F1" in remaining  # canonical kept
    assert "F2" not in remaining  # flagged dup deleted


def test_canonical_missing_blocks_delete(hygiene_env):
    """If the canonical row is deleted between plan and execute, the duplicate SURVIVES."""
    tmp_path, news_db = hygiene_env
    worker = DatabaseHygieneWorker(
        repo_root=tmp_path,
        mode=WorkerMode.SAFE_CLEAN,
        apply_deletes=True
    )
    # Build plan while A exists
    conn_ro = sqlite3.connect(f"file:{news_db}?mode=ro", uri=True)
    plan = worker.planner.build_plan("news", conn_ro)
    conn_ro.close()
    
    # Now kill canonical row A
    conn = sqlite3.connect(str(news_db))
    conn.execute("DELETE FROM news_articles WHERE article_id = 'A'")
    conn.commit()
    
    worker.executor.apply_plan("news", str(news_db), plan, "run-1", apply_deletes=True)
    
    remaining = {r[0] for r in conn.execute("SELECT article_id FROM news_articles").fetchall()}
    conn.close()
    
    # B and C survive because their canonical A is gone
    assert "B" in remaining
    assert "C" in remaining


def test_missing_columns_graceful(tmp_path: Path):
    """Detector does not crash if canonical_url or body columns are missing."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    news_db = artifacts / "news.db"
    
    conn = sqlite3.connect(str(news_db))
    conn.executescript(
        """
        CREATE TABLE news_articles (
            article_id TEXT PRIMARY KEY,
            article_hash TEXT UNIQUE NOT NULL
        );
        INSERT INTO news_articles VALUES ('X', 'hashX');
        """
    )
    conn.close()
    
    worker = DatabaseHygieneWorker(repo_root=tmp_path, mode=WorkerMode.DRY_RUN)
    res = worker.run_cycle(["news"])
    assert res["databases"]["news"]["duplicates_found"] == 0
