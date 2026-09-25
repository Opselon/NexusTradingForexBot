"""News impact time decay (PHASE 12).

New information matters more; impact MUST decay with age, with different
decay classes (BREAKING minutes / MACRO hours / POLICY hours-days /
STRUCTURAL days-weeks). One fixed decay for every news type is explicitly
forbidden.

    freshness(t) = 0.5 ** (age_sec / half_life_sec)

Configurable and testable (see NewsDecayConfig). A central-bank regime
change may remain relevant much longer than a minor headline.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from nexus_scalp.news.config import NewsDecayConfig
from nexus_scalp.news.models import NewsImpactHorizon

#: Default half-lives if no config is provided (seconds).
_DEFAULT_HALF_LIVES: dict[NewsImpactHorizon, float] = {
    NewsImpactHorizon.BREAKING: 15.0 * 60.0,  # 15 minutes
    NewsImpactHorizon.MACRO: 4.0 * 3600.0,  # 4 hours
    NewsImpactHorizon.POLICY: 24.0 * 3600.0,  # 24 hours
    NewsImpactHorizon.STRUCTURAL: 5.0 * 86400.0,  # 5 days
}


class NewsDecayEngine:
    """Computes time-decayed freshness for impact horizons."""

    def __init__(self, config: NewsDecayConfig | None = None) -> None:
        self.config = config or NewsDecayConfig()
        self.half_lives = self._build_half_lives()

    def _build_half_lives(self) -> dict[NewsImpactHorizon, float]:
        c = self.config
        return {
            NewsImpactHorizon.BREAKING: c.breaking_half_life_min * 60.0,
            NewsImpactHorizon.MACRO: c.macro_half_life_hours * 3600.0,
            NewsImpactHorizon.POLICY: c.policy_half_life_hours * 3600.0,
            NewsImpactHorizon.STRUCTURAL: c.structural_half_life_days * 86400.0,
        }

    def half_life_sec(self, horizon: NewsImpactHorizon) -> float:
        return self.half_lives.get(horizon, self.half_lives[NewsImpactHorizon.MACRO])

    def freshness(
        self,
        published_at: datetime,
        now: datetime | None = None,
        horizon: NewsImpactHorizon = NewsImpactHorizon.MACRO,
    ) -> float:
        """Exponential decay freshness in [0, 1]."""
        now = now or datetime.now(UTC)
        published_at = _as_utc(published_at)
        age_sec = max(0.0, (now - published_at).total_seconds())
        half_life = self.half_life_sec(horizon)
        if half_life <= 0:
            return 0.0
        return round(float(math.pow(0.5, age_sec / half_life)), 4)

    def decayed_strength(
        self,
        strength: float,
        published_at: datetime,
        horizon: NewsImpactHorizon,
        now: datetime | None = None,
    ) -> float:
        """Decayed impact strength = strength * freshness."""
        return round(strength * self.freshness(published_at, now, horizon), 4)

    def is_stale(
        self,
        published_at: datetime,
        now: datetime | None = None,
        stale_after_sec: float | None = None,
    ) -> bool:
        """True when the event is older than the staleness threshold."""
        now = now or datetime.now(UTC)
        published_at = _as_utc(published_at)
        threshold = stale_after_sec if stale_after_sec is not None else self.config.stale_after_sec
        return (now - published_at).total_seconds() > threshold


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
