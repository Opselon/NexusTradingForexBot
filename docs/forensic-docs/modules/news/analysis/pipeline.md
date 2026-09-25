# src/nexus_scalp/news/analysis/pipeline.py

- PURPOSE: News analysis pipeline orchestrator — 10-stage agentic pipeline.
  External AI is OPTIONAL and never mandatory: LOCAL analysis is the
  authoritative fallback on missing key / rate limit / timeout / malformed
  response / quota exhaustion. HYBRID (default) routes to API only for
  important/ambiguous/high-relevance events.
- ARCHITECTURE LAYER: Analysis orchestration (application service).
- RESPONSIBILITY: per-article staged analysis + persistence; batch
  bounded analysis of unanalyzed articles; external AI fail-safe merging.
- DEPENDENCIES: LocalNewsAnalyzer, NewsDecayEngine, NewsDatabase, models,
  httpx (optional, lazy), observability.
- CONNECTS TO: NewsEngine (analyze_cycle, analyze_article_id); DB analysis/
  impacts/entities/topics/runs tables.
- KEY CONCEPTS:
  - Stages (docstring): 1-2 ingest/dedup (done upstream); 3 local relevance
    filtering; 4 local importance; 5 entity/topic; 6 impact hypothesis;
    7 external AI ONLY when warranted; 8 validation; 9 persisted record;
    10 integration via NewsContextCache.
  - `ExternalNewsAnalyzer` (line 45): contract — "Implementations must
    NEVER raise into the pipeline: every failure path returns None so the
    local analysis remains authoritative." available() False by default.
  - `DefaultExternalAnalyzer` (line 72): OpenAI-compatible
    POST {base}/chat/completions with Bearer key; available() requires
    api_base_url+model+api_key; strict-JSON system prompt, temperature 0.2,
    response_format json_object; body capped at 2000 chars; known_entities/
    topics + local_direction/importance passed as context; 429/500/502/
    503/504 or any exception -> None (API_FALLBACK logged). Synchronous
    httpx — safe because the pipeline runs off the tick path.
  - `NewsAnalysisPipeline.analyze_article` (line 193): run/analysis ids;
    local STAGE 3-6; result built with confidence = 0.4 + importance*0.3;
    source_priority from registry; impact_strength = max XAUUSD impact
    strength.
  - API eligibility (line 275): external.available() AND (mode API_ONLY OR
    importance >= floor (0.55) OR xauusd_rel >= 0.5) — bounded per cycle
    by the worker's ANALYZE_PER_CYCLE, not max_api_per_cycle (that config
    field is unused here).
  - `_merge_external` (line 285): direction/horizon parsed with ValueError
    fallback to local; strength/confidence/relevance clamped [0,1]; local
    entities/topics/impacts/importance retained; external adds surprise_
    assessment, market_mechanism, contradictory_factors, risks,
    reasoning_trace_id; local_only=False, provider=external name.
  - `_persist` (line 333): insert_analysis + replace_entities/topics/
    impacts (delete-all-then-insert — idempotent re-analysis).
  - `analyze_recent_unanalyzed` (line 378): list_articles(100) -> skip
    analyzed -> build NewsArticle from row -> analyze_article — bounded by
    limit.
- HOT PATH / PERFORMANCE: worker thread; up to limit articles per batch,
  one httpx call each only when eligible; everything else is regex+dict.
- EDGE CASES & PITFALLS: API failure after a partial response is treated
  as None (full local fallback); _parse_response requires json.loads of
  the content string — an API returning JSON without a content string
  yields None; `max_api_per_cycle` is unused (per-cycle bounding is done
  by the worker); `_merge_external` keeps local.importance_score even when
  the API implied different importance (intentional — external can't
  override the bounded local scoring).