"""BUG-282 regression net — RSS timestamp parsing + poll-stable identity.

Wave 2026-09-14 (lanes 04 RC2/RC3 + 06 §3, VERIFIED): RSS 2.0 ``pubDate`` is
RFC-822, but the parser only accepted ISO-8601 and SILENTLY FABRICATED
``now()`` on failure. 88.1% of the 28,446 production rows carry an ingest-time
stamp; because ``compute_article_hash`` mixes a 60-second publish bucket, every
re-poll re-minted a fresh identity for the same story (Bank of England: 11,086
rows for 53 URLs), so the article-hash UNIQUE guard and both tombstone tables
could never fire — 26,161 byte-identical rows, ~80 MB of pure duplication.

Pins (RED-before behavior in comments):
  * RFC-822 pubDate parses to the TRUE event time (RED: -> now()).
  * Unparseable/missing time returns None / INGEST_TIME provenance
    (RED: silent wall-clock fabrication).
  * Identity WITHOUT a real publish time is poll-stable
    (RED: fresh hash every 60-s bucket).
  * Identity WITH a real publish time keeps the 60-s merge bucket (unchanged).
  * insert_article persists published_at_source; legacy DBs heal idempotently.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.ingest.deduplicator import (
    canonicalize_item,
    compute_article_hash,
)
from nexus_scalp.news.models import (
    PUBLISHED_AT_SOURCE_FEED,
    PUBLISHED_AT_SOURCE_INGEST,
)
from nexus_scalp.news.sources.base import RSSNewsSourceAdapter, _utc_now

_PARSE = RSSNewsSourceAdapter._parse_dt


# ---------------------------------------------------------------- RFC-822


def test_rfc822_gmt_variants_parse_to_true_event_time():
    # RED-before: all three fell through to now().
    expected = datetime(2026, 9, 14, 0, 5, tzinfo=UTC)
    for raw in (
        "Mon, 14 Sep 2026 00:05:00 GMT",
        "Sun, 13 Sep 2026 20:05:00 -0400",  # same instant, ET
        "Mon, 14 Sep 2026 02:05:00 +0200",  # same instant, CEST
    ):
        parsed = _PARSE(raw)
        assert parsed is not None, raw
        assert parsed == expected, raw


def test_rfc822_named_zone_est_parses():
    parsed = _PARSE("Wed, 09 Sep 2026 14:00:00 EST")
    assert parsed is not None
    assert parsed == datetime(2026, 9, 9, 19, 0, tzinfo=UTC)


def test_iso8601_paths_unchanged():
    assert _PARSE("2026-09-13T20:36:30.727139Z") == datetime(
        2026, 9, 13, 20, 36, 30, 727139, tzinfo=UTC
    )
    aware = datetime(2026, 9, 14, 1, 2, tzinfo=UTC)
    assert _PARSE(aware) == aware


def test_unparseable_is_none_not_now():
    # RED-before: returned a fabricated now(UTC) with microseconds.
    before = _utc_now()
    assert _PARSE("definitely not a date") is None
    assert _PARSE("") is None
    assert _PARSE(None) is None
    assert _utc_now() >= before


# ------------------------------------------------------------- identity


def test_hash_omits_time_bucket_when_no_real_publish_time():
    # RED-before: two calls seconds apart produced DIFFERENT hashes because
    # the fabricated now() crossed 60-s buckets; here identity must be stable.
    h1 = compute_article_hash(
        url="https://example.com/story",
        title="BoE Statistics Notice",
        source_id="boe",
        published_at=None,
        summary="same text",
        body="same text",
    )
    h2 = compute_article_hash(
        url="https://example.com/story",
        title="BoE Statistics Notice",
        source_id="boe",
        published_at=None,
        summary="same text",
        body="same text",
    )
    assert h1 == h2


def test_hash_still_merges_60s_bucket_with_real_time():
    dt1 = datetime(2026, 10, 10, 12, 0, 10, tzinfo=UTC)
    dt2 = datetime(2026, 10, 10, 12, 0, 45, tzinfo=UTC)
    dt3 = datetime(2026, 10, 10, 12, 1, 10, tzinfo=UTC)
    common = dict(url="https://example.com/n/1", title="T", source_id="s")
    assert compute_article_hash(published_at=dt1, **common) == compute_article_hash(
        published_at=dt2, **common
    )
    assert compute_article_hash(published_at=dt1, **common) != compute_article_hash(
        published_at=dt3, **common
    )


def test_hash_with_time_differs_from_timeless_identity():
    common = dict(url="https://example.com/n/1", title="T", source_id="s")
    timed = compute_article_hash(
        published_at=datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC), **common
    )
    timeless = compute_article_hash(published_at=None, **common)
    assert timed != timeless


def test_canonicalize_item_stamps_provenance():
    fed = canonicalize_item(
        {
            "title": "Gold rallies",
            "url": "https://x/1",
            "published_at": "Mon, 14 Sep 2026 00:05:00 GMT",
        },
        source_id="fx",
        source_name="Fx",
    )
    assert fed["published_at_source"] == PUBLISHED_AT_SOURCE_FEED
    assert fed["published_at"] == datetime(2026, 9, 14, 0, 5, tzinfo=UTC)

    now_fab = canonicalize_item(
        {"title": "No date story", "url": "https://x/2", "published_at": None},
        source_id="fx",
        source_name="Fx",
    )
    assert now_fab["published_at"] is None
    assert now_fab["published_at_source"] == PUBLISHED_AT_SOURCE_INGEST


# ---------------------------------------------------------------- persistence


def test_insert_article_persists_provenance_and_schema_heals(tmp_path):
    db = NewsDatabase(tmp_path / "news.db")
    db.initialize_schema()
    cols = {r[1] for r in db._connect().execute("PRAGMA table_info(news_articles)").fetchall()}
    assert "published_at_source" in cols

    db.insert_article(
        {
            "article_id": "a1",
            "article_hash": "h1",
            "title": "t",
            "published_at": "2026-09-14T00:05:00+00:00",
            "published_at_source": PUBLISHED_AT_SOURCE_FEED,
        }
    )
    db.insert_article({"article_id": "a2", "article_hash": "h2", "title": "t2"})
    rows = {
        r["article_id"]: r["published_at_source"]
        for r in db._connect()
        .execute("SELECT article_id, published_at_source FROM news_articles")
        .fetchall()
    }
    assert rows["a1"] == PUBLISHED_AT_SOURCE_FEED
    # Missing provenance on a legacy-style dict must not raise or fake FEED.
    assert rows["a2"] == "UNKNOWN"
    # Idempotent re-init (the ALTER path must not fail on an already-migrated DB).
    db.initialize_schema()


def test_legacy_db_without_column_gets_unknown_backfill(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE news_articles (
            article_id TEXT PRIMARY KEY,
            article_hash TEXT UNIQUE NOT NULL,
            canonical_url TEXT DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            body TEXT DEFAULT '',
            language TEXT DEFAULT 'en',
            source_id TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            published_at TEXT NOT NULL,
            updated_at TEXT DEFAULT '',
            raw_categories TEXT DEFAULT '[]',
            entities TEXT DEFAULT '[]',
            topics TEXT DEFAULT '[]',
            importance TEXT DEFAULT 'MINOR',
            importance_score REAL NOT NULL DEFAULT 0.0,
            novelty TEXT DEFAULT 'NEW',
            is_duplicate INTEGER NOT NULL DEFAULT 0,
            duplicate_of TEXT DEFAULT '',
            evidence_sources TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO news_articles (article_id, article_hash, title, published_at, created_at) "
        "VALUES ('old', 'oh', 't', '2026-09-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db = NewsDatabase(path)
    db.initialize_schema()
    row = (
        db._connect()
        .execute("SELECT published_at_source FROM news_articles WHERE article_id='old'")
        .fetchone()
    )
    assert row[0] == "UNKNOWN"
