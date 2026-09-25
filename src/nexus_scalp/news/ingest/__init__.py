"""News ingest package (PHASE 12): fetcher/scheduler/normalizer/deduplicator."""

from nexus_scalp.news.ingest.deduplicator import (
    NewsDeduplicator,
    canonicalize_item,
    compute_article_hash,
    compute_title_hash,
    normalize_title,
    normalize_url,
)
from nexus_scalp.news.ingest.fetcher import NewsFetcher, NewsIngestor, NewsScheduler

__all__ = [
    "NewsDeduplicator",
    "NewsFetcher",
    "NewsIngestor",
    "NewsScheduler",
    "canonicalize_item",
    "compute_article_hash",
    "compute_title_hash",
    "normalize_title",
    "normalize_url",
]
