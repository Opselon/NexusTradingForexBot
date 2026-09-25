"""BUG-227 Wave C2 regression — pin the regime hysteresis escalation-margin
contract (mutation-census gap).

Census gap: ``_REGIME_ACTIVITY`` (the escalation ordinal), the
``switch_prob_margin`` escalation gate (regime_classifier.py:575-585) and
``min_regime_hold_sec`` had only transitive/absent pins — a mutation could
make the safe RANGING baseline absorbing (the pre-BUG-132 defect) or let a
single noisy tick escalate into a high-intervention regime.

Pinned behavior (from the BUG-132 fix, regime_classifier.py:556-591):
  1. ESCALATION MARGIN: switching UP the activity ordinal requires
     candidate_prob >= stable_prob + switch_prob_margin; a weak candidate is
     rejected with reason HYSTERESIS_MARGIN.
  2. DE-ESCALATION: switching DOWN (back toward RANGING) does NOT require the
     margin — the neutral baseline must stay reachable (anti-absorbing).
  3. UNSAFE RELAXATION: from CHOP/NEWS the classifier can always relax
     immediately regardless of margins (never stuck frozen).
  4. MIN HOLD: within min_regime_hold_sec of a switch, the stable regime is
     retained with reason HYSTERESIS_HOLD.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.regime_classifier import (
    MarketRegimeClassifier,
    RecommendedExecutionType,
    RegimeReason,
    RegimeType,
)

_EPOCH = datetime(2025, 6, 1, 0, 0, tzinfo=UTC).timestamp()


def _tick(ts: datetime, bid: float, ask: float) -> TickData:
    return TickData(symbol="XAUUSD", timestamp=ts, bid=bid, ask=ask, volume=1.0)


def _feed_ticks(clf: MarketRegimeClassifier, n: int, *, bid0: float = 2000.0) -> None:
    """Feed n flat ticks (cold start warmup + stable RANGING baseline)."""
    t0 = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
    for i in range(n):
        clf.classify_tick(_tick(t0 + timedelta(seconds=i), bid0, bid0 + 0.05))


def test_escalation_requires_probability_margin() -> None:
    """A candidate one ordinal ABOVE the stable regime with prob < stable+margin
    must be rejected (HYSTERESIS_MARGIN), not accepted."""
    clf = MarketRegimeClassifier(min_regime_hold_sec=4.0, switch_prob_margin=0.10)
    _feed_ticks(clf, 40)
    assert clf._stable_regime == RegimeType.RANGING_MEAN_REVERSION

    # Force a TRENDING candidate (ordinal 1 > 0) with a LOW probability that
    # cannot clear stable_prob + 0.10. Drive through the classifier's own
    # hysteresis gate directly to isolate the margin contract.
    ok = clf._apply_hysteresis(
        now_sec=_EPOCH + 100.0,
        candidate_regime=RegimeType.TRENDING_MOMENTUM,
        candidate_prob=clf._stable_prob + 0.05,  # below stable + margin
        candidate_exec=RecommendedExecutionType.IOC_MARKET,
        candidate_reason=RegimeReason.OFI_TREND_ALIGN,
    )
    regime, prob, _exec, reason = ok
    assert regime == RegimeType.RANGING_MEAN_REVERSION, regime
    assert reason == RegimeReason.HYSTERESIS_MARGIN, reason

    # A candidate that clears the margin IS accepted.
    ok2 = clf._apply_hysteresis(
        now_sec=_EPOCH + 200.0,
        candidate_regime=RegimeType.TRENDING_MOMENTUM,
        candidate_prob=clf._stable_prob + 0.20,
        candidate_exec=RecommendedExecutionType.IOC_MARKET,
        candidate_reason=RegimeReason.OFI_TREND_ALIGN,
    )
    assert ok2[0] == RegimeType.TRENDING_MOMENTUM, ok2


def test_deescalation_needs_no_margin() -> None:
    """From TRENDING (ordinal 1) a RANGING candidate (ordinal 0) with a LOW
    probability must still be accepted — the neutral baseline is never
    absorbing (the pre-BUG-132 defect)."""
    clf = MarketRegimeClassifier(min_regime_hold_sec=4.0, switch_prob_margin=0.10)
    _feed_ticks(clf, 40)
    # Climb into TRENDING with a clearing margin.
    clf._apply_hysteresis(
        now_sec=_EPOCH + 100.0,
        candidate_regime=RegimeType.TRENDING_MOMENTUM,
        candidate_prob=clf._stable_prob + 0.30,
        candidate_exec=RecommendedExecutionType.IOC_MARKET,
        candidate_reason=RegimeReason.OFI_TREND_ALIGN,
    )
    assert clf._stable_regime == RegimeType.TRENDING_MOMENTUM
    # Now de-escalate with a probability BELOW stable+margin: must switch.
    ok = clf._apply_hysteresis(
        now_sec=_EPOCH + 200.0,
        candidate_regime=RegimeType.RANGING_MEAN_REVERSION,
        candidate_prob=max(0.0, clf._stable_prob - 0.40),
        candidate_exec=RecommendedExecutionType.PASSIVE_LIMIT,
        candidate_reason=RegimeReason.DEFAULT_RANGE,
    )
    assert ok[0] == RegimeType.RANGING_MEAN_REVERSION, ok
    assert ok[3] != RegimeReason.HYSTERESIS_MARGIN, ok


def test_unsafe_regimes_relax_without_margin() -> None:
    """From HIGH_SPREAD_CHOP the classifier can always return to RANGING
    immediately — FREEZE_ALL must never be a trap (safety contract)."""
    clf = MarketRegimeClassifier(min_regime_hold_sec=4.0, switch_prob_margin=0.10)
    _feed_ticks(clf, 40)
    # Warmup leaves stable_prob at its 1.0 ceiling on a perfectly flat feed;
    # refresh the same-regime probability to a live-like 0.70 first (the
    # same-regime branch refreshes _stable_prob without a hold-time gate).
    clf._apply_hysteresis(
        now_sec=_EPOCH + 100.0,
        candidate_regime=RegimeType.RANGING_MEAN_REVERSION,
        candidate_prob=0.70,
        candidate_exec=RecommendedExecutionType.PASSIVE_LIMIT,
        candidate_reason=RegimeReason.DEFAULT_RANGE,
    )
    # Enter CHOP.
    clf._apply_hysteresis(
        now_sec=_EPOCH + 200.0,
        candidate_regime=RegimeType.HIGH_SPREAD_CHOP,
        candidate_prob=0.99,
        candidate_exec=RecommendedExecutionType.FREEZE_ALL,
        candidate_reason=RegimeReason.SPREAD_SCHMITT,
    )
    assert clf._stable_regime == RegimeType.HIGH_SPREAD_CHOP
    # Relax with an arbitrarily low probability — no margin demanded.
    ok = clf._apply_hysteresis(
        now_sec=_EPOCH + 300.0,
        candidate_regime=RegimeType.RANGING_MEAN_REVERSION,
        candidate_prob=0.10,
        candidate_exec=RecommendedExecutionType.PASSIVE_LIMIT,
        candidate_reason=RegimeReason.DEFAULT_RANGE,
    )
    assert ok[0] == RegimeType.RANGING_MEAN_REVERSION, ok


def test_min_hold_retains_stable_regime() -> None:
    """Within min_regime_hold_sec of the last switch the stable regime is
    retained (HYSTERESIS_HOLD) even for a strong candidate."""
    clf = MarketRegimeClassifier(min_regime_hold_sec=60.0, switch_prob_margin=0.10)
    _feed_ticks(clf, 40)
    # Refresh stable_prob to 0.70 (flat-feed warmup saturates at 1.0) so the
    # 0.95 TRENDING candidate can clear the escalation margin.
    clf._apply_hysteresis(
        now_sec=_EPOCH + 100.0,
        candidate_regime=RegimeType.RANGING_MEAN_REVERSION,
        candidate_prob=0.70,
        candidate_exec=RecommendedExecutionType.PASSIVE_LIMIT,
        candidate_reason=RegimeReason.DEFAULT_RANGE,
    )
    clf._apply_hysteresis(
        now_sec=_EPOCH + 200.0,
        candidate_regime=RegimeType.TRENDING_MOMENTUM,
        candidate_prob=0.95,
        candidate_exec=RecommendedExecutionType.IOC_MARKET,
        candidate_reason=RegimeReason.OFI_TREND_ALIGN,
    )
    assert clf._stable_regime == RegimeType.TRENDING_MOMENTUM
    # 10s later (< 60s hold): even a strong opposite candidate is held off.
    ok = clf._apply_hysteresis(
        now_sec=_EPOCH + 210.0,
        candidate_regime=RegimeType.RANGING_MEAN_REVERSION,
        candidate_prob=0.99,
        candidate_exec=RecommendedExecutionType.PASSIVE_LIMIT,
        candidate_reason=RegimeReason.DEFAULT_RANGE,
    )
    assert ok[0] == RegimeType.TRENDING_MOMENTUM, ok
    assert ok[3] == RegimeReason.HYSTERESIS_HOLD, ok
