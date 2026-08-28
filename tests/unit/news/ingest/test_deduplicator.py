from datetime import UTC, datetime

from nexus_scalp.news.ingest.deduplicator import compute_article_hash


def test_compute_article_hash_deterministic():
    """Ensure identical inputs yield the same deterministic hash."""
    dt = datetime(2023, 10, 10, 12, 0, 0, tzinfo=UTC)
    hash1 = compute_article_hash(
        url="https://example.com/news/123",
        title="BREAKING: Market Hits All-Time High!",
        source_id="news_source_1",
        published_at=dt,
        summary="A brief summary of the market.",
    )
    hash2 = compute_article_hash(
        url="https://example.com/news/123",
        title="BREAKING: Market Hits All-Time High!",
        source_id="news_source_1",
        published_at=dt,
        summary="A brief summary of the market.",
    )
    assert hash1 == hash2


def test_compute_article_hash_time_bucketing():
    """Ensure times within the same 60-second bucket yield the same hash."""
    # 12:00:10 and 12:00:45 are in the same minute bucket
    dt1 = datetime(2023, 10, 10, 12, 0, 10, tzinfo=UTC)
    dt2 = datetime(2023, 10, 10, 12, 0, 45, tzinfo=UTC)

    hash1 = compute_article_hash(
        url="https://example.com/news/123",
        title="Market Update",
        source_id="src1",
        published_at=dt1,
    )
    hash2 = compute_article_hash(
        url="https://example.com/news/123",
        title="Market Update",
        source_id="src1",
        published_at=dt2,
    )
    assert hash1 == hash2

    # 12:01:10 is in a different minute bucket
    dt3 = datetime(2023, 10, 10, 12, 1, 10, tzinfo=UTC)
    hash3 = compute_article_hash(
        url="https://example.com/news/123",
        title="Market Update",
        source_id="src1",
        published_at=dt3,
    )
    assert hash1 != hash3


def test_compute_article_hash_tz_naive():
    """Ensure timezone-naive datetimes are treated as UTC."""
    dt_naive = datetime(2023, 10, 10, 12, 0, 0)
    dt_aware = datetime(2023, 10, 10, 12, 0, 0, tzinfo=UTC)

    hash_naive = compute_article_hash(
        url="https://example.com/news/123",
        title="Market Update",
        source_id="src1",
        published_at=dt_naive,
    )
    hash_aware = compute_article_hash(
        url="https://example.com/news/123",
        title="Market Update",
        source_id="src1",
        published_at=dt_aware,
    )
    assert hash_naive == hash_aware


def test_compute_article_hash_sensitivity():
    """Ensure changes to different parameters produce different hashes."""
    base_kwargs = {
        "url": "https://example.com/news/123",
        "title": "Market Update",
        "source_id": "src1",
        "published_at": datetime(2023, 10, 10, 12, 0, 0, tzinfo=UTC),
        "summary": "Summary text",
    }
    base_hash = compute_article_hash(**base_kwargs)

    # Change URL
    kwargs_url = base_kwargs.copy()
    kwargs_url["url"] = "https://example.com/news/456"
    assert compute_article_hash(**kwargs_url) != base_hash

    # Change Title (meaningful change)
    kwargs_title = base_kwargs.copy()
    kwargs_title["title"] = "Economic Downturn"
    assert compute_article_hash(**kwargs_title) != base_hash

    # Change Source ID
    kwargs_source = base_kwargs.copy()
    kwargs_source["source_id"] = "src2"
    assert compute_article_hash(**kwargs_source) != base_hash

    # Change Summary
    kwargs_summary = base_kwargs.copy()
    kwargs_summary["summary"] = "Different summary"
    assert compute_article_hash(**kwargs_summary) != base_hash
