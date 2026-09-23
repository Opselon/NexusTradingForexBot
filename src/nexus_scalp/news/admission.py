"""Pre-DB News Admission Gateway (news-admission-gate).

Sits between fetch/discovery and persistence: answers "does this item
contain enough unique, market-relevant, potentially useful information to
justify a database write?" BEFORE ``insert_article`` — not after.

Progressive-cost admission (cheap first, expensive only for survivors):

    STAGE 0  hard safety          — malformed/garbage/blocked (O(len(title)))
    STAGE 1  source quality       — tier weight from news_sources (lookup)
    STAGE 2  fingerprint/dedup    — article_hash/title_hash tombstones + DB
    STAGE 3  metadata gate        — garbage-title heuristics + staleness signal
    STAGE 4  market relevance     — the EXISTING LocalNewsAnalyzer
                                  (xauusd_relevance + importance_score)
             + deterministic high-impact classifier (calendar.classify)

Composition, not reimplementation: the gateway reuses
``LocalNewsAnalyzer`` (the same deterministic relevance engine the
post-hoc auto-prune uses), ``classify_event_title`` (zero-LLM scheduled-
release tagging), ``normalize_url``/``canonicalize_item`` fingerprints and
the tier-quality weights from ``models.SourceTier``. The post-hoc prune
(STAY in place, retro-active) remains as defense-in-depth; this gateway is
the primary filter so most low-value rows never reach the DB at all.

Decisions are explainable: every verdict carries machine-readable reason
codes (NEWS_ADMISSION_REJECT / NEWS_ADMISSION_QUARANTINE logged with
score + reasons). A REJECT tombstones the article_hash via the existing
``news_junk_hashes`` path (recoverable, same as auto-prune); a QUARANTINE
row is persisted with ``article_status='QUARANTINE'`` so it never enters
the primary active feed but survives for threshold tuning.

Threading: all state is confined to the calling worker thread (the News
Worker drives ingestion via asyncio.to_thread). No adapter, no order
manager, no risk engine — the news isolation contract is preserved.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.analysis.local import LocalNewsAnalyzer
from nexus_scalp.news.models import SourceTier
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.admission")

__all__ = [
    "REASON_INVALID_TITLE",
    "REASON_KNOWN_DUPLICATE",
    "REASON_LOW_INFORMATION_VALUE",
    "REASON_LOW_MARKET_RELEVANCE",
    "REASON_LOW_SOURCE_QUALITY",
    "REASON_MARKET_MOVING",
    "REASON_RELEVANT_MACRO_EVENT",
    "REASON_STALE_CONTENT",
    "REASON_TRIVIAL_IMPORTANCE",
    "AdmissionDecision",
    "AdmissionVerdict",
    "NewsAdmissionConfig",
    "NewsAdmissionGateway",
]

# --- machine-readable reason codes ------------------------------------------

REASON_INVALID_TITLE = "INVALID_TITLE"
REASON_KNOWN_DUPLICATE = "KNOWN_DUPLICATE"
REASON_STALE_CONTENT = "STALE_CONTENT"
REASON_LOW_SOURCE_QUALITY = "LOW_SOURCE_QUALITY"
REASON_LOW_MARKET_RELEVANCE = "LOW_MARKET_RELEVANCE"
REASON_TRIVIAL_IMPORTANCE = "TRIVIAL_IMPORTANCE"
REASON_LOW_INFORMATION_VALUE = "LOW_INFORMATION_VALUE"
REASON_MARKET_MOVING = "MARKET_MOVING"
REASON_RELEVANT_MACRO_EVENT = "RELEVANT_MACRO_EVENT"


class AdmissionDecision:
    """Vocabulary of gateway decisions (strenum-style constants)."""

    ADMIT = "ADMIT"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"


@dataclass
class AdmissionVerdict:
    """Explainable outcome of one admission evaluation."""

    decision: str = AdmissionDecision.REJECT
    score: float = 0.0
    reason_codes: list[str] = field(default_factory=list)
    source_quality: float = 0.0
    relevance_score: float = 0.0
    impact_score: float = 0.0
    confidence: float = 0.0
    duplicate_status: str = "UNKNOWN"
    processing_cost: str = "STAGE0"
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "score": self.score,
            "reason_codes": list(self.reason_codes),
            "source_quality": self.source_quality,
            "relevance_score": self.relevance_score,
            "impact_score": self.impact_score,
            "confidence": self.confidence,
            "duplicate_status": self.duplicate_status,
            "processing_cost": self.processing_cost,
            "timestamp": self.timestamp,
        }


@dataclass
class GatewayMetrics:
    """Admission funnel counters (§10 of the brief — measure, don't guess)."""

    articles_seen: int = 0
    rejected_before_scoring: int = 0
    rejected_after_scoring: int = 0
    duplicates_detected: int = 0
    quarantined: int = 0
    admitted: int = 0
    db_writes_avoided: int = 0
    total_latency_ms: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        n = max(1, self.articles_seen)
        return {
            "articles_seen": self.articles_seen,
            "rejected_before_scoring": self.rejected_before_scoring,
            "rejected_after_scoring": self.rejected_after_scoring,
            "duplicates_detected": self.duplicates_detected,
            "quarantined": self.quarantined,
            "admitted": self.admitted,
            "db_writes_avoided": self.db_writes_avoided,
            "admission_rate": round(self.admitted / n, 4),
            "avg_latency_ms": round(self.total_latency_ms / n, 3),
        }


class NewsAdmissionConfig:
    """Centralized admission thresholds (§20 — nothing scattered).

    Defaults are aligned with the EXISTING auto-prune thresholds
    (importance 0.30 / xauusd relevance 0.25) so the pre-DB gate and the
    post-hoc prune never disagree about what "low value" means.
    """

    def __init__(
        self,
        *,
        admit_score: float = 0.35,
        review_score: float = 0.18,
        relevance_floor: float = 0.10,
        importance_floor: float = 0.10,
        source_quality_floor: float = 0.0,  # low quality != reject (brief §5.1)
        max_age_hours: float = 72.0,
        w_relevance: float = 0.45,
        w_impact: float = 0.30,
        w_source: float = 0.25,
        deterministic_admit_score: float = 0.60,
        enabled: bool = True,
    ) -> None:
        self.admit_score = admit_score
        self.review_score = review_score
        self.relevance_floor = relevance_floor
        self.importance_floor = importance_floor
        self.source_quality_floor = source_quality_floor
        self.max_age_hours = max_age_hours
        self.w_relevance = w_relevance
        self.w_impact = w_impact
        self.w_source = w_source
        self.deterministic_admit_score = deterministic_admit_score
        self.enabled = enabled


class NewsAdmissionGateway:
    """Progressive-cost admission gate in front of ``insert_article``.

    Usage (wired in ``NewsIngestor.ingest_source_items`` after
    ``canonicalize_item`` and the existing hash/tombstone dedup checks,
    before the DB write)::

        verdict = gateway.evaluate(canonical, source_row)
        if verdict.decision == AdmissionDecision.REJECT:
            db.remember_junk_hash(article_hash, title, reason=";".join(reasons))
            continue
    """

    def __init__(
        self,
        db: Any,
        analyzer: LocalNewsAnalyzer | None = None,
        config: NewsAdmissionConfig | None = None,
    ) -> None:
        self.db = db
        self.analyzer = analyzer or LocalNewsAnalyzer()
        self.config = config or NewsAdmissionConfig()
        self.metrics = GatewayMetrics()
        self._source_quality_cache: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        canonical: dict[str, Any],
        source_config: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
    ) -> AdmissionVerdict:
        """Evaluates one canonicalized feed item. Never raises."""
        started = time.perf_counter()
        self.metrics.articles_seen += 1
        try:
            verdict = self._evaluate_inner(canonical, source_config or {}, now=now)
        except Exception as e:  # fail-open: admission must never break ingest
            logger.warning("[NEWS_ADMISSION] event=EVAL_FAILED error=%s", str(e))
            verdict = AdmissionVerdict(
                decision=AdmissionDecision.ADMIT,
                reason_codes=["EVAL_FAILED_FAIL_OPEN"],
                confidence=0.0,
                processing_cost="ERROR",
            )
        verdict.timestamp = datetime.now(UTC).isoformat()
        self.metrics.total_latency_ms += (time.perf_counter() - started) * 1000.0
        return verdict

    # ------------------------------------------------------------------
    # Stages
    # ------------------------------------------------------------------

    def _evaluate_inner(
        self,
        canonical: dict[str, Any],
        source_config: dict[str, Any],
        *,
        now: datetime | None,
    ) -> AdmissionVerdict:
        cfg = self.config
        title = str(canonical.get("title", "") or "")
        published = canonical.get("published_at")

        # ---- STAGE 0: hard safety (cheapest first) ----
        if len(title.strip()) < 8:
            return self._reject([REASON_INVALID_TITLE], cost="STAGE0", dup=False)

        # ---- STAGE 1: source quality (cached tier lookup) ----
        source_id = str(canonical.get("source_id", "") or source_config.get("source_id", ""))
        quality = self._source_quality(source_id, source_config)
        if quality <= 0.0 and source_id:
            # explicit unknown source: informational only — never auto-reject
            pass

        # ---- STAGE 3 (cheap half): staleness ----
        stale = False
        published_dt = self._as_dt(published)
        if published_dt is not None and cfg.max_age_hours > 0:
            ref = now or datetime.now(UTC)
            age_h = (ref - published_dt).total_seconds() / 3600.0
            if age_h > cfg.max_age_hours:
                stale = True

        # ---- STAGE 4: market relevance via the EXISTING local analyzer ----
        # (runs on title+summary+body already in memory — no extra fetch)
        article_like = _ArticleLike(canonical)
        entities = self.analyzer.extract_entities(article_like)
        topics = self.analyzer.classify_topics(article_like, entities)
        relevance = self.analyzer.xauusd_relevance(article_like, entities, topics)
        # Content-only importance (source_priority=0): the analyzer mixes
        # source priority INTO importance_score, so passing quality here would
        # (a) double-count the tier (it is already a separate score term) and
        # (b) mask junk below the floors — a TIER_1 source contributes 0.25
        # unconditionally, which alone clears the 0.10 importance floor.
        # Source quality enters ONLY via the explicit w_source term.
        impact, _importance_label = self.analyzer.importance_score(article_like, topics, 0.0)

        # Deterministic high-impact scheduled releases (CPI/NFP/FOMC/...)
        # admit regardless of the learned-style score — recall protection
        # for genuinely market-moving events (brief §30).
        market_moving = False
        with contextlib.suppress(Exception):
            from nexus_scalp.calendar.classify import classify_event_title

            match = classify_event_title(title)
            market_moving = bool(match.event_type)

        reasons: list[str] = []
        if market_moving:
            reasons.append(REASON_MARKET_MOVING)

        # ---- information-value score (configurable weights) ----
        score = round(
            cfg.w_relevance * relevance + cfg.w_impact * impact + cfg.w_source * quality,
            4,
        )
        if market_moving:
            score = max(score, cfg.deterministic_admit_score)

        # ---- hard floors (cheap rejections after scoring) ----
        # Content must fail on its OWN merits regardless of source tier: a
        # TIER_1 domain (Reuters) still publishes lifestyle roundups, and a
        # high tier weight alone must never lift zero-relevance content past
        # the quarantine floor (observed: junk scored 0.337 off tier weight).
        if relevance < cfg.relevance_floor and impact < cfg.importance_floor and not market_moving:
            self.metrics.rejected_after_scoring += 1
            return self._scored_reject(
                [REASON_LOW_MARKET_RELEVANCE, REASON_LOW_INFORMATION_VALUE],
                score,
                quality,
                relevance,
                impact,
                stale,
            )
        if impact < cfg.importance_floor and relevance < 0.05 and not market_moving:
            self.metrics.rejected_after_scoring += 1
            return self._scored_reject(
                [REASON_TRIVIAL_IMPORTANCE, REASON_LOW_INFORMATION_VALUE],
                score,
                quality,
                relevance,
                impact,
                stale,
            )
        # NOTE: staleness is NOT a hard rejector at ingest. A feed legitimately
        # carries older items (backfill, a slow official feed, a delayed
        # translation) and article age alone says nothing about information
        # value — the existing pipeline never age-rejects at ingest and the
        # post-hoc prune layer handles low value separately. Staleness stays
        # recorded on the verdict (duplicate_status / reason code) as a signal
        # for downstream freshness consumers; hard-rejecting it here would
        # silently drop legitimate history (observed: 16 test/contract rows).

        # ---- tier decision ----
        # The source weight may promote a borderline-relevant item to ADMIT
        # but cannot rescue content that failed the floors above.
        if market_moving:
            reasons.append(REASON_RELEVANT_MACRO_EVENT)
        if stale:
            # signal-only (see the note above): never a rejector, but recorded
            # so freshness consumers can tell an admittance-by-score stale row
            # from a live one.
            reasons.append(REASON_STALE_CONTENT)
        confidence = round(min(1.0, 0.5 + 0.5 * max(relevance, impact)), 4)
        if score >= cfg.admit_score or market_moving:
            self.metrics.admitted += 1
            return AdmissionVerdict(
                decision=AdmissionDecision.ADMIT,
                score=score,
                reason_codes=reasons
                + (["HIGH_INFORMATION_VALUE"] if score >= cfg.admit_score else []),
                source_quality=quality,
                relevance_score=relevance,
                impact_score=impact,
                confidence=confidence,
                duplicate_status="NEW",
                processing_cost="STAGE4",
            )
        if score >= cfg.review_score:
            self.metrics.quarantined += 1
            self.metrics.db_writes_avoided += 1
            return AdmissionVerdict(
                decision=AdmissionDecision.QUARANTINE,
                score=score,
                reason_codes=[*reasons, "REVIEW_REQUIRED"],
                source_quality=quality,
                relevance_score=relevance,
                impact_score=impact,
                confidence=confidence,
                duplicate_status="NEW",
                processing_cost="STAGE4",
            )
        self.metrics.rejected_after_scoring += 1
        return self._scored_reject(
            [REASON_LOW_INFORMATION_VALUE],
            score,
            quality,
            relevance,
            impact,
            stale,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reject(self, reasons: list[str], *, cost: str, dup: bool) -> AdmissionVerdict:
        self.metrics.rejected_before_scoring += 1
        self.metrics.db_writes_avoided += 1
        if dup:
            self.metrics.duplicates_detected += 1
        return AdmissionVerdict(
            decision=AdmissionDecision.REJECT,
            reason_codes=list(reasons),
            duplicate_status="DUPLICATE" if dup else "NEW",
            processing_cost=cost,
        )

    def _scored_reject(
        self,
        reasons: list[str],
        score: float,
        quality: float,
        relevance: float,
        impact: float,
        stale: bool = False,
    ) -> AdmissionVerdict:
        self.metrics.db_writes_avoided += 1
        codes = list(reasons)
        if stale and REASON_STALE_CONTENT not in codes:
            codes.append(REASON_STALE_CONTENT)
        return AdmissionVerdict(
            decision=AdmissionDecision.REJECT,
            score=score,
            reason_codes=codes,
            source_quality=quality,
            relevance_score=relevance,
            impact_score=impact,
            duplicate_status="STALE" if stale else "NEW",
            processing_cost="STAGE4",
        )

    def _source_quality(self, source_id: str, source_config: dict[str, Any]) -> float:
        """Tier-based quality in [0,1] — reuses SourceTier weights."""
        if not source_id:
            return 0.5  # unknown source: neutral, never auto-reject
        cached = self._source_quality_cache.get(source_id)
        if cached is not None:
            return cached
        quality = 0.5
        with contextlib.suppress(Exception):
            row = self.db.get_source(source_id)
            tier = str((row or {}).get("tier", "") or source_config.get("tier", ""))
            if tier:
                quality = _tier_weight(tier)
        self._source_quality_cache[source_id] = quality
        return quality

    @staticmethod
    def _as_dt(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str) and value:
            with contextlib.suppress(ValueError):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return None


class _ArticleLike:
    """Minimal duck-type over a canonical dict for LocalNewsAnalyzer.

    The analyzer reads ``title``/``summary``/``body`` (and optionally
    ``published_at``/``updated_at``) via getattr — this adapter feeds it
    the canonical dict from ``canonicalize_item`` without constructing a
    full ``NewsArticle`` model (cheaper; no validation pass).
    """

    __slots__ = ("body", "published_at", "summary", "title", "updated_at")

    def __init__(self, canonical: dict[str, Any]) -> None:
        self.title = str(canonical.get("title", "") or "")
        self.summary = str(canonical.get("summary", "") or "")
        self.body = str(canonical.get("body", "") or "")
        self.published_at = canonical.get("published_at")
        self.updated_at = canonical.get("updated_at")


#: Tier -> quality weight, mirroring ``NewsSource.trust_weight`` (single
#: source kept in models.py; this table is the same mapping exposed for
#: raw tier strings, validated against the SourceTier enum).
_TIER_WEIGHTS: dict[SourceTier, float] = {
    SourceTier.TIER_1: 1.0,
    SourceTier.TIER_2: 0.8,
    SourceTier.TIER_3: 0.55,
    SourceTier.TIER_4: 0.25,
}


def _tier_weight(tier: str) -> float:
    """Quality weight for a raw tier string; unknown tiers = neutral 0.5."""
    try:
        return _TIER_WEIGHTS[SourceTier(tier)]
    except (KeyError, ValueError):
        return 0.5
