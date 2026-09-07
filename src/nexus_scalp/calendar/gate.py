"""High-impact event risk gate (Phase 6D / 6E / 9).

Reads the canonical CalendarEnvelope and answers ONE question for the
pre-trade path: is a high-impact economic event window active right now?

Contract:
    * window durations are CONFIGURATION, never hard-coded — defaults are
      conservative placeholders documented as uncalibrated (no OOS
      evidence yet), and the mission forbids dressing defaults as
      evidence (Phase 6D / global rule 6),
    * STALE/INVALID calendar data has explicit fail-safe semantics:
      when the calendar is invalid AND the policy chose to fail closed,
      the gate reports UNKNOWN_RISK (the caller decides per policy mode),
    * pure function of (envelope, now, config) — no I/O, no network,
      safe to call from the tick path (INV-001: it reads an in-memory
      envelope the calendar worker refreshed off-path),
    * advisory layer only: it can never place/modify/close an order and
      never bypasses risk (INV-002/003/004).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from nexus_scalp.calendar.models import (
    CALENDAR_MAX_STALE_SEC,
    CalendarEnvelope,
    CalendarHealth,
    EconomicEvent,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.calendar.gate")


#: States the gate can report for the current moment.
class EventGateState(StrEnum):
    CLEAR = "CLEAR"  # no high-impact event window
    PRE_EVENT = "PRE_EVENT"  # inside the pre-event restricted window
    POST_EVENT = "POST_EVENT"  # inside the post-event settling window
    UNKNOWN_RISK = "UNKNOWN_RISK"  # calendar stale/invalid (fail-safe path)


@dataclass(frozen=True)
class EventGatePolicy:
    """Configurable, validated event-window policy (Phase 6D/15).

    Defaults are PLACEHOLDERS pending the Phase 14 evaluation — explicit
    ``calibrated=False`` marks them so observability never presents them
    as evidence-based.
    """

    enabled: bool = True
    #: currencies whose high-impact events gate entries (empty = all)
    currencies: tuple[str, ...] = ("USD",)  # XAUUSD driver currency
    pre_event_min: float = 15.0  # minutes BEFORE release
    post_event_min: float = 15.0  # minutes AFTER release (settling)
    #: behavior when the calendar is STALE/INVALID:
    #:   "observe" -> keep trading, log telemetry only (aux-info semantics)
    #:   "block_high_impact" -> treat as UNKNOWN_RISK and restrict entries
    stale_mode: str = "observe"
    max_calendar_stale_sec: float = CALENDAR_MAX_STALE_SEC
    calibrated: bool = False  # becomes True only after Phase 14 evidence

    def __post_init__(self) -> None:
        if float(self.pre_event_min) < 0 or float(self.post_event_min) < 0:
            raise ValueError("event windows must be >= 0 minutes")
        if self.stale_mode not in ("observe", "block_high_impact"):
            raise ValueError("stale_mode must be 'observe' or 'block_high_impact'")
        if float(self.max_calendar_stale_sec) <= 0:
            raise ValueError("max_calendar_stale_sec must be > 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "currencies": list(self.currencies),
            "pre_event_min": self.pre_event_min,
            "post_event_min": self.post_event_min,
            "stale_mode": self.stale_mode,
            "max_calendar_stale_sec": self.max_calendar_stale_sec,
            "calibrated": self.calibrated,
        }


@dataclass
class EventGateVerdict:
    """Explainable verdict for one evaluation (never an order decision)."""

    state: EventGateState = EventGateState.CLEAR
    reason: str = ""
    event_id: str = ""
    event_title: str = ""
    event_type: str = ""
    minutes_delta: float = 0.0
    calendar_health: str = CalendarHealth.INVALID.value
    calendar_age_sec: float | None = None
    policy_calibrated: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "event_id": self.event_id,
            "event_title": self.event_title,
            "event_type": self.event_type,
            "minutes_delta": round(self.minutes_delta, 2),
            "calendar_health": self.calendar_health,
            "calendar_age_sec": (
                round(self.calendar_age_sec, 1) if self.calendar_age_sec is not None else None
            ),
            "policy_calibrated": self.policy_calibrated,
            "notes": self.notes,
        }


def evaluate_event_window(
    *,
    envelope: CalendarEnvelope | None,
    now: datetime | None = None,
    policy: EventGatePolicy | None = None,
) -> EventGateVerdict:
    """Evaluates the high-impact event window for `now` (pure, O(events))."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        # Phase 6E: never compare naive local timestamps — normalize or fail.
        now = now.replace(tzinfo=UTC)
    policy = policy or EventGatePolicy()

    verdict = EventGateVerdict(
        calendar_health=envelope.health.value if envelope else CalendarHealth.INVALID.value,
        policy_calibrated=policy.calibrated,
    )

    if not policy.enabled:
        verdict.reason = "POLICY_DISABLED"
        verdict.notes.append("event gate disabled by configuration")
        return verdict

    if envelope is None:
        verdict.state = (
            EventGateState.UNKNOWN_RISK
            if policy.stale_mode == "block_high_impact"
            else EventGateState.CLEAR
        )
        verdict.reason = "NO_CALENDAR"
        verdict.notes.append("no envelope available (worker never fetched)")
        return verdict

    verdict.calendar_age_sec = envelope.age_sec

    # Freshness first (Phase 9: stale calendar has its own semantics).
    if envelope.health == CalendarHealth.INVALID or envelope.is_stale(
        policy.max_calendar_stale_sec
    ):
        if policy.stale_mode == "block_high_impact":
            verdict.state = EventGateState.UNKNOWN_RISK
            verdict.reason = "CALENDAR_STALE_FAIL_CLOSED"
            verdict.notes.append(
                f"calendar age {envelope.age_sec:.0f}s > max {policy.max_calendar_stale_sec:.0f}s"
            )
        else:
            verdict.state = EventGateState.CLEAR
            verdict.reason = "CALENDAR_STALE_OBSERVE"
            verdict.notes.append("stale calendar; observe-mode (telemetry only)")
        return verdict

    currencies = {c.upper() for c in policy.currencies}
    relevant = [
        e
        for e in envelope.events
        if e.is_high_impact() and (not currencies or e.country.upper() in currencies)
    ]
    # nearest by absolute time distance decides the state
    nearest: EconomicEvent | None = None
    nearest_delta = 0.0
    for e in relevant:
        delta = (e.scheduled_at - now).total_seconds() / 60.0
        if e.in_window(
            now=now,
            pre_event_min=policy.pre_event_min,
            post_event_min=policy.post_event_min,
        ):
            if nearest is None or abs(delta) < abs(nearest_delta):
                nearest, nearest_delta = e, delta
    if nearest is None:
        verdict.state = EventGateState.CLEAR
        verdict.reason = "NO_ACTIVE_WINDOW"
        return verdict

    verdict.event_id = nearest.event_id
    verdict.event_title = nearest.title
    verdict.event_type = nearest.event_type
    verdict.minutes_delta = nearest_delta
    if nearest_delta > 0:
        verdict.state = EventGateState.PRE_EVENT
        verdict.reason = "PRE_EVENT_WINDOW_ACTIVE"
    else:
        verdict.state = EventGateState.POST_EVENT
        verdict.reason = "POST_EVENT_WINDOW_ACTIVE"
    return verdict


def next_window_open(
    *,
    envelope: CalendarEnvelope | None,
    now: datetime | None = None,
    policy: EventGatePolicy | None = None,
) -> datetime | None:
    """When does the next active window CLOSE (for scheduling resume)?"""
    now = now or datetime.now(UTC)
    policy = policy or EventGatePolicy()
    if envelope is None or not policy.enabled:
        return None
    currencies = {c.upper() for c in policy.currencies}
    best: datetime | None = None
    for e in envelope.events:
        if not e.is_high_impact() or (currencies and e.country.upper() not in currencies):
            continue
        resume = e.scheduled_at.timestamp() + policy.post_event_min * 60.0
        resume_dt = datetime.fromtimestamp(resume, tz=UTC)
        if resume_dt > now and (best is None or resume_dt < best):
            best = resume_dt
    return best
