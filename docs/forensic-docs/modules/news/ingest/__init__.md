# src/nexus_scalp/news/ingest/__init__.py

- PURPOSE: News ingest package facade — fetcher/scheduler/normalizer/
  deduplicator public surface for the PHASE 12 ingestion stage.
- ARCHITECTURE LAYER: Package facade.
- RESPONSIBILITY: re-export NewsDeduplicator, NewsFetcher, NewsIngestor,
  NewsScheduler plus the identity primitives canonicalize_item,
  compute_article_hash, compute_title_hash, normalize_title,
  normalize_url.
- DEPENDENCIES: ingest/deduplicator, ingest/fetcher.
- CONNECTS TO: engine (imports NewsFetcher / NewsIngestor /
  NewsScheduler from nexus_scalp.news.ingest); external callers needing
  deterministic hash / normalization helpers for news identity.
- KEY CONCEPTS:
  - Thin re-export module — keeps the engine's import surface stable
    even if the internal file layout changes.
  - The exported hash/normalize functions are the canonical identity
    primitives: compute_article_hash (full canonical identity with a
    60s publication-time bucket), compute_title_hash (fast exact-title
    identity), normalize_title / normalize_url (stopword-pruned /
    tracking-param-stripped) — shared by the ingestor and the
    deduplicator.
  - The normalizer named in the docstring IS canonicalize_item (it
    lives in deduplicator.py, not in a separate module).
- HOT PATH / PERFORMANCE: import-time only; the heavy lifting happens
  in the concrete modules, all off the live tick path.
- EDGE CASES & PITFALLS: no logic here; keep __all__ synced with the
  import block; the docstring's module list ("fetcher/scheduler/
  normalizer/deduplicator") slightly overstates the file layout — the
  normalizer is implemented as canonicalize_item inside deduplicator.py.
- NOTE: identity primitives are pure and deterministic — the same
  article re-fetched days later hashes identically and dedups onto the
  same canonical event.

- RELATED ARTIFACTS:
  - src/nexus_scalp/news/ingest/deduplicator.py — the identity + window
    logic behind these helpers.
  - src/nexus_scalp/news/ingest/fetcher.py — scheduler/fetcher/ingestor.
- REVISION NOTES: the 60s publication-time bucket inside
  compute_article_hash is the key merge lever for syndication timing
  differences.
