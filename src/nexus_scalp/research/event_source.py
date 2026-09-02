"""Historical Event Sources (CHG-0035, STREAMING_REPLAY v1).

Canonical research replay consumes a stream of historical events — bars OR
ticks — through ONE interface so the StreamingReplayEngine never knows where
the data came from:

    HistoricalEventSource (protocol)
        BarEventSource      — completed bars (rates frame / pre-built bars)
        TickEventSource     — raw ticks (time_msc/bid/ask/last/flags/volume)
        ChunkedEventSource  — bounded-RAM chunking over any inner source

CONTRACTS (user brief §12-§23, §47-§49, §54-§56):

* Determinism: iterating a source twice yields byte-identical event
  sequences (test-enforced). Chunk boundaries MUST NOT change semantics.
* Malformed events become ``DATA_ERROR`` records with diagnostic context
  (timestamp/symbol/reason) — they are NEVER silently fabricated or dropped
  without a trace.
* Validation (validate_event_source): chronological ordering, duplicate
  timestamps, bid/ask sanity (0 < bid <= ask), price finiteness, gap
  detection (informational).
* This module is DATA-ONLY: no MT5 imports, no adapters, no execution. The
  MT5 acquisition layer lives in mt5_tick_dataset.py.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.event_source")

#: Events with no timestamp successor for longer than this are reported as
#: informational gaps by validate_event_source (never an error: sessions
#: legitimately have no ticks over weekends).
DEFAULT_GAP_REPORT_SECONDS: float = 3600.0 * 4


class EventKind(StrEnum):
    BAR = "BAR"
    TICK = "TICK"
    DATA_ERROR = "DATA_ERROR"


@dataclass(frozen=True, slots=True)
class BarEvent:
    """One completed historical bar (M1 by convention)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: float = 0.0
    symbol: str = "XAUUSD"
    timeframe: str = "M1"

    @property
    def kind(self) -> EventKind:
        return EventKind.BAR


@dataclass(frozen=True, slots=True)
class TickEvent:
    """One historical tick with the FULL probed MT5 field set preserved.

    Fields mirror the Agent-3 probe contract for ``copy_ticks_range`` with
    ``COPY_TICKS_ALL`` (package 5.0.6090): time, time_msc, bid, ask, last,
    flags, volume. ``volume`` is the tick/real volume as provided by the
    source — tick replay never invents it.
    """

    timestamp: datetime
    bid: float
    ask: float
    time_msc: int = 0
    last: float = 0.0
    flags: int = 0
    volume: float = 0.0
    symbol: str = "XAUUSD"

    @property
    def kind(self) -> EventKind:
        return EventKind.TICK


@dataclass(frozen=True, slots=True)
class DataErrorEvent:
    """A malformed source record, preserved with diagnosis (brief §54).

    The engine treats these as transparent pass-through markers: they never
    alter strategy/position state, and they are surfaced in run results so
    data-quality problems are visible instead of hidden.
    """

    timestamp: datetime | None
    reason: str
    symbol: str = "XAUUSD"
    source_name: str = ""
    raw_index: int = -1

    @property
    def kind(self) -> EventKind:
        return EventKind.DATA_ERROR


ReplayEvent = BarEvent | TickEvent | DataErrorEvent


@runtime_checkable
class HistoricalEventSource(Protocol):
    """Minimal interface every historical data source satisfies (§42)."""

    name: str
    event_kind: EventKind
    symbol: str

    def events(self) -> Iterator[ReplayEvent]:
        """Yields events in strict chronological order."""
        ...


# ---------------------------------------------------------------------------
# Validation helpers (brief §54-§56)
# ---------------------------------------------------------------------------


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def validate_tick_record(rec: dict[str, Any], index: int) -> tuple[TickEvent | None, str]:
    """Validates one raw tick record -> (TickEvent, "") or (None, reason)."""
    ts = rec.get("timestamp")
    if not isinstance(ts, datetime):
        return None, f"tick[{index}]: missing/invalid timestamp"
    bid = rec.get("bid")
    ask = rec.get("ask")
    if bid is None or ask is None:
        return None, f"tick[{index}] @ {ts.isoformat()}: missing bid/ask"
    try:
        bid_f = float(bid)
        ask_f = float(ask)
    except (TypeError, ValueError):
        return None, f"tick[{index}] @ {ts.isoformat()}: non-numeric bid/ask"
    if not _finite(bid_f) or not _finite(ask_f):
        return None, f"tick[{index}] @ {ts.isoformat()}: non-finite bid/ask"
    if bid_f <= 0.0 or ask_f <= 0.0:
        return None, f"tick[{index}] @ {ts.isoformat()}: non-positive bid/ask ({bid_f}/{ask_f})"
    if ask_f < bid_f:
        return None, f"tick[{index}] @ {ts.isoformat()}: crossed quote ask {ask_f} < bid {bid_f}"
    tsc = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    vol = rec.get("volume", 0.0)
    try:
        vol_f = max(0.0, float(vol)) if vol is not None else 0.0
    except (TypeError, ValueError):
        vol_f = 0.0
    last = rec.get("last", 0.0)
    try:
        last_f = max(0.0, float(last)) if last is not None else 0.0
    except (TypeError, ValueError):
        last_f = 0.0
    flags = rec.get("flags", 0)
    try:
        flags_i = int(flags) if flags is not None else 0
    except (TypeError, ValueError):
        flags_i = 0
    tmsc = rec.get("time_msc", 0)
    try:
        tmsc_i = int(tmsc) if tmsc is not None else 0
    except (TypeError, ValueError):
        tmsc_i = 0
    return (
        TickEvent(
            timestamp=tsc,
            bid=bid_f,
            ask=ask_f,
            time_msc=tmsc_i,
            last=last_f,
            flags=flags_i,
            volume=vol_f,
            symbol=str(rec.get("symbol", "XAUUSD")),
        ),
        "",
    )


