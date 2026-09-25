"""Session spread-percentile SKETCH — the off-loop read surface of C3 gate (b).

BUG-292 (perf-wave R6, NSE-Swarm role 2, 2026-09-15)
----------------------------------------------------
``docs/audit/wave_20260914/10_performance.md`` §11 risk 6 found that the C3 (b)
session spread gate ran a synchronous SQLite connect + a same-day scan of
``audit_paper_executions`` **inside** policy evaluation — on the event-loop
thread, once per spread-positive candidate
(``LiveEngine._session_spread_percentile_provider``). The finding is
explicitly "documented, bounded, but still loop-thread I/O + per-call
connect", and the prescribed fix is:

    "maintain the same-day spread-percentile sketch incrementally off-loop
     (audit worker or maintenance tick), expose RAM read; refresh cadence
     <= 60 s."

That is exactly this module. The policy's injected callable becomes a pure
in-memory read (INV-001: no connection, no query, no lock); the only I/O is
:meth:`SpreadSessionSketch.refresh`, driven off-loop by the maintenance
cycle on a <= 60 s throttle.

SEMANTIC PARITY IS THE CONTRACT
-------------------------------
Moving the read must not change the gate. ``percentile()`` reproduces
``broker_history.session_spread_percentile`` element for element:

* FILLED rows with ``spread IS NOT NULL AND spread > 0`` only (quote-less
  defensive rows would drag the percentile toward zero and fail open);
* exact ``symbol`` match;
* ``ts`` bounds compared as ISO strings, exactly as SQLite compares TEXT —
  so the same rows (including the microsecond-boundary shape) qualify;
* nearest-rank linear interpolation (numpy ``linear`` semantics) — shared
  with the SQL path through :func:`interpolated_percentile`, so the two
  formulas can never drift;
* honest ``None`` below ``min_samples`` — the caller must treat None as a
  NO-OP gate and never substitute 0.0.

The sketch deliberately holds the **whole current UTC day** instead of the
4 h window: one day of paper fills is a few hundred rows, and a superset
makes the read-side window arithmetic skew-proof (the refresh uses the host
clock, the read uses the TICK clock — see the clock-domain note below).

CLOCK-DOMAIN NOTE (BUG-259/261/268/275 class, honored)
------------------------------------------------------
Refresh cadence is wall/monotonic time (it runs off-loop); every *decision*
input (the window bounds, the upper bound) comes from the ``now_utc`` the
POLICY passes, which is tick-domain. Nothing here compares a tick stamp to a
host stamp to make a trading decision, so broker/host skew can never widen
or freeze the distribution. The one wall-clock use is a failure alarm
throttle (observability only).

Durability: the sketch rebuilds from the durable ``audit_paper_executions``
copy — the same table the SQL provider read — so a restart rehydrates the
session distribution rather than starting empty.

Import rules: stdlib + the app logger only. No torch/polars, no
``application`` imports (this must stay importable from the maintenance seam
without pulling the serving chain).
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.adapters.database.broker_history import interpolated_percentile
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.spread_sketch")

__all__ = ["SpreadSessionSketch", "interpolated_percentile"]

#: Hard ceiling on resident samples (newest win). A paper session produces far
#: fewer fills per day than this; the cap exists so a pathological export
#: burst can never turn the sketch into the unbounded-growth class that
#: BUG-290/291 just closed.
#:
#: PARITY ENVELOPE (measured, not assumed): while the trailing window holds
#: <= MAX_SAMPLES fills, the sketch's read equals the SQL provider's read to
#: the last bit (pinned across 20 percentile x reference-instant combinations).
#: Beyond the cap the newest samples win, so the percentile is computed over
#: the most recent MAX_SAMPLES fills of the window instead of all of them —
#: a documented, bounded deviation under a saturation that requires > 5000
#: fills inside one 4 h window (~21/minute sustained). That condition is made
#: LOUD rather than silent: :attr:`truncated_reads` counts every read whose
#: window reached past the oldest resident sample (pinned + surfaced).
MAX_SAMPLES = 5000

#: Rows fetched per refresh QUERY. Equal to ``MAX_SAMPLES`` so a cold rebuild
#: always lands the FULL resident set the sketch is allowed to hold: the read
#: window (trailing N hours, same UTC day) is a subset of the newest
#: ``MAX_SAMPLES`` rows of the day whenever the window itself holds no more
#: than that — which is the exact, tested parity envelope vs the SQL provider.
#: A backlog wider than the cap is recorded (``refresh_truncated``) instead of
#: silently re-shaped.
REFRESH_BATCH_LIMIT = MAX_SAMPLES

#: Incremental drain: at most this many queries per refresh pass. 4 x 5000 rows
#: per 60 s pass is orders of magnitude above any real fill rate; when the
#: backlog still has more, the pass stops bounded and the NEXT pass continues
#: (never an unbounded read on a worker thread).
REFRESH_MAX_QUERIES_PER_PASS = 4

#: Cold-rebuild LOOK-BACK margin (seconds) beyond the window bound.
#:
#: The refresh runs on HOST wall time; the read runs on TICK time (the policy
#: passes ``current_tick.timestamp`` — BUG-259/261/268 clock discipline).
#: Broker-behind-host skew or a late/replayed tick means a read may legitimately
#: ask about an instant a few seconds OLDER than the last refresh. Without a
#: margin its window would start before the oldest resident row and silently
#: miss the fills in between. Reading 5 extra minutes of history makes the
#: resident set a strict SUPERSET of every read window inside the skew budget
#: (bounded by ``max_age_sec`` too), which is what makes "RAM == SQL" a
#: theorem instead of a hope.
COLD_REBUILD_LOOKBACK_SEC: float = 300.0

#: Consecutive refresh failures after which the read surface degrades to the
#: honest unknown (gate no-op) AND one loud alarm fires. A frozen sketch must
#: never defend a stale distribution indefinitely — but "no new fills" is
#: normal, so only genuine faults trip it.
MAX_REFRESH_FAILURE_STREAK = 3

#: Alarm throttle for the refresh-failure CRITICAL (observability only).
_FAILURE_ALARM_INTERVAL_SEC = 600.0


class SpreadSessionSketch:
    """Bounded, incremental, in-process session spread distribution.

    Thread model: :meth:`refresh` runs on a worker thread
    (``asyncio.to_thread`` from the maintenance cycle); :meth:`percentile`
    runs on the event-loop thread inside policy evaluation. Mutations build a
    NEW tuple of samples and swap it in as one atomic reference assignment,
    and readers snapshot that reference first — so the tick path can never
    observe a half-built distribution and never takes a lock (INV-001).
    """

    # NOTE: RUF023 wants slots sorted; the grouping comments below are dropped
    # in favour of that gate (semantics documented in the class body).
    __slots__ = (
        "_failure_streak",
        "_last_failure_alarm",
        "_last_success_monotonic",
        "_pending_watermark",
        "_samples",
        "_watermark_rowid",
        "_window_start_day",
        "cold_rebuilds",
        "dropped_by_cap",
        "dropped_stale_day",
        "max_age_sec",
        "min_samples",
        "pass_through_notes",
        "refresh_backlog_truncated",
        "refresh_failures",
        "refreshes",
        "stale_degradations",
        "truncated_reads",
        "window_hours",
    )

    def __init__(
        self,
        *,
        window_hours: float = 4.0,
        min_samples: int = 5,
        max_age_sec: float = 180.0,
    ) -> None:
        if window_hours <= 0.0:
            raise ValueError(f"window_hours must be positive, got {window_hours!r}")
        if min_samples < 1:
            raise ValueError(f"min_samples must be >= 1, got {min_samples!r}")
        self.window_hours = float(window_hours)
        self.min_samples = int(min_samples)
        #: A sketch whose last successful refresh is older than this is an
        #: HONEST UNKNOWN again (the maintenance stage died -> stop gating on
        #: a frozen distribution instead of lying about it).
        self.max_age_sec = float(max_age_sec)
        self._samples: tuple[tuple[str, str, float], ...] = ()
        self._watermark_rowid: int | None = None
        self._window_start_day: str = ""  # UTC day the resident set was read for
        self.refreshes = 0
        self.refresh_failures = 0
        self.cold_rebuilds = 0
        self.dropped_by_cap = 0
        self.dropped_stale_day = 0
        self.pass_through_notes = 0
        self.stale_degradations = 0
        #: Reads whose window reached PAST the oldest resident sample (i.e. the
        #: MAX_SAMPLES cap clipped the distribution the SQL provider would have
        #: scanned). The honest signal that the parity envelope is exceeded.
        self.truncated_reads = 0
        #: Refresh passes that ended on a full batch (backlog may remain). The
        #: next pass continues from the watermark; never an unbounded read.
        self.refresh_backlog_truncated = 0
        self._failure_streak = 0
        self._pending_watermark: int | None = None
        # None = "never refreshed successfully", so the staleness guard below
        # treats it as "nothing resident yet" (an empty read -> None) instead
        # of a 0.0-vs-monotonic comparison that silently passes on a young
        # host (BUG-273 class).
        self._last_success_monotonic: float | None = None
        # None = "never alarmed", so the FIRST failure is always due (the
        # 0.0-vs-monotonic shape silently suppresses it on a young host).
        self._last_failure_alarm: float | None = None

    # ------------------------------------------------------------------
    # mutation surface (worker threads only — never the tick path)
    # ------------------------------------------------------------------
    def refresh(self, conn: sqlite3.Connection, *, now_utc: datetime | None = None) -> int:
        """ONE bounded read of the durable copy; returns the resident count.

        Cold start / new UTC day: rebuild the day's FILLED spread rows
        (newest-first under the batch cap). Steady state: absorb only rowids
        above the watermark. Never raises — a refresh fault keeps the
        previous distribution and lets the staleness guard degrade the gate
        to a no-op if the faults persist.
        """
        now = _aware_utc(now_utc) if now_utc is not None else datetime.now(UTC)
        day = now.strftime("%Y-%m-%d")
        try:
            if not _durable_table_present(conn):
                # This database has no paper-execution copy at all (a LIVE
                # install that never exported a PAPER ledger). That is HONEST
                # ABSENCE, not a refresh fault: count the pass, keep the read
                # surface empty (-> gate no-op), never alarm.
                self.refreshes += 1
                self._failure_streak = 0
                self._last_success_monotonic = time.monotonic()
                return 0
            if self._watermark_rowid is None or self._window_start_day != day:
                # Cold rebuild reads the SAME bound the SQL provider uses (the
                # same-UTC-day-clamped trailing window) plus a look-back
                # margin, so every later read window is a SUBSET of what is
                # resident — see COLD_REBUILD_LOOKBACK_SEC.
                cold_bound = (
                    _window_start(now, self.window_hours)
                    - timedelta(seconds=COLD_REBUILD_LOOKBACK_SEC)
                ).isoformat()
                rows = conn.execute(
                    """
                    SELECT rowid, ts, symbol, spread
                    FROM audit_paper_executions
                    WHERE ts >= ?
                      AND status = 'FILLED'
                      AND spread IS NOT NULL
                      AND spread > 0.0
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (cold_bound, REFRESH_BATCH_LIMIT),
                ).fetchall()
                self.replace_from_rows(list(rows), day=day)
                self.cold_rebuilds += 1
            else:
                # Steady state: drain the backlog above the watermark, bounded
                # per pass (a multi-hour outage of the maintenance stage must
                # not turn one refresh into an unbounded read).
                for _ in range(REFRESH_MAX_QUERIES_PER_PASS):
                    rows = conn.execute(
                        """
                        SELECT rowid, ts, symbol, spread
                        FROM audit_paper_executions
                        WHERE rowid > ?
                          AND status = 'FILLED'
                          AND spread IS NOT NULL
                          AND spread > 0.0
                        ORDER BY rowid ASC
                        LIMIT ?
                        """,
                        (self._watermark_rowid, REFRESH_BATCH_LIMIT),
                    ).fetchall()
                    self.add_rows(list(rows), day=day)
                    if len(rows) < REFRESH_BATCH_LIMIT or self._watermark_rowid is None:
                        # Watermark None means add_rows hit a day rollover and
                        # reset: the NEXT pass cold-rebuilds, this one stops.
                        break
                    self.refresh_backlog_truncated += 1
            self.refreshes += 1
            self._failure_streak = 0
            self._last_success_monotonic = time.monotonic()
            return len(self._samples)
        except Exception as exc:  # never propagate into the maintenance cycle
            self.refreshes += 1
            self.refresh_failures += 1
            self._failure_streak += 1
            self._alarm_refresh_failure(exc)
            return len(self._samples)

    def replace_from_rows(self, rows: Iterable[Any], *, day: str | None = None) -> int:
        """Cold rebuild from ``(rowid, ts, symbol, spread)`` rows.

        Every fetched row stays resident (the set is capped in ``_publish``):
        the read applies the exact same-day + trailing-window bounds itself, so
        holding the look-back margin costs nothing and makes a skew-shifted
        read (a tick a few seconds behind the refresh, or one just before UTC
        midnight) able to reproduce the SQL row set instead of silently
        degrading to a thin sample.
        """
        parsed = self._parse(rows)
        reference_day = day or datetime.now(UTC).strftime("%Y-%m-%d")
        self._publish(parsed, day=reference_day, watermark=True)
        return len(self._samples)

    def add_rows(self, rows: Iterable[Any], *, day: str | None = None) -> int:
        """Absorb newer-than-watermark rows without a re-read of the day."""
        parsed = self._parse(rows)
        if not parsed:
            return 0
        now = datetime.now(UTC)
        current_day = day or now.strftime("%Y-%m-%d")
        if self._window_start_day and self._window_start_day != current_day[:10]:
            # The day rolled over between passes: a merge would keep yesterday
            # resident while today's rows are missing. Drop everything and let
            # the next refresh cold-rebuild; until then the read is thin and
            # honestly returns None (never a mixed-day distribution).
            self.dropped_stale_day += len(self._samples)
            self._samples = ()
            self._watermark_rowid = None
            self._window_start_day = ""
            return 0
        samples = list(self._samples)
        samples.extend(parsed)
        self._publish(samples, day=current_day, watermark=True)
        return len(parsed)

    def note_rows(self, rows: Iterable[Any]) -> int:
        """Freshness accelerator: absorb already-known ``(rowid, ts, symbol,
        spread)`` rows without waiting for the next refresh pass.

        NOT wired by default, on purpose. The watermark discipline means a
        row fed here must ALSO have its rowid recorded, or the following
        refresh re-reads it from the durable copy and the sample lands twice
        (double weight = a silently skewed percentile). Callers that use this
        must pass real rowids and accept that the refresh watermark follows
        them; the maintenance path simply refreshes (:meth:`refresh`), which
        is the cadence the perf finding asked for (<= 60 s).
        """
        parsed = self._parse(rows)
        if not parsed:
            return 0
        samples = list(self._samples)
        samples.extend(parsed)
        self._publish(samples, day=self._window_start_day or parsed[0][0][:10], watermark=True)
        self.pass_through_notes += len(parsed)
        return len(parsed)

    # ------------------------------------------------------------------
    # read surface (event loop / tick path — ZERO I/O, ZERO locks)
    # ------------------------------------------------------------------
    def percentile(
        self,
        symbol: str,
        now_utc: datetime,
        percentile: float,
        *,
        window_hours: float | None = None,
        min_samples: int | None = None,
    ) -> float | None:
        """Session percentile straight out of RAM.

        Signature-parity with :func:`broker_history.session_spread_percentile`
        minus the connection argument. Returns ``None`` (honest unknown -> the
        policy no-ops the gate) when the resident sample is thin or when the
        off-loop refresh has stalled.
        """
        if not 50.0 <= float(percentile) <= 99.0:
            raise ValueError(f"percentile out of contract range 50..99: {percentile!r}")
        hours = self.window_hours if window_hours is None else float(window_hours)
        floor = self.min_samples if min_samples is None else int(min_samples)
        if self._degraded():
            return None
        # Deliberately NO tz normalization here: the window bounds are built
        # from the caller's own datetime and compared as ISO STRINGS, exactly
        # the way SQLite compares the stored TEXT stamps. Same bounds, same
        # comparison, same row set as the SQL provider (parity contract), and
        # the "now" stays TICK-domain (BUG-259/261/268 clock discipline).
        cutoff = _window_start(now_utc, hours).isoformat()
        upper = now_utc.isoformat()
        target = str(symbol)
        samples = self._samples  # atomic reference snapshot
        if samples and len(samples) >= MAX_SAMPLES and samples[0][0] > cutoff:
            # PARITY-ENVELOPE BREACH (documented, loud): the resident set is
            # capped and the requested window still reaches past the oldest
            # resident sample, so the SQL provider would have scanned rows this
            # read cannot see. Percentile = the most recent MAX_SAMPLES fills of
            # the window. Counted + surfaced on the debug snapshot, never hidden
            # (the same lesson BUG-285 taught about write-only states).
            self.truncated_reads += 1
        values = sorted(
            spread for ts, sym, spread in samples if sym == target and cutoff <= ts <= upper
        )
        if len(values) < floor:
            return None
        return interpolated_percentile(values, float(percentile))

    def snapshot_debug(self) -> dict[str, Any]:
        """Operator-facing state of the seam (observability only)."""
        return {
            "session_day": self._window_start_day or None,
            "samples": len(self._samples),
            "watermark_rowid": self._watermark_rowid,
            "refreshes": self.refreshes,
            "refresh_failures": self.refresh_failures,
            "failure_streak": self._failure_streak,
            "cold_rebuilds": self.cold_rebuilds,
            "dropped_by_cap": self.dropped_by_cap,
            "dropped_stale_day": self.dropped_stale_day,
            "pass_through_notes": self.pass_through_notes,
            "stale_degradations": self.stale_degradations,
            "truncated_reads": self.truncated_reads,
            "refresh_backlog_truncated": self.refresh_backlog_truncated,
            "max_samples": MAX_SAMPLES,
            "max_age_sec": self.max_age_sec,
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _degraded(self) -> bool:
        """True when the resident set must not be trusted for a decision."""
        if self._failure_streak >= MAX_REFRESH_FAILURE_STREAK:
            self.stale_degradations += 1
            return True
        last = getattr(self, "_last_success_monotonic", None)
        if last is None:
            return False  # never refreshed -> empty anyway -> thin-sample None below
        if (time.monotonic() - float(last)) > self.max_age_sec:
            self.stale_degradations += 1
            return True
        return False

    def _parse(self, rows: Iterable[Any]) -> list[tuple[str, str, float]]:
        out: list[tuple[str, str, float]] = []
        max_rowid = self._watermark_rowid
        for row in rows or []:
            try:
                rowid = int(row[0])
                ts = str(row[1])
                symbol = str(row[2])
                spread = _as_float(row[3])
            except (TypeError, ValueError, IndexError):
                continue
            if spread is None or spread <= 0.0 or not ts:
                continue
            out.append((ts, symbol, spread))
            if max_rowid is None or rowid > max_rowid:
                max_rowid = rowid
        self._pending_watermark = max_rowid
        return out

    def _publish(self, samples: list[tuple[str, str, float]], *, day: str, watermark: bool) -> None:
        ordered = sorted(samples, key=lambda item: item[0])
        if len(ordered) > MAX_SAMPLES:
            dropped = len(ordered) - MAX_SAMPLES
            ordered = ordered[dropped:]  # newest win
            self.dropped_by_cap += dropped
        self._samples = tuple(ordered)
        self._window_start_day = day[:10]
        if watermark:
            pending = getattr(self, "_pending_watermark", None)
            if pending is not None:
                self._watermark_rowid = pending

    def _alarm_refresh_failure(self, exc: BaseException) -> None:
        """Rate-limited CRITICAL: the off-loop refresh is dead, so the C3 (b)
        gate is silently degrading to a no-op on every evaluation."""
        now = time.monotonic()
        if (
            self._last_failure_alarm is not None
            and now - self._last_failure_alarm < _FAILURE_ALARM_INTERVAL_SEC
        ):
            return
        self._last_failure_alarm = now
        logger.error(
            "[SPREAD_GATE] event=SKETCH_REFRESH_DEAD streak=%s error=%r "
            "(C3 gate (b) degrades to no-op until the maintenance refresh recovers)",
            self._failure_streak,
            exc,
        )


# ---------------------------------------------------------------------------
# helpers (module-level so tests can pin them directly)
# ---------------------------------------------------------------------------


def _window_start(now_utc: datetime, window_hours: float) -> datetime:
    """Same-UTC-day trailing window start (mirrors broker_history exactly)."""
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(day_start, now_utc - timedelta(hours=float(window_hours)))


def _aware_utc(when: datetime) -> datetime:
    """Normalize to aware UTC once, so naive/aware comparison can never raise
    and ISO string bounds stay comparable with the stored TEXT stamps."""
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _durable_table_present(conn: sqlite3.Connection) -> bool:
    """Does THIS database carry the durable paper-execution copy?

    The table is created lazily by the parity exporter
    (``create_paper_executions_table``), so a LIVE-only install legitimately
    has none. Probing sqlite_master keeps "no substrate" distinct from "the
    refresh is broken" — the first is an honest unknown, the second alarms.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit_paper_executions' "
        "LIMIT 1"
    ).fetchone()
    return row is not None
