# src/nexus_scalp/news/models.py

- PURPOSE: Immutable domain contracts for the whole news subsystem — enums,
  article/source/analysis/impact/consensus records, the live decision cache
  and worker state. Mirrors the repo's frozen-pydantic pattern; pure data,
  no execution capability.
- ARCHITECTURE LAYER: Domain.
- RESPONSIBILITY: type-safe contracts consumed by ingest, analysis, gate,
  context, worker, memory, database, engine and the Web UI.
- DEPENDENCIES: pydantic (BaseModel, ConfigDict, Field, field_validator),
  stdlib (datetime/UTC, StrEnum).
- CONNECTS TO: everything in news/; CurrentNewsContext is the live-path
  handoff object read by the NewsGate and the 60D/70D feature builders.
- KEY CONCEPTS:
  - `_utc` (line 17): naive datetimes are tagged UTC, aware ones are
    converted — every datetime in the system is UTC.
  - Enums: NewsDirection (BULLISH/BEARISH/NEUTRAL/MIXED/CONFLICTED),
    NewsImpactHorizon (BREAKING minutes / MACRO hours / POLICY hours-days /
    STRUCTURAL days-weeks — drives decay half-lives), NewsImportance
    (TRIVIAL..CRITICAL), 21-value NewsTopic taxonomy, NewsState (the
    context state consumed by the gate: NORMAL/ELEVATED/HIGH_IMPACT/
    CONFLICTED/BREAKING/STALE), NewsNovelty, NewsAnalysisStatus
    (incl. LOCAL_ONLY + RATE_LIMITED), SourceTier (TIER_1..4) + SourceKind.
  - `NewsSource.trust_weight` (line 136): tier-derived trust (1.0/0.8/
    0.55/0.25) used by consensus — source count alone is never certainty.
  - `NewsSourceHealth.effective_backoff_sec` (line 161): exponential
    backoff base*2**(failures-1), capped at 3600s.
  - `NewsArticle` (line 183): identity is `article_hash` (deterministic,
    UNIQUE in DB); `is_duplicate` + `duplicate_of` carry dedup state;
    updates produce versions (news_article_versions). All datetimes
    coerced to UTC.
  - `NewsImpact.bounded_adjustment` (line 240): signed strength*relevance
    in [-1,1]; NEUTRAL/MIXED/CONFLICTED forced to 0.0 — the bounded
    adjustment source for the gate.
  - `NewsConsensus`: agreement/conflict/directions + weighted direction
    with confidence.
  - `NewsAnalysisResult`: the final per-article record incl. local_only
    flag, provider name, per-asset impacts, surprise assessment,
    contradictory_factors, reasoning_trace_id.
  - `CurrentNewsContext` (line 338): the ONLY news object the live path
    touches. Defaults are SAFE when news unavailable — available=False,
    all scores 0.0 (never fake-neutral confidence, docstring line 342).
    `.news_adjustment` (line 364) = (bullish-bearish)*confidence*freshness
    clamped by the caller to ±max_news_adjustment.
  - `TradeNewsLink`: trade<->news attribution row (news_trade_links).
  - Helpers: `normalize_datetime`, `model_dump_jsonable` (mode='json' safe).
- HOT PATH / PERFORMANCE: CurrentNewsContext is a cache-read per tick (no
  DB); frozen models are cheap; `.news_adjustment` is a few float ops.
- EDGE CASES & PITFALLS: bounded fields (0..1) everywhere — a >1.0 value
  raises a pydantic validation error, which historically made the whole
  context unavailable (see context.py clamp); direction strings stored in
  DB must be upper-cased before NewsDirection() construction (pipeline/
  context handle this).