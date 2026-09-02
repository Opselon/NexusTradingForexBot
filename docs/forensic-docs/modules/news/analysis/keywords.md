# src/nexus_scalp/news/analysis/keywords.py

- PURPOSE: Comprehensive deterministic keyword library + corpus-coverage
  analytics — 200+ keywords across 10 categories with topic mapping,
  XAUUSD directional bias, weight, aliases and negatives. The analytic
  backbone for local news pipeline and live coverage stats for the Web UI.
  Pure module data + pure functions, no I/O.
- ARCHITECTURE LAYER: Analysis (dataset + analytics).
- RESPONSIBILITY: keyword dataset (versioned), match primitives, coverage
  scan, per-article hits, precompiled-pattern cache.
- DEPENDENCIES: models (NewsDirection, NewsTopic); stdlib re/Counter/
  dataclasses. No DB access.
- CONNECTS TO: analysis/__init__ exports; Web UI coverage endpoint;
  keyword_hits_for_article for the feed UI.
- KEY CONCEPTS:
  - Snowflake dataclasses: NewsKeyword (keyword, category, topics,
    direction_bias, weight clamped [0,1], aliases, negatives);
    KeywordCoverage; KeywordDatasetSummary (version, totals, top N,
    direction distribution). KEYWORD_DATASET_VERSION = "2026-08-18-v3".
  - Dataset sections: _CURRENCIES (12, incl. pairs mapped to USD topic),
    _ASSETS (20 incl. SPOT GOLD/BULLION/S&P 500 with GOLD negatives
    GOLDEN STATE/GOLD MEDAL/GOLDEN GATE/GOLDMAN), _INSTITUTIONS (21),
    _MACRO (29 incl. RATE HIKE bearish / RATE CUT bullish / QT/QE /
    STAGFLATION/DEFLATION), _GEOPOLITICS (19 incl. IRAN/ISRAEL/HORMUZ/
    TARIFF/TRADE WAR/CEASEFIRE bearish), _ENERGY (9 incl. OPEC/HORMUZ),
    _DIRECTIONAL (bullish/bearish/neutral phrases), _FX_PAIRS (9).
  - Index/set derivations: _KEYWORD_INDEX (upper-cased), _BULLISH_/
    _BEARISH_KEYWORDS frozensets.
  - `_count_mentions` (line 429): word-boundary count over upper-cased
    text; ANY negative phrase present suppresses the keyword ENTIRELY
    (mirrors local.py _GOLD_NEGATIVES).
  - PRECOMPILED PATTERN CACHE (lines 450-558) — the hot-path fix:
    previously each (keyword x article) compiled a fresh regex; a coverage
    scan of 500 articles x 189 keywords = ~94,500 compilations. Now:
    `_dataset_pattern_fingerprint()` (line 464) builds a deterministic
    fingerprint over keyword text+aliases+negatives; `_ensure_pattern_cache`
    (line 505) rebuilds ONLY when the fingerprint changes (~300 patterns
    once, reused); `_word_boundary_pattern` (line 485) is the bounded
    per-token cache; `_count_mentions_cached` (line 529) adds a substring
    pre-filter (token not in text -> skip regex — exactly equivalent, never
    a false negative, since the boundary regex requires the literal token).
    Concurrency-safe: dict reads atomic; rebuild keyed by fingerprint can
    only serve same-dataset patterns. No re.IGNORECASE — text is pre-
    upper-cased and matching stays case-exact. Bounds: cache cleared above
    8192 entries.
  - `analyze_keyword_coverage` (line 561): pure scan — article_hits +
    mention totals + direction/topic distributions; empty corpus -> summary
    with zeros; top_n sort by (-hits, -mentions, name) deterministic;
    share = hits/total_articles.
  - `keyword_hits_for_article` (line 644): feed-UI list of matching
    keywords with mentions + topics. `pattern_cache_stats` (line 665):
    compiled_patterns + total_compilations observability.
- HOT PATH / PERFORMANCE: the cache turns O(articles x keywords) regex
  compilations into O(unique tokens) compilations + O(1) dict lookups;
  substring pre-filter skips regex for absent tokens (the common case).
  Scan is still O(articles x keywords) membership checks — bounded by
  limit_texts.
- EDGE CASES & PITFALLS: negative suppression is document-wide (a single
  "gold medal" mention anywhere kills GOLD for the whole article); tokens
  with spaces (e.g. "RATE HIKE") rely on exact literal presence after
  uppercasing; `limit_texts` caps texts AFTER iterating (no early break on
  the generator abort — the loop still walks articles); category_coverage
  in the summary is always {} (declared but never populated).