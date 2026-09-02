# src/nexus_scalp/news/context.py

- PURPOSE: The live decision-context cache — one derived CurrentNewsContext
  object that the live tick path reads WITHOUT touching the news DB.
- ARCHITECTURE LAYER: Application service bridging analysis -> live path.
- RESPONSIBILITY: build/refresh a bounded context from recent analyses with
  natural decay; safe defaults (available=False) when no evidence exists;
  keep the tick path DB-free.
- DEPENDENCIES: NewsDatabase, NewsDecayEngine, CurrentNewsContext +
  direction/horizon/state enums.
- CONNECTS TO: NewsEngine.current_context (tick path: cache-only get) and
  NewsWorker (refresh off the event loop); feeds NewsGate and the feature
  builders (60D/70D news blocks).
- KEY CONCEPTS:
  - `get(force=False)` (line 51): cache-only on the live path. First call
    falls to `build_once_safe` (line 65) — a safe context with
    available=False, no DB hit, so even the very first tick is safe before
    the worker has run.
  - `refresh()` (line 76): worker-only rebuild; the TTL never triggers a
    synchronous SQLite query inside the tick pipeline.
  - `build()` (line 86) aggregation over the newest 100 analyses:
    per-event freshness = decay.freshness(analyzed_at, now, horizon);
    events with freshness <= 0.02 drop out; weight w = freshness *
    confidence * (0.5 + relevance*0.5); bullish/bearish accumulate w*
    relevance; MIXED/CONFLICTED accumulate conflict mass; state ladder:
    CONFLICTED (conflict share > 10%) > BREAKING (freshness>0.3 breaking
    event) > HIGH_IMPACT (max importance >= 0.75) > ELEVATED (>=0.5) >
    NORMAL; stale if newest analyzed_at older than stale_after_sec; every
    score clamped to [0,1]; active_high_impact = article_ids with
    importance>=0.6 & freshness>0.2 (max 10).
  - Never fake-neutral: no analyses -> available=False with confidence 0.0
    (line 95-97); DB failure -> STALE + stale=True (line 100).
  - Freshness clamp fix (lines 215-222): the raw weighted average of decay
    freshness can exceed 1.0 when weights < 1 (weighted denominator
    shrinks while fresh_sum stays ~count-sized), which previously raised a
    pydantic le=1.0 error and made the WHOLE context unavailable (UI news
    panel stuck/empty). Now normalized by article count and clamped —
    semantically the correct average.
- HOT PATH / PERFORMANCE: per-tick cost = attribute read of a cached frozen
  object. All DB work and decay math happens in the worker cycle.
- EDGE CASES & PITFALLS: rows with unparsable analyzed_at are skipped;
  every row processed in a try/except-continue so one corrupt row cannot
  kill the build; xauusd/usd relevance take the MAX over events (not the
  mean); direction parsed with .upper() and guarded by NewsDirection()
  construction inside the try.