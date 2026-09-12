"""BUG-260 regression: residual wall-clock hold-age consumers on the tick path.

Sweep history: G3 (order_manager._arbitrate_decision), then BUG-259
(scoring._calculate_hold_value_score) both replaced host-wall-clock hold-age
derivations with the tick-threaded ``now``. This battery pins the remaining
residues found by the follow-up sweep:

1. ``live_engine._position_performance`` derived ``holding_duration_sec`` from
   ``datetime.now(UTC)`` while its caller ``_observe_positions`` is fed the
   current tick on the hot path (tick_pipeline.py:517) — any host-vs-tick skew
   corrupted the PositionPerformance timeline durations (same clock-domain
   defect class as BUG-259).
2. ``order_manager._update_mfe_mae`` fell back to ``datetime.now(UTC)`` when
   the manage loop did not thread ``now`` — wall-clock arithmetic again.
3. Static guard: the manage-loop hold-age paths must never read the wall clock.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, TickData
from nexus_scalp.execution.order_manager import OrderLifecycleManager


def _make_om() -> OrderLifecycleManager:
    return OrderLifecycleManager(adapter=Mock(), audit_repo=None)


def _make_pos(ticket: int = 3001) -> Position:
    return Position(
        ticket=ticket,
        symbol="XAUUSD",
        type=OrderType.BUY,
        volume=1.0,
        price_open=2330.0,
        sl=2320.0,
        tp=2350.0,
        profit=-50.0,
        magic=888101,
    )


# ---------------------------------------------------------------------------
# 1. live_engine._position_performance clock domain
# ---------------------------------------------------------------------------


def test_position_performance_uses_tick_threaded_now() -> None:
    """A 60s-old entry measured at tick now=entry+60s reports ~60s duration even
    when the HOST wall clock is hours off (here: 3h behind the tick domain).
    Pre-fix the duration came from datetime.now(UTC) and read as negative."""
    from nexus_scalp.application.live_engine import LiveEngine

    engine = LiveEngine.__new__(LiveEngine)  # bypass __init__; method under test is standalone
    om = _make_om()
    engine.order_manager = om  # type: ignore[attr-defined]

    entry = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    om._entry_prices[3001] = 2330.0
    om._entry_sls[3001] = 2320.0
    om._mfe_tracker[3001] = 0.0
    om._mae_tracker[3001] = -0.5
    om._peak_profit_usd[3001] = 10.0
    om._peak_drawdown_usd[3001] = 5.0
    om._entry_timestamps[3001] = entry

    host_wall_stuck_3h_behind = entry + timedelta(hours=3)
    perf = engine._position_performance(3001, now=host_wall_stuck_3h_behind)
    assert perf.holding_duration_sec >= 59.0, (
        f"duration must come from the threaded tick-domain now, got {perf.holding_duration_sec}"
    )
    # The tick-domain now is what the duration follows: shift it and the
    # duration shifts with it (wall-clock independence proof).
    perf2 = engine._position_performance(
        3001, now=host_wall_stuck_3h_behind + timedelta(seconds=30)
    )
    assert abs(perf2.holding_duration_sec - (perf.holding_duration_sec + 30.0)) < 1e-6


# ---------------------------------------------------------------------------
# 2. order_manager._update_mfe_mae fallback clock domain
# ---------------------------------------------------------------------------


def test_update_mfe_mae_fallback_is_tick_domain_not_wall() -> None:
    """Threading now=tick.timestamp yields a tick-domain time_to_mfe even when
    the host wall clock is hours off the tick domain."""
    om = _make_om()
    entry = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    tick_now = entry + timedelta(seconds=90)
    om._entry_timestamps[3001] = entry
    # Seed the pre-existing mfe so the first update wins the max and stamps elapsed.
    om._tracking._mfe_tracker[3001] = 0.0
    om._tracking._time_to_mfe_sec.pop(3001, None)
    om._update_mfe_mae(3001, profit_price_delta=0.6, now=tick_now)
    assert (
        om._tracking._time_to_mfe_sec.get(3001) is not None
        and om._tracking._time_to_mfe_sec[3001] >= 89.0
    ), f"expected tick-domain elapsed ~90s, got {om._tracking._time_to_mfe_sec.get(3001)}"


def test_update_mfe_mae_no_now_records_zero_not_wall() -> None:
    """No threaded now -> conservative zero stamp, never wall-clock arithmetic."""
    om = _make_om()
    om._entry_timestamps[3001] = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)
    om._tracking._mfe_tracker.pop(3001, None)
    om._tracking._time_to_mfe_sec.pop(3001, None)
    om._update_mfe_mae(3001, profit_price_delta=0.6, now=None)
    assert om._tracking._time_to_mfe_sec.get(3001, 0.0) == 0.0


# ---------------------------------------------------------------------------
# 3. Static guards: the sweep's clock-domain contract at HEAD
# ---------------------------------------------------------------------------


def test_position_performance_source_has_no_wall_clock() -> None:
    from nexus_scalp.application import live_engine

    src = inspect.getsource(live_engine.LiveEngine._position_performance)
    assert "datetime.now" not in src, (
        "_position_performance must derive holding duration from the tick domain"
    )


def test_manage_loop_hold_age_paths_have_no_wall_clock() -> None:
    src = inspect.getsource(OrderLifecycleManager._arbitrate_decision)
    assert "datetime.now" not in src, "G3 regression: wall clock returned to arbitrate"
    src2 = inspect.getsource(OrderLifecycleManager._update_mfe_mae)
    assert "now or datetime.now" not in src2 and "datetime.now(UTC) - " not in src2, (
        "BUG-260 regression: wall clock used for elapsed arithmetic in _update_mfe_mae"
    )
