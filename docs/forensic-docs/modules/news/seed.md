# src/nexus_scalp/news/seed.py

- PURPOSE: Deterministic, idempotent, repeatable, versioned seeding: source
  registry (authoritative sources first), source priorities/tiers, topic
  taxonomy (models.NewsTopic), asset mapping (AssetImpactProfile), default
  analysis config. Running the seed twice must NOT create duplicates
  (upsert on source_id PK).
- ARCHITECTURE LAYER: Bootstrap/seed (infrastructure).
- RESPONSIBILITY: canonical source list + asset driver profiles; safe to
  run repeatedly; NO API key required — seed acts as "source registry v2":
  verified feed URLs, conservative tier assignment, local analysis ready.
- DEPENDENCIES: NewsDatabase (upsert_source), models (AssetImpactProfile,
  SourceKind, SourceTier). No I/O beyond DB upserts.
- CONNECTS TO: NewsEngine constructor (seed_news_database runs on every
  engine start), relevance engine (get_asset_profiles / get_asset_profile —
  in-memory registry).
- KEY CONCEPTS:
  - SEED_VERSION = "2026-08-16-v2" — bump when seed content changes.
  - Tier 1 official sources (line 28): fed (FOMC, priority 1.0), bls
    (1.0), bea (0.95), ecb (0.95), boe (0.9), cftc (0.9 but enabled=False
    — verified 2026-08-16: no public CFTC RSS feed exists; kept registered
    for the COT calendar but DISABLED so it never silently fails), 
    ustreasury (0.85). Comments document live feed verifications
    (bea /rss/news 404 -> HTML page; ustreasury feed 503 -> HTML releases
    page) — honest registry, no fake feed URLs.
  - Tier 2: reuters (RSS, 0.8), marketwatch (0.7). Tier 3: forexlive
    (0.6), zerohedge (0.45). Feed URLs + poll intervals per source
    (180-600s).
  - _ASSET_PROFILES (line 167): XAUUSD COMMODITY with drivers (USD 0.30,
    BOND_YIELDS 0.25, INFLATION 0.20, GEOPOLITICS 0.15, RISK_OFF 0.10)
    and inverse_drivers (USD 1.0, BOND_YIELDS 1.0, RISK_ON 0.5 — weaker
    USD / falling yields support gold); USD, EUR, GBP, JPY, CHF currency
    profiles with central-bank/safety drivers.
  - `_source_row` (line 238): flattens enum .value, stamps seed_version.
  - `seed_sources` (line 254): upserts all sources — idempotent by PK.
  - `seed_asset_profiles` (line 264): returns the profile count; profiles
    are consumed in-memory by the relevance engine (the docstring claims
    persistence via the seed payload, but no column stores them — the
    authoritative copy is the module constant).
  - `seed_news_database` (line 283): full deterministic seed -> {sources,
    asset_profiles, seed_version}.
- HOT PATH / PERFORMANCE: startup-only; a dozen single-row upserts.
- EDGE CASES & PITFALLS: re-seeding overwrites operator edits to
  priority/poll_interval_sec/enabled on seeded source_ids (full update on
  conflict); sources absent from the seed list are never pruned from an
  existing DB; the cftc source is intentionally disabled (registered but
  silent); seed_asset_profiles persists nothing to the DB — the docstring
  overstates its persistence.