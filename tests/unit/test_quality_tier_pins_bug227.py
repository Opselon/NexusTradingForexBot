"""BUG-227 Wave E regression — pin quality_tier banding (relative + absolute).

Census gap: TIER_A_PCT / TIER_B_PCT / TIER_C_MIN and the ``quality_tier``
banding function (sample_maker.py:30-70) had no direct pin — a mutation of
the percentile cuts or the absolute floor would silently re-label the elite
sample pool (the training data quality gate).

Pinned behavior (documented contract in the docstring):
  RELATIVE path (percentile given, 0=worst .. 1=elite):
    percentile >= 1-0.10 -> TIER_A;  >= 1-0.35 -> TIER_B;  else TIER_C
  ABSOLUTE path (no percentile):
    quality >= 0.80 -> TIER_A; >= 0.70 -> TIER_B; >= 0.55 -> TIER_C;
    below 0.55 -> NO_TRADE
  Boundaries are INCLUSIVE (>=); the function must be pure (same input ->
  same tier regardless of call order).
"""

from __future__ import annotations

import pytest

from nexus_scalp.model_generation.sample_maker import (
    TIER_A_PCT,
    TIER_B_PCT,
    TIER_C_MIN,
    quality_tier,
)


def test_relative_bands_at_declared_cuts() -> None:
    assert TIER_A_PCT == pytest.approx(0.10)
    assert TIER_B_PCT == pytest.approx(0.35)
    # 1-0.10 = 0.90 boundary: inclusive A above, B below.
    assert quality_tier(0.95, percentile=0.90) == "TIER_A"
    assert quality_tier(0.95, percentile=0.899) == "TIER_B"
    # 1-0.35 = 0.65 boundary: inclusive B above, C below.
    assert quality_tier(0.60, percentile=0.65) == "TIER_B"
    assert quality_tier(0.60, percentile=0.649) == "TIER_C"
    assert quality_tier(0.60, percentile=0.0) == "TIER_C"


def test_absolute_floors_inclusive() -> None:
    assert TIER_C_MIN == pytest.approx(0.55)
    assert quality_tier(0.80) == "TIER_A"
    assert quality_tier(0.799) == "TIER_B"
    assert quality_tier(0.70) == "TIER_B"
    assert quality_tier(0.699) == "TIER_C"
    assert quality_tier(0.55) == "TIER_C"
    assert quality_tier(0.549) == "NO_TRADE"
    assert quality_tier(0.0) == "NO_TRADE"


def test_percentile_beats_absolute() -> None:
    """A quality below the absolute TIER_A floor still earns TIER_A when its
    percentile rank is in the top band (relative mode is authoritative)."""
    assert quality_tier(0.60, percentile=0.95) == "TIER_A"


def test_band_edges_never_cross() -> None:
    """Monotonicity: raising quality (or percentile) never lowers the tier."""
    tiers = ["NO_TRADE", "TIER_C", "TIER_B", "TIER_A"]
    prev = -1
    for q in (0.0, 0.549, 0.55, 0.699, 0.70, 0.799, 0.80, 1.0):
        idx = tiers.index(quality_tier(q))
        assert idx >= prev, f"tier regressed at quality {q}"
        prev = idx
