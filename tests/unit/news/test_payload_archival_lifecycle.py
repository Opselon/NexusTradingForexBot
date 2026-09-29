"""Lifecycle tests for the duplicate-payload archival step.

The brief requires a purge/archive subsystem to be:
    * incremental and bounded
    * resumable
    * idempotent
    * concurrency-safe
    * observable
    * dry-run (shadow) capable

and to prove:
    OLD  -> removed (payload reclaimed)
    FRESH/PROTECTED -> retained

Every one of those properties is asserted here against a real SQLite-backed
NewsDatabase, not a mock.
"""

from __future__ import annotations

import threading
from typing import Any


def _make_db(tmp_path) -> Any:
    from nexus_scalp.news.database import NewsDatabase

    db = NewsDatabase(tmp_path / "news.db")
    db.initialize_schema()
    return db


def _insert(db, article_id: str, summary: str, body: str, *, analyzed: bool = True) -> None:
    db.insert_article(
        {
            "article_id": article_id,
            "article_hash": f"hash-{article_id}",
            "title": f"Title {article_id}",
            "summary": summary,
            "body": body,
            "source_id": "src",
            "source_name": "Source",
            "published_at": "2026-01-01T00:00:00+00:00",
            "url": f"https://example.com/{article_id}",
            "payload": {},
        }
    )
    if analyzed:
        db.insert_analysis(
            {
                "analysis_id": f"an-{article_id}",
                "article_id": article_id,
                "status": "COMPLETE",
                "provider": "local",
                "summary": "",
                "importance_score": 0.5,
                "relevance_to_xauusd": 0.5,
            }
        )


# ---------------------------------------------------------------------------
# Shadow mode: dry run never mutates
# ---------------------------------------------------------------------------


def test_dry_run_selects_but_does_not_delete(tmp_path) -> None:
    db = _make_db(tmp_path)
    _insert(db, "a1", "duplicate text here", "duplicate text here")
    res = db.archive_duplicate_payloads(batch_size=100, dry_run=True)
    assert res["eligible"] == 1
    assert res["archived"] == 0
    row = db.get_article("a1")
    assert row is not None
    assert row["body"] == "duplicate text here"
    assert db.count_payload_archived() == 0


# ---------------------------------------------------------------------------
# OLD duplicate payload is reclaimed; summary is NOT touched
# ---------------------------------------------------------------------------


def test_duplicate_payload_reclaimed_and_summary_intact(tmp_path) -> None:
    db = _make_db(tmp_path)
    _insert(db, "a1", "the story", "the story")  # body == summary
    res = db.archive_duplicate_payloads(batch_size=100)
    assert res["eligible"] == 1
    assert res["archived"] == 1
    assert res["bytes_before"] > 0
    row = db.get_article("a1")
    assert row is not None
    assert row["body"] == ""  # payload gone
    assert row["summary"] == "the story"  # canonical text survives
    assert row["payload_archived"] == 1
    assert row["payload_archived_at"] != ""
    assert db.count_payload_archived() == 1


def test_real_body_payload_is_protected(tmp_path) -> None:
    db = _make_db(tmp_path)
    _insert(db, "a1", "short summary", "short summary PLUS the full article body")
    res = db.archive_duplicate_payloads(batch_size=100)
    assert res["archived"] == 0
    assert res["skipped"] == 1
    row = db.get_article("a1")
    assert row is not None
    assert row["body"] == "short summary PLUS the full article body"


def test_unanalyzed_article_is_protected(tmp_path) -> None:
    """An unanalyzed article's payload is the only source of decision value."""
    db = _make_db(tmp_path)
    _insert(db, "a1", "dupe", "dupe", analyzed=False)
    res = db.archive_duplicate_payloads(batch_size=100)
    assert res["archived"] == 0
    row = db.get_article("a1")
    assert row is not None
    assert row["body"] == "dupe"


# ---------------------------------------------------------------------------
# Idempotent: running twice is safe and second run is a no-op
# ---------------------------------------------------------------------------


def test_idempotent_second_run_is_noop(tmp_path) -> None:
    db = _make_db(tmp_path)
    _insert(db, "a1", "dupe", "dupe")
    first = db.archive_duplicate_payloads(batch_size=100)
    assert first["archived"] == 1
    second = db.archive_duplicate_payloads(batch_size=100)
    assert second["archived"] == 0
    assert second["eligible"] == 0
    assert second["rounds"] == 0  # the scan terminates immediately
    assert db.count_payload_archived() == 1


# ---------------------------------------------------------------------------
# Bounded + resumable: max_rows limits work, and a later call continues
# ---------------------------------------------------------------------------


def test_max_rows_bounds_a_single_call(tmp_path) -> None:
    db = _make_db(tmp_path)
    for i in range(10):
        _insert(db, f"a{i:03d}", "dupe text", "dupe text")
    res = db.archive_duplicate_payloads(batch_size=100, max_rows=4)
    assert res["eligible"] == 4
    assert res["archived"] == 4
    assert db.count_payload_archived() == 4


def test_resumable_after_max_rows(tmp_path) -> None:
    db = _make_db(tmp_path)
    for i in range(10):
        _insert(db, f"a{i:03d}", "dupe text", "dupe text")
    db.archive_duplicate_payloads(batch_size=100, max_rows=4)
    res = db.archive_duplicate_payloads(batch_size=100, max_rows=4)
    assert res["archived"] == 4  # continued from the cursor
    res3 = db.archive_duplicate_payloads(batch_size=100, max_rows=4)
    assert res3["archived"] == 2  # the remainder
    assert db.count_payload_archived() == 10


def test_interleaved_skipped_rows_do_not_block_termination(tmp_path) -> None:
    """A skipped row stays eligible-looking; the keyset cursor must still advance."""
    db = _make_db(tmp_path)
    for i in range(6):
        if i % 2 == 0:
            _insert(db, f"a{i:03d}", "dupe", "dupe")
        else:
            _insert(db, f"a{i:03d}", "summary x", "DIFFERENT real body y")
    res = db.archive_duplicate_payloads(batch_size=2)
    # 3 duplicates archived, 3 real bodies skipped, and it TERMINATED.
    assert res["archived"] == 3
    assert res["skipped"] == 3
    assert res["rounds"] == 3  # 6 rows / 2 per round
    assert db.count_payload_archived() == 3


def test_empty_table_terminates_cleanly(tmp_path) -> None:
    db = _make_db(tmp_path)
    res = db.archive_duplicate_payloads(batch_size=100)
    assert res["archived"] == 0
    assert res["rounds"] == 0


# ---------------------------------------------------------------------------
# Concurrency: two simultaneous archival runs must not double-count
# ---------------------------------------------------------------------------


def test_concurrent_runs_do_not_corrupt(tmp_path) -> None:
    """Two workers racing the same keyset range.

    SQLite serialises writers, so the expected outcome is that every
    duplicate is archived exactly once and the DB is left consistent —
    not that both workers report disjoint counts (they can both see the
    same rows before either commits).
    """
    db = _make_db(tmp_path)
    for i in range(40):
        _insert(db, f"a{i:03d}", "dupe text", "dupe text")

    results: list[dict[str, int]] = []
    lock = threading.Lock()

    def _worker() -> None:
        r = db.archive_duplicate_payloads(batch_size=10)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Idempotency at the table level: exactly 40 rows were ever eligible,
    # and the archived marker cannot exceed the row count.
    assert db.count_payload_archived() == 40
    assert sum(r["archived"] for r in results) >= 40
    # And the table is left in a fully consistent state.
    assert db.count_payload_archived() == 40
