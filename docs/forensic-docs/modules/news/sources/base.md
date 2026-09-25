# src/nexus_scalp/news/sources/base.py

- PURPOSE: News source adapters — a common Source -> Fetcher -> Normalizer
  -> Canonical Article contract so the engine is NOT coupled to RSS: RSS/
  Atom today, official feeds, future API adapters implement the same
  interface.
- ARCHITECTURE LAYER: Infrastructure (external I/O boundary).
- RESPONSIBILITY: typed fetch results, RSS/Atom parsing (feedparser with a
  minimal XML fallback), official-source strictness, adapter factory.
- DEPENDENCIES: models (NewsArticle), stdlib (abc, xml.etree), optional
  httpx + feedparser (ImportError-tolerant).
- CONNECTS TO: NewsFetcher (adapter.fetch + conditional-GET header
  plumbing), engine.
- KEY CONCEPTS:
  - `SourceFetchResult` (line 22): ok / items / status / error /
    rate_limited / retry_after_sec — typed failure taxonomy (no exceptions
    leak to the caller).
  - `NewsSourceAdapter` ABC: feed_url + timeout from source_config;
    abstract fetch(); `_make_article` raises NotImplementedError (defined
    but unused by the concrete adapters — they return raw dicts instead);
    `_parse_dt` UTC-normalizes.
  - `RSSNewsSourceAdapter.fetch` (line 87): BANDWIDTH BUDGET (2026-08-18
    comment): Accept-Encoding gzip/deflate (70-85% smaller bodies),
    If-Modified-Since / If-None-Match conditional GET (304 -> ok with ZERO
    body download, parse skipped), 2MB Content-Length cap (runaway feed
    truncated quickly), User-Agent NexusScalpEngine/1.0. 429 -> typed
    rate-limit result with Retry-After; >=400 -> typed HTTP failure;
    validators persisted into source_config for the caller to mirror into
    health.
  - `_parse_feed` (line 150): feedparser; bozo + no entries -> malformed
    error; ImportError -> `_parse_xml_minimal` fallback (RSS item / Atom
    entry via ElementTree) so the subsystem remains importable anywhere.
  - `_normalize_feedparser_entry` (line 167): IMPORTANT — checks
    `"updated" in entry` BEFORE direct attribute access to avoid
    feedparser's deprecated published->updated fallback DeprecationWarning
    on every call for published-only feeds (comment lines 172-177); body
    from content[0].value; tags -> categories terms.
  - `OfficialSourceAdapter` (line 231): same mechanics but an empty or
    malformed official feed is a typed FAILURE ("official feed returned no
    items") — never silently trusted (line 241-246).
  - `build_adapter` (line 250): factory — OFFICIAL/CALENDAR ->
    OfficialSourceAdapter, else RSSNewsSourceAdapter.
- HOT PATH / PERFORMANCE: off-tick (worker thread); conditional GET is the
  big win (no body parse on 304); parsing via feedparser is C-backed, the
  XML fallback is slower but rare.
- EDGE CASES & PITFALLS: `_make_article` exists but concrete adapters never
  build NewsArticle objects (they return dicts — NewsIngestor canonicalizes)
  — dead-ish API surface; CALENDAR kind gets the official adapter with no
  calendar semantics; 2MB cap checks Content-Length only (chunked responses
  without CL are not capped); _parse_feed swallows generic parse errors
  into a typed error result; retry_after defaults to 60 when the header
  is missing on 429.