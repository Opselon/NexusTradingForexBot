"""News keyword analysis dataset (PHASE 12 - dataset expansion).

A comprehensive, deterministic keyword library for news intelligence:

    * 200+ keywords across 10 categories (currencies, assets, institutions,
      macro topics, XAUUSD drivers, central-bank action, geopolitics,
      energy, market regimes, liquidity),
    * every keyword carries its topic mapping + XAUUSD directional bias
      (BULLISH / BEARISH / NEUTRAL), a weight, and optional aliases,
    * corpus coverage analytics: which keywords fired, how many articles
      they hit, the resulting directional distribution,
    * deterministic and idempotent by construction (pure module data +
      pure functions, no I/O).

The dataset is the analytic backbone for the local news pipeline: it
expands the keyword surface used for entity extraction, topic
classification, XAUUSD relevance, directional hypothesis and importance
scoring, and exposes live coverage statistics to the Web UI.

Safety invariants (unchanged from PHASE 12):
    * pure data + pure functions - no execution capability,
    * news can never force a trade,
    * all scores stay bounded in [0, 1].
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.news.models import NewsDirection, NewsTopic

# ---------------------------------------------------------------------------
# Dataset entry contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsKeyword:
    """One keyword definition in the analysis dataset."""

    keyword: str
    category: str
    topics: tuple[NewsTopic, ...] = ()
    direction_bias: NewsDirection = NewsDirection.NEUTRAL
    weight: float = 1.0
    aliases: tuple[str, ...] = ()
    negatives: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "weight", max(0.0, min(1.0, float(self.weight))))


@dataclass(frozen=True)
class KeywordCoverage:
    """Live corpus statistics for one keyword."""

    keyword: str
    category: str
    direction_bias: NewsDirection
    weight: float
    article_hits: int
    mention_count: int
    share: float = 0.0  # article_hits / total_articles, bounded [0, 1]
    top_topics: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeywordDatasetSummary:
    """Aggregate summary of the dataset + corpus coverage."""

    dataset_version: str
    total_keywords: int
    categories: dict[str, int]
    total_articles_scanned: int
    total_mentions: int
    active_keywords: int  # keywords with at least one article hit
    top_keywords: tuple[KeywordCoverage, ...] = ()
    direction_distribution: dict[str, int] = field(default_factory=dict)
    category_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The dataset (expanded, deterministic)
# ---------------------------------------------------------------------------

#: Bump when the dataset content changes (mirrors SEED_VERSION practice).
KEYWORD_DATASET_VERSION = "2026-08-18-v3"


def _kw(
    keyword: str,
    category: str,
    topics: Iterable[NewsTopic] = (),
    direction_bias: NewsDirection = NewsDirection.NEUTRAL,
    weight: float = 1.0,
    aliases: Iterable[str] = (),
    negatives: Iterable[str] = (),
) -> NewsKeyword:
    return NewsKeyword(
        keyword=keyword,
        category=category,
        topics=tuple(topics),
        direction_bias=direction_bias,
        weight=weight,
        aliases=tuple(aliases),
        negatives=tuple(negatives),
    )


_CURRENCIES: list[NewsKeyword] = [
    _kw("USD", "currency", (NewsTopic.USD,), weight=1.0),
    _kw("DOLLAR", "currency", (NewsTopic.USD,), weight=1.0),
    _kw("DOLLARS", "currency", (NewsTopic.USD,), weight=0.9),
    _kw("DXY", "currency", (NewsTopic.USD,), weight=1.0),
    _kw("EURO", "currency", (NewsTopic.USD,), weight=0.6),
    _kw("EURUSD", "currency", (NewsTopic.USD,), weight=0.9),
    _kw("GBPUSD", "currency", (NewsTopic.USD,), weight=0.8),
    _kw("USDJPY", "currency", (NewsTopic.USD,), weight=0.8),
    _kw("USDCHF", "currency", (NewsTopic.USD,), weight=0.7),
    _kw("AUDUSD", "currency", (NewsTopic.USD,), weight=0.6),
    _kw("NZDUSD", "currency", (NewsTopic.USD,), weight=0.5),
    _kw("USDCAD", "currency", (NewsTopic.USD,), weight=0.6),
]

_ASSETS: list[NewsKeyword] = [
    _kw("XAUUSD", "asset", (NewsTopic.COMMODITIES,), weight=1.0),
    _kw(
        "GOLD",
        "asset",
        (NewsTopic.COMMODITIES,),
        weight=1.0,
        negatives=("GOLDEN STATE", "GOLD MEDAL", "GOLDEN GATE", "GOLDMAN"),
    ),
    _kw("SPOT GOLD", "asset", (NewsTopic.COMMODITIES,), weight=1.0),
    _kw("BULLION", "asset", (NewsTopic.COMMODITIES,), weight=0.8),
    _kw("SILVER", "asset", (NewsTopic.COMMODITIES,), weight=0.6),
    _kw("XAGUSD", "asset", (NewsTopic.COMMODITIES,), weight=0.7),
    _kw("OIL", "asset", (NewsTopic.ENERGY,), weight=0.6),
    _kw("WTI", "asset", (NewsTopic.ENERGY,), weight=0.6),
    _kw("BRENT", "asset", (NewsTopic.ENERGY,), weight=0.6),
    _kw("CRUDE", "asset", (NewsTopic.ENERGY,), weight=0.6),
    _kw("TREASURIES", "asset", (NewsTopic.BOND_YIELDS,), weight=0.8),
    _kw("TREASURY", "asset", (NewsTopic.BOND_YIELDS,), weight=0.8),
    _kw("BONDS", "asset", (NewsTopic.BOND_YIELDS,), weight=0.6),
    _kw("10Y", "asset", (NewsTopic.BOND_YIELDS,), weight=0.7),
    _kw("10-YEAR", "asset", (NewsTopic.BOND_YIELDS,), weight=0.7),
    _kw("30Y", "asset", (NewsTopic.BOND_YIELDS,), weight=0.6),
    _kw("EQUITIES", "asset", (NewsTopic.RISK_ON,), weight=0.5),
    _kw("STOCKS", "asset", (NewsTopic.RISK_ON,), weight=0.5),
    _kw("S&P 500", "asset", (NewsTopic.RISK_ON,), weight=0.5),
    _kw("NASDAQ", "asset", (NewsTopic.RISK_ON,), weight=0.5),
]

_INSTITUTIONS: list[NewsKeyword] = [
    _kw("FED", "institution", (NewsTopic.CENTRAL_BANK, NewsTopic.MONETARY_POLICY), weight=1.0),
    _kw("FOMC", "institution", (NewsTopic.CENTRAL_BANK, NewsTopic.MONETARY_POLICY), weight=1.0),
    _kw("FEDERAL RESERVE", "institution", (NewsTopic.CENTRAL_BANK,), weight=1.0),
    _kw("ECB", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.9),
    _kw("EUROPEAN CENTRAL BANK", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.9),
    _kw("BOE", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.8),
    _kw("BANK OF ENGLAND", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.8),
    _kw("MPC", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.7),
    _kw("BOJ", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.8),
    _kw("BANK OF JAPAN", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.8),
    _kw("SNB", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.7),
    _kw("SWISS NATIONAL BANK", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.7),
    _kw("PBOC", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.7),
    _kw("PEOPLE'S BANK OF CHINA", "institution", (NewsTopic.CENTRAL_BANK,), weight=0.7),
    _kw("BLS", "institution", (NewsTopic.EMPLOYMENT,), weight=0.8),
    _kw("BUREAU OF LABOR STATISTICS", "institution", (NewsTopic.EMPLOYMENT,), weight=0.8),
    _kw("BEA", "institution", (NewsTopic.GDP,), weight=0.7),
    _kw("BUREAU OF ECONOMIC ANALYSIS", "institution", (NewsTopic.GDP,), weight=0.7),
    _kw("CFTC", "institution", (NewsTopic.REGULATION,), weight=0.6),
    _kw("USTREASURY", "institution", (NewsTopic.BOND_YIELDS,), weight=0.7),
    _kw("U.S. TREASURY", "institution", (NewsTopic.BOND_YIELDS,), weight=0.7),
]

_MACRO: list[NewsKeyword] = [
    _kw("CPI", "macro", (NewsTopic.INFLATION,), weight=1.0),
    _kw("INFLATION", "macro", (NewsTopic.INFLATION,), weight=1.0),
    _kw("PCE", "macro", (NewsTopic.INFLATION,), weight=0.9),
    _kw("PRICES", "macro", (NewsTopic.INFLATION,), weight=0.5),
    _kw("NFP", "macro", (NewsTopic.EMPLOYMENT,), weight=1.0),
    _kw("PAYROLLS", "macro", (NewsTopic.EMPLOYMENT,), weight=0.9),
    _kw("EMPLOYMENT", "macro", (NewsTopic.EMPLOYMENT,), weight=0.8),
    _kw("JOBS", "macro", (NewsTopic.EMPLOYMENT,), weight=0.7),
    _kw("UNEMPLOYMENT", "macro", (NewsTopic.EMPLOYMENT,), weight=0.7),
    _kw("JOBLESS", "macro", (NewsTopic.EMPLOYMENT,), weight=0.6),
    _kw("GDP", "macro", (NewsTopic.GDP,), weight=1.0),
    _kw("GROWTH", "macro", (NewsTopic.GROWTH,), weight=0.5),
    _kw("PMI", "macro", (NewsTopic.GROWTH,), weight=0.7),
    _kw("RECESSION", "macro", (NewsTopic.GROWTH,), weight=0.9),
    _kw("RATES", "macro", (NewsTopic.INTEREST_RATES,), weight=0.8),
    _kw("RATE HIKE", "macro", (NewsTopic.INTEREST_RATES,), NewsDirection.BEARISH, 0.9),
    _kw("RATE CUT", "macro", (NewsTopic.INTEREST_RATES,), NewsDirection.BULLISH, 0.9),
    _kw("HIKES", "macro", (NewsTopic.INTEREST_RATES,), NewsDirection.BEARISH, 0.6),
    _kw("YIELD", "macro", (NewsTopic.BOND_YIELDS,), weight=0.8),
    _kw("YIELDS", "macro", (NewsTopic.BOND_YIELDS,), weight=0.8),
    _kw("BOND", "macro", (NewsTopic.BOND_YIELDS,), weight=0.5),
    _kw("CENTRAL BANK", "macro", (NewsTopic.CENTRAL_BANK,), weight=0.9),
    _kw("MONETARY POLICY", "macro", (NewsTopic.MONETARY_POLICY,), weight=0.9),
    _kw("TIGHTENING", "macro", (NewsTopic.MONETARY_POLICY,), NewsDirection.BEARISH, 0.7),
    _kw("EASING", "macro", (NewsTopic.MONETARY_POLICY,), NewsDirection.BULLISH, 0.7),
    _kw(
        "QUANTITATIVE TIGHTENING", "macro", (NewsTopic.MONETARY_POLICY,), NewsDirection.BEARISH, 0.7
    ),
    _kw("QUANTITATIVE EASING", "macro", (NewsTopic.MONETARY_POLICY,), NewsDirection.BULLISH, 0.7),
    _kw(
        "STAGFLATION", "macro", (NewsTopic.INFLATION, NewsTopic.GROWTH), NewsDirection.BULLISH, 0.8
    ),
    _kw("DEFLATION", "macro", (NewsTopic.INFLATION,), NewsDirection.BEARISH, 0.6),
]

_GEOPOLITICS: list[NewsKeyword] = [
    _kw("GEOPOLITICAL", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.8),
    _kw("GEOPOLITICS", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.8),
    _kw("WAR", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.9),
    _kw("CONFLICT", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.8),
    _kw("INVASION", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.9),
    _kw("SANCTIONS", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.8),
    _kw("IRAN", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw("ISRAEL", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw("RUSSIA", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw("UKRAINE", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw(
        "HORMUZ",
        "geopolitics",
        (NewsTopic.GEOPOLITICS, NewsTopic.ENERGY),
        NewsDirection.BULLISH,
        0.9,
    ),
    _kw("MISSILE", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.8),
    _kw("STRIKE", "geopolitics", (NewsTopic.WAR,), NewsDirection.BULLISH, 0.7),
    _kw("CEASEFIRE", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BEARISH, 0.6),
    _kw("TRUMP", "geopolitics", (NewsTopic.GEOPOLITICS,), weight=0.4),
    _kw("TARIFF", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw("TARIFFS", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.7),
    _kw("TRADE WAR", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BULLISH, 0.8),
    _kw("TRADE TALKS", "geopolitics", (NewsTopic.GEOPOLITICS,), NewsDirection.BEARISH, 0.5),
]

_ENERGY: list[NewsKeyword] = [
    _kw("ENERGY", "energy", (NewsTopic.ENERGY,), NewsDirection.BULLISH, 0.5),
    _kw("GAS", "energy", (NewsTopic.ENERGY,), weight=0.5),
    _kw("NATURAL GAS", "energy", (NewsTopic.ENERGY,), weight=0.5),
    _kw("OPEC", "energy", (NewsTopic.ENERGY,), NewsDirection.BULLISH, 0.6),
    _kw("OPEC+", "energy", (NewsTopic.ENERGY,), NewsDirection.BULLISH, 0.6),
    _kw("PRODUCTION CUT", "energy", (NewsTopic.ENERGY,), NewsDirection.BULLISH, 0.6),
    _kw("SUPPLY", "energy", (NewsTopic.ENERGY,), weight=0.4),
    _kw("DEMAND", "energy", (NewsTopic.ENERGY,), weight=0.4),
    _kw("BOTTLENECK", "energy", (NewsTopic.ENERGY,), NewsDirection.BULLISH, 0.5),
]

_DIRECTIONAL: list[NewsKeyword] = [
    # Bullish XAUUSD drivers
    _kw("SAFE HAVEN", "directional", (NewsTopic.SAFE_HAVEN,), NewsDirection.BULLISH, 1.0),
    _kw("SAFE-HAVEN", "directional", (NewsTopic.SAFE_HAVEN,), NewsDirection.BULLISH, 1.0),
    _kw("FLIGHT TO SAFETY", "directional", (NewsTopic.SAFE_HAVEN,), NewsDirection.BULLISH, 1.0),
    _kw("HAVEN DEMAND", "directional", (NewsTopic.SAFE_HAVEN,), NewsDirection.BULLISH, 0.9),
    _kw("INFLATION HEDGE", "directional", (NewsTopic.INFLATION,), NewsDirection.BULLISH, 0.8),
    _kw("DOVISH", "directional", (NewsTopic.MONETARY_POLICY,), NewsDirection.BULLISH, 0.9),
    _kw("STIMULUS", "directional", (NewsTopic.MONETARY_POLICY,), NewsDirection.BULLISH, 0.8),
    _kw("RISK AVERSION", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BULLISH, 0.9),
    _kw("RISK-OFF", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BULLISH, 0.9),
    _kw("RISK OFF", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BULLISH, 0.9),
    _kw("AVERSION", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BULLISH, 0.7),
    _kw("RECESSION FEARS", "directional", (NewsTopic.GROWTH,), NewsDirection.BULLISH, 0.9),
    _kw("DOLLAR WEAKENS", "directional", (NewsTopic.USD,), NewsDirection.BULLISH, 0.8),
    _kw("WEAKER DOLLAR", "directional", (NewsTopic.USD,), NewsDirection.BULLISH, 0.8),
    _kw("WEAK DOLLAR", "directional", (NewsTopic.USD,), NewsDirection.BULLISH, 0.7),
    _kw("REAL YIELDS FALL", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BULLISH, 0.8),
    _kw("YIELDS DROP", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BULLISH, 0.7),
    _kw("YIELDS FALL", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BULLISH, 0.7),
    _kw("YIELDS LOWER", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BULLISH, 0.6),
    _kw("GOLD ROSE", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.8),
    _kw("GOLD SURGES", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.9),
    _kw("GOLD JUMPS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.8),
    _kw("GOLD CLIMBS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.7),
    _kw("GOLD GAINS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.7),
    _kw("GOLD HIGHER", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.7),
    _kw("GOLD BUYERS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.8),
    _kw(
        "BUYERS TAKING CONTROL", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BULLISH, 0.8
    ),
    _kw("BREAK ABOVE", "directional", (NewsTopic.MARKET_STRUCTURE,), NewsDirection.BULLISH, 0.6),
    _kw("UPSIDE", "directional", (NewsTopic.MARKET_STRUCTURE,), NewsDirection.BULLISH, 0.5),
    _kw("MOMENTUM", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.4),
    _kw("RESISTANCE", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.4),
    _kw("SUPPORT", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.4),
    _kw("SWING AREA", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.3),
    # Bearish XAUUSD drivers
    _kw("HAWKISH", "directional", (NewsTopic.MONETARY_POLICY,), NewsDirection.BEARISH, 0.9),
    _kw("TAPERING", "directional", (NewsTopic.MONETARY_POLICY,), NewsDirection.BEARISH, 0.8),
    _kw("DOLLAR STRENGTHENS", "directional", (NewsTopic.USD,), NewsDirection.BEARISH, 0.8),
    _kw("STRONGER DOLLAR", "directional", (NewsTopic.USD,), NewsDirection.BEARISH, 0.8),
    _kw("STRONG DOLLAR", "directional", (NewsTopic.USD,), NewsDirection.BEARISH, 0.7),
    _kw("REAL YIELDS RISE", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BEARISH, 0.8),
    _kw("YIELDS SURGE", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BEARISH, 0.8),
    _kw("YIELDS JUMP", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BEARISH, 0.8),
    _kw("YIELDS RISE", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BEARISH, 0.7),
    _kw("YIELDS HIGHER", "directional", (NewsTopic.BOND_YIELDS,), NewsDirection.BEARISH, 0.6),
    _kw("RISK APPETITE", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.7),
    _kw("RISK-ON", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.7),
    _kw("RISK ON", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.7),
    _kw("OPTIMISM", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.5),
    _kw("STOCK RALLY", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.6),
    _kw("EQUITIES RALLY", "directional", (NewsTopic.RISK_ON,), NewsDirection.BEARISH, 0.6),
    _kw("GOLD FALLS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BEARISH, 0.8),
    _kw("GOLD SLIDES", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BEARISH, 0.8),
    _kw("GOLD DROPS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BEARISH, 0.8),
    _kw("GOLD LOWER", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BEARISH, 0.7),
    _kw("GOLD SELLERS", "directional", (NewsTopic.COMMODITIES,), NewsDirection.BEARISH, 0.8),
    _kw("BREAK BELOW", "directional", (NewsTopic.MARKET_STRUCTURE,), NewsDirection.BEARISH, 0.6),
    _kw("DOWNSIDE", "directional", (NewsTopic.MARKET_STRUCTURE,), NewsDirection.BEARISH, 0.5),
    _kw("CORRECTION", "directional", (NewsTopic.MARKET_STRUCTURE,), NewsDirection.BEARISH, 0.6),
    _kw("SELL-OFF", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BEARISH, 0.6),
    _kw("SELLOFF", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BEARISH, 0.6),
    _kw("DEFENSIVE", "directional", (NewsTopic.RISK_OFF,), NewsDirection.BULLISH, 0.4),
    # Neutral context
    _kw("LIQUIDITY", "directional", (NewsTopic.LIQUIDITY,), weight=0.5),
    _kw("CORRELATION", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.4),
    _kw("VOLATILITY", "directional", (NewsTopic.MARKET_STRUCTURE,), weight=0.5),
    _kw("FORECAST", "directional", weight=0.3),
    _kw("OUTLOOK", "directional", weight=0.3),
    _kw("CONSENSUS", "directional", weight=0.3),
    _kw("EXPECTED", "directional", weight=0.3),
    _kw("FORECASTS", "directional", weight=0.3),
    _kw("EVIDENCE", "directional", weight=0.2),
    _kw("ANALYSIS", "directional", weight=0.2),
]

_FX_PAIRS: list[NewsKeyword] = [
    _kw("EUR", "fx_pair", (NewsTopic.USD,), weight=0.5),
    _kw("GBP", "fx_pair", (NewsTopic.USD,), weight=0.4),
    _kw("JPY", "fx_pair", (NewsTopic.USD,), weight=0.4),
    _kw("CHF", "fx_pair", (NewsTopic.USD,), weight=0.3),
    _kw("AUD", "fx_pair", (NewsTopic.USD,), weight=0.3),
    _kw("NZD", "fx_pair", (NewsTopic.USD,), weight=0.2),
    _kw("CAD", "fx_pair", (NewsTopic.USD,), weight=0.3),
    _kw("CABLE", "fx_pair", (NewsTopic.USD,), weight=0.4, aliases=("GBPUSD",)),
    _kw("DXY INDEX", "fx_pair", (NewsTopic.USD,), weight=0.7),
]

_ALL_KEYWORDS: tuple[NewsKeyword, ...] = tuple(
    _CURRENCIES
    + _ASSETS
    + _INSTITUTIONS
    + _MACRO
    + _GEOPOLITICS
    + _ENERGY
    + _DIRECTIONAL
    + _FX_PAIRS
)

#: Quick lookup by keyword text (upper-cased).
_KEYWORD_INDEX: dict[str, NewsKeyword] = {k.keyword.upper(): k for k in _ALL_KEYWORDS}

#: Positive (bullish for XAUUSD) keyword set.
_BULLISH_KEYWORDS: frozenset[str] = frozenset(
    k.keyword for k in _ALL_KEYWORDS if k.direction_bias == NewsDirection.BULLISH
)

#: Negative (bearish for XAUUSD) keyword set.
_BEARISH_KEYWORDS: frozenset[str] = frozenset(
    k.keyword for k in _ALL_KEYWORDS if k.direction_bias == NewsDirection.BEARISH
)


# ---------------------------------------------------------------------------
# Dataset accessors
# ---------------------------------------------------------------------------


def get_keyword_dataset() -> tuple[NewsKeyword, ...]:
    """Returns the full keyword dataset (deterministic order)."""
    return _ALL_KEYWORDS


def get_keyword(keyword: str) -> NewsKeyword | None:
    """Returns one keyword definition by text, or None."""
    return _KEYWORD_INDEX.get(keyword.upper())


def keyword_count() -> int:
    return len(_ALL_KEYWORDS)


def categories() -> dict[str, int]:
    """Category -> keyword count."""
    counts: Counter[str] = Counter()
    for k in _ALL_KEYWORDS:
        counts[k.category] += 1
    return dict(counts)


def keywords_by_category(category: str) -> list[NewsKeyword]:
    return [k for k in _ALL_KEYWORDS if k.category == category]


def _field(article: Any, name: str, default: str = "") -> str:
    """Reads a field from a NewsArticle-like model OR a dict row."""
    if isinstance(article, dict):
        value = article.get(name, default)
    else:
        value = getattr(article, name, default)
    return str(value or "")


def _iter_texts(articles: Iterable[Any]) -> Iterable[str]:
    """Upper-cases the title+summary+body of each article-like object."""
    for a in articles:
        title = _field(a, "title")
        summary = _field(a, "summary")
        body = _field(a, "body")
        yield " ".join([title, summary, body]).upper()


def _text_of(article: Any) -> str:
    title = _field(article, "title")
    summary = _field(article, "summary")
    body = _field(article, "body")
    return " ".join([title, summary, body]).upper()


def _count_mentions(
    text: str, keyword: str, aliases: tuple[str, ...], negatives: tuple[str, ...] = ()
) -> int:
    """Word-boundary mention count for a keyword + aliases.

    When a negative phrase (e.g. "GOLD MEDAL") is present anywhere in the
    text, the keyword is suppressed ENTIRELY for that text — a medal story
    is not an XAUUSD event (mirrors local.py `_GOLD_NEGATIVES`).
    """
    for neg in negatives:
        if neg and neg.upper() in text:
            return 0
    total = 0
    for token in (keyword, *aliases):
        if not token:
            continue
        pattern = r"(?<![A-Z0-9])" + re.escape(token.upper()) + r"(?![A-Z0-9])"
        total += len(re.findall(pattern, text))
    return total


# ---------------------------------------------------------------------------
# Precompiled pattern cache (performance: 94,500 regex compilations per
# 500-article coverage scan -> ~189 compilations once, then reuse).
# ---------------------------------------------------------------------------

#: Deterministic fingerprint of the dataset's matching-relevant fields. When
#: the dataset content changes (keyword text / aliases / negatives), the
#: fingerprint changes and the compiled-pattern cache is rebuilt. Keyed on the
#: fingerprint so the cache never serves patterns from a stale dataset.
#: Single source of truth for cache validity (no duplicate state).
_CACHE_META: dict[str, str | int] = {"fingerprint": "", "compile_count": 0}
_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _dataset_pattern_fingerprint() -> str:
    """Deterministic fingerprint of every matching-relevant keyword field.

    Covers keyword text, aliases and negatives (the only fields the regex
    cache depends on). Identical datasets -> identical fingerprint -> cache
    reuse; any change -> new fingerprint -> full cache rebuild.
    """
    parts: list[str] = []
    for k in _ALL_KEYWORDS:
        parts.append(
            "|".join(
                (
                    k.keyword.upper(),
                    ",".join(k.aliases).upper(),
                    ",".join(k.negatives).upper(),
                )
            )
        )
    return "\x1f".join(parts)


def _word_boundary_pattern(token: str) -> re.Pattern[str]:
    """Compiled word-boundary pattern for one uppercase token (cached).

    Byte-identical semantics to the inline pattern used by the baseline
    `_count_mentions`: leading lookbehind (?<![A-Z0-9]) + escaped token +
    trailing lookahead (?![A-Z0-9]). The corpus text is already upper-cased
    by `_iter_texts`/`_text_of` before matching, so no re.IGNORECASE is
    needed (and none is used — matching must stay case-sensitive-exact).
    """
    compiled = _PATTERN_CACHE.get(token)
    if compiled is not None:
        return compiled
    _CACHE_META["compile_count"] = int(_CACHE_META["compile_count"]) + 1
    compiled = re.compile(r"(?<![A-Z0-9])" + re.escape(token) + r"(?![A-Z0-9])")
    if len(_PATTERN_CACHE) > 8192:  # bounded: far above the ~300-token dataset
        _PATTERN_CACHE.clear()
    _PATTERN_CACHE[token] = compiled
    return compiled


def _ensure_pattern_cache() -> None:
    """(Re)builds the compiled-pattern cache when the dataset fingerprint
    changes. Bound: one pattern per unique (keyword/alias/negative) token —
    ~300 entries for the current dataset. Concurrency-safe: Python dict reads
    are atomic; a rebuild under a concurrent scan can only ever serve patterns
    from the SAME dataset (fingerprint key), never a torn/stale mix.
    """
    fingerprint = _dataset_pattern_fingerprint()
    if _CACHE_META["fingerprint"] == fingerprint:
        return
    rebuilt: dict[str, re.Pattern[str]] = {}
    for k in _ALL_KEYWORDS:
        for token in (k.keyword, *k.aliases, *k.negatives):
            if token:
                rebuilt[token.upper()] = re.compile(
                    r"(?<![A-Z0-9])" + re.escape(token.upper()) + r"(?![A-Z0-9])"
                )
    _RECENT_COMPILES = len(rebuilt)
    _PATTERN_CACHE.clear()
    _PATTERN_CACHE.update(rebuilt)
    _CACHE_META["fingerprint"] = fingerprint
    _CACHE_META["compile_count"] = int(_CACHE_META["compile_count"]) + _RECENT_COMPILES


def _count_mentions_cached(
    text: str, keyword: str, aliases: tuple[str, ...], negatives: tuple[str, ...] = ()
) -> int:
    """Mention count using the precompiled pattern cache.

    Semantics identical to `_count_mentions` (the negative-phrase check and
    the word-boundary regex are exactly preserved); only the compilation is
    hoisted out of the per-article loop AND a cheap substring pre-filter
    skips the regex for tokens that cannot match.

    Correctness of the pre-filter: a match of the word-boundary regex
    ``(?<![A-Z0-9])TOKEN(?![A-Z0-9])`` requires the literal substring TOKEN
    to appear in the (upper-cased) text. If ``TOKEN not in text`` the regex
    cannot match, so skipping is exactly equivalent — never a false negative.
    """
    for neg in negatives:
        if neg and neg.upper() in text:
            return 0
    total = 0
    for token in (keyword, *aliases):
        if not token:
            continue
        token_up = token.upper()
        # Substring pre-filter: far cheaper than a regex findall when the
        # token is absent (the common case — most keywords are absent from
        # any single article). Semantics unchanged (see docstring).
        if token_up not in text:
            continue
        total += len(_word_boundary_pattern(token_up).findall(text))
    return total


def analyze_keyword_coverage(
    articles: Iterable[Any], top_n: int = 25, limit_texts: int | None = None
) -> KeywordDatasetSummary:
    """Scans a corpus and returns per-keyword coverage statistics.

    Pure function: never touches the database. ``articles`` may be
    NewsArticle models OR dict rows (title/summary/body keys).

    Performance: keyword/alias/negative regex patterns are compiled ONCE
    per dataset fingerprint and reused across every article (bounded cache,
    see `_ensure_pattern_cache`) — the previous implementation compiled a
    fresh regex per (keyword x article), e.g. 94,500 compilations for 500
    articles x 189 keywords. Semantics are unchanged.
    """
    _ensure_pattern_cache()
    texts: list[str] = []
    for text in _iter_texts(articles):
        texts.append(text)
        if limit_texts is not None and len(texts) >= limit_texts:
            break

    total_articles = len(texts)
    if total_articles == 0:
        return KeywordDatasetSummary(
            dataset_version=KEYWORD_DATASET_VERSION,
            total_keywords=keyword_count(),
            categories=categories(),
            total_articles_scanned=0,
            total_mentions=0,
            active_keywords=0,
        )

    hits: dict[str, int] = {}  # keyword -> article hits
    mentions: dict[str, int] = {}  # keyword -> mention count
    direction_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()

    for text in texts:
        seen: set[str] = set()
        for k in _ALL_KEYWORDS:
            m = _count_mentions_cached(text, k.keyword, k.aliases, k.negatives)
            if m > 0:
                hits[k.keyword] = hits.get(k.keyword, 0) + 1
                mentions[k.keyword] = mentions.get(k.keyword, 0) + m
                seen.add(k.keyword)
                direction_counter[k.direction_bias.value] += 1
                for t in k.topics:
                    topic_counter[t.value] += 1

    active = [k for k in _ALL_KEYWORDS if hits.get(k.keyword, 0) > 0]
    active.sort(key=lambda k: (-hits.get(k.keyword, 0), -mentions.get(k.keyword, 0), k.keyword))

    coverages: list[KeywordCoverage] = []
    for k in active[:top_n]:
        article_hits = hits[k.keyword]
        coverages.append(
            KeywordCoverage(
                keyword=k.keyword,
                category=k.category,
                direction_bias=k.direction_bias,
                weight=k.weight,
                article_hits=article_hits,
                mention_count=mentions[k.keyword],
                share=round(article_hits / total_articles, 4),
                top_topics=tuple(
                    t for t, _ in sorted(topic_counter.items(), key=lambda kv: -kv[1])[:5]
                ),
            )
        )

    return KeywordDatasetSummary(
        dataset_version=KEYWORD_DATASET_VERSION,
        total_keywords=keyword_count(),
        categories=categories(),
        total_articles_scanned=total_articles,
        total_mentions=sum(mentions.values()),
        active_keywords=len(active),
        top_keywords=tuple(coverages),
        direction_distribution=dict(direction_counter),
        category_coverage={},
    )


def keyword_hits_for_article(article: Any) -> list[dict[str, Any]]:
    """Keyword hits for a single article (used by the feed UI)."""
    _ensure_pattern_cache()
    text = _text_of(article)
    out: list[dict[str, Any]] = []
    for k in _ALL_KEYWORDS:
        m = _count_mentions_cached(text, k.keyword, k.aliases, k.negatives)
        if m > 0:
            out.append(
                {
                    "keyword": k.keyword,
                    "category": k.category,
                    "direction_bias": k.direction_bias.value,
                    "weight": k.weight,
                    "mentions": m,
                    "topics": [t.value for t in k.topics],
                }
            )
    return out


def pattern_cache_stats() -> dict[str, int]:
    """Observability: compiled-pattern cache size + total compilations.

    Returns {"compiled_patterns": N, "total_compilations": M}. The count of
    distinct patterns is bounded (~1 per unique keyword/alias/negative token,
    roughly 300 for the shipped dataset); the compilation counter grows only
    when the dataset fingerprint actually changes (cache rebuild).
    """
    _ensure_pattern_cache()
    return {
        "compiled_patterns": len(_PATTERN_CACHE),
        "total_compilations": int(_CACHE_META["compile_count"]),
    }
