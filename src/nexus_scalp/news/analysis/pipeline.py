"""News analysis pipeline orchestrator (PHASE 12).

Multi-stage agentic pipeline:

    STAGE 1  ingestion / normalization (done by ingest layer)
    STAGE 2  deduplication / canonicalization (done by ingest layer)
    STAGE 3  local relevance filtering
    STAGE 4  local importance scoring
    STAGE 5  local entity/topic extraction
    STAGE 6  local market-impact hypothesis
    STAGE 7  external AI deep analysis ONLY when warranted
    STAGE 8  independent validation / consistency check
    STAGE 9  final News Intelligence Record (persisted)
    STAGE 10 market/strategy integration (via NewsContext cache)

External AI is OPTIONAL and never mandatory: LOCAL analysis remains
authoritative fallback on missing key / rate limit / timeout / malformed
response / quota exhausted. HYBRID mode (default) routes to the API only
for important/ambiguous/high-relevance events and continues locally when
rate-limited.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.analysis.decay import NewsDecayEngine
from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import (
    NewsAnalysisResult,
    NewsAnalysisStatus,
    NewsDirection,
    NewsImpactHorizon,
    NewsImportance,
    NewsNovelty,
    normalize_datetime,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.analysis")


class ExternalNewsAnalyzer:
    """External AI provider interface (optional enhancement).

    Implementations must NEVER raise into the pipeline: every failure path
    returns None so the local analysis remains authoritative.
    """

    provider_name: str = "external"
    api_base_url: str = ""
    model: str = ""

    def __init__(self, config: Any | None = None) -> None:
        self.config = config

    def available(self) -> bool:
        """True when the provider is configured and usable."""
        return False

    def analyze(self, article: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        """Deep analysis of one canonical article.

        Returns a dict matching the strict AI response schema, or None on
        ANY failure (missing key, rate limit, timeout, malformed, quota).
        """
        return None


class DefaultExternalAnalyzer(ExternalNewsAnalyzer):
    """OpenAI-compatible HTTP provider (config-driven, optional).

    Uses httpx synchronously - the pipeline runs off the tick path in the
    News Worker, so blocking here is safe by construction.
    """

    provider_name = "openai-compatible"

    def available(self) -> bool:
        cfg = self.config
        if not cfg:
            return False
        base = getattr(cfg, "api_base_url", "") or ""
        model = getattr(cfg, "model", "") or ""
        key = getattr(cfg, "api_key", "") or ""
        return bool(base and model and key)

    def analyze(self, article: Any, context: dict[str, Any]) -> dict[str, Any] | None:
        if not self.available():
            return None
        try:
            import httpx

            cfg = self.config
            payload = self._build_payload(article, context)
            headers = {
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            }
            url = f"{cfg.api_base_url.rstrip('/')}/chat/completions"
            timeout = float(getattr(cfg, "request_timeout_sec", 20.0))
            resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    "[NEWS_ANALYSIS] event=API_FALLBACK reason=HTTP_%d provider=%s",
                    resp.status_code,
                    self.provider_name,
                )
                return None
            if resp.status_code != 200:
                logger.warning(
                    "[NEWS_ANALYSIS] event=API_FALLBACK reason=HTTP_%d provider=%s",
                    resp.status_code,
                    self.provider_name,
                )
                return None
            return self._parse_response(resp.json())
        except Exception as e:
            logger.warning(
                "[NEWS_ANALYSIS] event=API_FALLBACK reason=%s provider=%s",
                type(e).__name__,
                self.provider_name,
            )
            return None

    def _build_payload(self, article: Any, context: dict[str, Any]) -> dict[str, Any]:
        system = (
            "You are the news intelligence analyst for a gold/FX quantitative "
            "trading system. Return STRICT JSON only, matching the required "
            "schema. Do not invent facts. Mark uncertainty explicitly."
        )
        user = {
            "title": getattr(article, "title", ""),
            "summary": getattr(article, "summary", ""),
            "body": getattr(article, "body", "")[:2000],
            "source": context.get("source_name", ""),
            "published": context.get("published_at", ""),
            "known_entities": [e.name for e in getattr(article, "entities", [])],
            "known_topics": [t.value for t in getattr(article, "topics", [])],
            "local_direction": context.get("local_direction", "NEUTRAL"),
            "local_importance": context.get("local_importance", 0.0),
        }
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": _json_dumps(user)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

    def _parse_response(self, data: Any) -> dict[str, Any] | None:
        try:
            content = data["choices"][0]["message"]["content"]
            parsed = __import__("json").loads(content)
            if not isinstance(parsed, dict):
                return None
            return parsed
        except Exception:
            return None


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)


class NewsAnalysisPipeline:
    """Drives the multi-stage analysis for one or many articles."""

    def __init__(
        self,
        db: NewsDatabase,
        config: Any | None = None,
        local: LocalNewsAnalyzer | None = None,
        external: ExternalNewsAnalyzer | None = None,
        decay: NewsDecayEngine | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.local = local or LocalNewsAnalyzer(config)
        self.external = external or DefaultExternalAnalyzer(getattr(config, "analysis", None))
        self.decay = decay or NewsDecayEngine(getattr(config, "decay", None))

    # ------------------------------------------------------------------
    # Per-article analysis
    # ------------------------------------------------------------------

    def analyze_article(self, article: Any, *, force: bool = False) -> NewsAnalysisResult:
        """Runs the full staged pipeline for one canonical article.

        Idempotent: if this article_hash was already analyzed, returns a cached
        SKIPPED result without overwriting the existing analysis (prevents
        re-analysis confusion when the same story re-enters via filters).
        Pass force=True for explicit user-requested re-analysis (bypasses tombstone).
        """
        # Idempotent guard before allocating a new run_id/analysis_id
        if not force:
            try:
                ah = str(
                    getattr(article, "article_hash", "")
                    or getattr(article, "articleHash", "")
                    or ""
                )
                aid = str(getattr(article, "article_id", "") or "")
                if ah and self.db.is_analyzed_hash(ah):
                    logger.info(
                        "[NEWS_ANALYSIS] event=SKIP_ALREADY_ANALYZED article_id=%s hash=%s",
                        aid,
                        ah[:12],
                    )
                    # Idempotent re-entry: mirror the stored analysis instead of
                    # re-running the pipeline or overwriting the original result.
                    existing = self.db.get_analysis(aid) if aid else None
                    if existing:
                        # Build a result mirroring the stored one so callers get truthful ids
                        from nexus_scalp.news.models import NewsAnalysisStatus as _St

                        return NewsAnalysisResult(
                            analysis_id=str(existing.get("analysis_id", "")),
                            article_id=aid,
                            run_id=str(existing.get("run_id", "")),
                            status=_St.COMPLETE,
                            local_only=bool(existing.get("local_only", 1)),
                            provider=str(existing.get("provider", "local")),
                            summary=str(existing.get("summary", "")),
                            entities=[],
                            topics=[],
                            direction=NewsDirection.NEUTRAL,
                            impact_strength=float(existing.get("impact_strength", 0) or 0),
                            confidence=float(existing.get("confidence", 0) or 0),
                            horizon=NewsImpactHorizon.MACRO,
                            importance=existing.get("importance", "MINOR"),
                            importance_score=float(existing.get("importance_score", 0) or 0),
                            relevance_to_xauusd=float(existing.get("relevance_to_xauusd", 0) or 0),
                            relevance_to_usd=float(existing.get("relevance_to_usd", 0) or 0),
                            impacts=[],
                            novelty=NewsNovelty.NEW,
                        )
                    # Hash known but no row for this article_id (re-ingested under new id): block re-analysis
                    return NewsAnalysisResult(
                        analysis_id=f"skip_{aid or ah[:8]}",
                        article_id=aid or ah,
                        run_id="skip",
                        status=NewsAnalysisStatus.COMPLETE,
                        local_only=True,
                        provider="skip",
                        summary="SKIPPED_ALREADY_ANALYZED",
                        entities=[],
                        topics=[],
                        direction=NewsDirection.NEUTRAL,
                        impact_strength=0.0,
                        confidence=0.0,
                        horizon=NewsImpactHorizon.MACRO,
                        importance=NewsImportance.MINOR,
                        importance_score=0.0,
                        relevance_to_xauusd=0.0,
                        relevance_to_usd=0.0,
                        impacts=[],
                        novelty=NewsNovelty.NEW,
                    )
                if aid and self.db.get_analysis(aid) is not None:
                    existing2 = self.db.get_analysis(aid) or {}
                    if ah:
                        with contextlib.suppress(Exception):
                            self.db.remember_analyzed_hash(
                                ah,
                                title=str(getattr(article, "title", "")),
                                analysis_id=str(existing2.get("analysis_id", "")),
                            )
                    logger.info("[NEWS_ANALYSIS] event=SKIP_ALREADY_ANALYZED article_id=%s", aid)
                    return NewsAnalysisResult(
                        analysis_id=str(existing2.get("analysis_id", "")),
                        article_id=aid,
                        run_id=str(existing2.get("run_id", "")),
                        status=NewsAnalysisStatus.COMPLETE,
                        local_only=bool(existing2.get("local_only", 1)),
                        provider=str(existing2.get("provider", "local")),
                        summary=str(existing2.get("summary", "")),
                        entities=[],
                        topics=[],
                        direction=NewsDirection.NEUTRAL,
                        impact_strength=float(existing2.get("impact_strength", 0) or 0),
                        confidence=float(existing2.get("confidence", 0) or 0),
                        horizon=NewsImpactHorizon.MACRO,
                        importance=existing2.get("importance", "MINOR"),
                        importance_score=float(existing2.get("importance_score", 0) or 0),
                        relevance_to_xauusd=float(existing2.get("relevance_to_xauusd", 0) or 0),
                        relevance_to_usd=float(existing2.get("relevance_to_usd", 0) or 0),
                        impacts=[],
                        novelty=NewsNovelty.NEW,
                    )
            except Exception:
                pass
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        analysis_id = f"ana_{uuid.uuid4().hex[:12]}"
        self.db.start_run(run_id)

        # STAGE 3-6: local analysis
        entities = self.local.extract_entities(article)
        topics = self.local.classify_topics(article, entities)
        direction, impacts = self.local.directional_hypothesis(article, entities, topics)
        source_priority = 0.5
        source = self.db.get_source(getattr(article, "source_id", ""))
        if source:
            source_priority = float(source.get("priority", 0.5))
        importance_score, importance = self.local.importance_score(article, topics, source_priority)
        xauusd_rel = self.local.xauusd_relevance(article, entities, topics)
        usd_rel = self.local.usd_relevance(article, entities, topics)
        novelty = self.local.assess_novelty(
            article, getattr(article, "duplicate_of", ""), getattr(article, "is_duplicate", False)
        )

        result = NewsAnalysisResult(
            analysis_id=analysis_id,
            article_id=article.article_id,
            run_id=run_id,
            status=NewsAnalysisStatus.COMPLETE,
            local_only=True,
            provider="local",
            summary=getattr(article, "summary", ""),
            entities=entities,
            topics=topics,
            direction=direction,
            impact_strength=max((i.strength for i in impacts if i.asset == "XAUUSD"), default=0.0),
            confidence=round(0.4 + importance_score * 0.3, 4),
            horizon=(impacts[0].horizon if impacts else NewsImpactHorizon.MACRO),
            importance=importance,
            importance_score=importance_score,
            relevance_to_xauusd=xauusd_rel,
            relevance_to_usd=usd_rel,
            impacts=impacts,
            novelty=novelty,
            market_mechanism=(impacts[0].mechanism if impacts else ""),
        )

        # STAGE 7: optional external AI when warranted (HYBRID / API_ONLY)
        mode = "HYBRID"
        if self.config and hasattr(self.config, "analysis"):
            mode = str(getattr(self.config.analysis, "mode", "HYBRID")).upper()
        want_api = mode in ("API_ONLY", "HYBRID")
        if mode == "API_ONLY":
            want_api = True
        if want_api and self._api_eligible(importance_score, xauusd_rel, mode):
            try:
                ext = self.external.analyze(
                    article,
                    context={
                        "source_name": getattr(article, "source_name", ""),
                        "published_at": _article_published_str(article),
                        "local_direction": direction.value,
                        "local_importance": importance_score,
                    },
                )
                result = self._merge_external(result, ext)
            except Exception as api_err:
                # ANY provider failure keeps the local analysis authoritative.
                logger.warning(
                    "[NEWS_ANALYSIS] event=API_FALLBACK reason=%s provider=%s",
                    type(api_err).__name__,
                    self.external.provider_name,
                )

        # STAGE 8-9: persist final record
        self._persist(article, result)
        self.db.finish_run(run_id, "COMPLETE", [article.article_id])
        logger.info(
            "[NEWS_ANALYSIS] event=LOCAL_COMPLETE article_id=%s relevance=%.2f importance=%.2f",
            article.article_id,
            xauusd_rel,
            importance_score,
        )
        return result

    def _api_eligible(self, importance_score: float, xauusd_rel: float, mode: str) -> bool:
        if not self.external.available():
            return False
        if mode == "API_ONLY":
            return True
        floor = 0.55
        if self.config and hasattr(self.config, "analysis"):
            floor = float(getattr(self.config.analysis, "api_importance_floor", 0.55))
        return importance_score >= floor or xauusd_rel >= 0.5

    def _merge_external(
        self, local: NewsAnalysisResult, ext: dict[str, Any] | None
    ) -> NewsAnalysisResult:
        if not ext:
            return local
        try:
            direction = NewsDirection(str(ext.get("direction", "NEUTRAL")).upper())
        except ValueError:
            direction = local.direction
        try:
            horizon = NewsImpactHorizon(str(ext.get("time_horizon", "MACRO")).upper())
        except ValueError:
            horizon = local.horizon
        xauusd = float(
            ext.get("relevance_to_xauusd", local.relevance_to_xauusd) or local.relevance_to_xauusd
        )
        usd = float(ext.get("relevance_to_usd", local.relevance_to_usd) or local.relevance_to_usd)
        strength = float(ext.get("impact_strength", local.impact_strength) or local.impact_strength)
        return NewsAnalysisResult(
            analysis_id=local.analysis_id,
            article_id=local.article_id,
            run_id=local.run_id,
            status=NewsAnalysisStatus.COMPLETE,
            local_only=False,
            provider=self.external.provider_name,
            summary=str(ext.get("summary", local.summary)) or local.summary,
            entities=local.entities,
            topics=local.topics,
            direction=direction,
            impact_strength=max(0.0, min(1.0, strength)),
            confidence=max(
                0.0, min(1.0, float(ext.get("confidence", local.confidence) or local.confidence))
            ),
            horizon=horizon,
            importance=local.importance,
            importance_score=local.importance_score,
            relevance_to_xauusd=max(0.0, min(1.0, xauusd)),
            relevance_to_usd=max(0.0, min(1.0, usd)),
            impacts=local.impacts,
            surprise_assessment=str(ext.get("surprise_assessment", "")),
            market_mechanism=str(ext.get("market_mechanism", local.market_mechanism))
            or local.market_mechanism,
            contradictory_factors=[str(x) for x in ext.get("contradictory_factors", [])],
            novelty=local.novelty,
            risks=[str(x) for x in ext.get("risks", [])],
            reasoning_trace_id=str(ext.get("reasoning_trace_id", "")),
        )

    def _persist(self, article: Any, result: NewsAnalysisResult) -> None:
        self.db.insert_analysis(
            {
                "analysis_id": result.analysis_id,
                "article_id": result.article_id,
                "run_id": result.run_id,
                "status": result.status.value,
                "local_only": int(result.local_only),
                "provider": result.provider,
                "summary": result.summary,
                "entities": [e.model_dump(mode="json") for e in result.entities],
                "topics": [t.value for t in result.topics],
                "direction": result.direction.value,
                "impact_strength": result.impact_strength,
                "confidence": result.confidence,
                "horizon": result.horizon.value,
                "importance": result.importance.value,
                "importance_score": result.importance_score,
                "relevance_to_xauusd": result.relevance_to_xauusd,
                "relevance_to_usd": result.relevance_to_usd,
                "impacts": [i.model_dump(mode="json") for i in result.impacts],
                "surprise_assessment": result.surprise_assessment,
                "market_mechanism": result.market_mechanism,
                "contradictory_factors": result.contradictory_factors,
                "novelty": result.novelty.value,
                "risks": result.risks,
                "reasoning_trace_id": result.reasoning_trace_id,
                "analyzed_at": datetime.now(UTC).isoformat(),
            }
        )
        # derived tables: entities / topics / impacts
        self.db.replace_entities(
            result.article_id,
            [e.model_dump(mode="json") for e in result.entities],
        )
        self.db.replace_topics(result.article_id, [t.value for t in result.topics])
        self.db.replace_impacts(
            result.article_id,
            [i.model_dump(mode="json") for i in result.impacts],
        )

    # ------------------------------------------------------------------
    # Batch analysis (bounded, priority-aware)
    # ------------------------------------------------------------------

    def analyze_recent_unanalyzed(self, limit: int = 20) -> list[NewsAnalysisResult]:
        """Analyzes the most recent articles that lack an analysis row.

        Idempotent: tombstoned hashes (already analyzed) are skipped even if
        the article was re-ingested under a new id and would otherwise pass
        the 'no analysis row' filter.
        """
        articles = self.db.list_articles(limit=100)
        results: list[NewsAnalysisResult] = []
        for art in articles:
            if len(results) >= limit:
                break
            ah_tmp = str(art.get("article_hash") or "")
            if ah_tmp and self.db.is_analyzed_hash(ah_tmp):
                continue
            existing = self.db.get_analysis(art["article_id"])
            if existing:
                with contextlib.suppress(Exception):
                    if ah_tmp:
                        self.db.remember_analyzed_hash(
                            ah_tmp,
                            title=str(art.get("title", "")),
                            analysis_id=str(existing.get("analysis_id", "")),
                        )
                continue
            from nexus_scalp.news.models import NewsArticle

            article = NewsArticle(
                article_id=art["article_id"],
                article_hash=art["article_hash"],
                canonical_url=art["canonical_url"] or "",
                title=art["title"],
                summary=art["summary"] or "",
                body=art["body"] or "",
                source_id=art["source_id"] or "",
                source_name=art["source_name"] or "",
                published_at=normalize_datetime(_parse_db_dt(art.get("published_at"))),
                raw_categories=[],
                novelty=NewsNovelty.NEW,
            )
            results.append(self.analyze_article(article))
        return results


def _parse_db_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _article_published_str(article: Any) -> str:
    """Safe ISO string for an article's published_at (datetime | str | None)."""
    value = getattr(article, "published_at", None)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return ""
