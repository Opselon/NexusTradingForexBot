# src/nexus_scalp/news/database.py

- PURPOSE: The dedicated News SQLite database (artifacts/news.db) — 13
  tables + 8 indexes, fully isolated from the trading audit ledger.
- ARCHITECTURE LAYER: Persistence (infrastructure). The trading
  AuditRepository is intentionally NOT used (docstring line 235).
- RESPONSIBILITY: schema bootstrap, source registry, canonical articles +
  dedup state, append-only versions, entities/topics, analysis/impacts/
  consensus/runs, trade+event links, source health, worker state,
  self-heal (rebuild_derived), summary.
- DEPENDENCIES: stdlib sqlite3/json; observability.logging. Models are
  NOT imported — rows are dicts (DB layer is model-agnostic).
- CONNECTS TO: NewsEngine, NewsFetcher, NewsIngestor, NewsAnalysisPipeline,
  NewsContextCache, PostEventValidator (which opens the same file path via
  sqlite3 directly), seed.
- KEY CONCEPTS:
  - Schema: news_sources, news_articles (article_hash UNIQUE NOT NULL),
    news_article_versions (append-only revision history), news_entities,
    news_topics, news_analysis, news_impacts, news_consensus,
    news_analysis_runs, news_worker_state, news_event_links,
    news_trade_links, news_health — journal_mode=WAL, synchronous=NORMAL.
  - Deterministic article identity: `article_hash UNIQUE` (line 55) is the
    dedup key; `mark_duplicate(article_hash, duplicate_of)` (line 403)
    set is_duplicate=1 + duplicate_of — the verified-duplicate record.
  - `insert_article` uses INSERT OR IGNORE (line 369) so a re-fetched
    article never duplicates; `add_evidence_source` (line 410) union-
    appends source_id to evidence_sources JSON — multi-source evidence
    without new rows.
  - `list_articles` (line 426): bounded limit [1,500]; excludes
    duplicates by default; optional asset_filter LIKE over title/summary/
    body; ORDER BY published_at DESC.
  - `insert_analysis` INSERT OR REPLACE keyed by analysis_id; derived
    tables replace_entities/replace_topics/replace_impacts are
    delete-all-then-insert per article — idempotent re-analysis.
  - `impact_timeline` (line 672): bucket aggregation (default 900s over
    24h, clamped 60..86400s / 1h..7d) of signed bullish/bearish strength*
    relevance for charting; uses SQLite datetime('now','-N hours') filter
    then Python bucketing.
  - `rebuild_derived` (line 909): self-heal — re-derives impacts/entities/
    topics/consensus counters from news_analysis payloads; NEVER touches
    raw article history.
- HOT PATH / PERFORMANCE: connections are per-call (5s timeout, no
  persistent conn) — fine for worker/API frequency; the live tick path
  never queries this DB (context cache only). Indexes cover published_at,
  source, duplicate_of, versions, analysis, impacts asset, trade links.
- EDGE CASES & PITFALLS: `_connect` failure inside initialize_schema is
  caught/logged but NOT raised — a broken DB silently yields empty reads
  later (context.build catches and marks state STALE); JSON columns use
  `default=str` for enums/datetimes; topics are stored as plain values
  (with `.value` unwrap); impact_timeline skips unparsable rows.
  `news_event_links` table exists but no writer is visible in this module.