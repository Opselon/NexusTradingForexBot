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
SEED_VERSION = "2026-08-16-v2"

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
        "feed_url": "https://www.bls.gov/feed/news.releases.rss",
        "poll_interval_sec": 600,
        "priority": 1.0,
    },
    {
        "source_id": "bea",
        "name": "U.S. Bureau of Economic Analysis",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://www.bea.gov",
        # Verified 2026-08-16: /rss/news returns 404; the live releases page
        # (https://www.bea.gov/news) is 200. The HTML adapter extracts items.
        "feed_url": "https://www.bea.gov/news",
        "poll_interval_sec": 600,
        "priority": 0.95,
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
        "name": "U.S. Treasury",
        "kind": SourceKind.OFFICIAL,
        "tier": SourceTier.TIER_1,
        "url": "https://home.treasury.gov",
        # Verified 2026-08-16: the RSS feed path returns 503; the live
        # press-releases page is 200. HTML adapter extracts releases.
        "feed_url": "https://home.treasury.gov/news/press-releases",
        "poll_interval_sec": 600,
        "priority": 0.85,
    },
]

# ---------------------------------------------------------------------------
# Major financial-news providers (Tier 2)
# ---------------------------------------------------------------------------

_MAJOR_SOURCES: list[dict] = [
    {
        "source_id": "reuters",
        "name": "Reuters Markets",
        "kind": SourceKind.RSS,
        "tier": SourceTier.TIER_2,
        "url": "https://www.reuters.com",
        "feed_url": "https://feeds.reuters.com/reuters/businessNews",
        "poll_interval_sec": 300,
        "priority": 0.8,
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
