"""Nexus News Intelligence Engine (PHASE 12).

A completely isolated, production-grade news intelligence subsystem for
XAUUSD / GOLD / USD / major FX markets. It collects, deduplicates, classifies,
analyzes, scores, ages, and correlates financial/news events with the existing
Nexus trading intelligence.

Isolation contract:
    * Dedicated `news.db` (never mixes with the trading audit ledger).
    * News never places, modifies or closes an order (no adapter, no order
      manager, no risk engine).
    * The News Worker runs concurrently via ``asyncio.to_thread`` and NEVER
      blocks the live tick path.
    * A News Engine failure can never stop trading.
"""

from nexus_scalp.news.config import NewsConfig
from nexus_scalp.news.context import NewsContextCache
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.engine import NewsEngine
from nexus_scalp.news.gate import NewsGate, NewsGateDecision, NewsGateVerdict
from nexus_scalp.news.models import (
    AssetImpactProfile,
    CurrentNewsContext,
    NewsAnalysisResult,
    NewsArticle,
    NewsConsensus,
    NewsDirection,
    NewsEntity,
    NewsImpact,
    NewsImpactHorizon,
    NewsImportance,
    NewsSource,
    NewsSourceHealth,
    NewsState,
    NewsTopic,
    NewsWorkerState,
    TradeNewsLink,
)
from nexus_scalp.news.seed import seed_news_database
from nexus_scalp.news.worker import NewsWorker, format_news_worker_status

__all__ = [
    "AssetImpactProfile",
    "CurrentNewsContext",
    "NewsAnalysisResult",
    "NewsArticle",
    "NewsConfig",
    "NewsConsensus",
    "NewsContextCache",
    "NewsDatabase",
    "NewsDirection",
    "NewsEngine",
    "NewsEntity",
    "NewsGate",
    "NewsGateDecision",
    "NewsGateVerdict",
    "NewsImpact",
    "NewsImpactHorizon",
    "NewsImportance",
    "NewsSource",
    "NewsSourceHealth",
    "NewsState",
    "NewsTopic",
    "NewsWorker",
    "NewsWorkerState",
    "TradeNewsLink",
    "format_news_worker_status",
    "seed_news_database",
]
