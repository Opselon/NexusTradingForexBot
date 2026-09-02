# src/nexus_scalp/news/engine.py

- PURPOSE: NewsEngine — orchestrator that wires sources -> ingest ->
  analysis -> memory -> context into one bounded, failure-isolated
  subsystem. Holds NO execution capability (no adapter / order manager /
  risk engine); it can never place a trade.
- ARCHITECTURE LAYER: Application service (facade over the subsystem).
- RESPONSIBILITY: seed + expose news.db; one bounded ingestion pass; bounded
  priority-aware analysis; live context cache maintenance; post-event
  validation + trade linkage; self-heal; health/summary.
- DEPENDENCIES: NewsConfig, NewsDatabase, seed_news_database, NewsScheduler,
  NewsFetcher, NewsIngestor, NewsAnalysisPipeline, NewsContextCache,
  PostEventValidator, models (CurrentNewsContext, NewsAnalysisResult,
  NewsArticle, NewsDirection, NewsNovelty, normalize_datetime).
- CONNECTS TO: NewsWorker (drives cycles), Web API (health, status,
  analyze_article_id, self_heal), application startup wiring.
- KEY CONCEPTS:
  - Constructor seeds the DB every time (idempotent upserts).
  - `ingest_cycle(max_sources=10)` (line 71): scheduler due-sources ->
    fetch -> on success, 304 handling ("conditional GET, feed unchanged:
    nothing to ingest… avoids dedup work + DB writes") -> ingest_source_items;
    per-source stats; failure-isolated (broken source just counts polled).
  - `analysis_cycle(limit=20)` delegates to pipeline.analyze_recent_
    unanalyzed — bounded.
  - `current_context(force=False)` (line 110): cache-only on the live tick
    path; force=True rebuilds from DB (worker/API/self-heal only); any
    build exception returns safe defaults (available=False) — engine
    failure can never stop trading.
  - `link_trade` (line 126): creates trade<-news attribution link
    (tlnk_uuid). `link_trade_to_best_news` (line 150): falls back from
    active_high_impact[0] to newest analysis row when context unavailable;
    alignment = ctx.news_adjustment.
  - `record_market_response` (line 175): feeds PostEventValidator with the
    stored analysis prediction.
  - `analyze_article_id` (line 202): manual/AI-Analyze-button path; never
    blocks on external AI (API failure falls back to local inside the
    pipeline); returns {ok, analysis_id, status}.
  - `self_heal` (line 232): rebuild_derived + forced context refresh.
  - `health` (line 244): subsystem availability is True even when the
    context honestly reports available=False (no evidence) — availability
    is about the SUBSYSTEM, not the derived state (comment lines 248-250).
- HOT PATH / PERFORMANCE: all heavy work is invoked from the worker off the
  event loop; tick path only calls current_context() (cache read).
- EDGE CASES & PITFALLS: `_parse` (line 264) tolerates str/None dates;
  `link_trade_to_best_news` can link a trade to a stale article when there
  is no active context (explicitly documented behavior); analyze_article_id
  rebuilds a fresh NewsArticle from the DB row so DB fields not present in
  the row default to empty (raw_categories reset).