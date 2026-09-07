"""Canonical market-entry gate (TASK-MKTCAL-ENTRYGATE).

Single reusable entry decision for ANY execution path: is the broker market
in a state where taking a NEW entry is acceptable? This module wraps
``nexus_scalp.accounting.market_calendar.market_state`` as the base
classification (OPEN / CLOSED / WEEKEND / PAUSED / UNKNOWN -- no weekend
math reimplemented here) and adds the two weekly broker-clock guards an
execution path must respect:

- ``WEEKLY_OPEN_GUARD`` -- the first ``opening_guard_minutes`` after the
  Sunday 21:00 UTC weekly open (spreads are widest and data thinnest right
  at the weekly roll).
- ``FRIDAY_CUTOFF`` -- the last ``friday_cutoff_minutes`` before the Friday
  22:00 UTC weekend close (no new entries into a held-over-weekend gap).

Policy is FAIL-CLOSED: an unknown broker clock or unknown tick freshness
blocks entry unless ``allow_when_unknown`` is explicitly enabled.

Purity contract: ``evaluate`` is a pure function of its inputs -- no I/O,
no clock reads, no logging side effects. The BROKER clock is the only clock
that drives decisions (``now_utc`` is accepted for forensic detail only and
never influences the verdict). Week boundaries are derived for the CURRENT
broker week from the broker instant via UTC weekday math -- no hardcoded
dates. Verdict states reuse the exact market_calendar strings
(OPEN/CLOSED/WEEKEND/PAUSED/UNKNOWN) plus the two guard states
(WEEKLY_OPEN_GUARD / FRIDAY_CUTOFF).

COMPATIBILITY (Sunday post-open window): market_calendar's documented
convention (and its ``next_open_iso``) is a Sunday 21:00 UTC reopen, but its
current implementation classifies the WHOLE Sunday as WEEKEND (the
``wd in (5, 6)`` clause precedes the tick-freshness evaluation), which would
make the weekly open guard unenforceable exactly where it matters. For the
narrow window Sunday >= 21:00 UTC, when the calendar reports WEEKEND this
gate evaluates the in-session freshness + guard logic instead of relaying
WEEKEND; every other state relays untouched. If market_calendar is later
fixed to honour its documented reopen, this path becomes a no-op (the
calendar already reports OPEN there).

NOTE (follow-up handoff): this gate is deliberately NOT wired into the
engine / live execution path -- wiring is a separate mission (same pattern
as the TASK-HOLD-CLOCK handoff). Nothing here imports or touches
live_engine / order_manager / adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.accounting.market_calendar import market_state

__all__ = [
    "MarketEntryGate",
    "MarketEntryGateConfig",
    "MarketEntryVerdict",
]

# Weekly gold/forex session convention (mirrors market_calendar's documented
# weekend window: Friday 22:00 UTC .. Sunday 21:00 UTC).
_SUNDAY = 6  # Monday=0 .. Sunday=6
_FRIDAY = 4
_WEEKLY_OPEN_HOUR = 21  # Sunday 21:00 UTC weekly open
_WEEKLY_CLOSE_HOUR = 22  # Friday 22:00 UTC weekend close

_STATE_OPEN = "OPEN"
_STATE_CLOSED = "CLOSED"
_STATE_WEEKEND = "WEEKEND"
_STATE_PAUSED = "PAUSED"
_STATE_UNKNOWN = "UNKNOWN"
_STATE_WEEKLY_OPEN_GUARD = "WEEKLY_OPEN_GUARD"
_STATE_FRIDAY_CUTOFF = "FRIDAY_CUTOFF"

_REASON_ALLOWED = "ENTRY_ALLOWED"
_REASON_NO_BROKER_TIME = "NO_BROKER_TIME"
_REASON_NO_TICK_FRESHNESS = "NO_TICK_FRESHNESS"
_REASON_WEEKLY_OPEN_GUARD = "WEEKLY_OPEN_GUARD"
_REASON_FRIDAY_CUTOFF = "FRIDAY_CUTOFF"
_REASON_UNKNOWN_ALLOWED = "UNKNOWN_ALLOWED_BY_CONFIG"
_REASON_UNRECOGNIZED_STATE = "UNRECOGNIZED_CALENDAR_STATE"

_WEEKEND_LIKE_STATES = (_STATE_WEEKEND, _STATE_CLOSED, _STATE_PAUSED)


@dataclass(frozen=True)
class MarketEntryGateConfig:
    """Tunables for :class:`MarketEntryGate` (guards in minutes, UTC clock).

    ``opening_guard_minutes=0.0`` disables the weekly open guard;
    ``friday_cutoff_minutes=0.0`` disables the Friday close cutoff.
    """

    opening_guard_minutes: float = 10.0
    friday_cutoff_minutes: float = 20.0
    tick_stale_after_sec: float = 120.0
    allow_when_unknown: bool = False

    def __post_init__(self) -> None:
        if self.opening_guard_minutes < 0.0:
            raise ValueError(
                f"opening_guard_minutes must be >= 0, got {self.opening_guard_minutes!r}"
            )
        if self.friday_cutoff_minutes < 0.0:
            raise ValueError(
                f"friday_cutoff_minutes must be >= 0, got {self.friday_cutoff_minutes!r}"
            )
        if self.tick_stale_after_sec < 0.0:
            raise ValueError(f"tick_stale_after_sec must be >= 0, got {self.tick_stale_after_sec!r}")


@dataclass(frozen=True)
class MarketEntryVerdict:
    """Immutable result of one gate evaluation.

    ``state`` reuses the exact market_calendar state strings plus the two
    guard states; ``detail`` carries the base calendar reason, tick age,
    broker/server ISO time and the next weekly boundaries for forensics.
    """

    allowed: bool
    state: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


def _normalize_instant(value: datetime | float | None) -> datetime | None:
    """Normalize a broker instant to tz-aware UTC (None when unusable).

    Naive datetimes are interpreted in the host-local timezone exactly as
    ``market_calendar.market_state`` does, so the gate's guard math always
    evaluates the SAME instant the base classifier used. Garbage values
    (NaN/inf epochs, wrong types) normalize to None -> fail-closed UNKNOWN.
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return datetime.fromtimestamp(float(value), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _at_utctime(moment: datetime, hour: int) -> datetime:
    """Same calendar day as ``moment`` (must be UTC-aware), at ``hour:00:00``."""
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0)


