"""News analysis package (PHASE 12): local + optional external + pipeline."""

from nexus_scalp.news.analysis.consensus import compute_consensus
from nexus_scalp.news.analysis.decay import NewsDecayEngine
from nexus_scalp.news.analysis.keywords import (
    KeywordCoverage,
    KeywordDatasetSummary,
    NewsKeyword,
    analyze_keyword_coverage,
    categories,
    get_keyword,
    get_keyword_dataset,
    keyword_count,
    keyword_hits_for_article,
    keywords_by_category,
    pattern_cache_stats,
)
from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.analysis.pipeline import (
    DefaultExternalAnalyzer,
    ExternalNewsAnalyzer,
    NewsAnalysisPipeline,
)

__all__ = [
    "DefaultExternalAnalyzer",
    "ExternalNewsAnalyzer",
    "KeywordCoverage",
    "KeywordDatasetSummary",
    "LocalNewsAnalyzer",
    "NewsAnalysisPipeline",
    "NewsDecayEngine",
    "NewsKeyword",
    "analyze_keyword_coverage",
    "categories",
    "compute_consensus",
    "get_keyword",
    "get_keyword_dataset",
    "keyword_count",
    "keyword_hits_for_article",
    "keywords_by_category",
    "pattern_cache_stats",
]
