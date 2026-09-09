"""PER-TICKET PROTECTION LEDGER INVARIANTS (audit 2026-09-09).

Why these tests exist
---------------------
``execution.protection_ledger`` owns the ONLY per-ticket profit-protection
facts (peak_win_usd high-water mark, giveback arming) consumed by
``lifecycle.protection``'s SL/BE/giveback logic — a defect here directly
mis-arms (or silently disarms) profit protection on live positions.

The extracted module had NO direct test file (its behavior was only reached
incidentally through heavy order-manager suites). These tests pin the
invariants at the unit level:

  1. peak_win_usd is MONOTONIC while open — never decreases, never NaN.
  2. Garbage PnL (NaN / inf / non-numeric) cannot corrupt the peak.
  3. retention_ratio returns 1.0 when unarmed (no positive peak) so giveback
     can never mis-fire from a micro-profit noise zone.
  4. Two tickets can NEVER contaminate each other's protection state
     (the "never a shared/global variable" contract).
  5. drop() releases exactly one ticket; ledger membership stays honest.
"""

from __future__ import annotations

import math

import pytest

from nexus_scalp.execution.protection_ledger import (
    PositionProtectionLedger,
    PositionProtectionState,
)

# ---------------------------------------------------------------------------
# PositionProtectionState.update_peak: monotonic + garbage-proof
# ---------------------------------------------------------------------------


def test_update_peak_is_monotonic_high_water_mark() -> None:
    st = PositionProtectionState()
    assert st.update_peak(1.0) == pytest.approx(1.0)
    assert st.update_peak(5.0) == pytest.approx(5.0)
    assert st.update_peak(2.0) == pytest.approx(5.0)  # retracement: peak holds
    assert st.update_peak(0.5) == pytest.approx(5.0)
    assert st.update_peak(-3.0) == pytest.approx(5.0)
    assert st.peak_win_usd == pytest.approx(5.0)


def test_update_peak_accepts_negative_pnl_without_corruption() -> None:
    st = PositionProtectionState()
    st.update_peak(4.0)
    assert st.update_peak(-50.0) == pytest.approx(4.0)


@pytest.mark.parametrize("garbage", [float("nan"), float("inf"), float("-inf")])
def test_update_peak_ignores_non_finite_pnl(garbage: float) -> None:
    st = PositionProtectionState()
    st.update_peak(2.5)
    assert st.update_peak(garbage) == pytest.approx(2.5)
    assert st.peak_win_usd == pytest.approx(2.5)


@pytest.mark.parametrize("junk", [None, [1.0], object()])
def test_update_peak_ignores_non_numeric_pnl(junk: object) -> None:
    st = PositionProtectionState()
    st.update_peak(3.0)
    assert st.update_peak(junk) == pytest.approx(3.0)  # type: ignore[arg-type]
    assert st.peak_win_usd == pytest.approx(3.0)


def test_update_peak_numeric_string_coerces_like_live_manager() -> None:
    # Documented behavior (not a bug): update_peak float()-coerces numeric
    # strings, so "12.0" RAISES the peak. The invariant still holds: the
    # result is a real finite number that beats the previous peak.
    st = PositionProtectionState()
    st.update_peak(3.0)
    assert st.update_peak("12.0") == pytest.approx(12.0)  # type: ignore[arg-type]
    assert math.isfinite(st.peak_win_usd)


def test_update_peak_zero_never_reduces_positive_peak() -> None:
    st = PositionProtectionState()
    st.update_peak(7.0)
    assert st.update_peak(0.0) == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# retention_ratio: unarmed positions can never mis-fire giveback
# ---------------------------------------------------------------------------


def test_retention_ratio_no_positive_peak_returns_one() -> None:
    st = PositionProtectionState()
    assert st.peak_win_usd == 0.0
    assert st.retention_ratio(1.0) == 1.0
    assert st.retention_ratio(-5.0) == 1.0


def test_retention_ratio_fraction_of_peak() -> None:
    st = PositionProtectionState()
    st.update_peak(10.0)
    assert st.retention_ratio(7.5) == pytest.approx(0.75)


def test_retention_ratio_over_peak_is_gt_one() -> None:
    # Peak lags PnL only within one management pass; ratio > 1 is legal input.
    st = PositionProtectionState()
    st.update_peak(10.0)
    assert st.retention_ratio(12.0) == pytest.approx(1.2)


def test_retention_ratio_bad_current_pnl_falls_back_to_one() -> None:
    st = PositionProtectionState()
    st.update_peak(10.0)
    assert st.retention_ratio("oops") == 1.0  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Ledger: per-ticket isolation + explicit teardown
# ---------------------------------------------------------------------------


def test_ledger_state_is_per_ticket_never_shared() -> None:
    ledger = PositionProtectionLedger()
    a = ledger.get(1001)
    b = ledger.get(1002)
    a.update_peak(9.0)
    b.update_peak(0.5)
    assert a.peak_win_usd == pytest.approx(9.0)
    assert b.peak_win_usd == pytest.approx(0.5)
    # Repeat get() returns the SAME live object (idempotent creation).
    assert ledger.get(1001) is a


def test_ledger_contains_and_len_reflect_membership() -> None:
    ledger = PositionProtectionLedger()
    assert len(ledger) == 0
    ledger.get(1)
    ledger.get(2)
    assert 1 in ledger and 2 in ledger
    assert len(ledger) == 2


def test_ledger_drop_releases_exactly_one_ticket() -> None:
    ledger = PositionProtectionLedger()
    ledger.get(1)
    ledger.get(2)
    ledger.drop(1)
    assert 1 not in ledger
    assert 2 in ledger
    assert len(ledger) == 1
    # Double drop is a no-op; a fresh get() after drop re-creates fresh state.
    ledger.drop(1)
    fresh = ledger.get(1)
    assert fresh.peak_win_usd == 0.0
    assert fresh.profit_giveback_triggered is False


def test_default_state_fields_honest_zeros() -> None:
    st = PositionProtectionState()
    assert st.peak_win_usd == 0.0
    assert st.was_sl_modified is False
    assert st.profit_giveback_triggered is False
    assert st.close_requested is False
    assert st.breakeven_sl_price == 0.0
    assert math.isfinite(st.last_be_attempt_time)