def _week_sunday_open(now: datetime) -> datetime:
    """Sunday 21:00 UTC that opened the current broker trading week.

    For a Sunday ``now`` this is the same day 21:00 (today's weekly open,
    possibly still in the future pre-open); for Mon..Sat it is the most
    recent Sunday.
    """
    if now.weekday() == _SUNDAY:
        return _at_utctime(now, _WEEKLY_OPEN_HOUR)
    return _at_utctime(now - timedelta(days=now.weekday() + 1), _WEEKLY_OPEN_HOUR)


def _week_friday_close(now: datetime) -> datetime:
    """Friday 22:00 UTC that closes the current broker trading week.

    The first Friday 22:00 at-or-after the current week's Sunday open (on
    Sunday post-open this is the UPCOMING Friday, not the previous one).
    """
    candidate = _at_utctime(now + timedelta(days=_FRIDAY - now.weekday()), _WEEKLY_CLOSE_HOUR)
    if candidate < _week_sunday_open(now):
        candidate += timedelta(days=7)
    return candidate


def _next_weekly_open(now: datetime) -> datetime:
    """The next Sunday 21:00 UTC weekly open strictly after ``now``."""
    week_open = _week_sunday_open(now)
    if week_open <= now:
        return week_open + timedelta(days=7)
    return week_open


def _is_sunday_post_open(now: datetime) -> bool:
    """True in the documented weekly-open window (Sunday >= 21:00 UTC)."""
    return now.weekday() == _SUNDAY and now >= _week_sunday_open(now)


def _unknown_verdict(config: MarketEntryGateConfig, detail: dict[str, Any]) -> MarketEntryVerdict:
    """Fail-closed (or config-opted-in) verdict for UNKNOWN base states."""
    detail["state"] = _STATE_UNKNOWN
    if config.allow_when_unknown:
        return MarketEntryVerdict(
            allowed=True, state=_STATE_UNKNOWN, reason=_REASON_UNKNOWN_ALLOWED, detail=detail
        )
    if detail.get("server_now_iso") is None:
        reason = _REASON_NO_BROKER_TIME
    else:
        reason = _REASON_NO_TICK_FRESHNESS
    return MarketEntryVerdict(allowed=False, state=_STATE_UNKNOWN, reason=reason, detail=detail)


def _blocked_verdict(state: str, reason: str, detail: dict[str, Any]) -> MarketEntryVerdict:
    detail["state"] = state
    return MarketEntryVerdict(allowed=False, state=state, reason=reason, detail=detail)


