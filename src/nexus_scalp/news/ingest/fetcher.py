"""News ingestion: fetcher + scheduler + normalizer (PHASE 12).

The fetcher drives per-source polling with rate-limit / backoff / jitter;
the scheduler decides which sources are due; the normalizer is the
Deduplicator's canonicalize_item plus DB write path.

The News Worker invokes these OFF the live tick path (via asyncio.to_thread).
"""

from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.ingest.deduplicator import (
    NewsDeduplicator,
    canonicalize_item,
)
from nexus_scalp.news.models import NewsNovelty, normalize_datetime
from nexus_scalp.news.sources import NewsSourceAdapter, SourceFetchResult, build_adapter
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.ingest")

#: A story merged into an existing canonical event within this window.
SYNDICATION_WINDOW_SEC = 3600.0


class NewsScheduler:
    """Decides which sources are due for polling (per-source interval)."""

    def __init__(self) -> None:
        self._last_poll: dict[str, float] = {}

    def due_sources(
        self, sources: list[dict[str, Any]], now: float | None = None
    ) -> list[dict[str, Any]]:
        now = now if now is not None else time.time()
        due: list[dict[str, Any]] = []
        for src in sources:
            interval = float(src.get("poll_interval_sec", 300))
            last = self._last_poll.get(src["source_id"])
            # never polled -> immediately due
            if last is None or (now - last) >= interval:
                due.append(src)
        return due

    def mark_polled(self, source_id: str, now: float | None = None) -> None:
        self._last_poll[source_id] = now if now is not None else time.time()


class NewsFetcher:
    """Per-source fetch driver with rate-limit awareness + backoff."""

    def __init__(self, db: NewsDatabase, config: Any) -> None:
        self.db = db
        self.config = config
        self._health: dict[str, dict[str, Any]] = {}

    def _load_health(self, source_id: str) -> dict[str, Any]:
        cached = self._health.get(source_id)
        if cached is not None:
            return cached
        row = self.db.get_health(source_id)
        health = dict(row) if row else {"healthy": True, "consecutive_failures": 0}
        self._health[source_id] = health
        return health

    def _save_health(self, source_id: str, health: dict[str, Any]) -> None:
        self._health[source_id] = health
        self.db.update_health(source_id, health)

    def _backed_off(self, health: dict[str, Any], now: float) -> bool:
        backoff_until = health.get("backoff_until", "")
        if backoff_until:
            try:
                until_ts = datetime.fromisoformat(backoff_until.replace("Z", "+00:00")).timestamp()
                if now < until_ts:
                    return True
            except ValueError:
                pass
        return False

    def fetch_source(self, source_config: dict[str, Any]) -> SourceFetchResult:
        source_id = source_config["source_id"]
        now = time.time()
        health = self._load_health(source_id)

        if self._backed_off(health, now):
            return SourceFetchResult(ok=False, error="backoff active", status=None)

        # Restore conditional-GET validators persisted from the last success so
        # the feed body is NOT redownloaded when it is unchanged (bandwidth).
        if health.get("last_modified"):
            source_config["last_modified"] = health["last_modified"]
        if health.get("etag"):
            source_config["etag"] = health["etag"]

        # jitter: +/- 10% delay before hitting a source (rate-limit etiquette)
        time.sleep(random.uniform(0.0, 1.0))

        adapter: NewsSourceAdapter = build_adapter(source_config)
        result = adapter.fetch(limit=self.config.max_articles_per_fetch)

        # Persist validator headers from the adapter (it stores them on
        # source_config after a 200) so the NEXT poll sends them.
        if source_config.get("last_modified"):
            health["last_modified"] = source_config["last_modified"]
        if source_config.get("etag"):
            health["etag"] = source_config["etag"]

        if result.rate_limited:
            health.update(
                rate_limited=True,
                retry_after_sec=result.retry_after_sec,
                consecutive_failures=health.get("consecutive_failures", 0) + 1,
                last_failure_at=datetime.now(UTC).isoformat(),
                last_status=result.status,
                healthy=False,
            )
            from datetime import timedelta

            backoff = max(result.retry_after_sec, 30.0)
            health["backoff_until"] = (datetime.now(UTC) + timedelta(seconds=backoff)).isoformat()
            self._save_health(source_id, health)
            logger.warning(
                "[NEWS_FETCH] source=%s status=RATE_LIMITED retry_after=%.0f",
                source_id,
                result.retry_after_sec,
            )
            return result

        if not result.ok:
            health.update(
                consecutive_failures=health.get("consecutive_failures", 0) + 1,
                last_failure_at=datetime.now(UTC).isoformat(),
                last_status=result.status,
                healthy=False,
            )
            from datetime import timedelta

            backoff_sec = min(3600.0, 30.0 * (2.0 ** max(0, health["consecutive_failures"] - 1)))
            health["backoff_until"] = (
                datetime.now(UTC) + timedelta(seconds=backoff_sec)
            ).isoformat()
            health["rate_limited"] = False
            self._save_health(source_id, health)
            logger.warning(
                "[NEWS_FETCH] source=%s status=FAILURE error=%s failures=%d",
                source_id,
                result.error,
                health["consecutive_failures"],
            )
            return result

        # success
        health.update(
            consecutive_failures=0,
            last_success_at=datetime.now(UTC).isoformat(),
            last_status=result.status or 200,
            rate_limited=False,
            retry_after_sec=0.0,
            backoff_until="",
            healthy=True,
        )
        self._save_health(source_id, health)
        if result.status == 304:
            # Conditional GET: feed unchanged — zero body downloaded, nothing
            # to ingest. Log at debug so a quiet feed doesn't spam.
            logger.debug(
                "[NEWS_FETCH] source=%s status=NOT_MODIFIED (no body downloaded)",
                source_id,
            )
        else:
            logger.info(
                "[NEWS_FETCH] source=%s status=SUCCESS items=%d",
                source_id,
                len(result.items),
            )
        return result


