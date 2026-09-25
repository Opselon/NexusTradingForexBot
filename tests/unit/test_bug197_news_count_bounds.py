"""BUG-197 regression — the live news-count encoding MUST be bounded.

FAILS BEFORE the fix: vectorize_news_context() emits the RAW active_event_count
(e.g. 27.0) into news10 slot 50 -> validate_70d_vector rejects the whole vector
(value out of [-3,+3]) -> live 70D inference blocked on EVERY tick while news is
active -> client permanently shows STALE with inference dead (P0 user journey).

PASSES AFTER: live encoding saturates at the training distribution (0/1 flag,
max 1.0) — bounded, in-distribution, and documented at the projection site.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.governance.alignment import vectorize_news_context  # noqa: E402
from nexus_scalp.news.models import CurrentNewsContext, NewsState  # noqa: E402
from nexus_scalp.shadow.shadow70.news_provider import build_news_10  # noqa: E402


def _ctx(count: int) -> CurrentNewsContext:
    return CurrentNewsContext(
        available=True,
        timestamp=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        state=NewsState.HIGH_IMPACT,
        active_event_count=count,
        xauusd_relevance=0.8,
        usd_relevance=0.5,
        bullish_score=0.21,
        bearish_score=0.09,
        confidence=0.6,
        conflict_score=0.03,
        freshness=0.95,
        source_consensus=0.7,
    )


@pytest.mark.parametrize("count", [0, 1, 2, 5, 27, 500])
def test_live_news_slot50_is_bounded_and_in_distribution(count: int) -> None:
    """Slot 50 must stay inside the 70D contract bounds [-3,+3] for ANY live
    count and must not exceed the training-frame maximum (1.0)."""
    news10, _ver = build_news_10(vectorize_news_context(_ctx(count)))
    slot50 = news10[0]
    assert -3.0 <= slot50 <= 3.0, f"slot50={slot50} violates the 70D bounds"
    assert slot50 <= 1.0, f"slot50={slot50} exceeds the training-frame max 1.0"


def test_live_news_slot50_zero_when_no_events() -> None:
    news10, _ = build_news_10(vectorize_news_context(_ctx(0)))
    assert news10[0] == 0.0


def test_live_news_slot50_one_when_events_active() -> None:
    news10, _ = build_news_10(vectorize_news_context(_ctx(1)))
    assert news10[0] == 1.0


def test_live_news_other_slots_keep_real_scores() -> None:
    """The bounded count must not corrupt the other news slots."""
    news10, _ = build_news_10(vectorize_news_context(_ctx(27)))
    assert news10[1] == pytest.approx(0.8)
    assert news10[3] == pytest.approx(0.21)
    assert news10[9] == pytest.approx(2.0)  # HIGH_IMPACT state encoding at slot 59
