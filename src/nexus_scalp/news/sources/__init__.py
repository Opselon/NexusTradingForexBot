"""News source adapters package (PHASE 12)."""

from nexus_scalp.news.sources.base import (
    NewsSourceAdapter,
    OfficialSourceAdapter,
    RSSNewsSourceAdapter,
    SourceFetchResult,
    build_adapter,
)

__all__ = [
    "NewsSourceAdapter",
    "OfficialSourceAdapter",
    "RSSNewsSourceAdapter",
    "SourceFetchResult",
    "build_adapter",
]
