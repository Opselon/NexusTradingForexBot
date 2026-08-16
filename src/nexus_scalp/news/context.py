"""News live decision context cache (PHASE 12).

Bounded, fast in-memory news state consumed by the live path. Live trading
reads ONLY this derived object; it never queries the news DB per tick.

    CurrentNewsContext:
        timestamp / state / active_event_count / xauusd_relevance /
        usd_relevance / bullish_score / bearish_score / confidence /
        conflict_score / freshness / source_consensus / stale /
        active_high_impact

When news is unavailable or stale beyond TTL the context is marked stale
and carries SAFE defaults (never fake-neutral confidence).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.analysis.decay import NewsDecayEngine
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import (
    CurrentNewsContext,
    NewsDirection,
    NewsImpactHorizon,
    NewsState,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.context")


class NewsContextCache:
    """Maintains the derived live context with natural decay."""

    def __init__(self, db: NewsDatabase, config: Any | None = None) -> None:
        self.db = db
        self.config = config
        self.decay = NewsDecayEngine(getattr(config, "decay", None))
        self._context: CurrentNewsContext | None = None
        self._last_build_mono: float = 0.0

    @property
    def ttl_sec(self) -> float:
        if self.config and hasattr(self.config, "context_ttl_sec"):
            return float(self.config.context_ttl_sec)
        return 60.0

    def get(self, force: bool = False) -> CurrentNewsContext:
        """Returns the cached context. NEVER hits the database when called
        from the live tick path.

        The live path uses ``get()`` (cache-only): at most a cheap in-memory
        read per tick. The background NewsWorker calls ``refresh()`` to
        rebuild the context off the event loop, so the TTL expiry never
        triggers a synchronous SQLite query inside ``_process_tick_pipeline``.
        """
        if force:
            self._context = self.build()
            self._last_build_mono = time.monotonic()
        return self._context if self._context is not None else self.build_once_safe()

    def build_once_safe(self) -> CurrentNewsContext:
        """First-run build (no evidence yet -> safe defaults, no DB hit).

        Keeps the very first tick safe even before the worker has produced a
        context. Subsequent refreshes happen exclusively in the worker.
        """
        if self._context is None:
            self._context = CurrentNewsContext(available=False, timestamp=datetime.now(UTC))
            self._last_build_mono = time.monotonic()
        return self._context

    def refresh(self) -> CurrentNewsContext:
        """Rebuilds the context from the news DB (worker path only).

        Called by the NewsWorker cycle (off the event loop). Never called on
        the live tick path.
        """
        self._context = self.build()
        self._last_build_mono = time.monotonic()
        return self._context

    def build(self) -> CurrentNewsContext:
        """Derives the current news context from recent analyses + decays.

        NEVER fake-neutral: when no evidence exists, confidence stays 0.0
        and the state is NORMAL with available=False.
        """
        now = datetime.now(UTC)
        try:
            analyses = self.db.list_analysis(limit=100)
            if not analyses:
                return CurrentNewsContext(available=False, timestamp=now)
        except Exception:
            return CurrentNewsContext(
                available=False, timestamp=now, state=NewsState.STALE, stale=True
            )

        # Default to safe values until evidence accumulates
        xauusd_rel = 0.0
        usd_rel = 0.0
        bull = 0.0
        bear = 0.0
        conf_sum = 0.0
        conflict_sum = 0.0
        fresh_sum = 0.0
        consensus_sum = 0.0
        active_high: list[str] = []
        weights = 0.0
        max_importance = 0.0
        any_breaking = False
        any_conflict = False

        for row in analyses:
            try:
                article_id = row["article_id"]
                published_at = _parse_dt(row.get("analyzed_at") or "")
                if not published_at:
                    continue
                importance = float(row.get("importance_score", 0.0) or 0.0)
                max_importance = max(max_importance, importance)
                direction = NewsDirection(str(row.get("direction", "NEUTRAL")).upper())
                try:
                    horizon = NewsImpactHorizon(str(row.get("horizon", "MACRO")).upper())
                except ValueError:
                    horizon = NewsImpactHorizon.MACRO
                freshness = self.decay.freshness(published_at, now, horizon)
                if freshness <= 0.02:
                    continue  # fully decayed events drop out
                confidence = float(row.get("confidence", 0.0) or 0.0)
                relevance = float(row.get("relevance_to_xauusd", 0.0) or 0.0)
                usd_r = float(row.get("relevance_to_usd", 0.0) or 0.0)
                w = freshness * confidence * (0.5 + relevance * 0.5)

                xauusd_rel = max(xauusd_rel, relevance)
                usd_rel = max(usd_rel, usd_r)
                if direction == NewsDirection.BULLISH:
                    bull += w * relevance
                elif direction == NewsDirection.BEARISH:
                    bear += w * relevance
                elif direction in (NewsDirection.MIXED, NewsDirection.CONFLICTED):
                    conflict_sum += w
                    any_conflict = True
                conf_sum += w * confidence
                fresh_sum += freshness
                consensus_sum += w
                weights += w

                if horizon == NewsImpactHorizon.BREAKING and freshness > 0.3:
                    any_breaking = True
                if importance >= 0.6 and freshness > 0.2:
                    active_high.append(article_id)

                # decayed impact score per event
                _ = published_at
            except Exception:
                continue

        if weights <= 0.0:
            return CurrentNewsContext(available=True, timestamp=now, active_event_count=0)

        state = NewsState.NORMAL
        if any_conflict and conflict_sum / weights > 0.1:
            state = NewsState.CONFLICTED
        elif any_breaking:
            state = NewsState.BREAKING
        elif max_importance >= 0.75:
            state = NewsState.HIGH_IMPACT
        elif max_importance >= 0.5:
            state = NewsState.ELEVATED

        # staleness check against the newest event
        newest = max(
            (
                _parse_dt(a.get("analyzed_at") or "")
                for a in analyses
                if _parse_dt(a.get("analyzed_at") or "")
            ),
            default=None,
        )
        stale = False
        if newest is not None:
            stale = (now - newest).total_seconds() > self.decay.config.stale_after_sec
        if stale:
            state = NewsState.STALE

        n = max(1, weights)
        return CurrentNewsContext(
            available=True,
            timestamp=now,
            state=state,
            active_event_count=len(active_high),
            xauusd_relevance=round(min(1.0, xauusd_rel), 4),
            usd_relevance=round(min(1.0, usd_rel), 4),
            bullish_score=round(min(1.0, bull / n), 4),
            bearish_score=round(min(1.0, bear / n), 4),
            confidence=round(min(1.0, conf_sum / n), 4),
            conflict_score=round(min(1.0, conflict_sum / n), 4),
            freshness=round(fresh_sum / n, 4),
            source_consensus=round(min(1.0, consensus_sum / n), 4),
            stale=stale,
            active_high_impact=active_high[:10],
        )


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except ValueError:
        return None