def validate_bar_record(rec: dict[str, Any], index: int) -> tuple[BarEvent | None, str]:
    """Validates one raw bar record -> (BarEvent, "") or (None, reason)."""
    ts = rec.get("timestamp")
    if not isinstance(ts, datetime):
        return None, f"bar[{index}]: missing/invalid timestamp"
    try:
        o, h, l, c = (float(rec[k]) for k in ("open", "high", "low", "close"))
    except (KeyError, TypeError, ValueError):
        return None, f"bar[{index}] @ {ts.isoformat()}: missing/non-numeric OHLC"
    if not all(_finite(v) for v in (o, h, l, c)):
        return None, f"bar[{index}] @ {ts.isoformat()}: non-finite OHLC"
    if min(o, h, l, c) <= 0.0:
        return None, f"bar[{index}] @ {ts.isoformat()}: non-positive OHLC"
    if h < max(o, c) or l > min(o, c) or h < l:
        return None, f"bar[{index}] @ {ts.isoformat()}: inconsistent OHLC (h={h} l={l} o={o} c={c})"
    tv = rec.get("tick_volume", 0)
    try:
        tv_i = max(0, int(tv))
    except (TypeError, ValueError):
        tv_i = 0
    tsc = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
    return (
        BarEvent(
            timestamp=tsc,
            open=o,
            high=h,
            low=l,
            close=c,
            tick_volume=tv_i,
            spread=float(rec.get("spread", 0.0) or 0.0),
            symbol=str(rec.get("symbol", "XAUUSD")),
            timeframe=str(rec.get("timeframe", "M1")),
        ),
        "",
    )


@dataclass
class SourceValidationReport:
    """Result of validate_event_source (data quality, brief §56)."""

    total_events: int = 0
    bar_count: int = 0
    tick_count: int = 0
    data_error_count: int = 0
    out_of_order: int = 0
    duplicate_timestamps: int = 0
    gaps: list[tuple[datetime, datetime, float]] = field(default_factory=list)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.out_of_order == 0 and not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "bar_count": self.bar_count,
            "tick_count": self.tick_count,
            "data_error_count": self.data_error_count,
            "out_of_order": self.out_of_order,
            "duplicate_timestamps": self.duplicate_timestamps,
            "gap_count": len(self.gaps),
            "first_timestamp": self.first_timestamp.isoformat() if self.first_timestamp else "",
            "last_timestamp": self.last_timestamp.isoformat() if self.last_timestamp else "",
            "ok": self.ok,
            "problems": list(self.problems),
        }


def validate_event_source(
    source: HistoricalEventSource,
    *,
    gap_report_seconds: float = DEFAULT_GAP_REPORT_SECONDS,
    max_gaps_reported: int = 50,
) -> SourceValidationReport:
    """Passes over the source and reports ordering/duplicates/sanity/gaps.

    Read-only pass (the source must be re-iterable); used BEFORE a replay to
    surface data-quality facts. Gaps are informational (weekend/session
    closures), out-of-order events are errors.
    """
    report = SourceValidationReport()
    prev_ts: datetime | None = None
    for ev in source.events():
        report.total_events += 1
        if isinstance(ev, DataErrorEvent):
            report.data_error_count += 1
            continue
        ts = ev.timestamp
        if report.first_timestamp is None:
            report.first_timestamp = ts
        report.last_timestamp = ts
        if prev_ts is not None:
            if ts < prev_ts:
                report.out_of_order += 1
                if len(report.problems) < 20:
                    report.problems.append(
                        f"out-of-order at {ts.isoformat()} (previous {prev_ts.isoformat()})"
                    )
            elif ts == prev_ts:
                report.duplicate_timestamps += 1
            else:
                gap_s = (ts - prev_ts).total_seconds()
                if gap_s > gap_report_seconds:
                    report.gaps.append((prev_ts, ts, gap_s))
        if isinstance(ev, BarEvent):
            report.bar_count += 1
        elif isinstance(ev, TickEvent):
            report.tick_count += 1
        prev_ts = ts
    for g_prev, g_next, _ in report.gaps[:max_gaps_reported]:
        logger.info(
            "[EVENT_SOURCE] gap detected",
            source=source.name,
            from_ts=g_prev.isoformat(),
            to_ts=g_next.isoformat(),
        )
    return report


