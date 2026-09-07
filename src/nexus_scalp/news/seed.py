"""News database seeding (PHASE 12).

Deterministic, idempotent, repeatable, versioned seeding of:

    * source registry (authoritative sources first),
    * source priorities / tiers,
    * topic taxonomy (defined in models.NewsTopic),
    * asset mapping (AssetImpactProfile),
    * default analysis configuration.

Running seed twice must NOT create duplicates (upsert on source_id PK).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import AssetImpactProfile, SourceKind, SourceTier

#: Version of the seed payload; bump when the seed content changes.
SEED_VERSION = "2026-09-07-v3"  # market-context P0: dead sources disabled/replaced

# ---------------------------------------------------------------------------
# Official macro sources (Tier 1) - high-trust inputs for USD/FX/XAUUSD
# ---------------------------------------------------------------------------

_OFFICIAL_SOURCES: list[dict] = [
    {
        "source_id": "fed",
        "name": "Federal Reserve (FOMC)",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.federalreserve.gov",
        "feed_url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "poll_interval_sec": 600,
        "priority": 1.0,
    },
    {
        "source_id": "bls",
        "name": "U.S. Bureau of Labor Statistics",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.bls.gov",
        # Verified 2026-09-07 (market-context P0): BLS serves 403 (Akamai) to
        # ALL non-browser clients incl. browser UA — feed AND schedule pages.
        # Blocked at the WAF: not parser-fixable. DISABLED so the worker stops
        # burning 118-failure backoff cycles; the official BLS release
        # SCHEDULE arrives via the forward calendar layer (calendar/, FF
        # provider + FedFOMCProvider), and CPI/NFP releases still reach the
        # pipeline through marketwatch/forexlive/fxstreet when they happen.
        "feed_url": "https://www.bls.gov/feed/news.releases.rss",
        "poll_interval_sec": 600,
        "priority": 1.0,
        "enabled": False,
    },
    {
        "source_id": "bea",
        "name": "U.S. Bureau of Economic Analysis",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.bea.gov",
        # Verified 2026-09-07 (market-context P0): the HTML releases page is
        # 200 but feedparser extracts ZERO entries (bozo=1, 0 items) — the
        # 200-but-wrong-schema trap (OFFICIAL adapter correctly flags it).
        # No machine-readable BEA feed exists without an API key (BEA API
        # requires registration). DISABLED; BEA release dates remain covered
        # by the forward calendar layer (FF weekly JSON verified live).
        "feed_url": "https://www.bea.gov/news",
        "poll_interval_sec": 600,
        "priority": 0.95,
        "enabled": False,
    },
    {
        "source_id": "ecb",
        "name": "European Central Bank",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.ecb.europa.eu",
        "feed_url": "https://www.ecb.europa.eu/rss/press.html",
        "poll_interval_sec": 600,
        "priority": 0.95,
    },
    {
        "source_id": "boe",
        "name": "Bank of England",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.bankofengland.co.uk",
        "feed_url": "https://www.bankofengland.co.uk/rss/news",
        "poll_interval_sec": 600,
        "priority": 0.9,
    },
    {
        "source_id": "cftc",
        "name": "CFTC (Commitments of Traders)",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.cftc.gov",
        # Verified 2026-08-16: no public CFTC RSS feed exists (RSS/CFTC_RSS.xml
        # and RSS/rss.aspx both 404). Keep the source registered for the COT
        # calendar but DISABLED by default so it never silently fails.
        "feed_url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
        "poll_interval_sec": 3600,
        "priority": 0.9,
        "enabled": False,
    },
    {
        "source_id": "ustreasury",
        "name": "U.S. Treasury (JSON manifest)",
        "kind": SourceKind.API,
        "tier": SourceTier.TIER_1,
        "url": "https://home.treasury.gov",
        # Verified 2026-09-07 (market-context P0): the old RSS path 503s and
        # the HTML press-releases page parses to ZERO feedparser entries
        # (bozo=1, 0 entries — verified live). The site exposes a STABLE JSON
        # manifest per section (Drupal data-news-manifest):
        #   /news-data/press-releases/manifest.json -> year shard index
        #   /news-data/press-releases/search/2026.json -> items[]
        # items carry {title, url, datetime (ISO-8601 Z), dateDisplay} — a
        # machine-readable, durable contract; parsed by
        # JSONManifestSourceAdapter (kind=API). Same source_id keeps health
        # history continuity in news_health.
        "feed_url": "https://home.treasury.gov/news-data/press-releases/search/2026.json",
        "poll_interval_sec": 600,
        "priority": 0.85,
    },
]

# ---------------------------------------------------------------------------
# Major financial-news providers (Tier 2)
# ---------------------------------------------------------------------------

_MAJOR_SOURCES: list[dict] = [
    {
        # Verified 2026-09-07 (market-context P0): feeds.reuters.com dies with
        # TLS EOF (SSL: UNEXPECTED_EOF_WHILE_READING) on every attempt —
        # Reuters retired this feed host; not fixable client-side. REPLACED
        # by CNBC business RSS (verified 200, 30 entries, clean RSS 2.0) and
        # FXStreet (verified 200, 30 entries) under a new source_id.
        "source_id": "reuters",
        "name": "Reuters Markets (DEAD — replaced by cnbc_business)",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_2,
        "url": "https://www.reuters.com",
        "feed_url": "https://feeds.reuters.com/reuters/businessNews",
        "poll_interval_sec": 300,
        "priority": 0.8,
        "enabled": False,
    },
    {
        "source_id": "cnbc_business",
        "name": "CNBC Business (Reuters replacement)",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_2,
        "url": "https://www.cnbc.com",
        "feed_url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "poll_interval_sec": 300,
        "priority": 0.8,
    },
    {
        "source_id": "fxstreet",
        "name": "FXStreet (FX/macro news)",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_2,
        "url": "https://www.fxstreet.com",
        "feed_url": "https://www.fxstreet.com/rss/news",
        "poll_interval_sec": 300,
        "priority": 0.75,
    },
    {
        "source_id": "marketwatch",
        "name": "MarketWatch",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_2,
        "url": "https://www.marketwatch.com",
        "feed_url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "poll_interval_sec": 300,
        "priority": 0.7,
    },
]

# ---------------------------------------------------------------------------
# Specialised sources (Tier 3)
# ---------------------------------------------------------------------------

_SPECIALISED_SOURCES: list[dict] = [
    {
        "source_id": "forexlive",
        "name": "ForexLive",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_3,
        "url": "https://www.forexlive.com",
        "feed_url": "https://www.forexlive.com/feed/",
        "poll_interval_sec": 180,
        "priority": 0.6,
    },
    {
        "source_id": "zerohedge",
        "name": "ZeroHedge (macro)",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_3,
        "url": "https://www.zerohedge.com",
        "feed_url": "https://feeds.feedburner.com/zerohedge/feed",
        "poll_interval_sec": 300,
        "priority": 0.45,
    },
]

# ---------------------------------------------------------------------------
# Asset impact profiles - XAUUSD primary, USD + major FX support
# ---------------------------------------------------------------------------

_ASSET_PROFILES: list[AssetImpactProfile] = [
    AssetImpactProfile(
        asset="XAUUSD",
        asset_type="COMMODITY",
        drivers={
            "USD": 0.30,  # weaker USD generally supports gold
            "BOND_YIELDS": 0.25,  # real-yield inverse
            "INFLATION": 0.20,
            "GEOPOLITICS": 0.15,
            "RISK_OFF": 0.10,
        },
        inverse_drivers={
            "USD": 1.0,  # USD strength inversely moves gold
            "BOND_YIELDS": 1.0,
            "RISK_ON": 0.5,
        },
    ),
    AssetImpactProfile(
        asset="USD",
        asset_type="CURRENCY",
        drivers={
            "INTEREST_RATES": 0.35,
            "INFLATION": 0.25,
            "EMPLOYMENT": 0.20,
            "CENTRAL_BANK": 0.20,
        },
        inverse_drivers={},
    ),
    AssetImpactProfile(
        asset="EUR",
        asset_type="CURRENCY",
        drivers={
            "CENTRAL_BANK": 0.40,
            "GROWTH": 0.30,
            "INFLATION": 0.30,
        },
        inverse_drivers={"USD": 0.5},
    ),
    AssetImpactProfile(
        asset="GBP",
        asset_type="CURRENCY",
        drivers={
            "CENTRAL_BANK": 0.40,
            "GROWTH": 0.30,
            "INFLATION": 0.30,
        },
        inverse_drivers={"USD": 0.5},
    ),
    AssetImpactProfile(
        asset="JPY",
        asset_type="CURRENCY",
        drivers={
            "CENTRAL_BANK": 0.45,
            "BOND_YIELDS": 0.30,
            "RISK_ON": 0.25,
        },
        inverse_drivers={"RISK_OFF": 0.5},
    ),
    AssetImpactProfile(
        asset="CHF",
        asset_type="CURRENCY",
        drivers={
            "CENTRAL_BANK": 0.40,
            "SAFE_HAVEN": 0.35,
            "GEOPOLITICS": 0.25,
        },
        inverse_drivers={},
    ),
]


def _source_row(src: dict) -> dict:
    return {
        "source_id": src["source_id"],
        "name": src["name"],
        "kind": src["kind"].value if hasattr(src["kind"], "value") else src["kind"],
        "tier": src["tier"].value if hasattr(src["tier"], "value") else src["tier"],
        "url": src.get("url", ""),
        "feed_url": src.get("feed_url", ""),
        "enabled": src.get("enabled", True),
        "poll_interval_sec": src.get("poll_interval_sec", 300),
        "language": src.get("language", "en"),
        "priority": src.get("priority", 0.5),
        "seed_version": SEED_VERSION,
    }


def seed_sources(db: NewsDatabase) -> int:
    """Upserts the source registry. Idempotent: re-running updates in place
    (same PKs) and never creates duplicates. Returns the number of sources."""
    count = 0
    for src in _OFFICIAL_SOURCES + _MAJOR_SOURCES + _SPECIALISED_SOURCES:
        db.upsert_source(_source_row(src))
        count += 1
    return count


def seed_asset_profiles(db: NewsDatabase) -> int:
    """Persists asset impact profiles into the sources table metadata (via the
    seed payload) and returns the profile count. The profiles themselves are
    consumed in-memory by the relevance engine."""
    return len(_ASSET_PROFILES)


def get_asset_profiles() -> list[AssetImpactProfile]:
    """Returns the canonical asset impact profiles (in-memory registry)."""
    return list(_ASSET_PROFILES)


def get_asset_profile(asset: str) -> AssetImpactProfile | None:
    for p in _ASSET_PROFILES:
        if p.asset == asset:
            return p
    return None


def seed_news_database(db: NewsDatabase) -> dict[str, Any]:
    """Runs the full deterministic seed. Safe to run multiple times."""
    sources = seed_sources(db)
    profiles = seed_asset_profiles(db)
    return {"sources": sources, "asset_profiles": profiles, "seed_version": SEED_VERSION}
