# src/nexus_scalp/news/config.py

- PURPOSE: Complete News subsystem configuration — independently
  disable-able, defaults to HYBRID analysis, NO mandatory external API key
  (runs LOCAL_ONLY / HYBRID out of the box).
- ARCHITECTURE LAYER: Configuration (Domain-adjacent; pydantic settings).
- RESPONSIBILITY: polling cadence, analysis routing, decay half-lives, the
  bounded-impact invariants, DB path resolution. Follows the repository's
  Pydantic settings pattern (configuration/config.py).
- DEPENDENCIES: pydantic (BaseModel, Field); NewsState from models.
- CONNECTS TO: NewsEngine, NewsFetcher, NewsAnalysisPipeline,
  NewsContextCache, NewsGate, NewsDecayEngine — each reads its slice of
  NewsConfig.
- KEY CONCEPTS:
  - `NewsPollingConfig` (line 17): fast_interval_sec=300 (ge=60, breaking
    feeds, bandwidth-conscious), medium_interval_sec=900 (official
    releases), slow_interval_sec=3600 (calendars/COT).
  - `NewsAnalysisConfig` (line 25): mode LOCAL_ONLY | API_ONLY | HYBRID
    (default HYBRID); provider/api_base_url/model empty by default (=
    local-only); max_api_per_cycle=5; api_importance_floor=0.55 (HYBRID
    API eligibility threshold); request_timeout_sec=20; enabled=True.
  - `NewsDecayConfig` (line 38): per-horizon half-lives — BREAKING 15min,
    MACRO 4h, POLICY 24h, STRUCTURAL 5d; stale_after_sec=3600 — all gt=0.
  - `NewsImpactBounds` (line 48): THE bounded-influence hard contract:
    max_confidence_boost=0.05 (cap 0.20) — the ±5% boost ceiling;
    max_confidence_penalty=0.10 (cap 0.30) — the −10% penalty ceiling;
    min_alignment_to_boost=0.40; conflict_caution_threshold=0.55;
    max_news_adjustment=0.05 (cap 0.20); blocked_states=[BREAKING,
    HIGH_IMPACT]; caution_states=[CONFLICTED, ELEVATED]. Invariant
    (docstring): "news can NEVER override risk/exposure/safety. It is a
    contextual multiplier with explicit caps."
  - `NewsConfig` (line 68): enabled=True; db_path="artifacts/news.db";
    max_articles_per_fetch=200 (ge=1 le=2000); max_queue_size=1000;
    worker_interval_sec=60; context_ttl_sec=60; nested polling/analysis/
    decay/bounds default factories.
  - `resolve_db_path` (line 82): absolute paths used as-is; relative
    paths resolve against repo_root (Path.cwd() default) — the
    artifacts/ convention for databases.
- HOT PATH / PERFORMANCE: config objects instantiated once at engine
  construction; attribute reads only thereafter.
- EDGE CASES & PITFALLS: bounds are pydantic-validated (ge/le caps) so a
  bad config cannot widen the gate's influence; NewsConfig is a plain
  BaseModel (NOT frozen) — mutable by design for hot-reload, but a partial
  copy could desync gate vs engine; api_importance_floor lives in
  analysis config while pipeline reads it via _api_eligible — keep both
  consumers on the same instance.