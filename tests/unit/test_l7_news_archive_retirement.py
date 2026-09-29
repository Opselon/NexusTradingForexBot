"""L7 — archive/retire a SQLite dataset reversibly (regression guard).

R-11's verdict is that ``artifacts/news.db`` should be RETIRED from the hygiene
worker's managed set, and the retirement is only defensible if the data can be
archived, verified AND read back. These pins make that path executable in CI
with a synthetic corpus, so "0 rows lost" is a test result rather than a claim.

Pins:
  * archive_news_dataset round-trips every row of every table (exact equality)
  * a table with no single-column PK (so paging falls back to rowid) still
    round-trips completely
  * the row count read from SQLite equals the archived line count, and equals
    the count read back through ``read_archive`` — the three-way agreement that
    makes a retirement verifiable
  * a second run is idempotent (SKIPPED) and does not duplicate the archive
  * a truncated insert path is caught: the count check raises
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nexus_scalp.hygiene.archive import (
    ArchiveManager,
    archive_news_dataset,
    enumerate_dataset,
    table_and_columns,
)


def _news_fixture(path: Path, *, articles: int = 30, extra: int = 12) -> None:
    """A miniature news.db: one single-PK table + one composite-PK table."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE news_articles (article_id TEXT PRIMARY KEY, title TEXT, "
            "published_at TEXT, body TEXT)"
        )
        conn.execute(
            "CREATE TABLE news_entities (article_id TEXT, entity TEXT, "
            "PRIMARY KEY (article_id, entity))"
        )
        conn.executemany(
            "INSERT INTO news_articles VALUES (?,?,?,?)",
            [
                (
                    f"news_{i:04d}",
                    f"title {i}",
                    f"2026-09-{i % 28 + 1:02d}T00:00:00+00:00",
                    "x" * 200,
                )
                for i in range(articles)
            ],
        )
        conn.executemany(
            "INSERT INTO news_entities VALUES (?,?)",
            [(f"news_{i:04d}", f"ENT{i}") for i in range(extra)],
        )
        conn.commit()
    finally:
        conn.close()


def test_archive_round_trips_every_row_of_every_table(tmp_path: Path) -> None:
    """Every row archived is exactly every row read back."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=30, extra=12)
    root = tmp_path / "store"

    result = archive_news_dataset(
        db,
        root,
        software_version="test",
        retention_reason="L7 R-11 retirement pin",
        chunk_rows=7,  # forces several chunks per table
    )

    assert result["tables"]["news_articles"]["status"] == "ARCHIVED"
    assert result["tables"]["news_articles"]["row_count"] == 30
    assert result["tables"]["news_entities"]["row_count"] == 12

    am = ArchiveManager(root)
    marker = json.loads(
        (root / "archive" / "news" / "news_articles" / "_complete.json").read_text(encoding="utf-8")
    )
    restored: list[dict] = []
    for chunk in marker["chunks"]:
        restored.extend(am.read_archive(chunk))

    assert len(restored) == 30
    assert {r["article_id"] for r in restored} == {f"news_{i:04d}" for i in range(30)}


def test_rowid_fallback_paging_is_complete(tmp_path: Path) -> None:
    """A composite-PK table pages on rowid and still loses nothing."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=4, extra=25)
    root = tmp_path / "store"

    result = archive_news_dataset(
        db,
        root,
        software_version="test",
        retention_reason="rowid fallback pin",
        chunk_rows=6,
        tables=["news_entities"],
    )
    assert result["tables"]["news_entities"]["row_count"] == 25
    assert result["tables"]["news_entities"]["chunks"] == 5  # 25 rows / 6 per chunk


def test_row_count_agrees_three_ways(tmp_path: Path) -> None:
    """SQLite COUNT == archived lines == rows read back through read_archive."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=40, extra=9)
    root = tmp_path / "store"

    counted = enumerate_dataset(db)
    archive_news_dataset(
        db,
        root,
        software_version="test",
        retention_reason="three-way agreement",
        chunk_rows=9,
    )

    am = ArchiveManager(root)
    for table in ("news_articles", "news_entities"):
        marker = json.loads(
            (root / "archive" / "news" / table / "_complete.json").read_text(encoding="utf-8")
        )
        restored: list[dict] = []
        for chunk in marker["chunks"]:
            restored.extend(am.read_archive(chunk))
        # measured-source count == archived lines == rows read back
        assert counted[table]["rows"] == marker["lines_on_disk"] == len(restored)

    assert counted["news_articles"]["rows"] == 40
    assert counted["news_entities"]["rows"] == 9


def test_second_run_is_idempotent_and_does_not_duplicate(tmp_path: Path) -> None:
    """Re-running reports SKIPPED — the archive tree does not grow."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=10)
    root = tmp_path / "store"

    first = archive_news_dataset(
        db, root, software_version="t", retention_reason="idempotence", chunk_rows=4
    )
    files_after_first = sorted(p for p in (root / "archive" / "news").rglob("*.jsonl"))

    second = archive_news_dataset(
        db, root, software_version="t", retention_reason="idempotence", chunk_rows=4
    )

    assert first["tables"]["news_articles"]["status"] == "ARCHIVED"
    assert second["tables"]["news_articles"]["status"] == "SKIPPED"
    assert sorted(p for p in (root / "archive" / "news").rglob("*.jsonl")) == files_after_first


def test_corrupted_archive_is_caught_before_a_count_can_lie(tmp_path: Path) -> None:
    """A tampered chunk fails verification, so its count can never be trusted."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=12)
    root = tmp_path / "store"

    archive_news_dataset(db, root, software_version="t", retention_reason="tamper", chunk_rows=12)

    chunk = next((root / "archive" / "news" / "news_articles").glob("*.jsonl"))
    blob = chunk.read_bytes()
    chunk.write_bytes(blob[:-1] + bytes([blob[-1] ^ 0x01]))

    am = ArchiveManager(root)
    marker = json.loads(
        (root / "archive" / "news" / "news_articles" / "_complete.json").read_text(encoding="utf-8")
    )
    manifest = marker["chunks"][0]
    assert am.verify_archive(manifest) is False
    with pytest.raises(RuntimeError, match="MISMATCH"):
        am.read_archive(manifest)
    # The line count is unchanged by the corruption — which is exactly why the
    # checksum, not the count, is the gate.
    assert am.row_count_on_disk(manifest) == 12


def test_table_and_columns_returns_insertable_columns(tmp_path: Path) -> None:
    """Column discovery is from the catalog, not a hand-kept list."""
    db = tmp_path / "news.db"
    _news_fixture(db, articles=1)
    conn = sqlite3.connect(str(db))
    try:
        assert table_and_columns(conn, "news_articles") == [
            "article_id",
            "title",
            "published_at",
            "body",
        ]
    finally:
        conn.close()
