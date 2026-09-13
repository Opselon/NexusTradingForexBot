"""BUG-268 regression: minimum-loss optimization grace period clock domain.

Clock-domain sweep lineage: G3 (15d39552, ``_arbitrate_decision``), BUG-259
(7b7aeaa2, ``_calculate_hold_value_score``) and BUG-260 (``_update_mfe_mae``,
``live_engine._position_performance``) all replaced host-wall-clock hold-age
derivations with the tick-threaded ``now`` plus a conservative 0.0 fallback.
The sweep MISSED the fourth member of the family:
``PositionScoringEngine._evaluate_minimum_loss_optimization`` kept a raw
``datetime.now()`` / ``datetime.now(UTC)`` fallback for its 60-second Spread
Overcome Grace Period whenever ``now`` is not threaded.

Why it matters (money path): the grace gate decides whether the MIN_LOSS
exit cluster (EV breach / deep drawdown / weak recovery) may fire at all.
A host wall clock AHEAD of the broker/tick domain (the production-observed
skew direction) inflates the derived duration past 60s for a freshly-opened
position, so the exit cluster can fire instantly on a position that has had
no chance to breathe. The opposite skew suppresses every minimum-loss exit
for the true grace duration plus the skew — the exact ``Age: -10781.6s``
failure class G3 exists to prevent.

Contract pinned here (parity with G3/BUG-259):
- ``now`` provided (same tz domain as the entry anchor) -> numeric age.
- ``now`` missing or naive/aware-mismatched -> duration 0.0 (still inside
  the 60s grace -> NO exit), never wall-clock arithmetic, plus exactly one
  rate-limited (300s) loud WARNING ``event=HOLD_AGE_FALLBACK``.
- Static guard: the method source must never read the wall clock.

Test discipline (BUG-112/118, mirrors test_bug259/test_hold_time_fallback_g3):
the MODULE logger is monkeypatched — never caplog, never real time.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest

import nexus_scalp.execution.lifecycle.scoring as scoring_module
from nexus_scalp.execution.order_manager import OrderLifecycleManager


class _LogProbe:
    """Stands in for the module structlog logger; records warning calls."""

    def __init__(self) -> None:
        self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append((args, kwargs))

    def debug(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def info(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass

    def error(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass


def _make_om() -> OrderLifecycleManager:
    return OrderLifecycleManager(adapter=Mock(), audit_repo=None)


def _call(
    om: OrderLifecycleManager,
    ticket: int,
    *,
    now: datetime | None,
) -> tuple[bool, str]:
    """Invoke minimum-loss optimization around the 60s grace gate.

    The evidence fires ONLY the weak-recovery branch once the grace gate
    opens: pnl -50 / risk 100 (drawdown fraction 0.50 <= 0.60 -> no
    deep-drawdown guard), EV = 0.10*180 - 0.65*50 = -14.5 >= -0.15*100
    (no EV breach), rec 0.10 < 0.25 AND adv 0.65 > 0.60 -> WEAK_RECOVERY.
    With the grace gate closed (duration < 60s) none of it can fire.
    """
    evidence = {"recovery_score": 0.10, "adverse_score": 0.65}
    return om._evaluate_minimum_loss_optimization(
        ticket,
        -50.0,
        100.0,
        evidence,
        now=now,
    )


# ---------------------------------------------------------------------------
# 1. tick-domain now owns the grace gate (positive + negative control)
# ---------------------------------------------------------------------------


def test_tick_domain_now_past_grace_allows_exit() -> None:
    """now 90s after entry -> grace open -> weak-recovery exit fires."""
    om = _make_om()
    entry = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    om._entry_timestamps[5001] = entry
    should_exit, reason = _call(om, 5001, now=entry + timedelta(seconds=90))
    assert should_exit is True
    assert "MIN_LOSS_OPTIMIZATION_WEAK_RECOVERY" in reason


def test_tick_domain_now_inside_grace_blocks_exit() -> None:
    """now 30s after entry -> still inside the 60s grace -> never exits."""
    om = _make_om()
    entry = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    om._entry_timestamps[5001] = entry
    should_exit, reason = _call(om, 5001, now=entry + timedelta(seconds=30))
    assert should_exit is False
    assert reason == ""


# ---------------------------------------------------------------------------
# 2. the defect shape: no threaded now must NEVER touch the wall clock
# ---------------------------------------------------------------------------


def test_no_now_conservative_zero_keeps_grace_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry 90s in the PAST per the wall clock + no threaded now ->
    duration conservatively 0.0 (grace CLOSED, exit suppressed).

    Pre-fix this read ``datetime.now(UTC)`` and the grace gate opened on
    host-wall-clock arithmetic — the BUG-268 defect. The entry is seeded
    90s before the real wall clock so the OLD code fires (RED) and the
    NEW conservative-zero code blocks (GREEN).
    """
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    om._entry_timestamps[5002] = datetime.now(UTC) - timedelta(seconds=90)
    should_exit, _reason = _call(om, 5002, now=None)
    assert should_exit is False, (
        "BUG-268: grace gate opened on host wall clock with no threaded tick now"
    )


def test_naive_aware_domain_mismatch_conservative_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aware entry + naive now (mixed domains) must not subtract: naive
    subtraction against a broker-domain anchor is the G3 corruption class.
    Conservative 0.0 keeps the grace closed."""
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    entry = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    om._entry_timestamps[5003] = entry
    naive_now = entry.replace(tzinfo=None) + timedelta(seconds=900)
    should_exit, _reason = _call(om, 5003, now=naive_now)
    assert should_exit is False


def test_no_now_fallback_warns_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conservative fallback is LOUD: exactly one HOLD_AGE_FALLBACK
    warning per 300s window (G3/BUG-259 rate-limit pattern), never per call."""
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    om._entry_timestamps[5004] = datetime.now(UTC) - timedelta(seconds=3600)
    for _ in range(5):
        _call(om, 5004, now=None)
    assert len(probe.warnings) == 1, (
        f"expected exactly 1 rate-limited warning, got {len(probe.warnings)}"
    )
    args, kwargs = probe.warnings[0]
    msg = " ".join(str(a) for a in args) + " " + str(kwargs)
    assert "HOLD_AGE_FALLBACK" in msg
    assert "5004" in msg  # ticket number present


def test_missing_entry_anchor_no_exit_no_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untracked ticket -> duration 0.0 via the no-entry branch, no warning
    (parity with test_hold_time_fallback_g3's no-entry case)."""
    probe = _LogProbe()
    monkeypatch.setattr(scoring_module, "logger", probe)
    om = _make_om()
    should_exit, _reason = _call(om, 5005, now=None)
    assert should_exit is False
    assert probe.warnings == []


# ---------------------------------------------------------------------------
# 3. static guards: the BUG-260 sweep must now cover this method too
# ---------------------------------------------------------------------------


def test_min_loss_method_source_has_no_wall_clock() -> None:
    """The BUG-268 defect shape must not regress: the minimum-loss grace
    path must never read the wall clock (parity with the BUG-259 static
    guard on _calculate_hold_value_score)."""
    src = inspect.getsource(
        scoring_module.PositionScoringEngine._evaluate_minimum_loss_optimization
    )
    assert "datetime.now" not in src, (
        "BUG-268 regression: wall clock returned to the minimum-loss grace path"
    )
