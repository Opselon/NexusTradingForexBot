"""News analysis package (PHASE 12): local + optional external + pipeline."""

from nexus_scalp.news.analysis.consensus import compute_consensus
from nexus_scalp.news.analysis.decay import NewsDecayEngine
from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.analysis.pipeline import (
    DefaultExternalAnalyzer,
    ExternalNewsAnalyzer,
    NewsAnalysisPipeline,
)

__all__ = [
    "DefaultExternalAnalyzer",
    "ExternalNewsAnalyzer",
    "LocalNewsAnalyzer",
    "NewsAnalysisPipeline",
    "NewsDecayEngine",
    "compute_consensus",
]
