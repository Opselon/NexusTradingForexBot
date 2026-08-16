"""News Intelligence domain models (PHASE 12).

Immutable contracts mirroring the repository's Pydantic frozen-model pattern
(see ``domain/models.py`` and the phase packages). No execution capability is
introduced here: every model is a pure data contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NewsDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    CONFLICTED = "CONFLICTED"


class NewsImpactHorizon(StrEnum):
    BREAKING = "BREAKING"  # minutes
    MACRO = "MACRO"  # hours / sessions
    POLICY = "POLICY"  # hours / days
    STRUCTURAL = "STRUCTURAL"  # days / weeks


class NewsImportance(StrEnum):
    TRIVIAL = "TRIVIAL"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class NewsTopic(StrEnum):
    MONETARY_POLICY = "MONETARY_POLICY"
    INFLATION = "INFLATION"
    EMPLOYMENT = "EMPLOYMENT"
    GROWTH = "GROWTH"
    GDP = "GDP"
    INTEREST_RATES = "INTEREST_RATES"
    BOND_YIELDS = "BOND_YIELDS"
    USD = "USD"
    CENTRAL_BANK = "CENTRAL_BANK"
    GEOPOLITICS = "GEOPOLITICS"
    WAR = "WAR"
    ENERGY = "ENERGY"
    COMMODITIES = "COMMODITIES"
    SAFE_HAVEN = "SAFE_HAVEN"
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    LIQUIDITY = "LIQUIDITY"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    REGULATION = "REGULATION"
    FINANCIAL_STABILITY = "FINANCIAL_STABILITY"
    OTHER = "OTHER"


class NewsState(StrEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH_IMPACT = "HIGH_IMPACT"
    CONFLICTED = "CONFLICTED"
    BREAKING = "BREAKING"
    STALE = "STALE"


class NewsNovelty(StrEnum):
    NEW = "NEW"
    UPDATED = "UPDATED"
    CONFIRMATION = "CONFIRMATION"
    REPETITION = "REPETITION"
    STALE = "STALE"


class NewsAnalysisStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    LOCAL_ONLY = "LOCAL_ONLY"


class SourceTier(StrEnum):
    TIER_1 = "TIER_1"  # official central banks / governments / regulators
    TIER_2 = "TIER_2"  # major financial news providers
    TIER_3 = "TIER_3"  # specialised financial sources
    TIER_4 = "TIER_4"  # aggregators / low-confidence sources


class SourceKind(StrEnum):
    RSS = "RSS"
    ATOM = "ATOM"
    CALENDAR = "CALENDAR"
    OFFICIAL = "OFFICIAL"
    API = "API"


# ---------------------------------------------------------------------------
# Sources / health
# ---------------------------------------------------------------------------


class NewsSource(BaseModel):
    """A configured news source with its trust/reliability profile."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    name: str
    kind: SourceKind = SourceKind.RSS
    tier: SourceTier = SourceTier.TIER_3
    url: str = ""
    feed_url: str = ""
    enabled: bool = True
    poll_interval_sec: int = Field(default=300, ge=10)
    language: str = "en"
    priority: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def trust_weight(self) -> float:
        """Tier-derived trust weight used by consensus/confidence."""
        return {
            SourceTier.TIER_1: 1.0,
            SourceTier.TIER_2: 0.8,
            SourceTier.TIER_3: 0.55,
            SourceTier.TIER_4: 0.25,
        }[self.tier]


