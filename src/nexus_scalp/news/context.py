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
            analyses = self.db.list_analysis(limit=300)
            if not analyses:
                logger.info("[NEWS] context build: no analyses in DB (available=False)")
                return CurrentNewsContext(available=False, timestamp=now)
        except Exception as e:
            logger.error("[NEWS] context build: list_analysis failed", error=str(e))
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
        count = 0
        max_importance = 0.0
        any_breaking = False
        any_conflict = False

        # Deduplicate to the latest analysis per article so a re-analyzed
        # old article does not double-count and new analysis is authoritative.
        seen: set[str] = set()
        deduped: list[dict] = []
        for _r in analyses:
            _aid = str(_r.get("article_id") or "")
            if not _aid or _aid in seen:
                continue
            seen.add(_aid)
            deduped.append(_r)
        analyses = deduped

        # Build a one-shot map of article_id -> published_at so freshness
        # decays from the real event time (publication), not analysis time.
        # A late-analyzed 4h-old article must sit at 4h-ago on any timeline
        # and must decay as a 4h-old event; newer analysis is still more valid
        # because its w = freshness * confidence recency is encoded below and
        # the article list itself is ordered by published recency where relevant.
        published_by_id: dict[str, object] = {}
        try:
            ids = [r.get("article_id") for r in analyses if r.get("article_id")]
            if ids:
                ph = ",".join("?" for _ in ids)
                with self.db._connect() as _c:
                    _rows = _c.execute(
                        f"SELECT article_id, published_at FROM news_articles WHERE article_id IN ({ph});",
                        ids,
                    ).fetchall()
                    for _r in _rows:
                        published_by_id[str(_r["article_id"])] = _r["published_at"]
        except Exception:
            published_by_id = {}

        for row in analyses:
            try:
                article_id = row["article_id"]
                # Prefer real publication time; fall back to analysis time only if unknown
                raw_pub = published_by_id.get(article_id) or row.get("published_at") or ""
                published_at = _parse_dt(raw_pub) or _parse_dt(row.get("analyzed_at") or "")
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
                # Junk-NEUTRAL guard: ultra-low-signal NEUTRAL (e.g. Venmo tuition) was
                # flooding the last-100 window and diluting bull/bear to 0%. It still
                # counts for freshness/xauusd_rel but not for the directional denominator.
                is_junk_neutral = (direction == NewsDirection.NEUTRAL and relevance < 0.35 and importance < 0.4)
                usd_r = float(row.get("relevance_to_usd", 0.0) or 0.0)
                w = freshness * confidence * (0.5 + relevance * 0.5)

                xauusd_rel = max(xauusd_rel, relevance)
                usd_rel = max(usd_rel, usd_r)
                # Junk NEUTRAL does not dilute the bull/bear denominator
                if is_junk_neutral:
                    fresh_sum += freshness
                    count += 1
                    continue
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
                count += 1

                if horizon == NewsImpactHorizon.BREAKING and freshness > 0.3:
                    any_breaking = True
                if importance >= 0.6 and freshness > 0.2:
                    active_high.append(article_id)

                # decayed impact score per event
                _ = published_at
            except Exception:
                continue

        if weights <= 0.0:
            # Junk-only or zero-weight window: still report freshness (true publication recency)
            # so a fresh window isn't reported as stale/empty. New analysis remains authoritative
            # because a re-analysis keeps the same published_at recency but refreshes confidence.
            if count > 0:
                return CurrentNewsContext(
                    available=True,
                    timestamp=now,
                    state=NewsState.NORMAL,
                    active_event_count=0,
                    freshness=round(min(1.0, fresh_sum / max(count, 1)), 4),
                )
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

        # staleness check against the newest *published* event (true event time)
        def _newest_pub(a: dict) -> object:
            v = published_by_id.get(str(a.get("article_id") or "")) or a.get("published_at") or a.get("analyzed_at") or ""
            return _parse_dt(v) if v else None
        newest = max(
            (_newest_pub(a) for a in analyses if _newest_pub(a) is not None),
            default=None,
        )
        stale = False
        if newest is not None:
            stale = (now - newest).total_seconds() > self.decay.config.stale_after_sec
        if stale:
            state = NewsState.STALE

        n = max(1, weights)
        logger.info(
            "[NEWS] context built",
            available=True,
            state=state.value,
            active_events=len(active_high),
            analyses=len(analyses),
            count=count,
            bullish=round(min(1.0, bull / max(1, weights)), 4),
            bearish=round(min(1.0, bear / max(1, weights)), 4),
            freshness=round(min(1.0, fresh_sum / max(count, 1)), 4),
        )
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
            # freshness must stay in [0,1] (Pydantic le=1.0). The raw average of
            # per-article decay freshness can exceed 1.0 when weights < 1 (the
            # weighted denominator shrinks while fresh_sum stays ~count-sized);
            # a value > 1.0 raised a validation error that made the WHOLE news
            # context unavailable (UI news panel stuck/empty). Clamp like every
            # other score below, and normalize by the article count, which is
            # the semantically correct average of the freshness values.
            freshness=round(min(1.0, fresh_sum / max(count, 1)), 4),
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
