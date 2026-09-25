"""
Deterministic Accounting Period Boundaries
==========================================
ONE definition of DAY / WEEK / MONTH / YEAR for the entire system.

WHY THIS MODULE EXISTS
----------------------
If the dashboard bucketed trades by local time while the worker bucketed them by
UTC, the same trade would appear in two different days and no two reports would
ever reconcile. Every consumer (core, worker, REST API, dashboard) resolves its
boundaries here and nowhere else.

CANONICAL POLICY (matches the repository's storage convention: the audit ledger
writes UTC timestamps via SQLite `DATETIME('now')`):

    DAY    [00:00:00, next 00:00:00)  UTC
    WEEK   ISO-8601 week: Monday 00:00:00 UTC .. next Monday 00:00:00 UTC
    MONTH  1st of month 00:00:00 UTC .. 1st of next month 00:00:00 UTC
    YEAR   Jan 1 00:00:00 UTC .. next Jan 1 00:00:00 UTC

All intervals are HALF-OPEN `[start, end)`. A trade closing exactly at midnight
belongs to the NEW period, never to both.

PERIOD KEYS are stable, sortable strings used as the primary key of the derived
aggregate table, so re-aggregating a period is an idempotent upsert:

    DAY    "2026-08-15"
    WEEK   "2026-W33"
    MONTH  "2026-08"
    YEAR   "2026"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

#: SQLite text timestamp format used across the audit schema.
SQL_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class PeriodKind(StrEnum):
    """Supported reporting granularities."""

    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


@dataclass(frozen=True)
class PeriodBounds:
    """
    A half-open UTC interval `[start, end)` plus its canonical key.

    Attributes:
        kind: Granularity this interval represents.
        key: Stable sortable identifier (e.g. "2026-08" for a month).
        start: Inclusive UTC start.
        end: Exclusive UTC end.
    """

    kind: PeriodKind
    key: str
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        """True when `moment` falls inside this half-open interval."""
        aware = ensure_utc(moment)
        return self.start <= aware < self.end

    @property
    def start_sql(self) -> str:
        """Inclusive start formatted for SQLite text comparison."""
        return self.start.strftime(SQL_TS_FORMAT)

    @property
    def end_sql(self) -> str:
        """Exclusive end formatted for SQLite text comparison."""
        return self.end.strftime(SQL_TS_FORMAT)

    @property
    def label(self) -> str:
        """Human-readable label for dashboards."""
        if self.kind is PeriodKind.DAY:
            return self.start.strftime("%Y-%m-%d")
        if self.kind is PeriodKind.WEEK:
            return f"Week {self.start.isocalendar().week}, {self.start.year}"
        if self.kind is PeriodKind.MONTH:
            return self.start.strftime("%B %Y")
        return self.start.strftime("%Y")


def ensure_utc(moment: datetime) -> datetime:
    """
    Normalizes any datetime to timezone-aware UTC.

    Naive datetimes are ASSUMED to already be UTC, which matches how the audit
    schema stores timestamps (`DATETIME('now')` is UTC in SQLite). Assuming local
    time here would silently shift every historical trade by the host's offset.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def utc_now() -> datetime:
    """Current instant as timezone-aware UTC."""
    return datetime.now(UTC)


def _floor_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def period_bounds(kind: PeriodKind, at: datetime | None = None) -> PeriodBounds:
    """
    Resolves the canonical bounds of the period CONTAINING `at`.

    Args:
        kind: Granularity to resolve.
        at: Any instant inside the desired period (defaults to now, UTC).

    Returns:
        The half-open `[start, end)` interval and its stable key.
    """
    moment = ensure_utc(at) if at is not None else utc_now()

    if kind is PeriodKind.DAY:
        start = _floor_day(moment)
        end = start + timedelta(days=1)
        return PeriodBounds(kind, start.strftime("%Y-%m-%d"), start, end)

    if kind is PeriodKind.WEEK:
        # ISO week: Monday is weekday 0 after this subtraction.
        start = _floor_day(moment) - timedelta(days=moment.weekday())
        end = start + timedelta(days=7)
        iso = start.isocalendar()
        return PeriodBounds(kind, f"{iso.year}-W{iso.week:02d}", start, end)

    if kind is PeriodKind.MONTH:
        start = _floor_day(moment).replace(day=1)
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return PeriodBounds(kind, start.strftime("%Y-%m"), start, end)

    start = _floor_day(moment).replace(month=1, day=1)
    end = start.replace(year=start.year + 1)
    return PeriodBounds(kind, start.strftime("%Y"), start, end)


def previous_period(bounds: PeriodBounds) -> PeriodBounds:
    """Resolves the period immediately preceding `bounds`."""
    return period_bounds(bounds.kind, bounds.start - timedelta(seconds=1))


def recent_periods(kind: PeriodKind, count: int, at: datetime | None = None) -> list[PeriodBounds]:
    """
    Returns the `count` most recent periods ending with the one containing `at`,
    ordered oldest -> newest (chart-friendly).

    Bounded by construction: callers cannot accidentally request the entire
    account history one period at a time.
    """
    if count <= 0:
        return []
    current = period_bounds(kind, at)
    out = [current]
    for _ in range(count - 1):
        current = previous_period(current)
        out.append(current)
    return list(reversed(out))


def parse_sql_timestamp(raw: str | None) -> datetime | None:
    """
    Parses a stored audit timestamp into aware UTC, tolerating the several shapes
    that exist in the schema (`DATETIME('now')` output, ISO-8601 with or without
    a `T` separator, fractional seconds, trailing `Z`).

    Returns None for missing/unparseable values so callers can treat the record
    as "no reliable timestamp" instead of silently bucketing it into today.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    for fmt in (SQL_TS_FORMAT, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None
