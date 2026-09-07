"""Economic-calendar providers (Phase 6B).

Two provider tiers, both VERIFIED against the live endpoints on 2026-09-07
(scratch probes; results in scratch/news_probe_results.json):

1. **FFCalendarProvider** — ForexFactory weekly JSON mirror
   (nfs.faireconomy.media/ff_calendar_thisweek.json). Verified: HTTP 200,
   81 events, 10 High-impact, fields {title, country, date, impact,
   forecast, previous}; the server answers 429 with an explicit
   Retry-After when polled too often. Handles: timezone-normalized
   datetimes (source carries -04:00 offset), rate-limit backoff, typed
   failures. Third-party justification: no single official source covers
   all scheduled macro releases cross-country in one machine-readable
   feed; BLS/DOL block non-browser clients (verified 403).

2. **FedFOMCProvider** — the official Federal Reserve FOMC meeting
   calendar page (federalreserve.gov/monetarypolicy/fomccalendars.htm).
   Verified: HTTP 200, per-meeting `fomc-meeting` rows with month + day
   range parseable for future years (2026/2027 anchors present). Used to
   anchor FOMC decision dates to an OFFICIAL source (Phase 6B rule:
   prefer official where a stable contract exists).

Contract (Phase 6B/6C):
    * every fetch returns a CalendarEnvelope (never a bare list),
    * on failure the envelope carries health + fetch_error — the consumer
      distinguishes GOOD / DEGRADED / INVALID and never pretends stale
      data is current,
    * all datetimes tz-aware UTC; provider offsets normalized at ingest,
    * no polling loop here — the caller (calendar worker) schedules.

Per-source health is owned by the caller via ProviderState (below).
"""

from __future__ import annotations

