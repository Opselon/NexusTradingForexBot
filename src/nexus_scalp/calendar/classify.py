"""Deterministic high-impact economic-event classifier (Phase 5A / 6A).

OBVIOUS high-impact events (CPI / NFP / FOMC / rate decisions / ECB) are
identified by a deterministic, alias-aware, case-normalized matcher — no
LLM required. This both:
    * tags calendar events with a canonical ``event_type`` (Phase 6A), and
    * lets the news pipeline skip LLM sentiment for articles that are a
      deterministic re-statement of a scheduled release (Phase 5A cost cut).

Design rules (per mission):
    * normalize case + punctuation + common abbreviations before matching,
    * aliases are explicit data (NFP / Non-Farm / Nonfarm / Employment
      Situation / Payrolls all -> nfp),
    * every match returns the canonical type + the matched alias +
      importance, so the call site can attach provenance,
    * pure functions, no I/O, no network, deterministic and testable.

The classifier intentionally covers ONLY events with a stable, official
identity. Ambiguous/contextual headlines remain for the LLM path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nexus_scalp.calendar.models import EventImportance


class HighImpactEventType(StrEnum):
    """Canonical event types with durable official identities."""

    CPI = "cpi"
    CORE_CPI = "core_cpi"
    PPI = "ppi"
    NFP = "nfp"
    UNEMPLOYMENT_RATE = "unemployment_rate"
    FOMC_DECISION = "fomc_decision"
    FOMC_MINUTES = "fomc_minutes"
    FED_CHAIR_TESTIMONY = "fed_chair_testimony"
    ECB_DECISION = "ecb_decision"
    BOE_DECISION = "boe_decision"
    FOMC_PRESS_CONF = "fomc_press_conf"
    ECB_PRESS_CONF = "ecb_press_conf"
    GDP = "gdp"
    RETAIL_SALES = "retail_sales"
    PMI = "pmi"
    JOLTS = "jolts"
    ADP = "adp"
    PCE = "pce"
    CLAIMS = "claims"
    RATE_DECISION_OTHER = "rate_decision_other"


@dataclass(frozen=True)
class EventMatch:
    """Result of a deterministic classification."""

    event_type: HighImpactEventType | None
    matched_alias: str = ""
    #: True when the match alone is enough to treat the item as high-impact
    strong: bool = False


_ALIASES: tuple[tuple[HighImpactEventType, bool, tuple[str, ...]], ...] = (
    # --- inflation -------------------------------------------------------
    (
        HighImpactEventType.CPI,
        True,
        ("cpi", "consumer price index", "inflation rate y/y", "headline inflation"),
    ),
    (
        HighImpactEventType.CORE_CPI,
        True,
        ("core cpi", "core consumer price index"),
    ),
    (
        HighImpactEventType.PPI,
        True,
        ("ppi", "producer price index"),
    ),
    (
        HighImpactEventType.PCE,
        False,
        ("core pce", "pce price index", "personal consumption expenditures price"),
    ),
    # --- employment --------------------------------------------------------
    (
        HighImpactEventType.NFP,
        True,
        (
            "nonfarm payrolls",
            "non-farm payrolls",
            "non-farm payroll",
            "nonfarm payroll",
            "nfp",
            "employment situation",
            "change in non-farm payrolls",
            "average hourly earnings m/m",
        ),
    ),
    (
        HighImpactEventType.UNEMPLOYMENT_RATE,
        False,
        ("unemployment rate", "jobless rate"),
    ),
    (
        HighImpactEventType.JOLTS,
        False,
        ("jolts", "job openings"),
    ),
    (
        HighImpactEventType.ADP,
        False,
        ("adp employment", "adp national employment"),
    ),
    (
        HighImpactEventType.CLAIMS,
        False,
        ("initial jobless claims", "jobless claims", "continuing claims"),
    ),
    # --- central banks -----------------------------------------------------
    (
        HighImpactEventType.FOMC_DECISION,
        True,
        (
            "federal funds rate",
            "fomc rate decision",
            "fomc statement",
            "federal reserve interest rate decision",
            "fed interest rate decision",
            "fed rate decision",
            "target range",
        ),
    ),
    (
        HighImpactEventType.FOMC_MINUTES,
        False,
        ("fomc minutes", "federal reserve minutes", "monetary policy minutes"),
    ),
    (
        HighImpactEventType.FED_CHAIR_TESTIMONY,
        True,
        ("powell testimony", "fed chair testimony", "semiannual monetary policy report"),
    ),
    (
        HighImpactEventType.ECB_DECISION,
        True,
        (
            "main refinancing rate",
            "ecb rate decision",
            "ecb interest rate decision",
            "ecb policy decision",
            "deposit facility rate",
        ),
    ),
    (
        HighImpactEventType.ECB_PRESS_CONF,
        True,
        ("ecb press conference", "lagarde press conference"),
    ),
    (
        HighImpactEventType.FOMC_PRESS_CONF,
        True,
        ("fomc press conference", "powell press conference", "fed press conference"),
    ),
    (
        HighImpactEventType.BOE_DECISION,
        True,
        (
            "bank of england rate",
            "boe rate decision",
            "bank rate",
            "monetary policy summary",
        ),
    ),
    (
        HighImpactEventType.RATE_DECISION_OTHER,
        False,
        ("interest rate decision", "rate decision", "policy rate"),
    ),
    # --- growth / activity --------------------------------------------------
    (
        HighImpactEventType.GDP,
        False,
        ("gdp", "gross domestic product"),
    ),
    (
        HighImpactEventType.RETAIL_SALES,
        False,
        ("retail sales",),
    ),
    (
        HighImpactEventType.PMI,
        False,
        ("ism manufacturing pmi", "ism services pmi", "manufacturing pmi", "services pmi"),
    ),
)

#: Pre-compiled patterns: word-boundary, whitespace-collapsed matching.
_PATTERNS: tuple[tuple[HighImpactEventType, bool, re.Pattern[str], str], ...] = tuple(
    (etype, strong, re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"), alias)
    for etype, strong, aliases in _ALIASES
    for alias in aliases
)


def _normalize(text: str) -> str:
    """Case/punctuation/whitespace normalization for deterministic matching."""
    lowered = (text or "").lower()
    # unify punctuation variants so 'non-farm' / 'non farm' / 'nonfarm' agree
    # (explicit en/em dashes included: RUF001 is satisfied by naming them here)
    lowered = re.sub("[-\u2013\u2014_/]+", " ", lowered)
    lowered = re.sub("[%\u00b0]", " percent", lowered)
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    # collapse common multiplets
    lowered = lowered.replace("non farm", "nonfarm")
    lowered = lowered.replace("y o y", "y y").replace("m o m", "m m")
    lowered = lowered.replace("y y", "y/y").replace("m m", "m/m")
    return lowered


def classify_event_title(title: str) -> EventMatch:
    """Deterministically classifies a title/summary as a known high-impact event.

    Returns EventMatch(event_type=None) when no canonical identity matches —
    that item stays eligible for the (budget-gated) LLM path.
    """
    norm = _normalize(title)
    if not norm:
        return EventMatch(event_type=None)
    best: EventMatch = EventMatch(event_type=None)
    for etype, strong, pattern, alias in _PATTERNS:
        if pattern.search(norm):
            # Specificity beats strength: a longer alias (e.g. "core cpi")
            # identifies the event more precisely than a shorter generic one
            # (e.g. "cpi") and must not be shadowed by the early `break` on
            # the first strong hit. Prefer longer matched aliases; break only
            # when an alias fully contains the matched span AND is longer
            # (handled implicitly by length ordering below).
            if (
                best.event_type is None
                or (strong and not best.strong)
                or (len(alias) > len(best.matched_alias))
            ):
                best = EventMatch(event_type=etype, matched_alias=alias, strong=strong)
    return best


def is_obvious_high_impact(title: str) -> bool:
    """True when the title deterministically identifies a high-impact event."""
    match = classify_event_title(title)
    return bool(match.event_type and match.strong)


def classify_country_calendar_event(
    title: str, country: str, importance: str = ""
) -> tuple[HighImpactEventType | None, EventImportance]:
    """Classifies a calendar row (title + country + provider importance).

    Returns (canonical_event_type, normalized_importance). USD events with a
    strong identity are always HIGH; otherwise the provider importance passes
    through (clamped to the known enum, default MEDIUM).
    """
    from nexus_scalp.calendar.models import EventImportance

    match = classify_event_title(title)
    imp_raw = (importance or "").strip().title()
    try:
        imp = EventImportance(imp_raw)
    except ValueError:
        imp = EventImportance.MEDIUM
    if (
        match.event_type
        and match.strong
        and (country or "").upper()
        in (
            "USD",
            "EUR",
            "GBP",
            "JPY",
            "CHF",
        )
    ):
        imp = EventImportance.HIGH
    return match.event_type, imp
