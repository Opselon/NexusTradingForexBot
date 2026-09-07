"""
Unit Tests - Canonical MarketEntryGate (TASK-MKTCAL-ENTRYGATE)
==============================================================
Offline, deterministic (no sleeps, no I/O, no MT5). Verifies the gate wraps
market_calendar.market_state (base classification) and enforces the weekly
broker-clock guards (Sunday-open guard, Friday close cutoff) fail-closed.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.accounting.market_calendar import market_state
from nexus_scalp.execution.market_entry_gate import (
    MarketEntryGate,
    MarketEntryGateConfig,
    MarketEntryVerdict,
)


def _utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


def _gate(**cfg: object) -> MarketEntryGate:
    return MarketEntryGate(MarketEntryGateConfig(**cfg))  # type: ignore[arg-type]


def _fresh(tue: datetime) -> float:
    """A fresh tick age for a moment far from any session edge."""
    return 3.0


# --- base classification passthrough --------------------------------------


def test_market_calendar_is_source_of_truth_for_base_states() -> None:
    # The gate must reuse the calendar's EXACT state strings.
    st = market_state(_utc(2026, 8, 20, 15, 0), last_tick_age_sec=3.0)
    assert st["state"] == "OPEN"


def test_market_closed_weekday_stale_tick_blocks() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 20, 15, 0),  # Thursday 15:00 UTC
        last_tick_age_sec=5000.0,  # stale way beyond 120 s
    )
    assert verdict.allowed is False
    assert verdict.state == "CLOSED"
    assert verdict.reason
    assert "week_friday_close_iso" in verdict.detail


def test_weekend_blocks() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 22, 12, 0),  # Saturday
        last_tick_age_sec=3600.0,
    )
    assert verdict.allowed is False
    assert verdict.state == "WEEKEND"
    assert verdict.reason


def test_fresh_tick_normal_tuesday_midday_allows() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 18, 12, 0),  # Tuesday 12:00 UTC
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is True
    assert verdict.state == "OPEN"
    assert verdict.reason == "ENTRY_ALLOWED"
    assert verdict.detail["state"] == "OPEN"
    assert verdict.detail["last_tick_age_sec"] == 3.0
    assert verdict.detail["server_now_iso"] == "2026-08-18T12:00:00+00:00"


def test_unknown_none_server_now_blocks() -> None:
    verdict = _gate().evaluate(server_now=None, last_tick_age_sec=3.0)
    assert verdict.allowed is False
    assert verdict.state == "UNKNOWN"
    assert verdict.reason == "NO_BROKER_TIME"


def test_unknown_missing_tick_freshness_blocks() -> None:
    verdict = _gate().evaluate(server_now=_utc(2026, 8, 18, 12, 0), last_tick_age_sec=None)
    assert verdict.allowed is False
    assert verdict.state == "UNKNOWN"
    assert verdict.reason == "NO_TICK_FRESHNESS"


def test_paused_blocks() -> None:
    # market_calendar maps a weekday stale tick to CLOSED; PAUSED is the
    # other blocked calendar state -- force it through the calendar directly.
    from unittest.mock import patch

    fake = {"state": "PAUSED", "last_tick_age_sec": 5.0, "next_open_iso": None, "reason": "halt"}
    with patch("nexus_scalp.execution.market_entry_gate.market_state", return_value=fake):
        verdict = _gate().evaluate(server_now=_utc(2026, 8, 18, 12, 0), last_tick_age_sec=5.0)
    assert verdict.allowed is False
    assert verdict.state == "PAUSED"
    assert verdict.reason


# --- WEEKLY_OPEN_GUARD (Sunday post-open window) ---------------------------


def test_opening_guard_blocks_sunday_21_05() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 23, 21, 5),  # Sunday 21:05 UTC (5 min after open)
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is False
    assert verdict.state == "WEEKLY_OPEN_GUARD"
    assert verdict.reason == "WEEKLY_OPEN_GUARD"
    assert verdict.detail["guard_minutes"] == 10.0
    assert verdict.detail["elapsed_sec"] == 300.0


def test_just_after_guard_sunday_21_15_allows() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 23, 21, 15),  # Sunday 21:15 UTC (15 min after open)
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is True
    assert verdict.state == "OPEN"


def test_sunday_pre_open_still_weekend() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 23, 12, 0),  # Sunday BEFORE 21:00 UTC
        last_tick_age_sec=3600.0,
    )
    assert verdict.allowed is False
    assert verdict.state == "WEEKEND"


# --- FRIDAY_CUTOFF ---------------------------------------------------------


def test_friday_21_45_blocks_with_cutoff() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 21, 21, 45),  # 15 min before 22:00 close
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is False
    assert verdict.state == "FRIDAY_CUTOFF"
    assert verdict.reason == "FRIDAY_CUTOFF"
    assert verdict.detail["cutoff_minutes"] == 20.0
    assert verdict.detail["seconds_to_close"] == 900.0


def test_friday_21_30_allows() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 21, 21, 30),  # 30 min before close (> 20 cutoff)
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is True
    assert verdict.state == "OPEN"


def test_friday_morning_allows() -> None:
    verdict = _gate().evaluate(
        server_now=_utc(2026, 8, 21, 10, 0),
        last_tick_age_sec=3.0,
    )
    assert verdict.allowed is True
    assert verdict.state == "OPEN"


# --- custom config overrides -----------------------------------------------


def test_custom_config_disabling_guards_allows_edge_moments() -> None:
    sunday_21_05 = _utc(2026, 8, 23, 21, 5)
    friday_21_45 = _utc(2026, 8, 21, 21, 45)

    guard_off = _gate(opening_guard_minutes=0.0)
    assert guard_off.evaluate(server_now=sunday_21_05, last_tick_age_sec=3.0).allowed is True

    cutoff_off = _gate(friday_cutoff_minutes=0.0)
    assert cutoff_off.evaluate(server_now=friday_21_45, last_tick_age_sec=3.0).allowed is True


def test_custom_config_widening_guards_blocks_edge_moments() -> None:
    friday_21_00 = _utc(2026, 8, 21, 21, 0)  # 60 min before close

    wide = _gate(friday_cutoff_minutes=90.0)
    verdict = wide.evaluate(server_now=friday_21_00, last_tick_age_sec=3.0)
    assert verdict.allowed is False
    assert verdict.state == "FRIDAY_CUTOFF"

    wide_guard = _gate(opening_guard_minutes=30.0)
    verdict2 = wide_guard.evaluate(server_now=_utc(2026, 8, 23, 21, 20), last_tick_age_sec=3.0)
    assert verdict2.allowed is False
    assert verdict2.state == "WEEKLY_OPEN_GUARD"


def test_allow_when_unknown_opt_in() -> None:
    permissive = _gate(allow_when_unknown=True)
    v1 = permissive.evaluate(server_now=None, last_tick_age_sec=3.0)
    assert v1.allowed is True
    assert v1.state == "UNKNOWN"
    assert v1.reason == "UNKNOWN_ALLOWED_BY_CONFIG"

    # Freshness-unknown still honoured by the same opt-in.
    v2 = permissive.evaluate(server_now=_utc(2026, 8, 18, 12, 0), last_tick_age_sec=None)
    assert v2.allowed is True
    assert v2.state == "UNKNOWN"


# --- boundary equality ------------------------------------------------------


def test_boundary_exactly_at_guard_expiry_allows() -> None:
    # elapsed == guard -> guard is over (half-open [open, open+guard)).
    verdict = _gate().evaluate(server_now=_utc(2026, 8, 23, 21, 10), last_tick_age_sec=3.0)
    assert verdict.allowed is True
    assert verdict.state == "OPEN"


def test_boundary_exactly_at_cutoff_blocks() -> None:
    # seconds_to_close == cutoff -> cutoff is active (half-open (close, close-cutoff]).
    verdict = _gate().evaluate(server_now=_utc(2026, 8, 21, 21, 40), last_tick_age_sec=3.0)
    assert verdict.allowed is False
    assert verdict.state == "FRIDAY_CUTOFF"
    assert verdict.detail["seconds_to_close"] == 1200.0


# --- epoch-seconds server_now accepted --------------------------------------


def test_epoch_seconds_server_now_equivalent() -> None:
    tue = _utc(2026, 8, 18, 12, 0)
    by_dt = _gate().evaluate(server_now=tue, last_tick_age_sec=3.0)
    by_epoch = _gate().evaluate(server_now=tue.timestamp(), last_tick_age_sec=3.0)
    assert by_dt.allowed == by_epoch.allowed is True
    assert by_dt.state == by_epoch.state == "OPEN"
    assert by_epoch.detail["server_now_iso"] == by_dt.detail["server_now_iso"]


# --- contract invariants ----------------------------------------------------


def test_every_blocked_verdict_has_nonempty_reason_and_allowed_false() -> None:
    gate = _gate()
    blocked_moments: list[tuple[datetime | None, float | None]] = [
        (None, 3.0),  # UNKNOWN (no broker time)
        (_utc(2026, 8, 18, 12, 0), None),  # UNKNOWN (no freshness)
        (_utc(2026, 8, 20, 15, 0), 5000.0),  # CLOSED (stale tick)
        (_utc(2026, 8, 22, 12, 0), 3600.0),  # WEEKEND (Saturday)
        (_utc(2026, 8, 23, 12, 0), 3600.0),  # WEEKEND (Sunday pre-open)
        (_utc(2026, 8, 23, 21, 5), 3.0),  # WEEKLY_OPEN_GUARD
        (_utc(2026, 8, 21, 21, 45), 3.0),  # FRIDAY_CUTOFF
    ]
    for server_now, age in blocked_moments:
        verdict = gate.evaluate(server_now=server_now, last_tick_age_sec=age)
        assert verdict.allowed is False, (server_now, age, verdict)
        assert verdict.reason, (server_now, age, verdict)
        assert isinstance(verdict.reason, str) and verdict.reason.strip() != ""


def test_verdict_is_immutable_dataclass() -> None:
    verdict = _gate().evaluate(server_now=_utc(2026, 8, 18, 12, 0), last_tick_age_sec=3.0)
    assert isinstance(verdict, MarketEntryVerdict)
    import dataclasses

    assert dataclasses.is_dataclass(verdict) and verdict.__dataclass_params__.frozen is True
    try:
        verdict.allowed = False  # type: ignore[misc]
        raise AssertionError("frozen dataclass mutation should raise")
    except dataclasses.FrozenInstanceError:
        pass


def test_config_is_frozen_dataclass_with_spec_defaults() -> None:
    import dataclasses

    cfg = MarketEntryGateConfig()
    assert cfg.opening_guard_minutes == 10.0
    assert cfg.friday_cutoff_minutes == 20.0
    assert cfg.tick_stale_after_sec == 120.0
    assert cfg.allow_when_unknown is False
    assert dataclasses.is_dataclass(cfg) and cfg.__dataclass_params__.frozen is True


def test_now_utc_is_forensic_only() -> None:
    tue = _utc(2026, 8, 18, 12, 0)
    without = _gate().evaluate(server_now=tue, last_tick_age_sec=3.0)
    with_host = _gate().evaluate(server_now=tue, last_tick_age_sec=3.0, now_utc=_utc(2026, 8, 22, 3, 0))
    assert without.allowed == with_host.allowed is True
    assert without.state == with_host.state == "OPEN"
    assert with_host.detail["now_utc_iso"] == "2026-08-22T03:00:00+00:00"
    assert without.detail["now_utc_iso"] is None


def test_week_boundaries_for_current_broker_week() -> None:
    tue = _utc(2026, 8, 18, 12, 0)  # Tuesday
    verdict = _gate().evaluate(server_now=tue, last_tick_age_sec=3.0)
    assert verdict.detail["week_sunday_open_iso"] == "2026-08-16T21:00:00+00:00"
    assert verdict.detail["week_friday_close_iso"] == "2026-08-21T22:00:00+00:00"
    assert verdict.detail["next_weekly_open_iso"] == "2026-08-23T21:00:00+00:00"

    # Sunday post-open: current week open is TODAY 21:00, next open is +7d.
    sun = _utc(2026, 8, 23, 21, 30)
    v2 = _gate().evaluate(server_now=sun, last_tick_age_sec=3.0)
    assert v2.detail["week_sunday_open_iso"] == "2026-08-23T21:00:00+00:00"
    assert v2.detail["week_friday_close_iso"] == "2026-08-28T22:00:00+00:00"
    assert v2.detail["next_weekly_open_iso"] == "2026-08-30T21:00:00+00:00"


def test_naive_server_now_follows_calendar_semantics() -> None:
    # market_calendar treats a naive datetime via astimezone(UTC) == host-local
    # interpretation. The gate must classify the SAME instant, not reject it.
    naive_tue = datetime(2026, 8, 18, 12, 0)
    v_naive = _gate().evaluate(server_now=naive_tue, last_tick_age_sec=3.0)
    v_aware = _gate().evaluate(server_now=naive_tue.astimezone(UTC), last_tick_age_sec=3.0)
    assert v_naive.state == v_aware.state
    assert v_naive.allowed == v_aware.allowed


def test_garbage_server_now_fails_closed() -> None:
    verdict = _gate().evaluate(server_now=float("nan"), last_tick_age_sec=3.0)
    assert verdict.allowed is False
    assert verdict.state == "UNKNOWN"
    assert verdict.reason == "NO_BROKER_TIME"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit("run via pytest")