class NewsSourceHealth(BaseModel):
    """Per-source operational health (rate-limit / backoff aware)."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_status: int | None = None
    consecutive_failures: int = 0
    rate_limited: bool = False
    retry_after_sec: float = 0.0
    backoff_until: datetime | None = None
    healthy: bool = True

    def effective_backoff_sec(self, base: float = 30.0, cap: float = 3600.0) -> float:
        """Exponential backoff: base * 2**consecutive_failures, capped."""
        return min(cap, base * (2.0 ** max(0, self.consecutive_failures - 1)))


# ---------------------------------------------------------------------------
# Articles / entities / canonical event
# ---------------------------------------------------------------------------


class NewsEntity(BaseModel):
    """An extracted entity (currency / asset / institution / macro concept)."""

    model_config = ConfigDict(frozen=True)

    name: str
    entity_type: str = "GENERIC"  # CURRENCY / ASSET / INSTITUTION / MACRO / GEO
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    mentions: int = Field(default=1, ge=0)
    is_primary: bool = False


class NewsArticle(BaseModel):
    """A canonical article. Identity is deterministic via article_hash.

    Multiple raw feed items deduplicate onto one canonical article; updates
    produce a new version (see news_article_versions).
    """

    model_config = ConfigDict(frozen=True)

    article_id: str
    article_hash: str
    canonical_url: str = ""
    title: str = ""
    summary: str = ""
    body: str = ""
    language: str = "en"
    source_id: str = ""
    source_name: str = ""
    published_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    raw_categories: list[str] = Field(default_factory=list)
    entities: list[NewsEntity] = Field(default_factory=list)
    topics: list[NewsTopic] = Field(default_factory=list)
    importance: NewsImportance = NewsImportance.MINOR
    importance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty: NewsNovelty = NewsNovelty.NEW
    is_duplicate: bool = False
    duplicate_of: str = ""
    evidence_sources: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("published_at", "updated_at", "created_at")
    @classmethod
    def _coerce_utc(cls, v: datetime | None) -> datetime | None:
        return _utc(v) if v is not None else None


# ---------------------------------------------------------------------------
# Analysis / impact / consensus
# ---------------------------------------------------------------------------


class NewsImpact(BaseModel):
    """Explainable per-asset impact hypothesis."""

    model_config = ConfigDict(frozen=True)

    asset: str = "XAUUSD"
    direction: NewsDirection = NewsDirection.NEUTRAL
    strength: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    horizon: NewsImpactHorizon = NewsImpactHorizon.MACRO
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    mechanism: str = ""
    evidence: list[str] = Field(default_factory=list)

    @property
    def bounded_adjustment(self) -> float:
        """Signed bounded adjustment in [-1, 1]: strength * relevance."""
        sign = 1.0 if self.direction == NewsDirection.BULLISH else -1.0
        if self.direction in (NewsDirection.NEUTRAL, NewsDirection.MIXED, NewsDirection.CONFLICTED):
            sign = 0.0
        return round(sign * self.strength * self.relevance, 4)


class NewsConsensus(BaseModel):
    """Multi-source consensus over one canonical event."""

    model_config = ConfigDict(frozen=True)

    article_id: str
    source_count: int = 0
    independent_count: int = 0
    agreement: float = Field(default=0.0, ge=0.0, le=1.0)  # 1.0 = full agreement
    conflict: float = Field(default=0.0, ge=0.0, le=1.0)  # 0.0 = no conflict
    directions: list[NewsDirection] = Field(default_factory=list)
    weighted_direction: NewsDirection = NewsDirection.NEUTRAL
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NewsAnalysisResult(BaseModel):
    """Final analysis record for one canonical article."""

    model_config = ConfigDict(frozen=True)

    analysis_id: str
    article_id: str
    run_id: str
    status: NewsAnalysisStatus = NewsAnalysisStatus.COMPLETE
    local_only: bool = True
    provider: str = ""
    summary: str = ""
    entities: list[NewsEntity] = Field(default_factory=list)
    topics: list[NewsTopic] = Field(default_factory=list)
    direction: NewsDirection = NewsDirection.NEUTRAL
    impact_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    horizon: NewsImpactHorizon = NewsImpactHorizon.MACRO
    importance: NewsImportance = NewsImportance.MINOR
    importance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_to_xauusd: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_to_usd: float = Field(default=0.0, ge=0.0, le=1.0)
    impacts: list[NewsImpact] = Field(default_factory=list)
    surprise_assessment: str = ""
    market_mechanism: str = ""
    contradictory_factors: list[str] = Field(default_factory=list)
    novelty: NewsNovelty = NewsNovelty.NEW
    risks: list[str] = Field(default_factory=list)
    reasoning_trace_id: str = ""
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AssetImpactProfile(BaseModel):
    """Per-asset mapping used by the XAUUSD / FX relevance engines."""

    model_config = ConfigDict(frozen=True)

    asset: str
    asset_type: str = "COMMODITY"  # COMMODITY / CURRENCY / INDEX
    drivers: dict[str, float] = Field(default_factory=dict)
    inverse_drivers: dict[str, float] = Field(default_factory=dict)


class TradeNewsLink(BaseModel):
    """Trade <-> news attribution link (news_trade_links)."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    trade_id: str
    article_id: str
    strategy_id: str = ""
    model_version: str = ""
    news_alignment: float = Field(default=0.0, ge=-1.0, le=1.0)
    linked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class NewsWorkerState(BaseModel):
    """Persisted worker checkpoint (news_worker_state)."""

    model_config = ConfigDict(frozen=True)

    scope: str = "news"
    cycle_count: int = 0
    last_cycle_at: datetime | None = None
    last_error: str = ""
    last_checkpoint: str = ""


# ---------------------------------------------------------------------------
# Live decision cache
# ---------------------------------------------------------------------------


class CurrentNewsContext(BaseModel):
    """Bounded, fast in-memory news state consumed by the live path.

    Live trading reads ONLY this derived object; it never queries the news
    database per tick. All fields default to SAFE values when news is
    unavailable (never fake-neutral confidence).
    """

    model_config = ConfigDict(frozen=True)

    available: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    state: NewsState = NewsState.NORMAL
    active_event_count: int = 0
    xauusd_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    usd_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    bullish_score: float = Field(default=0.0, ge=0.0, le=1.0)
    bearish_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_score: float = Field(default=0.0, ge=0.0, le=1.0)
    freshness: float = Field(default=0.0, ge=0.0, le=1.0)
    source_consensus: float = Field(default=0.0, ge=0.0, le=1.0)
    stale: bool = False
    active_high_impact: list[str] = Field(default_factory=list)

    @property
    def news_adjustment(self) -> float:
        """Bounded signed adjustment in [-1, 1] for the target asset.

        = (bullish - bearish) * confidence * freshness, clamped to
        [-MAX_NEWS_ADJUSTMENT, +MAX_NEWS_ADJUSTMENT] by the caller.
        """
        return round(
            (self.bullish_score - self.bearish_score) * self.confidence * self.freshness, 4
        )


def normalize_datetime(v: datetime | None) -> datetime:
    return _utc(v) if v is not None else datetime.now(UTC)


def model_dump_jsonable(model: BaseModel) -> dict[str, Any]:
    """Safe JSON dump for persistence (mode='json')."""
    return model.model_dump(mode="json")