# ---------------------------------------------------------------------------
# Concrete sources
# ---------------------------------------------------------------------------


class BarEventSource:
    """Completed bars from an iterable of raw bar records (rates contract).

    Accepts any iterable of mappings with timestamp/open/high/low/close
    (+optional tick_volume/spread/symbol/timeframe). Malformed records
    become DataErrorEvent entries in the stream (never fabricated bars).
    """

    event_kind = EventKind.BAR

    def __init__(
        self,
        records: Iterable[dict[str, Any]],
        *,
        symbol: str = "XAUUSD",
        name: str = "bar-source",
    ) -> None:
        self.symbol = symbol
        self.name = name
        self._records = list(records)

    def events(self) -> Iterator[ReplayEvent]:
        for i, rec in enumerate(self._records):
            bar, reason = validate_bar_record(rec, i)
            if bar is None:
                ts = rec.get("timestamp") if isinstance(rec, dict) else None
                yield DataErrorEvent(
                    timestamp=ts if isinstance(ts, datetime) else None,
                    reason=reason,
                    symbol=self.symbol,
                    source_name=self.name,
                    raw_index=i,
                )
                continue
            yield bar

    def __len__(self) -> int:
        return len(self._records)


class TickEventSource:
    """Raw ticks from an iterable of raw tick records (probed MT5 contract).

    Preserves time_msc/flags/last/volume alongside bid/ask (brief §14 —
    tick replay is NOT reduced to close price). Malformed records become
    DataErrorEvent entries.
    """

    event_kind = EventKind.TICK

    def __init__(
        self,
        records: Iterable[dict[str, Any]],
        *,
        symbol: str = "XAUUSD",
        name: str = "tick-source",
    ) -> None:
        self.symbol = symbol
        self.name = name
        self._records = list(records)

    def events(self) -> Iterator[ReplayEvent]:
        for i, rec in enumerate(self._records):
            tick, reason = validate_tick_record(rec, i)
            if tick is None:
                ts = rec.get("timestamp") if isinstance(rec, dict) else None
                yield DataErrorEvent(
                    timestamp=ts if isinstance(ts, datetime) else None,
                    reason=reason,
                    symbol=self.symbol,
                    source_name=self.name,
                    raw_index=i,
                )
                continue
            yield tick

    def __len__(self) -> int:
        return len(self._records)


class ChunkedEventSource:
    """Bounded-RAM chunking over an inner record provider (brief §48-§49).

    The provider is a callable(chunk_start_ts, chunk_end_ts) -> records for
    the given half-open time window. This class slices the requested replay
    window into ``chunk_minutes``-sized windows and yields events from each
    in order. Because the STREAMING engine carries all state (features /
    strategy / positions) across chunk boundaries and only consumes events,
    chunk geometry cannot change simulation semantics — that invariant is
    test-enforced (chunk determinism test).

    The class itself holds at most ONE chunk of records in memory.
    """

    event_kind = EventKind.TICK

    def __init__(
        self,
        provider: Any,
        *,
        start: datetime,
        end: datetime,
        chunk_minutes: int = 60,
        symbol: str = "XAUUSD",
        name: str = "chunked-source",
    ) -> None:
        if end <= start:
            raise ValueError("ChunkedEventSource: end must be after start")
        if chunk_minutes <= 0:
            raise ValueError("chunk_minutes must be positive")
        self.provider = provider
        self.symbol = symbol
        self.name = name
        self._start = start if start.tzinfo else start.replace(tzinfo=UTC)
        self._end = end if end.tzinfo else end.replace(tzinfo=UTC)
        self._chunk = chunk_minutes

    def chunks(self) -> list[tuple[datetime, datetime]]:
        """The deterministic chunk plan (half-open windows)."""
        from datetime import timedelta

        plan: list[tuple[datetime, datetime]] = []
        cur = self._start
        step = timedelta(minutes=self._chunk)
        while cur < self._end:
            nxt = min(cur + step, self._end)
            plan.append((cur, nxt))
            cur = nxt
        return plan

    def events(self) -> Iterator[ReplayEvent]:
        for c_start, c_end in self.chunks():
            records = self.provider(c_start, c_end) or []
            inner = TickEventSource(
                records, symbol=self.symbol, name=f"{self.name}[{c_start.isoformat()}]"
            )
            yield from inner.events()
