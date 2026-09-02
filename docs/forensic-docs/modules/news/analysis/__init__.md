# src/nexus_scalp/news/analysis/__init__.py

- PURPOSE: News analysis package facade — the public surface for the
  local + optional external + pipeline stages of the news subsystem
  (PHASE 12).
- ARCHITECTURE LAYER: Package facade (analysis stage of the pipeline).
- RESPONSIBILITY: re-export compute_consensus, NewsDecayEngine, the
  full keywords API (KeywordCoverage, KeywordDatasetSummary,
  NewsKeyword, analyze_keyword_coverage, categories, get_keyword,
  get_keyword_dataset, keyword_count, keyword_hits_for_article,
  keywords_by_category, pattern_cache_stats), LocalNewsAnalyzer,
  NewsAnalysisPipeline, DefaultExternalAnalyzer, ExternalNewsAnalyzer.
- DEPENDENCIES: analysis modules consensus, decay, keywords, local,
  pipeline.
- CONNECTS TO: engine (imports NewsAnalysisPipeline from
  nexus_scalp.news.analysis); Web UI / coverage tooling use the keywords
  API; external callers use the analyzer interfaces directly.
- KEY CONCEPTS:
  - One-stop import surface for the analysis stage; the pipeline is the
    entry point (analyze_article / analyze_recent_unanalyzed).
  - pattern_cache_stats is exported so the precompile-patterns hot-path
    fix (keywords.py) is observable at runtime — compiled_patterns count
    and total_compilations.
  - The exports mirror the module layout: consensus (multi-source
    agreement), decay (half-life freshness), keywords (dataset +
    coverage analytics), local (rule-based analyzer, NO API KEY
    REQUIRED), pipeline (orchestrator + optional external AI with local
    fallback).
- HOT PATH / PERFORMANCE: import-time only; runtime cost lives in the
  concrete modules (all off the tick path).
- EDGE CASES & PITFALLS: keep exports synced with __all__; `categories`
  is a function (not a constant dict) — callers must invoke it; adding
  a new analysis module requires updating this facade and __all__
  together.
- NOTE: every export is a pure computation — nothing in this package
  can place, modify or close an order.

- RELATED ARTIFACTS:
  - src/nexus_scalp/news/analysis/pipeline.py — the orchestrator that
    consumes every other analysis module.
  - src/nexus_scalp/news/analysis/keywords.py — dataset + the
    precompiled-pattern cache (the ~94,500-regex-per-call hot path fix).
- REVISION NOTES: pattern_cache_stats was added to the public surface
  together with the pattern-cache fix so the cache behavior is
  observable.
