"""News Intelligence Engine orchestrator (PHASE 12).

Wires sources -> ingest -> analysis -> memory -> context into one bounded,
failure-isolated subsystem. The engine itself holds NO execution capability:
no adapter, no order manager, no risk engine. It can never place a trade.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.news.analysis import NewsAnalysisPipeline
from nexus_scalp.news.config import NewsConfig
from nexus_scalp.news.context import NewsContextCache
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.ingest import NewsFetcher, NewsIngestor, NewsScheduler
from nexus_scalp.news.memory import PostEventValidator
from nexus_scalp.news.models import (
    CurrentNewsContext,
    NewsAnalysisResult,
    NewsArticle,
    NewsDirection,
    NewsNovelty,
    normalize_datetime,
)
from nexus_scalp.news.seed import seed_news_database
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.engine")


class NewsEngine:
    """Canonical News Intelligence Engine.

    Responsibilities:
        * seed + expose news.db,
        * drive one ingestion pass (due sources -> fetch -> dedup -> persist),
        * drive analysis for unanalyzed articles (bounded per cycle),
        * maintain the live CurrentNewsContext cache,
        * expose post-event validation + trade linkage,
        * self-heal derived state.
    """

    def __init__(self, config: NewsConfig | None = None, db: NewsDatabase | None = None) -> None:
        self.config = config or NewsConfig()

        repo_root = Path.cwd()
        db_path = self.config.resolve_db_path(repo_root)
        self.db = db or NewsDatabase(db_path)
        self.seed_result = seed_news_database(self.db)

        self.scheduler = NewsScheduler()
        self.fetcher = NewsFetcher(self.db, self.config)
        self.ingestor = NewsIngestor(self.db)
        self.pipeline = NewsAnalysisPipeline(self.db, self.config)
        self.context = NewsContextCache(self.db, self.config)
        self.validator = PostEventValidator(self.db)

        self.last_cycle_at: datetime | None = None
        self.cycle_count = 0
        self.last_error: str = ""
        self._stats: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Ingestion pass
    # ------------------------------------------------------------------

    def ingest_cycle(self, max_sources: int = 10) -> dict[str, Any]:
        """One bounded ingestion pass over due sources.

        Returns per-source stats. Failure-isolated: one broken source never
        stops the others.
        """
        sources = self.db.list_sources(enabled_only=True)
        due = self.scheduler.due_sources(sources)
        stats: dict[str, Any] = {"sources_polled": 0, "new": 0, "duplicate": 0, "merged": 0}
        for src in due[:max_sources]:
            self.scheduler.mark_polled(src["source_id"])
            result = self.fetcher.fetch_source(src)
            if not result.ok:
                stats["sources_polled"] += 1
                continue
            stats["sources_polled"] += 1
            ingested = self.ingestor.ingest_source_items(src, result)
            stats["new"] += ingested.get("new", 0)
            stats["duplicate"] += ingested.get("duplicate", 0)
            stats["merged"] += ingested.get("merged_evidence", 0)
        self._stats = dict(stats)
        return stats

    # ------------------------------------------------------------------
    # Analysis pass (bounded, priority-aware)
    # ------------------------------------------------------------------

    def analysis_cycle(self, limit: int = 20) -> list[NewsAnalysisResult]:
        """Analyzes recent unanalyzed articles (bounded)."""
        return self.pipeline.analyze_recent_unanalyzed(limit=limit)

    # ------------------------------------------------------------------
    # Context for the live path
    # ------------------------------------------------------------------

    def current_context(self, force: bool = False) -> CurrentNewsContext:
        """Returns the cached news context (safe defaults when unavailable).

        Cache-only on the live tick path: ``force=True`` rebuilds from the
        DB and is intended for the worker / API / self-heal paths only.
        """
        try:
            return self.context.get(force=force)
        except Exception as e:
            logger.error("[NEWS_CONTEXT] build failed (safe defaults)", error=str(e))
            return CurrentNewsContext(available=False, timestamp=datetime.now(UTC))

    # ------------------------------------------------------------------
    # Trade linkage
    # ------------------------------------------------------------------

    def link_trade(
        self,
        *,
        trade_id: str,
        article_id: str,
        strategy_id: str = "",
        model_version: str = "",
        alignment: float = 0.0,
    ) -> str | None:
        """Links a trade to the most relevant recent news event."""
        link_id = f"tlnk_{uuid.uuid4().hex[:12]}"
        self.db.insert_trade_link(
            {
                "link_id": link_id,
                "trade_id": str(trade_id),
                "article_id": article_id,
                "strategy_id": strategy_id,
                "model_version": model_version,
                "news_alignment": float(alignment),
                "linked_at": datetime.now(UTC).isoformat(),
            }
        )
        return link_id

    def link_trade_to_best_news(
        self, trade_id: str, strategy_id: str = "", model_version: str = ""
    ) -> str | None:
        """Links a trade to the current most relevant active news event."""
        ctx = self.current_context()
        if not ctx.available or not ctx.active_high_impact:
            # still link to the most recent high-relevance article if any
            candidates = self.db.list_analysis(limit=5)
            if not candidates:
                return None
            article_id = candidates[0]["article_id"]
        else:
            article_id = ctx.active_high_impact[0]
        return self.link_trade(
            trade_id=trade_id,
            article_id=article_id,
            strategy_id=strategy_id,
            model_version=model_version,
            alignment=ctx.news_adjustment,
        )

    # ------------------------------------------------------------------
    # Post-event validation
    # ------------------------------------------------------------------

    def record_market_response(
        self,
        *,
        article_id: str,
        response_samples: list[tuple[datetime, float]],
        regime: str = "",
    ) -> dict[str, Any] | None:
        analysis = self.db.get_analysis(article_id)
        if not analysis:
            return None
        try:
            direction = NewsDirection(str(analysis.get("direction", "NEUTRAL")).upper())
        except ValueError:
            direction = NewsDirection.NEUTRAL
        return self.validator.record_response(
            article_id=article_id,
            predicted_direction=direction,
            predicted_strength=float(analysis.get("impact_strength", 0.0) or 0.0),
            predicted_horizon=str(analysis.get("horizon", "MACRO")),
            response_samples=response_samples,
            regime=regime,
        )

    # ------------------------------------------------------------------
    # Manual analysis (AI Analyze button / API)
    # ------------------------------------------------------------------

    def analyze_article_id(self, article_id: str) -> dict[str, Any]:
        """Analyzes (or re-analyzes) one article by id. Returns a summary
        dict with the job status - never blocks waiting on external AI."""
        art = self.db.get_article(article_id)
        if not art:
            return {"ok": False, "error": "ARTICLE_NOT_FOUND"}
        try:
            article = NewsArticle(
                article_id=art["article_id"],
                article_hash=art["article_hash"],
                canonical_url=art.get("canonical_url", "") or "",
                title=art["title"],
                summary=art.get("summary", "") or "",
                body=art.get("body", "") or "",
                source_id=art.get("source_id", "") or "",
                source_name=art.get("source_name", "") or "",
                published_at=normalize_datetime(_parse(art.get("published_at"))),
                raw_categories=[],
                novelty=NewsNovelty.NEW,
            )
            result = self.pipeline.analyze_article(article)
            return {"ok": True, "analysis_id": result.analysis_id, "status": result.status.value}
        except Exception as e:
            logger.error("[NEWS_ANALYSIS] event=FAILED article_id=%s", article_id, error=str(e))
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Self-heal
    # ------------------------------------------------------------------

    def self_heal(self) -> dict[str, Any]:
        """Rebuilds derived state from raw articles + analysis payloads."""
        rebuilt = self.db.rebuild_derived()
        # re-derive the live context (forces a rebuild)
        self.context.refresh()
        logger.info("[NEWS_HEAL] event=REBUILD status=SUCCESS rebuilt=%s", rebuilt)
        return {"status": "SUCCESS", "rebuilt": rebuilt}

    # ------------------------------------------------------------------
    # Health / summary
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        try:
            ctx = self.current_context()
            return {
                # engine availability is about the SUBSYSTEM, not the
                # presence of analysis rows; context available reflects the
                # derived state (which is honestly False with no evidence).
                "available": True,
                "subsystem": "NEWS_INTELLIGENCE",
                "state": ctx.state.value,
                "stale": ctx.stale,
                "db": self.db.summary(),
                "cycle_count": self.cycle_count,
                "last_error": self.last_error,
                "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else "",
            }
        except Exception as e:
            return {"available": False, "error": str(e)}


def _parse(value: Any):
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value
