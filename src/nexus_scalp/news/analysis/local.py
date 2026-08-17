"""Local news analysis engine - NO API KEY REQUIRED (PHASE 12).

A robust deterministic/rule-based analysis path that performs:

    * keyword/entity extraction (currencies, assets, institutions, macro),
    * topic classification (controlled taxonomy),
    * source credibility weighting,
    * novelty assessment,
    * sentiment/directional hypothesis,
    * event importance scoring,
    * market relevance + XAUUSD relevance + USD relevance,
    * currency mapping,
    * rule-based impact scoring,
    * conflict detection.

Context matters: "gold" inside "Golden State" or a medal story is NOT an
XAUUSD event. The engine only marks XAUUSD-relevant events when meaningful
drivers (USD strength, real yields, Fed, inflation, geopolitics, safe haven,
risk sentiment, energy, liquidity, macro releases) are present.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from nexus_scalp.news.models import (
    NewsDirection,
    NewsEntity,
    NewsImpact,
    NewsImpactHorizon,
    NewsImportance,
    NewsNovelty,
    NewsTopic,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.analysis.local")

# ---------------------------------------------------------------------------
# Entity dictionaries
# ---------------------------------------------------------------------------

_CURRENCIES: dict[str, str] = {
    "USD": "USD",
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "DXY": "USD",
    "EUR": "EUR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "GBP": "GBP",
    "POUND": "GBP",
    "STERLING": "GBP",
    "JPY": "JPY",
    "YEN": "JPY",
    "CHF": "CHF",
    "FRANC": "CHF",
    "SWISSIE": "CHF",
    "CNY": "CNY",
    "YUAN": "CNY",
    "RENMINBI": "CNY",
}

_ASSETS: dict[str, str] = {
    "XAUUSD": "XAUUSD",
    "GOLD": "XAUUSD",
    "GOLD PRICE": "XAUUSD",
    "SPOT GOLD": "XAUUSD",
    "BULLION": "XAUUSD",
    "SILVER": "SILVER",
    "XAGUSD": "SILVER",
    "OIL": "OIL",
    "WTI": "OIL",
    "BRENT": "OIL",
    "CRUDE": "OIL",
    "TREASURIES": "TREASURIES",
    "TREASURY": "TREASURIES",
    "BONDS": "TREASURIES",
    "10Y": "TREASURIES",
    "10-YEAR": "TREASURIES",
}

_INSTITUTIONS: dict[str, str] = {
    "FED": "FED",
    "FEDERAL RESERVE": "FED",
    "FOMC": "FED",
    "ECB": "ECB",
    "EUROPEAN CENTRAL BANK": "ECB",
    "BOE": "BOE",
    "BANK OF ENGLAND": "BOE",
    "MPC": "BOE",
    "BOJ": "BOJ",
    "BANK OF JAPAN": "BOJ",
    "SNB": "SNB",
    "SWISS NATIONAL BANK": "SNB",
    "PBOC": "PBOC",
    "PEOPLE'S BANK OF CHINA": "PBOC",
    "TREASURY": "USTREASURY",
    "U.S. TREASURY": "USTREASURY",
    "BLS": "BLS",
    "BUREAU OF LABOR STATISTICS": "BLS",
    "BEA": "BEA",
    "BUREAU OF ECONOMIC ANALYSIS": "BEA",
    "CFTC": "CFTC",
    "COMMODITY FUTURES TRADING COMMISSION": "CFTC",
}

_MACRO_CONCEPTS: dict[str, NewsTopic] = {
    "CPI": NewsTopic.INFLATION,
    "INFLATION": NewsTopic.INFLATION,
    "PCE": NewsTopic.INFLATION,
    "PRICES": NewsTopic.INFLATION,
    "NFP": NewsTopic.EMPLOYMENT,
    "PAYROLLS": NewsTopic.EMPLOYMENT,
    "EMPLOYMENT": NewsTopic.EMPLOYMENT,
    "JOBS": NewsTopic.EMPLOYMENT,
    "UNEMPLOYMENT": NewsTopic.EMPLOYMENT,
    "JOBLESS": NewsTopic.EMPLOYMENT,
    "GDP": NewsTopic.GDP,
    "GROWTH": NewsTopic.GROWTH,
    "PMI": NewsTopic.GROWTH,
    "RECESSION": NewsTopic.GROWTH,
    "RATES": NewsTopic.INTEREST_RATES,
    "RATE HIKE": NewsTopic.INTEREST_RATES,
    "RATE CUT": NewsTopic.INTEREST_RATES,
    "HIKES": NewsTopic.INTEREST_RATES,
    "YIELD": NewsTopic.BOND_YIELDS,
    "YIELDS": NewsTopic.BOND_YIELDS,
    "BOND": NewsTopic.BOND_YIELDS,
    "CENTRAL BANK": NewsTopic.CENTRAL_BANK,
    "MONETARY POLICY": NewsTopic.MONETARY_POLICY,
    "TIGHTENING": NewsTopic.MONETARY_POLICY,
    "EASING": NewsTopic.MONETARY_POLICY,
    "GEOPOLITICAL": NewsTopic.GEOPOLITICS,
    "GEOPOLITICS": NewsTopic.GEOPOLITICS,
    "WAR": NewsTopic.WAR,
    "CONFLICT": NewsTopic.WAR,
    "INVASION": NewsTopic.WAR,
    "SANCTIONS": NewsTopic.WAR,
    "ENERGY": NewsTopic.ENERGY,
    "OIL": NewsTopic.ENERGY,
    "GAS": NewsTopic.ENERGY,
    "COMMODITIES": NewsTopic.COMMODITIES,
    "SAFE HAVEN": NewsTopic.SAFE_HAVEN,
    "SAFE-HAVEN": NewsTopic.SAFE_HAVEN,
    "RISK-ON": NewsTopic.RISK_ON,
    "RISK ON": NewsTopic.RISK_ON,
    "RISK-APPETITE": NewsTopic.RISK_ON,
    "RISK-OFF": NewsTopic.RISK_OFF,
    "RISK OFF": NewsTopic.RISK_OFF,
    "AVERSION": NewsTopic.RISK_OFF,
    "LIQUIDITY": NewsTopic.LIQUIDITY,
    "REGULATION": NewsTopic.REGULATION,
    "BANKING": NewsTopic.FINANCIAL_STABILITY,
    "FINANCIAL STABILITY": NewsTopic.FINANCIAL_STABILITY,
    "CREDIT CRUNCH": NewsTopic.FINANCIAL_STABILITY,
}

# ---------------------------------------------------------------------------
# Direction lexicon (contextual; not a hard rule)
# ---------------------------------------------------------------------------

_BULLISH_XAUUSD: list[str] = [
    "safe haven",
    "flight to safety",
    "haven demand",
    "geopolitical risk",
    "inflation hedge",
    "inflation rises",
    "inflation surge",
    "inflation shock",
    "fed cut",
    "rate cut",
    "cuts rates",
    "dovish",
    "easing",
    "stimulus",
    "dollar weakens",
    "dollar fell",
    "dollar slides",
    "weaker dollar",
    "real yields fall",
    "yields drop",
    "yields fall",
    "risk aversion",
    "recession fears",
    "easing cycle",
]

_BEARISH_XAUUSD: list[str] = [
    "rate hike",
    "hikes rates",
    "hawkish",
    "tightening",
    "tapering",
    "dollar strengthens",
    "dollar rises",
    "dollar rallies",
    "stronger dollar",
    "real yields rise",
    "yields surge",
    "yields jump",
    "risk appetite",
    "risk-on",
    "optimism",
    "stock rally",
    "equities rally",
    "gold falls",
    "gold slides",
    "gold drops",
]

_BULLISH_FX_ASSET: dict[str, list[str]] = {
    "USD": [
        "hawkish",
        "rate hike",
        "tightening",
        "stronger dollar",
        "dollar rallies",
        "higher yields",
    ],
    "EUR": ["ecb", "european growth", "eurozone", "ecb hike"],
    "GBP": ["boe", "mpc", "uk growth", "british economy"],
    "JPY": ["boj", "yen", "japanese yields", "japan"],
    "CHF": ["snb", "swiss", "safe haven franc"],
}

_BEARISH_FX_ASSET: dict[str, list[str]] = {
    "USD": ["dovish", "rate cut", "weaker dollar", "dollar slides", "lower yields"],
    "EUR": ["eurozone weakness", "ecb dovish", "europe recession"],
    "GBP": ["uk recession", "boe dovish", "sterling falls"],
    "JPY": ["boj dovish", "yen weakens"],
    "CHF": ["snb dovish", "franc weakness"],
}

#: Words that should NOT be treated as gold/XAUUSD mentions.
_GOLD_NEGATIVES: list[str] = [
    "golden state",
    "gold medal",
    "golden gate",
    "goldman",
]


def _title_and_text(article: Any) -> str:
    title = getattr(article, "title", "") or ""
    summary = getattr(article, "summary", "") or ""
    body = getattr(article, "body", "") or ""
    return " ".join([title, summary, body]).upper()


class LocalNewsAnalyzer:
    """Deterministic rule-based analysis - the authoritative fallback path."""

    def __init__(self, config: Any | None = None) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Entity extraction
    # ------------------------------------------------------------------

    def extract_entities(self, article: Any) -> list[NewsEntity]:
        text = _title_and_text(article)
        entities: dict[str, tuple[str, int]] = {}
        for token, canon in _CURRENCIES.items():
            count = _count_occurrences(text, token)
            if count > 0:
                entities[canon] = ("CURRENCY", entities.get(canon, ("CURRENCY", 0))[1] + count)
        for token, canon in _ASSETS.items():
            count = _count_occurrences(text, token)
            if count > 0:
                entities[canon] = ("ASSET", entities.get(canon, ("ASSET", 0))[1] + count)
        for token, canon in _INSTITUTIONS.items():
            count = _count_occurrences(text, token)
            if count > 0:
                entities[canon] = (
                    "INSTITUTION",
                    entities.get(canon, ("INSTITUTION", 0))[1] + count,
                )
        for token, _topic in _MACRO_CONCEPTS.items():
            if _count_occurrences(text, token) > 0:
                canon = token
                entities[canon] = ("MACRO", entities.get(canon, ("MACRO", 0))[1] + 1)

        out: list[NewsEntity] = []
        total = max(1, sum(c for _, c in entities.values()))
        for name, (etype, count) in sorted(entities.items(), key=lambda kv: -kv[1][1]):
            out.append(
                NewsEntity(
                    name=name,
                    entity_type=etype,
                    relevance=round(min(1.0, count / total * 3.0), 4),
                    mentions=count,
                    is_primary=count >= 3,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Topics
    # ------------------------------------------------------------------

    def classify_topics(self, article: Any, entities: list[NewsEntity]) -> list[NewsTopic]:
        text = _title_and_text(article)
        topics: list[NewsTopic] = []
        for token, topic in _MACRO_CONCEPTS.items():
            if _count_occurrences(text, token) > 0 and topic not in topics:
                topics.append(topic)
        # Entity-driven topic hints
        entity_names = {e.name for e in entities}
        if "FED" in entity_names or "ECB" in entity_names or "BOE" in entity_names:
            if NewsTopic.CENTRAL_BANK not in topics:
                topics.append(NewsTopic.CENTRAL_BANK)
        if "XAUUSD" in entity_names and NewsTopic.COMMODITIES not in topics:
            topics.append(NewsTopic.COMMODITIES)
        if not topics:
            topics.append(NewsTopic.OTHER)
        return topics

    # ------------------------------------------------------------------
    # Novelty
    # ------------------------------------------------------------------

    def assess_novelty(self, article: Any, duplicate_of: str, is_duplicate: bool) -> NewsNovelty:
        if is_duplicate and duplicate_of:
            return NewsNovelty.REPETITION
        updated = getattr(article, "updated_at", None)
        published = getattr(article, "published_at", None)
        if updated and published and (updated - published) > timedelta(minutes=15):
            return NewsNovelty.UPDATED
        return NewsNovelty.NEW

    # ------------------------------------------------------------------
    # XAUUSD relevance (evidence/rule-based, not keyword-only)
    # ------------------------------------------------------------------

    def xauusd_relevance(
        self, article: Any, entities: list[NewsEntity], topics: list[NewsTopic]
    ) -> float:
        text = _title_and_text(article)
        score = 0.0
        entity_names = {e.name for e in entities}

        has_gold = "XAUUSD" in entity_names or "GOLD" in text
        if has_gold:
            # A direct gold mention earns a LOW base relevance; only when
            # market drivers are present does relevance become meaningful.
            # ("Gold medal" ≠ an XAUUSD event.)
            score += 0.20
        # Calibration upgrade (2026-08-17): XAUUSD drivers WITHOUT a literal
        # gold token still move gold (USD, real yields, CPI, Fed, geopolitics,
        # oil, risk-off). Previously `else: return 0.0` zeroed every such
        # headline, so ~93% of driver articles scored 0 (measured on live DB).
        # Driver-only articles now earn a scaled baseline when the driver is
        # strong, capped below the direct-gold tier:
        #   strong single driver -> 0.45, two+ drivers -> up to 0.75.

        driver_topics = {
            NewsTopic.USD: 0.18,
            NewsTopic.BOND_YIELDS: 0.20,
            NewsTopic.INFLATION: 0.20,
            NewsTopic.GEOPOLITICS: 0.16,
            NewsTopic.SAFE_HAVEN: 0.18,
            NewsTopic.INTEREST_RATES: 0.16,
            NewsTopic.CENTRAL_BANK: 0.14,
            NewsTopic.RISK_OFF: 0.14,
            NewsTopic.ENERGY: 0.12,
            NewsTopic.LIQUIDITY: 0.10,
            NewsTopic.MONETARY_POLICY: 0.14,
            NewsTopic.EMPLOYMENT: 0.10,
            NewsTopic.GROWTH: 0.06,
        }
        topic_hits = 0
        for topic in topics:
            score += driver_topics.get(topic, 0.0)
            if topic in driver_topics:
                topic_hits += 1

        # strong driver keywords
        driver_kws = [
            "FED",
            "FOMC",
            "CPI",
            "PCE",
            "NFP",
            "YIELD",
            "TREASURY",
            "DOLLAR",
            "DXY",
            "INFLATION",
            "RATE HIKE",
            "RATE CUT",
            "HAVEN",
            "GEOPOLITICAL",
            "WAR",
            "IRAN",
            "SANCTIONS",
            "OIL",
            "CRUDE",
            "RISK OFF",
            "RISK-OFF",
            "RECESSION",
        ]
        kw_hits = sum(1 for kw in driver_kws if _count_occurrences(text, kw) > 0)
        score += min(0.25, kw_hits * 0.05)

        # price-action verbs make a gold mention directly market-relevant
        for kw in [
            "GOLD ROSE",
            "GOLD FELL",
            "GOLD SURGED",
            "GOLD SLID",
            "GOLD DROPPED",
            "GOLD JUMPED",
            "GOLD PLUNGED",
            "SPOT GOLD",
            "GOLD BREAK",
            "GOLD BUYERS",
            "GOLD SELLERS",
        ]:
            if kw in text:
                score += 0.15

        # Driver-only baseline: no gold token but meaningful driver mass.
        if not has_gold:
            driver_mass = topic_hits + kw_hits
            if driver_mass >= 3:
                score = max(score, 0.55)  # multi-driver macro headline
            elif driver_mass == 2:
                score = max(score, 0.40)
            elif driver_mass == 1:
                score = max(score, 0.25)

        return round(min(1.0, score), 4)

    def usd_relevance(
        self, article: Any, entities: list[NewsEntity], topics: list[NewsTopic]
    ) -> float:
        text = _title_and_text(article)
        entity_names = {e.name for e in entities}
        score = 0.0
        if "USD" in entity_names or "DOLLAR" in text:
            score += 0.5
        for topic in (
            NewsTopic.USD,
            NewsTopic.INTEREST_RATES,
            NewsTopic.CENTRAL_BANK,
            NewsTopic.INFLATION,
            NewsTopic.EMPLOYMENT,
            NewsTopic.BOND_YIELDS,
        ):
            if topic in topics:
                score += 0.12
        if any(k in entity_names for k in ("FED", "BLS", "BEA", "USTREASURY")):
            score += 0.15
        return round(min(1.0, score), 4)

    # ------------------------------------------------------------------
    # Direction / impact hypothesis
    # ------------------------------------------------------------------

    def directional_hypothesis(
        self, article: Any, entities: list[NewsEntity], topics: list[NewsTopic]
    ) -> tuple[NewsDirection, list[NewsImpact]]:
        text = _title_and_text(article)
        impacts: list[NewsImpact] = []

        # XAUUSD direction
        xauusd_dir = NewsDirection.NEUTRAL
        bull_hits = sum(1 for kw in _BULLISH_XAUUSD if kw.upper() in text)
        bear_hits = sum(1 for kw in _BEARISH_XAUUSD if kw.upper() in text)
        relevance = self.xauusd_relevance(article, entities, topics)
        if relevance >= 0.3:
            if bull_hits > bear_hits:
                xauusd_dir = NewsDirection.BULLISH
            elif bear_hits > bull_hits:
                xauusd_dir = NewsDirection.BEARISH
            elif bull_hits and bear_hits:
                xauusd_dir = NewsDirection.MIXED
            strength = min(1.0, (abs(bull_hits - bear_hits) * 0.2) + 0.2 + relevance * 0.3)
            impacts.append(
                NewsImpact(
                    asset="XAUUSD",
                    direction=xauusd_dir,
                    strength=round(strength, 4),
                    confidence=round(0.5 + relevance * 0.3, 4),
                    horizon=self._horizon(topics),
                    relevance=relevance,
                    mechanism=self._mechanism(topics, xauusd_dir),
                )
            )

        # FX asset directions
        for asset, bull_kws in _BULLISH_FX_ASSET.items():
            if not (any(e.name == asset for e in entities) or asset in text):
                continue
            b = sum(1 for kw in bull_kws if kw.upper() in text)
            s = sum(1 for kw in _BEARISH_FX_ASSET.get(asset, []) if kw.upper() in text)
            fx_dir = NewsDirection.NEUTRAL
            if b > s:
                fx_dir = NewsDirection.BULLISH
            elif s > b:
                fx_dir = NewsDirection.BEARISH
            impacts.append(
                NewsImpact(
                    asset=asset,
                    direction=fx_dir,
                    strength=round(min(1.0, 0.3 + abs(b - s) * 0.2), 4),
                    confidence=round(0.5, 4),
                    horizon=NewsImpactHorizon.MACRO,
                    relevance=round(0.5, 4),
                    mechanism=f"{asset} directional hypothesis",
                )
            )
        return xauusd_dir, impacts

    # ------------------------------------------------------------------
    # Importance
    # ------------------------------------------------------------------

    def importance_score(
        self, article: Any, topics: list[NewsTopic], source_priority: float
    ) -> tuple[float, NewsImportance]:
        text = _title_and_text(article)
        score = 0.0
        # source credibility
        score += source_priority * 0.25
        # topic impact class
        high_topics = {
            NewsTopic.MONETARY_POLICY,
            NewsTopic.INFLATION,
            NewsTopic.EMPLOYMENT,
            NewsTopic.GEOPOLITICS,
            NewsTopic.WAR,
            NewsTopic.FINANCIAL_STABILITY,
            NewsTopic.INTEREST_RATES,
            NewsTopic.CENTRAL_BANK,
            NewsTopic.GDP,
        }
        for t in topics:
            score += 0.12 if t in high_topics else 0.04
        # institution mentions
        for inst in ("FED", "FOMC", "ECB", "BOE", "BOJ", "SNB", "PBOC", "BLS", "CFTC"):
            if _count_occurrences(text, inst) > 0:
                score += 0.08
        # macro release markers
        for marker in ("CPI", "PCE", "NFP", "GDP", "PMI", "rate decision", "rate cut", "rate hike"):
            if marker.upper() in text:
                score += 0.10
        score = round(min(1.0, score), 4)
        if score >= 0.7:
            importance = NewsImportance.CRITICAL
        elif score >= 0.5:
            importance = NewsImportance.HIGH
        elif score >= 0.3:
            importance = NewsImportance.MODERATE
        elif score >= 0.12:
            importance = NewsImportance.MINOR
        else:
            importance = NewsImportance.TRIVIAL
        return score, importance

    def _horizon(self, topics: list[NewsTopic]) -> NewsImpactHorizon:
        if NewsTopic.GEOPOLITICS in topics or NewsTopic.WAR in topics:
            return NewsImpactHorizon.POLICY
        if NewsTopic.MONETARY_POLICY in topics or NewsTopic.CENTRAL_BANK in topics:
            return NewsImpactHorizon.POLICY
        if (
            NewsTopic.INFLATION in topics
            or NewsTopic.EMPLOYMENT in topics
            or NewsTopic.BOND_YIELDS in topics
        ):
            return NewsImpactHorizon.MACRO
        return NewsImpactHorizon.MACRO

    def _mechanism(self, topics: list[NewsTopic], direction: NewsDirection) -> str:
        if direction in (NewsDirection.BULLISH, NewsDirection.BEARISH):
            parts = [t.value.lower() for t in topics[:3]]
            return f"{direction.value.lower()} via " + (
                "/".join(parts) if parts else "macro driver"
            )
        return "neutral / unclear mechanism"


def _count_occurrences(text: str, token: str) -> int:
    """Word-boundary count of a token in upper-cased text."""
    if not token:
        return 0
    pattern = r"(?<![A-Z0-9])" + re.escape(token.upper()) + r"(?![A-Z0-9])"
    return len(re.findall(pattern, text))
