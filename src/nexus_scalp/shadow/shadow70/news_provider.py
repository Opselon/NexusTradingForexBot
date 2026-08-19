"""Canonical News-family 10D mapping for the 70D vector (TASK-10).

WHY THIS EXISTS
---------------
The 70D contract reserves indices 50..59 for the NEWS family
(INV-70D-002). The canonical live news context vectorizes to a
12-field ``news_context_v1`` layout (``vectorize_news_context``). The
previous live-path mapping ``(nv + [0.0]*10)[:10]`` was an ARBITRARY
prefix slice that silently discarded the 12th-field state encoding
(HIGH_IMPACT/ELEVATED flag) and the time-since-event field — i.e. the
"News 10D" did NOT contain the news STATE, violating the semantics the
70D contract documents.

This module defines the explicit, named 10-slot projection used ONLY as
the NEWS-FAMILY block of the 70D vector:
    idx 50  active_event_count        (real)
    idx 51  xauusd_relevance          (real)
    idx 52  usd_relevance             (real)
    idx 53  bullish_pressure          (real)
    idx 54  bearish_pressure          (real)
    idx 55  conflict_score            (real)
    idx 56  novelty_encoding          (real)
    idx 57  freshness                 (real)
    idx 58  confidence                (real)
    idx 59  state_encoding            (real)   <- formerly DROPPED

The state encoding (2.0 = HIGH_IMPACT, 1.0 = ELEVATED, ...) is the
single most decision-relevant news signal; it must never be silently
dropped. ``time_since_event_sec`` remains available in the full 12-field
canonical context (news_context_v1) — the 70D NEWS-FAMILY block simply
does not carry it (10 slots), and that omission is DOCUMENTED, not
accidental.

SAFETY: pure function, no I/O. Raises on a non-12 input (never silently
reshapes). The shadow/observe path calls this with the canonical
``news_context_v1`` vector; any mismatch is an explicit error.
"""

from __future__ import annotations

import math

from nexus_scalp.governance.alignment import NEWS_CONTEXT_DIM

NEWS_FAMILY_DIM: int = 10

#: Canonical position of the news-state encoding INSIDE the 12-field
#: news_context_v1 vector (see vectorize_news_context order).
STATE_ENC_INDEX: int = 10
#: Canonical position of time_since_event_sec INSIDE the 12-field vector.
TIME_SINCE_EVENT_INDEX: int = 11

#: Human-readable names for the 10 family slots (for telemetry/tests).
NEWS_FAMILY_SLOT_NAMES: tuple[str, ...] = (
    "active_event_count",
    "xauusd_relevance",
    "usd_relevance",
    "bullish_pressure",
    "bearish_pressure",
    "conflict_score",
    "novelty_encoding",
    "freshness",
    "confidence",
    "state_encoding",
)


def build_news_10(news_context_v12: list[float] | tuple[float, ...]) -> tuple[list[float], str]:
    """Explicit 12 -> 10 NEWS-FAMILY projection for the 70D vector.

    Returns ``(news10, version)``. The projection is NAMED, never an
    arbitrary slice; the state encoding is preserved at slot 59.
    time_since_event_sec is deliberately excluded (documented; it stays
    in the canonical 12-field context for the model path).

    Raises ValueError when the input width != 12 (no silent pad/truncate).
    """
    if len(news_context_v12) != NEWS_CONTEXT_DIM:
        raise ValueError(
            f"news family projection requires the {NEWS_CONTEXT_DIM}-field "
            f"news_context_v1 vector, got {len(news_context_v12)}"
        )
    src = list(news_context_v12)
    # Explicit mapping (NOT a slice): preserve the state encoding.
    news10 = [src[i] for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, STATE_ENC_INDEX)]
    return news10, "news_family_v1"


def verify_news_family(news10: list[float]) -> bool:
    """Sanity: the family block is exactly 10 finite values in [-3, +3]."""
    return (
        len(news10) == NEWS_FAMILY_DIM
        and all(math.isfinite(v) for v in news10)
        and all(-3.0 <= v <= 3.0 for v in news10)
    )