def _in_session_verdict(
    config: MarketEntryGateConfig,
    broker_now: datetime,
    last_tick_age_sec: float | None,
    detail: dict[str, Any],
) -> MarketEntryVerdict:
    """Freshness re-check + weekly timing guards for an in-session moment.

    Freshness is re-checked here (idempotent on the calendar-OPEN path) so
    the Sunday post-open compatibility window -- where the calendar reports
    WEEKEND before ever evaluating freshness -- still gets the same
    fail-closed stale/missing-tick semantics as a normal weekday.
    """
    if last_tick_age_sec is None:
        return _unknown_verdict(config, detail)
    if last_tick_age_sec > config.tick_stale_after_sec:
        return _blocked_verdict(_STATE_CLOSED, "MARKET_CLOSED", detail)

    elapsed_sec = (broker_now - _week_sunday_open(broker_now)).total_seconds()
    guard_sec = config.opening_guard_minutes * 60.0
    if 0.0 <= elapsed_sec < guard_sec:
        detail.update(
            {
                "state": _STATE_WEEKLY_OPEN_GUARD,
                "guard_minutes": config.opening_guard_minutes,
                "elapsed_sec": elapsed_sec,
            }
        )
        return MarketEntryVerdict(
            allowed=False,
            state=_STATE_WEEKLY_OPEN_GUARD,
            reason=_REASON_WEEKLY_OPEN_GUARD,
            detail=detail,
        )

    if broker_now.weekday() == _FRIDAY:
        seconds_to_close = (_week_friday_close(broker_now) - broker_now).total_seconds()
        cutoff_sec = config.friday_cutoff_minutes * 60.0
        if 0.0 <= seconds_to_close <= cutoff_sec:
            detail.update(
                {
                    "state": _STATE_FRIDAY_CUTOFF,
                    "cutoff_minutes": config.friday_cutoff_minutes,
                    "seconds_to_close": seconds_to_close,
                }
            )
            return MarketEntryVerdict(
                allowed=False,
                state=_STATE_FRIDAY_CUTOFF,
                reason=_REASON_FRIDAY_CUTOFF,
                detail=detail,
            )

    detail["state"] = _STATE_OPEN
    return MarketEntryVerdict(allowed=True, state=_STATE_OPEN, reason=_REASON_ALLOWED, detail=detail)


class MarketEntryGate:
    """Single reusable market-entry decision point (pure; no I/O, no logging).

    Construct with a :class:`MarketEntryGateConfig` and call :meth:`evaluate`
    per candidate entry. The gate owns no state beyond the frozen config, so
    one instance is safely shareable across the execution path.
    """

    def __init__(self, config: MarketEntryGateConfig) -> None:
        self._config = config

    def evaluate(
        self,
        *,
        server_now: datetime | float | None,
        last_tick_age_sec: float | None,
        now_utc: datetime | None = None,
    ) -> MarketEntryVerdict:
        """Return the entry verdict for one candidate entry moment.

        Args:
            server_now: BROKER server time (tz-aware datetime or epoch
                seconds). Naive datetimes follow market_calendar semantics
                (interpreted host-local). None/garbage -> fail-closed
                UNKNOWN (``NO_BROKER_TIME``).
            last_tick_age_sec: Age of the latest symbol tick in seconds;
                None means freshness unknown -> fail-closed UNKNOWN
                (``NO_TICK_FRESHNESS``) unless ``allow_when_unknown``.
            now_utc: Optional host wall clock -- forensic detail only, never
                influences the verdict.

        Returns:
            MarketEntryVerdict with the effective state (calendar states or
            WEEKLY_OPEN_GUARD / FRIDAY_CUTOFF), a stable reason code and a
            forensic detail dict (base calendar reason, tick age, ISO
            server time, current-week boundaries).
        """
        config = self._config
        broker_now = _normalize_instant(server_now)
        base = market_state(
            broker_now,
            last_tick_age_sec=last_tick_age_sec,
            tick_stale_after_sec=config.tick_stale_after_sec,
        )
        base_state = str(base.get("state", _STATE_UNKNOWN))
        base_reason = str(base.get("reason", ""))
        detail: dict[str, Any] = {
            "state": base_state,
            "base_state": base_state,
            "base_reason": base_reason,
            "last_tick_age_sec": last_tick_age_sec,
            "tick_stale_after_sec": config.tick_stale_after_sec,
            "server_now_iso": broker_now.isoformat() if broker_now is not None else None,
            "now_utc_iso": now_utc.astimezone(UTC).isoformat() if now_utc is not None else None,
        }
        if broker_now is not None:
            detail["week_sunday_open_iso"] = _week_sunday_open(broker_now).isoformat()
            detail["week_friday_close_iso"] = _week_friday_close(broker_now).isoformat()
            detail["next_weekly_open_iso"] = _next_weekly_open(broker_now).isoformat()

        if base_state == _STATE_UNKNOWN or broker_now is None:
            return _unknown_verdict(config, detail)
        if base_state == _STATE_WEEKEND and _is_sunday_post_open(broker_now):
            # Compatibility correction for the calendar's whole-Sunday WEEKEND
            # clause -- see module docstring. In-session logic re-checks
            # freshness itself (the calendar short-circuited before doing so).
            return _in_session_verdict(config, broker_now, last_tick_age_sec, detail)
        if base_state in _WEEKEND_LIKE_STATES:
            return _blocked_verdict(base_state, f"MARKET_{base_state}", detail)
        if base_state == _STATE_OPEN:
            return _in_session_verdict(config, broker_now, last_tick_age_sec, detail)
        # Defensive fail-closed for any future/unrecognized calendar state.
        return _blocked_verdict(base_state, _REASON_UNRECOGNIZED_STATE, detail)
