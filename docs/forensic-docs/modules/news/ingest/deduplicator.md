# src/nexus_scalp/news/ingest/deduplicator.py

- PURPOSE: The news deduplication engine — deterministic article identity so
  the same story via multiple feeds / syndication / rewritten headlines /
  different URLs collapses into ONE canonical event with MULTIPLE source
  evidence.
- ARCHITECTURE LAYER: Domain/ingest logic (pure functions + a windowed
  in-memory index).
- RESPONSIBILITY: canonical identity hashing (title_hash + article_hash),
  URL/title normalization, exact-dup and syndication-window matching.
- DEPENDENCIES: stdlib hashlib/re/unicodedata; models not imported (pure
  data in/out).
- CONNECTS TO: NewsIngestor (canonicalize_item, find_duplicate_title,
  register_canonical), DB write path.
- KEY CONCEPTS:
  - Identity (docstring): sha256 over canonical URL (normalized) +
    normalized title + source + publication time bucket + content
    fingerprint.
  - `normalize_url` (line 87): strips scheme/www, query+fragment
    (drops ALL query params incl. utm), trailing slash; lowercased.
  - `normalize_title` (line 98): NFKD normalize, lowercase, non-alnum ->
    space, whitespace squeeze, stopword prune (large _STOPWORDS set incl.
    "new/report/says/will") — identity tokens only.
  - `compute_article_hash` (line 119): source_id IS part of identity, and
    published_at is bucketed to 60s (line 135) so identical stories
    published seconds apart still merge while distinct coverage stays
    distinct; payload joined with '|'; sha256 hexdigest.
  - `canonicalize_item` (line 150): normalizes one raw feed item into the
    canonical dict shape (datetimes UTC; _as_dt defaults to now for
    unparsable timestamps).
  - `NewsDeduplicator` (line 200): strategy — 1) exact article_hash hit ->
    duplicate (evidence added upstream); 2) normalized-title + source hit
    within merge window -> duplicate; 3) normalized-title ANY source within
    short window (syndication) -> duplicate; 4) else canonical NEW.
    `find_duplicate_title` (line 221): window measured on PUBLICATION time
    proximity, NOT ingestion wall-clock — a story published 10:00 and
    syndicated 10:02 is the same story whether ingested today or three days
    later. `register_canonical` keeps a title_hash -> [(published_ts,
    article_id)] map (unbounded growth of _recent_by_title — no pruning).
- HOT PATH / PERFORMANCE: per-item sha256 over ~2KB text + regex cleaning —
  fine at feed scale; the window map is in-memory per ingestor instance.
- EDGE CASES & PITFALLS: `_STOPWORDS` includes "new" — "new gold record"
  and "gold record" collide; normalize_url drops fragment+query entirely
  (two URLs differing only in utm merge — intended); `_recent_by_title`
  grows without bound over the process lifetime (no TTL sweep) — memory
  grows with unique titles ingested; _as_dt silently uses now() when a
  timestamp cannot be parsed (a malformed feed timestamp becomes
  ingest-time, affecting the 60s bucket); title_hash only covers title —
  two different stories sharing a headline collide within the window.