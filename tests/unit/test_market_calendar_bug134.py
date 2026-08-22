"""
Unit Tests - Broker-driven market calendar (BUG-134)
====================================================
Tests market_state / current_trading_day / day_bounds_utc using FAKE
adapters (no MT5). The module is pure and adapter-driven: server time via
tick time, market OPEN/CLOSED/WEEKEND classification, and the server-time
trading-day key so the UI "1 Day" matches the broker instead of wall clock.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.accounting.market_calendar import (
    current_trading_day,
    day_bounds_utc,
    market_state,
    probe_server_time,
)


class _FakeAdapter:
    def __init__(self, tick_epoch: float | None) -> None:
        self._tick = tick_epoch

    def last_tick_time_utc(self) -> float | None:
        return self._tick


def _utc(y, m, d, hh=0, mm=0, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


def test_probe_server_time_uses_tick() -> None:
    a = _FakeAdapter(1787310000.0)
    assert probe_server_time(a) == 1787310000.0


def test_probe_server_time_none_on_missing() -> None:
    a = _FakeAdapter(None)
    assert probe_server_time(a) is None


def test_market_open_on_fresh_tick_weekday() -> None:
    now = _utc(2026, 8, 20, 15, 0)  # Thursday 15:00 UTC
    st = market_state(now, last_tick_age_sec=3.0)
    assert st["state"] == "OPEN"
    assert st["reason"] == "fresh tick"


def test_market_closed_weekday_no_fresh_tick() -> None:
    now = _utc(2026, 8, 20, 15, 0)
    st = market_state(now, last_tick_age_sec=5000.0)
    assert st["state"] == "CLOSED"
    assert st["next_open_iso"] is None


def test_weekend_state_and_next_open() -> None:
    # Friday 23:00 UTC -> WEEKEND, next open Sunday 21:00 UTC
    friday_late = _utc(2026, 8, 21, 23, 0)
    st = market_state(friday_late, last_tick_age_sec=9999.0)
    assert st["state"] == "WEEKEND"
    next_open = datetime.fromisoformat(st["next_open_iso"])
    assert next_open.weekday() == 6  # Sunday
    assert next_open.hour == 21
    assert next_open > friday_late


def test_saturday_weekend() -> None:
    sat = _utc(2026, 8, 22, 12, 0)
    st = market_state(sat, last_tick_age_sec=3600.0)
    assert st["state"] == "WEEKEND"


def test_unknown_when_no_time_and_no_freshness() -> None:
    st = market_state(None)
    assert st["state"] == "UNKNOWN"


def test_current_trading_day_uses_server_time_date() -> None:
    # Server tick 2026-08-20T22:59 UTC -> the BROKER's day key is 2026-08-20
    # (NOT wall-clock-UTC date if wall differs). With a +3 offset adapter the
    # tick epoch already carries the broker offset via broker_epoch_to_utc.
    day = current_trading_day(_utc(2026, 8, 20, 22, 59))
    assert day == "2026-08-20"
    assert current_trading_day(None) is None


def test_day_bounds_utc_half_open() -> None:
    s, e = day_bounds_utc("2026-08-20")
    assert s == _utc(2026, 8, 20)
    assert e == _utc(2026, 8, 21)
    assert (e - s) == timedelta(days=1)
