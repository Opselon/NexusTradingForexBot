# src/nexus_scalp/news/__init__.py

- PURPOSE: Public surface of the News Intelligence Engine (PHASE 12) — a
  completely isolated, production-grade news subsystem for XAUUSD / GOLD /
  USD / major FX markets.
- ARCHITECTURE LAYER: Package facade (Application-adjacent wiring).
- RESPONSIBILITY: re-export every public contract so consumers import from
  `nexus_scalp.news` instead of deep internals:
  NewsConfig, NewsContextCache, NewsDatabase, NewsEngine,
  NewsGate / NewsGateDecision / NewsGateVerdict, all domain models
  (NewsArticle, NewsConsensus, NewsDirection, NewsEntity, NewsImpact,
  NewsImpactHorizon, NewsImportance, NewsSource, NewsSourceHealth,
  NewsState, NewsTopic, NewsWorkerState, TradeNewsLink,
  CurrentNewsContext, AssetImpactProfile, NewsAnalysisResult),
  NewsWorker, format_news_worker_status, seed_news_database.
- DEPENDENCIES: every module inside news/ (config, context, database,
  engine, gate, models, seed, worker).
- CONNECTS TO: live path via NewsEngine + NewsGate / NewsContextCache;
  web UI via format_news_worker_status and the model types.
- KEY CONCEPTS:
  - Single import surface; the multiline import listing mirrors the
    `__all__` order 1:1 — the canonical public API of the subsystem.
  - Isolation contract (docstring lines 8-14) is architectural, not just
    cosmetic: the news package must be deletable without touching the
    trading ledger.
  - The docstring states the guarantees the live path relies on:
    dedicated news.db (never mixes with the trading audit ledger); news
    never places/modifies/closes an order (no adapter, no order manager,
    no risk engine); the News Worker runs concurrently via
    asyncio.to_thread and NEVER blocks the live tick path; a News Engine
    failure can never stop trading.
- HOT PATH / PERFORMANCE: import-time only; no runtime cost on the live
  path.
- EDGE CASES & PITFALLS: no logic here; if a name is missing from
  `__all__` the UI/application import breaks — keep `__all__` in sync
  with the import block (they are currently identical).
- NOTE: this facade deliberately exposes zero internals — no raw sqlite
  handles, no fetcher internals, no scheduler state.

- RELATED ARTIFACTS:
  - src/nexus_scalp/news/models.py — the frozen domain contracts exposed
    here (NewsArticle, CurrentNewsContext, TradeNewsLink, ...).
  - docs/70D_NEWS_INTELLIGENCE_MODEL.md — the phase-12 specification the
    package implements.
- REVISION NOTES: facade re-exports unchanged since the PHASE 12 cut;
  engine/gate/context have evolved independently without changing the
  public surface.
