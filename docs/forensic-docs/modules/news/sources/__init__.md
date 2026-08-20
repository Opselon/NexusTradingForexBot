# src/nexus_scalp/news/sources/__init__.py

- PURPOSE: News source-adapters package facade — the "plugin registry"
  surface for source adapters (RSS/Atom today, official feeds, future
  API adapters).
- ARCHITECTURE LAYER: Package facade (plugin registry surface).
- RESPONSIBILITY: re-export NewsSourceAdapter (the ABC),
  OfficialSourceAdapter, RSSNewsSourceAdapter, SourceFetchResult (the
  typed fetch outcome), and build_adapter (the factory that maps a
  source_config row to an adapter).
- DEPENDENCIES: sources/base.
- CONNECTS TO: fetcher (build_adapter per source), engine; future
  adapter plugins register here and via build_adapter's dispatch.
- KEY CONCEPTS:
  - The registry is the seam that keeps the engine decoupled from any
    concrete feed format: RSS/Atom today, official HTML pages (seed
    sources point feed_url at the live release pages where RSS is
    missing or broken), and future API adapters all implement the same
    NewsSourceAdapter.fetch contract.
  - SourceFetchResult carries a typed failure taxonomy (ok / status /
    error / rate_limited / retry_after_sec) so callers never have to
    catch adapter exceptions.
  - Official adapters enforce strictness: an empty or malformed
    official feed is a typed failure, never silently trusted.
- HOT PATH / PERFORMANCE: import-time only; adapter fetch runs off the
  tick path in the worker.
- EDGE CASES & PITFALLS: the dispatch logic lives in base.build_adapter
  — keep the two surfaces in sync (kind OFFICIAL/CALENDAR ->
  OfficialSourceAdapter; everything else -> RSSNewsSourceAdapter);
  adding a new adapter kind requires touching both this facade (export)
  and the factory dispatch.
- NOTE: adapters are the ONLY place external network I/O happens in the
  news subsystem — the rest of the pipeline is pure computation.

- RELATED ARTIFACTS:
  - src/nexus_scalp/news/sources/base.py — ABC + RSS/Atom adapter +
    official adapter + build_adapter factory.
  - src/nexus_scalp/news/seed.py — the source registry rows these
    adapters consume.
- REVISION NOTES: the 2026-08-18 bandwidth work (gzip, conditional GET,
  2MB cap) lives entirely inside base.py behind this facade.