class NewsIngestor:
    """Applies dedup + normalization + persistence for fetched items."""

    def __init__(self, db: NewsDatabase, deduplicator: NewsDeduplicator | None = None) -> None:
        self.db = db
        self.deduplicator = deduplicator or NewsDeduplicator(
            merge_window_sec=SYNDICATION_WINDOW_SEC
        )

    def ingest_source_items(
        self, source_config: dict[str, Any], result: SourceFetchResult
    ) -> dict[str, int]:
        """Ingests fetched items; returns counts for observability."""
        stats = {"new": 0, "duplicate": 0, "merged_evidence": 0, "skipped": 0}
        source_id = source_config["source_id"]
        source_name = source_config.get("name", source_id)
        now_ts = time.time()

        for item in result.items:
            canonical = canonicalize_item(item, source_id, source_name)
            title = canonical["title"]
            if not title:
                stats["skipped"] += 1
                continue

            article_hash = canonical["article_hash"]
            # Tombstone: previously pruned junk OR already-analyzed stories never re-enter
            # (re-analysis would confuse the AI decision layer — analyze once, never again).
            try:
                if self.db.is_analyzed_hash(article_hash):
                    stats["duplicate"] += 1
                    continue
                if self.db.is_junk_hash(article_hash):
                    stats["duplicate"] += 1
                    continue
            except Exception:
                pass
            existing = self.db.get_article_by_hash(article_hash)
            if existing:
                # Exact duplicate: strengthen evidence, no new impact.
                self.db.add_evidence_source(existing["article_id"], source_id)
                stats["duplicate"] += 1
                continue

            # Syndication / rewritten headline within the merge window.
            dup_id = self.deduplicator.find_duplicate_title(
                title_hash=canonical["title_hash"],
                published_ts=_ts(canonical["published_at"], now_ts),
                now_ts=now_ts,
            )
            if dup_id:
                self.db.mark_duplicate(article_hash=article_hash, duplicate_of=dup_id)
                self.db.add_evidence_source(dup_id, source_id)
                stats["merged_evidence"] += 1
                continue

            # Brand-new canonical article.
            article_id = f"news_{uuid.uuid4().hex[:12]}"
            published = normalize_datetime(_parse_dt(canonical["published_at"]))
            updated_raw = canonical.get("updated_at")
            updated = normalize_datetime(_parse_dt(updated_raw)) if updated_raw else None
            self.db.insert_article(
                {
                    "article_id": article_id,
                    "article_hash": article_hash,
                    "canonical_url": canonical["url"],
                    "title": title,
                    "summary": canonical["summary"],
                    "body": canonical["body"],
                    "language": source_config.get("language", "en"),
                    "source_id": source_id,
                    "source_name": source_name,
                    "published_at": published.isoformat(),
                    "updated_at": updated.isoformat() if updated else "",
                    "raw_categories": canonical.get("raw_categories", []),
                    "entities": [],
                    "topics": [],
                    "importance": "MINOR",
                    "importance_score": 0.0,
                    "novelty": NewsNovelty.NEW.value,
                    "is_duplicate": 0,
                    "duplicate_of": "",
                    "evidence_sources": [source_id],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self.deduplicator.register_canonical(
                article_hash=article_hash,
                title_hash=canonical["title_hash"],
                published_ts=_ts(canonical["published_at"], now_ts),
                article_id=article_id,
            )
            stats["new"] += 1
            logger.info(
                "[NEWS] event=INGESTED canonical_id=%s title=%.60s",
                article_id,
                title,
            )

        if stats["duplicate"] or stats["merged_evidence"]:
            logger.info(
                "[NEWS] event=DEDUPLICATED new=%d dup=%d merged=%d source=%s",
                stats["new"],
                stats["duplicate"],
                stats["merged_evidence"],
                source_id,
            )
        return stats


def _ts(value: Any, fallback: float) -> float:
    dt = _parse_dt(value)
    return dt.timestamp() if dt else fallback


def _parse_dt(value: Any):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
