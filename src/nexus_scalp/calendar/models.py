"""Forward-looking economic calendar domain contracts (ECON_EVENT v1).

WHY
---
News RSS is backward-looking: a scheduled CPI/NFP/FOMC release only enters
the news pipeline AFTER it has happened. The engine therefore "discovers
scheduled macro events only after they happen" (market-context mission P0).
This module is the canonical forward-looking event layer: events are
ingested BEFORE their release time, carry explicit timezone-normalized
timestamps, and expose a freshness/health envelope so stale calendar data
can never masquerade as current information.

Safety / boundaries:
    * pure data + pure functions — no execution capability (INV-002),
    * never touches orders/risk/models — policy gates CONSUME this layer,
    * every datetime is tz-aware UTC (naive input rejected — INV-008
      causality + DST-correct comparison per Phase 6E),
    * no I/O here: providers (providers_ff.py) produce these contracts.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventImportance(StrEnum):
    """Market impact expectation BEFORE the release (FF-compatible)."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    HOLIDAY = "Holiday"


class EventStatus(StrEnum):
    """Lifecycle of a scheduled economic event."""

    SCHEDULED = "SCHEDULED"  # in the future, awaiting release
    RELEASED = "RELEASED"  # actual value published
    CANCELLED = "CANCELLED"  # officially cancelled
    POSTPONED = "POSTPONED"  # moved; scheduled_at updated by provider
    UNKNOWN = "UNKNOWN"  # provider could not classify


class CalendarHealth(StrEnum):
    """Health of the whole calendar layer (fail-safe contract)."""

    GOOD = "GOOD"  # fresh fetch + future events present
    DEGRADED = "DEGRADED"  # fetch failing but last data within max age
    INVALID = "INVALID"  # no usable data / data beyond max age


EVENT_MODEL_VERSION = "econ_event_v1"
#: Calendar data older than this can no longer claim to know the future
#: schedule (fail-safe: high-impact policy treats INVALID as UNKNOWN risk).
CALENDAR_MAX_STALE_SEC: float = 72 * 3600.0


class EconomicEvent(BaseModel):
    """One scheduled/released economic event (frozen contract, ECON_EVENT v1)."""

    model_config = ConfigDict(frozen=True)

    model_version: str = EVENT_MODEL_VERSION
    event_id: str
    event_type: str  # canonical deterministic classifier (cpi/nfp/fomc/...)
    title: str
    country: str  # FF-style currency code: USD / EUR / GBP / JPY / ...
    currency: str = ""  # usually same as country for FX-style calendars
    scheduled_at: datetime  # ALWAYS tz-aware UTC
    importance: EventImportance = EventImportance.LOW
    source: str  # provenance: provider id, e.g. "ff_calendar" / "fed_fomc"
    source_url: str = ""
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timezone: str = "UTC"
    status: EventStatus = EventStatus.SCHEDULED
    actual: str = ""
    forecast: str = ""
    previous: str = ""

    @field_validator("scheduled_at", "retrieved_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("EconomicEvent datetimes must be tz-aware (naive rejected)")
        return v.astimezone(UTC)

    def is_high_impact(self) -> bool:
        return self.importance == EventImportance.HIGH

    def minutes_until(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        return (self.scheduled_at - now).total_seconds() / 60.0

    def in_window(
        self,
        *,
        now: datetime,
        pre_event_min: float,
        post_event_min: float,
    ) -> bool:
        """True when `now` falls inside [scheduled_at - pre, scheduled_at + post]."""
        delta_min = (self.scheduled_at - now).total_seconds() / 60.0
        if delta_min >= 0:
            return delta_min <= pre_event_min
        return -delta_min <= post_event_min


class CalendarEnvelope(BaseModel):
    """The canonical read-model the engine/policy consumes. NEVER a bare list.

    Carries provenance + freshness so a consumer can distinguish
    GOOD calendar data from a stale/failed provider (Phase 6C).
    """

    model_config = ConfigDict(frozen=True)

    model_version: str = EVENT_MODEL_VERSION
    provider: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_success_at: datetime | None = None
    next_update_due_at: datetime | None = None
    health: CalendarHealth = CalendarHealth.INVALID
    events: tuple[EconomicEvent, ...] = ()
    fetch_error: str = ""

    @field_validator("generated_at", "last_success_at", "next_update_due_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("CalendarEnvelope datetimes must be tz-aware")
        return v.astimezone(UTC)

    @property
    def age_sec(self) -> float:
        """Seconds since last successful fetch (large when never fetched)."""
        if self.last_success_at is None:
            return float("inf")
        return max(0.0, (datetime.now(UTC) - self.last_success_at).total_seconds())

    def is_stale(self, max_age_sec: float = CALENDAR_MAX_STALE_SEC) -> bool:
        return self.age_sec > max_age_sec

    def future_high_impact(self, now: datetime | None = None) -> list[EconomicEvent]:
        now = now or datetime.now(UTC)
        return sorted(
            (
                e
                for e in self.events
                if e.is_high_impact()
                and e.status in (EventStatus.SCHEDULED, EventStatus.UNKNOWN)
                and e.scheduled_at > now
            ),
            key=lambda e: e.scheduled_at,
        )

    def next_high_impact(self, now: datetime | None = None) -> EconomicEvent | None:
        fut = self.future_high_impact(now)
        return fut[0] if fut else None

    def summary(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "provider": self.provider,
            "health": self.health.value,
            "generated_at": self.generated_at.isoformat(),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "event_count": len(self.events),
            "future_high_impact_count": len(self.future_high_impact()),
            "fetch_error": self.fetch_error,
        }


def make_event_id(source: str, title: str, scheduled_at: datetime) -> str:
    """Deterministic event identity: stable across re-fetches of the same event."""
    stamp = scheduled_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    norm_title = " ".join((title or "").lower().split())
    raw = f"{source}|{norm_title}|{stamp}"
    return f"ev_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def fallback_event_id() -> str:
    return f"ev_{uuid.uuid4().hex[:16]}"
