# src/nexus_scalp/news/ingest/fetcher.py

- PURPOSE: News ingestion driver: per-source polling with rate-limit /
  backoff / jitter (NewsFetcher), due-source scheduling (NewsScheduler),
  and dedup + normalization + persistence (NewsIngestor). Runs OFF the
  live tick path (via asyncio.to_thread).
- ARCHITECTURE LAYER: Ingest (application service).
- RESPONSIBILITY: decide due sources, fetch with conditional GET + health
  tracking, canonicalize/dedup/persist items.
- DEPENDENCIES: NewsDatabase, NewsDeduplicator + canonicalize_item,
  sources.build_adapter / NewsSourceAdapter / SourceFetchResult, models
  (NewsNovelty, normalize_datetime), random/time/uuid.
- CONNECTS TO: NewsEngine.ingest_cycle -> NewsFetcher -> NewsIngestor;
  DB health/evidence tables.
- KEY CONCEPTS:
  - SYNDICATION_WINDOW_SEC = 3600 (merge window for rewritten headlines).
  - `NewsScheduler` (line 33): in-memory per-source last-poll map;
    never-polled sources are immediately due; due = now-last >= interval.
  - `NewsFetcher` (line 56): per-source health cache (in-memory + DB
    news_health); `_backed_off` skips sources inside backoff_until; restores
    last_modified/etag from health into source_config (conditional GET);
    0..1s jitter before hitting a source (rate-limit etiquette). On
    rate-limit (429 via adapter result): sets rate_limited, backoff =
    max(retry_after, 30s). On failure: exponential backoff
    30*2**(failures-1) capped 3600s. On success: resets consecutive_failures,
    persists validators for the NEXT poll. 304 -> ok=True with no items —
    zero body downloaded, logged at debug (quiet feeds don't spam).
  - `NewsIngestor.ingest_source_items` (line 196): per item — canonicalize;
    empty title skipped; exact article_hash hit -> add_evidence_source +
    stats.duplicate; title-window dup -> mark_duplicate(article_hash,
    duplicate_of=dup_id) + add_evidence_source (the verified duplicate
    record: is_duplicate=1, duplicate_of=<canonical id>) + merged_evidence;
    else new article (news_uuid, novelty NEW, evidence_sources=[source_id])
    + register_canonical. Returns {new, duplicate, merged_evidence, skipped}.
- HOT PATH / PERFORMANCE: 0..1s sleep per source (bounded jitter, blocking
  but off-tick); httpx conditional GET avoids body download on 304;
  dedup is DB-hash lookup + in-memory window; max_articles_per_fetch
  bounds parsing.
- EDGE CASES & PITFALLS: `_ts`/`_parse_dt` fallback to now on unparsable
  dates; a 304 keeps health "success" (correct — feed unchanged);
  `fetch_source` mutates source_config in place with validator headers —
  callers must pass a dict they own; a source with kind CALENDAR routes to
  OfficialSourceAdapter (build_adapter line 253) but no calendar-specific
  parsing exists — CALENDAR feeds parse as RSS/Atom or fail honestly.