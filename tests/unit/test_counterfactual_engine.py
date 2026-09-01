"""Unit tests: NO_TRADE counterfactual engine (CHG-0041, offline+deterministic).

Protects the brief's contracts: future leakage impossibility, entry side
semantics, MFE/MAE math, cost handling, classification rules, provenance
honesty (RR_NOT_RECORDED), incomplete coverage => INCONCLUSIVE, duplicate
ticks, timezone boundaries, deterministic rerun.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.counterfactual import (
    DecisionCandidate,
    OutcomeClass,
    Tick,
    build_candidates,
    parse_direction,
    results_fingerprint,
    walk_candidate,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _cand(**over) -> DecisionCandidate:
    base = dict(
        decision_id="DEC-1",
        timestamp=T0,
        symbol="XAUUSD",
        direction="BUY",
        confidence=0.29,
        entry_price=3300.0,
        stop_loss=3290.0,  # $10 risk
        take_profit=3318.0,  # $18 target (1.8R)
        regime="RANGING_MEAN_REVERSION",
        gate="CONFIDENCE_GATE",
        blocked_by="CONFIDENCE_FAIL",
        reason_code="INSUFFICIENT_CONFIDENCE",
        model_action="BUY_MARKET",
    )
    base.update(over)
    return DecisionCandidate(**base)


def _ticks(
    start: datetime,
    minutes: int,
    price_fn,
    *,
    entry_offset: datetime | None = None,
    per_min: int = 1,
) -> list[Tick]:
    out = []
    for m in range(minutes):
        for s in range(per_min):
            ts = start + timedelta(minutes=m, seconds=30.0 * s)
            p = price_fn(m, s)
            out.append(Tick(timestamp=ts, bid=p - 0.1, ask=p + 0.1))
    return out


# ---------------------------------------------------------------------------
# Direction parsing + candidate building (provenance honesty)
# ---------------------------------------------------------------------------


def test_parse_direction_prefers_model_action() -> None:
    assert parse_direction("BUY_LIMIT", "NO_TRADE") == "BUY"
    assert parse_direction("SELL_MARKET", "NO_TRADE") == "SELL"
    assert parse_direction("NO_TRADE", "NO_TRADE") == ""
    assert parse_direction("", "") == ""


def test_build_candidates_utc_naive_timestamp_normalized() -> None:
    rows = [
        {
            "request_id": "R1",
            "generated_at": "2026-09-01T12:00:00",  # naive
            "action": "NO_TRADE",
            "model_action": "BUY_MARKET",
            "confidence": 0.3,
            "proposed_entry": 3300.0,
            "stop_loss": 3290.0,
            "take_profit": 3318.0,
            "regime": "RANGING_MEAN_REVERSION",
            "decision_stage": "CONFIDENCE_GATE",
            "blocked_by": "CONFIDENCE_FAIL",
            "reason_code": "x",
            "symbol": "XAUUSD",
        }
    ]
    cands = build_candidates(rows)
    assert len(cands) == 1
    assert cands[0].timestamp.tzinfo is UTC
    assert cands[0].direction == "BUY"


def test_build_candidates_skips_malformed_but_counts() -> None:
    rows = [{"request_id": "BAD", "generated_at": "not-a-date"}]
    assert build_candidates(rows) == []


# ---------------------------------------------------------------------------
# Entry side semantics (certified: BUY@ASK / SELL@BID)
# ---------------------------------------------------------------------------


def test_buy_entry_uses_ask_sell_entry_uses_bid() -> None:
    ticks = [Tick(timestamp=T0, bid=3299.9, ask=3300.1)]
    buy = walk_candidate(_cand(direction="BUY"), ticks, horizon_minutes=5)
    sell = walk_candidate(_cand(direction="SELL"), ticks, horizon_minutes=5)
    assert buy.entry_price == 3300.1
    assert sell.entry_price == 3299.9


# ---------------------------------------------------------------------------
# MFE / MAE + R + classification
# ---------------------------------------------------------------------------


def test_winner_is_false_rejection_with_r_mfe() -> None:
    """BUY @3300.1(ask), risk $10.1; rally caps at bid 3317.9 (just under the
    recorded TP 3318) = ~1.76R at walk end -> FALSE_REJECTION with MFE >> MAE
    and time_to_target=None (TP honestly never touched)."""

    def px(m: int, s: int) -> float:
        return 3300.0 + min(18.0, 1.5 * m)  # steady rally

    ticks = _ticks(T0, 60, px)
    res = walk_candidate(_cand(), ticks, horizon_minutes=60)
    assert res.outcome == OutcomeClass.FALSE_REJECTION.value
    assert res.theoretical_r >= 0.5
    assert res.mfe is not None and res.mfe > 0
    assert res.mae is not None and res.mae <= 0
    assert res.time_to_target_sec is None  # TP never touched: honest None


def test_loser_is_correct_rejection() -> None:
    def px(m: int, s: int) -> float:
        return 3300.0 - min(12.0, 1.5 * m)  # steady slide

    ticks = _ticks(T0, 60, px)
    res = walk_candidate(_cand(), ticks, horizon_minutes=60)
    assert res.outcome == OutcomeClass.CORRECT_REJECTION.value
    assert res.theoretical_r <= -0.5
    assert res.time_to_stop_sec is not None


def test_noise_band_is_inconclusive() -> None:
    def px(m: int, s: int) -> float:
        return 3300.0 + (0.3 if m % 2 == 0 else -0.3)  # chop around entry

    ticks = _ticks(T0, 60, px)
    res = walk_candidate(_cand(), ticks, horizon_minutes=60)
    assert res.outcome == OutcomeClass.INCONCLUSIVE.value
    assert "noise band" in res.classification_basis


def test_missing_geometry_is_rr_not_recorded() -> None:
    """No usable SL geometry => RR_NOT_RECORDED, never a fabricated R.
    Excursion proxy classifies only on dominant 1.8R excursions."""

    def px(m: int, s: int) -> float:
        return 3300.0 + 2.0 * m  # strong rally

    ticks = _ticks(T0, 60, px)
    res = walk_candidate(_cand(stop_loss=0.0), ticks, horizon_minutes=60)
    assert res.theoretical_r == "RR_NOT_RECORDED"
    assert res.outcome == OutcomeClass.FALSE_REJECTION.value  # MFE dominated >= 1.8R
    assert "EXCURSION" in res.classification_basis


def test_incomplete_future_coverage_is_inconclusive() -> None:
    ticks = [Tick(timestamp=T0, bid=3299.9, ask=3300.1)]
    res = walk_candidate(_cand(), ticks, horizon_minutes=60)
    assert res.outcome == OutcomeClass.INCONCLUSIVE.value
    assert res.classification_basis == "INSUFFICIENT_FUTURE_COVERAGE"


def test_no_tick_coverage_is_inconclusive() -> None:
    res = walk_candidate(_cand(), [], horizon_minutes=60)
    assert res.outcome == OutcomeClass.INCONCLUSIVE.value
    assert res.classification_basis == "NO_TICK_COVERAGE"


def test_unresolved_direction_is_inconclusive() -> None:
    ticks = _ticks(T0, 30, lambda m, s: 3300.0)
    res = walk_candidate(_cand(direction=""), ticks, horizon_minutes=60)
    assert res.outcome == OutcomeClass.INCONCLUSIVE.value
    assert res.classification_basis == "UNRESOLVED_DIRECTION"


# ---------------------------------------------------------------------------
# Chronology / leakage / duplicates / timezone
# ---------------------------------------------------------------------------


def test_walk_never_uses_ticks_before_decision() -> None:
    """Ticks BEFORE the decision instant must not affect MFE/MAE/R."""
    past = [Tick(timestamp=T0 - timedelta(hours=1), bid=3200.0, ask=3200.2)]
    future = _ticks(T0, 60, lambda m, s: 3310.0)
    with_past = walk_candidate(_cand(), past + future, horizon_minutes=60)
    clean = walk_candidate(_cand(), list(future), horizon_minutes=60)
    assert with_past.mfe == clean.mfe
    assert with_past.mae == clean.mae
    assert with_past.theoretical_r == clean.theoretical_r


def test_duplicate_timestamps_keep_stream_order() -> None:
    dup = Tick(timestamp=T0, bid=3301.0, ask=3301.2)
    ticks = [Tick(timestamp=T0, bid=3299.9, ask=3300.1), dup, dup]
    res = walk_candidate(_cand(), ticks, horizon_minutes=5)
    assert res.ticks_seen == 3


def test_horizon_cut_stops_the_walk() -> None:
    def px(m: int, s: int) -> float:
        return 3300.0 + 5.0 * m

    ticks = _ticks(T0, 500, px)
    res = walk_candidate(_cand(), ticks, horizon_minutes=30)
    assert res.coverage_sec <= 30 * 60 + 60  # boundary tolerance (last tick)


def test_timezone_boundary_naive_entry_joined_as_utc() -> None:
    ticks = _ticks(T0, 60, lambda m, s: 3300.0)
    cand = _cand(timestamp=datetime(2026, 9, 1, 12, 0))  # naive == UTC
    res = walk_candidate(cand, ticks, horizon_minutes=60)
    assert res.ticks_seen == 60


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_deterministic_rerun_same_fingerprint() -> None:
    def px(m: int, s: int) -> float:
        return 3300.0 + ((m * 7) % 23) * 0.3 - 3.0

    ticks = _ticks(T0, 90, px)
    cands = [_cand(decision_id=f"D{i}", timestamp=T0 + timedelta(minutes=i)) for i in range(5)]
    r1 = [walk_candidate(c, ticks, horizon_minutes=60) for c in cands]
    r2 = [walk_candidate(c, ticks, horizon_minutes=60) for c in cands]
    assert results_fingerprint(r1) == results_fingerprint(r2)