import contextlib
import re
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.calendar.classify import (
    classify_country_calendar_event,
)
from nexus_scalp.calendar.models import (
    CALENDAR_MAX_STALE_SEC,
    CalendarEnvelope,
    CalendarHealth,
    EconomicEvent,
    EventImportance,
    EventStatus,
    make_event_id,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.calendar.providers")

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

#: FOMC decision lands ~14:00 ET on the meeting's LAST day; the calendar
#: page gives only day ranges, so the provider stamps 18:00 UTC on the
#: final meeting day (documented approximation, DST-safe via UTC stamping).
FOMC_DECISION_UTC_HOUR = 18

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 NexusScalp/1.0"


class ProviderState:
    """Mutable per-provider fetch bookkeeping (owned by the caller/worker)."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str = ""
        self.last_http_status: int | None = None
        self.consecutive_failures: int = 0
        self.backoff_until: float = 0.0  # monotonic seconds
        self.fetch_count: int = 0
        self.event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_error": self.last_error,
            "last_http_status": self.last_http_status,
            "consecutive_failures": self.consecutive_failures,
            "backoff_until": self.backoff_until,
            "fetch_count": self.fetch_count,
            "event_count": self.event_count,
        }


class FFCalendarProvider:
    """ForexFactory weekly JSON economic-calendar provider (third-party)."""

    provider_id = "ff_calendar"

    def __init__(self, url: str = FF_URL, timeout_sec: float = 25.0) -> None:
        self.url = url
        self.timeout_sec = timeout_sec
        self.state = ProviderState(self.provider_id)

    def fetch(self, now: datetime | None = None) -> CalendarEnvelope:
        """One bounded fetch -> CalendarEnvelope (typed failure, never raises)."""
        now = now or datetime.now(UTC)
        if time.monotonic() < self.state.backoff_until:
            return self._envelope(CalendarHealth.DEGRADED, [], "backoff active (rate-limit window)")
        events: list[EconomicEvent] = []
        error = ""
        try:
            import httpx

            with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
                resp = client.get(
                    self.url,
                    headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip, deflate"},
                )
                self.state.last_http_status = resp.status_code
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "300") or 300)
                    self.state.backoff_until = time.monotonic() + min(3600.0, retry_after)
                    error = f"rate limited (retry_after={retry_after:.0f}s)"
                elif resp.status_code != 200:
                    error = f"HTTP {resp.status_code}"
                else:
                    rows = resp.json()
                    events = self._parse(rows, now)
                    if not events:
                        error = "200 OK but zero parseable events"
        except Exception as exc:  # typed failure boundary
            error = f"{type(exc).__name__}: {exc}"
        if error:
            self.state.last_failure_at = datetime.now(UTC)
            self.state.consecutive_failures += 1
            self.state.last_error = error
            health = (
                CalendarHealth.DEGRADED
                if self.state.last_success_at is not None
                and (now - self.state.last_success_at).total_seconds() <= CALENDAR_MAX_STALE_SEC
                else CalendarHealth.INVALID
            )
            return self._envelope(health, [], error)
        # success
        self.state.last_success_at = now
        self.state.consecutive_failures = 0
        self.state.last_error = ""
        self.state.fetch_count += 1
        self.state.event_count = len(events)
        return self._envelope(CalendarHealth.GOOD, events, "")

    def _parse(self, rows: list[dict[str, Any]], now: datetime) -> list[EconomicEvent]:
        events: list[EconomicEvent] = []
        for row in rows:
            with contextlib.suppress(Exception):
                title = str(row.get("title") or "").strip()
                country = str(row.get("country") or "").strip().upper()
                raw_date = str(row.get("date") or "").strip()
                if not (title and country and raw_date):
                    continue
                # FF dates carry an explicit offset (e.g. 2026-09-10T08:15:00-04:00).
                scheduled = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                if scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=UTC)  # defensive; documented
                scheduled = scheduled.astimezone(UTC)
                etype, importance = classify_country_calendar_event(
                    title, country, str(row.get("impact") or "")
                )
                actual = str(row.get("actual") or "").strip()
                events.append(
                    EconomicEvent(
                        event_id=make_event_id(self.provider_id, title, scheduled),
                        event_type=etype.value if etype else "",
                        title=title,
                        country=country,
                        currency=country,
                        scheduled_at=scheduled,
                        importance=importance,
                        source=self.provider_id,
                        source_url=self.url,
                        retrieved_at=now,
                        timezone="UTC (normalized from provider offset)",
                        status=EventStatus.RELEASED if actual else EventStatus.SCHEDULED,
                        actual=actual,
                        forecast=str(row.get("forecast") or "").strip(),
                        previous=str(row.get("previous") or "").strip(),
                    )
                )
        return events

    def _envelope(
        self,
        health: CalendarHealth,
        events: list[EconomicEvent],
        error: str,
    ) -> CalendarEnvelope:
        return CalendarEnvelope(
            provider=self.provider_id,
            last_success_at=self.state.last_success_at,
            next_update_due_at=None,  # scheduled by the worker, not the provider
            health=health,
            events=tuple(events),
            fetch_error=error,
        )


_MONTHS = {
    m.lower(): i + 1
    for i, m in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
    )
}


class FedFOMCProvider:
    """Official Federal Reserve FOMC meeting-calendar provider.

    Parses the public fomccalendars.htm meeting rows (verified contract:
    `fomc-meeting__month` + `fomc-meeting__date` divs per meeting, forward
    years anchored). Emits one SCHEDULED event per future meeting day-set,
    stamped at the documented decision time on the meeting's LAST day.
    """

    provider_id = "fed_fomc"

    def __init__(self, url: str = FOMC_URL, timeout_sec: float = 25.0) -> None:
        self.url = url
        self.timeout_sec = timeout_sec
        self.state = ProviderState(self.provider_id)

    def fetch(self, now: datetime | None = None) -> CalendarEnvelope:
        now = now or datetime.now(UTC)
        events: list[EconomicEvent] = []
        error = ""
        try:
            import httpx

            with httpx.Client(timeout=self.timeout_sec, follow_redirects=True) as client:
                resp = client.get(self.url, headers={"User-Agent": _USER_AGENT})
                self.state.last_http_status = resp.status_code
                if resp.status_code != 200:
                    error = f"HTTP {resp.status_code}"
                else:
                    events = self._parse(resp.text, now)
                    if not events:
                        error = "200 OK but zero FOMC meetings parsed (page contract drifted)"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error:
            self.state.last_failure_at = datetime.now(UTC)
            self.state.consecutive_failures += 1
            self.state.last_error = error
            health = (
                CalendarHealth.DEGRADED
                if self.state.last_success_at is not None
                and (now - self.state.last_success_at).total_seconds() <= CALENDAR_MAX_STALE_SEC
                else CalendarHealth.INVALID
            )
            return CalendarEnvelope(
                provider=self.provider_id,
                last_success_at=self.state.last_success_at,
                health=health,
                events=(),
                fetch_error=error,
            )
        self.state.last_success_at = now
        self.state.consecutive_failures = 0
        self.state.last_error = ""
        self.state.fetch_count += 1
        self.state.event_count = len(events)
        return CalendarEnvelope(
            provider=self.provider_id,
            last_success_at=now,
            health=CalendarHealth.GOOD,
            events=tuple(events),
            fetch_error="",
        )

    def _parse(self, html: str, now: datetime) -> list[EconomicEvent]:
        """Extracts (year, month, day) tuples from year-segmented meeting rows.

        Page contract (verified 2026-09-07): per-year panels appear in
        document order, each introduced by an anchor like
        ``<a id="42828">2026</a>`` inside the panel (or a year link table);
        every ``fomc-meeting`` row after a year anchor belongs to that year.
        """
        meetings: list[tuple[int, int, int]] = []
        current_year = now.year
        # Merge-iterate: PANEL headings set the panel year; month rows use it.
        # Verified contract (2026-09-07): each year panel begins
        # ``<h4><a id="NNNN">YYYY FOMC Meetings</a></h4>`` — the year table in
        # the nav is NOT a panel boundary and must not move the pointer.
        token_re = re.compile(
            r'<a id="\d+">(\d{4})(?:\s*FOMC Meetings)?\s*</a>'
            r"|fomc-meeting__month[^>]*><strong>([A-Za-z]+)</strong></div>\s*"
            r'<div class="fomc-meeting__date[^"]*">([\d\-\u2013\s]+)</div>'
        )
        for m in token_re.finditer(html):
            year_tok = m.group(1)
            if year_tok:
                # Panels list history too (2021..2027); the pointer must
                # FOLLOW DOCUMENT ORDER unconditionally — a past panel after
                # a future one must move the pointer BACK. The `scheduled <=
                # now` filter below discards historical meetings anyway.
                current_year = int(year_tok)
                continue
            month_name = (m.group(2) or "").lower()
            month = _MONTHS.get(month_name)
            day_nums = [int(d) for d in re.findall(r"\d{1,2}", m.group(3) or "")]
            if not month or not day_nums:
                continue
            meetings.append((current_year, month, max(day_nums)))
        events: list[EconomicEvent] = []
        seen: set[tuple[int, int, int]] = set()
        for year, month, day in meetings:
            key = (year, month, day)
            if key in seen:
                continue
            seen.add(key)
            scheduled = datetime(year, month, day, FOMC_DECISION_UTC_HOUR, tzinfo=UTC)
            if scheduled <= now:
                continue
            title = "FOMC Statement and Federal Funds Rate Decision"
            events.append(
                EconomicEvent(
                    event_id=make_event_id(self.provider_id, title, scheduled),
                    event_type="fomc_decision",
                    title=title,
                    country="USD",
                    currency="USD",
                    scheduled_at=scheduled,
                    importance=EventImportance.HIGH,
                    source=self.provider_id,
                    source_url=self.url,
                    retrieved_at=now,
                    timezone="UTC (decision ~14:00 ET on last meeting day)",
                    status=EventStatus.SCHEDULED,
                )
            )
        events.sort(key=lambda e: e.scheduled_at)
        return events


def default_providers() -> list[FFCalendarProvider | FedFOMCProvider]:
    """Provider set used by the calendar worker (order = fetch priority)."""
    return [FFCalendarProvider(), FedFOMCProvider()]
