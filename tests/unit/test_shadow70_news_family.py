"""TEST-70D-NEWS-01..05 — TASK-10: the 70D NEWS-FAMILY projection contract.

Regression guard for the live-path ``[:10]`` truncation bug (news state
encoding silently dropped from the 70D NEWS-FAMILY block at indices
50..59). EXTENDS the shadow70 module coverage per repo convention.
"""

from __future__ import annotations

import pytest

from nexus_scalp.shadow.shadow70.news_provider import (
    NEWS_FAMILY_DIM,
    NEWS_FAMILY_SLOT_NAMES,
    build_news_10,
    verify_news_family,
)

# A canonical 12-field news_context_v1 vector (vectorize_news_context order):
# [active, xauusd_relevance, usd_relevance, bullish, bearish, conflict,
#  novelty, freshness, confidence, source_consensus, state_enc, time_since]
HIGH_IMPACT_V12 = [
    3.0, 0.92, 0.55, 0.70, 0.10, 0.0, 0.0, 0.90, 0.80, 0.60, 2.0, 42.0,
]


def test_news_family_is_exactly_10() -> None:
    news10, ver = build_news_10(HIGH_IMPACT_V12)
    assert len(news10) == NEWS_FAMILY_DIM == 10
    assert ver == "news_family_v1"


def test_news_family_preserves_state_encoding() -> None:
    """TEST-70D-NEWS-02 — the HIGH_IMPACT state flag MUST survive at slot 59."""
    news10, _ = build_news_10(HIGH_IMPACT_V12)
    assert news10[-1] == 2.0  # state_enc = HIGH_IMPACT


def test_news_family_values_are_real_fields() -> None:
    """TEST-70D-NEWS-03 — every slot is a REAL context field, in order."""
    news10, _ = build_news_10(HIGH_IMPACT_V12)
    expected = [3.0, 0.92, 0.55, 0.70, 0.10, 0.0, 0.0, 0.90, 0.80, 2.0]
    assert news10 == expected
    assert len(NEWS_FAMILY_SLOT_NAMES) == 10
    assert NEWS_FAMILY_SLOT_NAMES[-1] == "state_encoding"


def test_news_family_rejects_wrong_width() -> None:
    """TEST-70D-NEWS-04 — no silent pad/truncate: 12 in -> 10 out ONLY."""
    with pytest.raises(ValueError):
        build_news_10([0.0] * 11)
    with pytest.raises(ValueError):
        build_news_10([0.0] * 13)


def test_news_family_finite_and_bounded() -> None:
    """TEST-70D-NEWS-05 — the family block satisfies INV-70D-005/006."""
    news10, _ = build_news_10(HIGH_IMPACT_V12)
    assert verify_news_family(news10)