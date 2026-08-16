"""News Intelligence configuration (PHASE 12).

Follows the repository's Pydantic settings pattern (``configuration/config.py``).
The News subsystem is independently disable-able and defaults to LOCAL_ONLY /
HYBRID analysis with no mandatory external API key.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from nexus_scalp.news.models import NewsState


class NewsPollingConfig(BaseModel):
    """Per-source-class polling intervals (seconds)."""

    fast_interval_sec: int = Field(default=180, ge=30)  # breaking feeds
    medium_interval_sec: int = Field(default=600, ge=60)  # official releases
    slow_interval_sec: int = Field(default=3600, ge=300)  # calendars / COT


class NewsAnalysisConfig(BaseModel):
    """Analysis routing: LOCAL_ONLY / API_ONLY / HYBRID (default)."""

    mode: str = "HYBRID"  # LOCAL_ONLY | API_ONLY | HYBRID
    provider: str = ""  # openai-compatible / gemini / anthropic / openrouter
    api_base_url: str = ""
    model: str = ""
    max_api_per_cycle: int = Field(default=5, ge=0)
    api_importance_floor: float = Field(default=0.55, ge=0.0, le=1.0)
    request_timeout_sec: float = Field(default=20.0, ge=1.0)
    enabled: bool = True


class NewsDecayConfig(BaseModel):
    """Time-decay parameters per horizon class (configurable, testable)."""

    breaking_half_life_min: float = Field(default=15.0, gt=0.0)
    macro_half_life_hours: float = Field(default=4.0, gt=0.0)
    policy_half_life_hours: float = Field(default=24.0, gt=0.0)
    structural_half_life_days: float = Field(default=5.0, gt=0.0)
    stale_after_sec: float = Field(default=3600.0, gt=0.0)


class NewsImpactBounds(BaseModel):
    """Bounded news influence on trading decisions.

    Hard invariant: news can NEVER override risk/exposure/safety. It is a
    contextual multiplier with explicit caps.
    """

    max_confidence_boost: float = Field(default=0.05, ge=0.0, le=0.20)
    max_confidence_penalty: float = Field(default=0.10, ge=0.0, le=0.30)
    min_alignment_to_boost: float = Field(default=0.40, ge=0.0, le=1.0)
    conflict_caution_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    max_news_adjustment: float = Field(default=0.05, ge=0.0, le=0.20)
    blocked_states: list[NewsState] = Field(
        default_factory=lambda: [NewsState.BREAKING, NewsState.HIGH_IMPACT]
    )
    caution_states: list[NewsState] = Field(
        default_factory=lambda: [NewsState.CONFLICTED, NewsState.ELEVATED]
    )


class NewsConfig(BaseModel):
    """Complete News subsystem configuration."""

    enabled: bool = True
    db_path: str = "artifacts/news.db"
    max_articles_per_fetch: int = Field(default=200, ge=1, le=2000)
    max_queue_size: int = Field(default=1000, ge=10, le=10000)
    worker_interval_sec: int = Field(default=60, ge=10)
    context_ttl_sec: float = Field(default=60.0, gt=0.0)
    polling: NewsPollingConfig = Field(default_factory=NewsPollingConfig)
    analysis: NewsAnalysisConfig = Field(default_factory=NewsAnalysisConfig)
    decay: NewsDecayConfig = Field(default_factory=NewsDecayConfig)
    bounds: NewsImpactBounds = Field(default_factory=NewsImpactBounds)

    def resolve_db_path(self, repo_root: Path | None = None) -> Path:
        """Resolves the news DB path relative to the repository root.

        Follows the repository convention of ``artifacts/`` for databases.
        """
        p = Path(self.db_path)
        if p.is_absolute():
            return p
        base = repo_root or Path.cwd()
        return base / p
