"""Broker-driven market calendar + smart period boundaries (BUG-134).

WHY
---
The accounting period DAY is canonically UTC [00:00,00:00). For a user in
Iran (UTC+3:30) the "1 Day" report at 01:00 IST on Aug-21 shows the UTC
day 2026-08-20 -- which "feels like yesterday". The engine also labels
"today" from wall-clock UTC while the BROKER server runs ahead (~GMT+2),
so market-state detection (open/closed/weekend) and the CURRENT TRADING
DAY must come from the BROKER, not the wall clock.

DESIGN (Phase 1 -- pure, adapter-driven, no engine coupling)
-------------------------------------------------------------
- `probe_server_time(adapter)` -- real MT5 read (symbol_info_tick / tick.time)
- `market_state(...)` -- OPEN / CLOSED / WEEKEND / PAUSED / UNKNOWN +
  next_open_iso + last_tick_age_sec
- `current_trading_day(...)` -- the day key ("YYYY-MM-DD") of the BROKER's
  current session day (server timezone), NOT wall-clock UTC
- `day_bounds_utc(server_trading_day)` -- [start,end) UTC for that key

Approximations (documented): without a per-symbol session table in the MT5
Python API, the WEEKEND rule (Fri 22:00 UTC .. Sun 21:00 UTC for gold/forex)
is the standard gold/XAUUSD convention when the broker's server offset is
known. The live tick freshness is the STRONGEST signal: a recent tick =>
OPEN; no tick for > X => CLOSED/PAUSED/WEEKEND depending on weekday.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Standard gold/XAU session (many brokers): Sun 21:00 UTC -> Fri 22:00 UTC.
_WEEKEND_CLOSE_UTC = 22  # Friday 22:00 UTC market close
_WEEKEND_OPEN_UTC = 21  # Sunday 21:00 UTC market open


class TickSource(Protocol):
    """Minimum surface the calendar needs from an MT5 adapter."""

    def last_tick_time_utc(self) -> float | None:
        """Epoch seconds of the latest symbol tick (or None when unavailable)."""


def probe_server_time(adapter: Any) -> float | None:
    """Current BROKER SERVER time (epoch sec) via a real MT5 read.

    Uses the latest symbol tick timestamp as the server-time anchor (the
    terminal rounds/advances it; for closed markets it is the last session
    time). Returns None when the adapter cannot provide it.
    """
    try:
        if hasattr(adapter, "last_tick_time_utc"):
            return adapter.last_tick_time_utc()
        # generic: a method returning epoch seconds
        for attr in ("server_time_utc", "tick_time"):
            if hasattr(adapter, attr):
                return float(getattr(adapter, attr)())
    except Exception as exc:  # failure-isolated
        logger.warning("[MARKET_CAL] probe_server_time failed: %s", exc)
        return None
    # No matching capability on the adapter -> None (caller treats as unknown).
    return None


def _weekday_utc(moment: datetime) -> int:
    return moment.weekday()  # Mon=0 .. Sun=6


def market_state(
    server_now: datetime | float | None,
    last_tick_age_sec: float | None = None,
    tick_stale_after_sec: float = 120.0,
) -> dict[str, Any]:
    """Classify the market from broker time + tick freshness.

    Returns:
        state: "OPEN" | "CLOSED" | "WEEKEND" | "PAUSED" | "UNKNOWN"
        last_tick_age_sec
        next_open_iso: RFC-3339 UTC when the market reopens (best-effort)
        reason: short human-readable cause
    """
    if server_now is None:
        return {
            "state": "UNKNOWN",
            "last_tick_age_sec": last_tick_age_sec,
            "next_open_iso": None,
            "reason": "no broker time",
        }
    now = (
        server_now if isinstance(server_now, datetime) else datetime.fromtimestamp(server_now, UTC)
    )
    now = now.astimezone(UTC)
    wd = _weekday_utc(now)
    # WEEKEND: Friday 22:00 UTC .. Sunday 21:00 UTC (gold/forex convention)
    if (
        (wd == 4 and now.hour >= _WEEKEND_CLOSE_UTC)
        or wd in (5, 6)
        or (wd == 6 and now.hour < _WEEKEND_OPEN_UTC + 1)
    ):
        # next open = Sunday 21:00 UTC
        days_ahead = 6 - wd if wd < 6 else 0
        next_open = (now + timedelta(days=days_ahead)).replace(
            hour=_WEEKEND_OPEN_UTC, minute=0, second=0, microsecond=0
        )
        if next_open <= now:
            next_open += timedelta(days=7)
        return {
            "state": "WEEKEND",
            "last_tick_age_sec": last_tick_age_sec,
            "next_open_iso": next_open.isoformat(),
            "reason": "weekend (Fri 22:00 UTC - Sun 21:00 UTC)",
        }
    if last_tick_age_sec is None:
        return {
            "state": "UNKNOWN",
            "last_tick_age_sec": None,
            "next_open_iso": None,
            "reason": "no tick freshness",
        }
    if last_tick_age_sec <= tick_stale_after_sec:
        return {
            "state": "OPEN",
            "last_tick_age_sec": round(last_tick_age_sec, 1),
            "next_open_iso": None,
            "reason": "fresh tick",
        }
    # Weekday but no fresh tick -> intraday pause/closed (e.g. lunch break, daily roll)
    return {
        "state": "CLOSED",
        "last_tick_age_sec": round(last_tick_age_sec, 1),
        "next_open_iso": None,
        "reason": "weekday, no fresh tick",
    }


def current_trading_day(server_now: datetime | float | None) -> str | None:
    """The BROKER-SESSION day key for '1 Day' (server timezone date).

    For a UTC-anchored broker this is the UTC date; for GMT+2/+3 brokers
    the server-date boundary may shift the key. This module deliberately
    keeps the key in the SERVER's date so the report matches what the
    broker terminal shows as 'today'.
    """
    if server_now is None:
        return None
    now = (
        server_now if isinstance(server_now, datetime) else datetime.fromtimestamp(server_now, UTC)
    )
    return now.strftime("%Y-%m-%d")


def day_bounds_utc(day_key: str) -> tuple[datetime, datetime]:
    """Half-open UTC bounds [start, end) for a 'YYYY-MM-DD' day key."""
    start = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=UTC)
    return start, start + timedelta(days=1)
