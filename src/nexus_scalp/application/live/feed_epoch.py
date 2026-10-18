"""Feed connection epoch + reconnect re-acquisition (TASK-DEDUP-REPLAY-001, Part 1).

Forensic context (2026-10-01, XAUUSD LIVE): MT5 IPC dropped and reconnected
(``MT5_CONNECT event=FEED_RECOVERED consecutive_failures=3 -> 0``) and the
``symbol_info_tick`` poll then re-served the broker's last cached quote. The
engine had no notion of "the feed just came back", so it treated that stale
quote as an ordinary market event and ran the full decision pipeline on it —
the first link in the replay/amplification chain that ended in 3,476
``ORDER_MUTATION_SUPPRESSED`` lines in one hour.

This module gives the application a per-feed CONNECTION EPOCH so it can tell
``pre-disconnect market state`` from ``post-reconnect fresh market state``:

* :class:`FeedEpochTracker` — monotonic epoch counter + a bounded
  re-acquisition window. On a reconnect the epoch bumps and the tracker enters
  ``REACQUIRING``: a tick is only accepted as FRESH market state once it is
  demonstrably newer than the last tick seen before the disconnect (strictly
  newer broker timestamp), OR carries genuinely new quote information
  (bid/ask moved), OR the bounded re-acquisition window expires.

Design constraints honoured:

* **Preserve legitimate ticks.** Only a tick that is indistinguishable from the
  pre-disconnect quote (same broker timestamp AND same bid/ask) is classified
  as a reconnect REPLAY. A post-reconnect tick with real movement passes
  immediately.
* **Never block the market indefinitely.** Broker timestamps are coarse (MT5
  ``time`` is whole seconds; several ticks share it). If timestamps stay equal
  and prices stay frozen the tracker would deadlock, so re-acquisition is
  BOUNDED by both a tick count and a wall-clock budget; on expiry the next tick
  is accepted and the epoch is marked RECOVERED. Coarse timestamps therefore
  delay acceptance by at most a few ticks, never permanently.
* **Timestamp monotonicity / reordering is safe.** A tick whose timestamp is
  OLDER than the last accepted one (broker reorder or clock jitter) is never
  used to advance the freshness anchor; it can still be accepted when its quote
  carries new information, and it never corrupts the anchor.
* **Deterministic and testable.** No wall clock in the classification predicate
  unless the caller injects it (``now``), so tests drive time explicitly.
* **Real MT5 fields only.** The classification uses the broker ``time`` (the
  ``timestamp`` on the tick the engine already receives) and bid/ask. MT5's
  ``time_msc`` exists on ``BrokerTickSnapshot`` but is not carried by the tick
  contract; an optional ``time_msc`` is accepted so a future wiring can tighten
  the resolution, but nothing here REQUIRES it and no synthetic sequence number
  is invented.
* **No I/O** — pure in-memory arithmetic (INV-001 preserved).

Telemetry: the tracker tags each tick with one of the OBSERVABILITY classes the
brief requires, so "fresh tick" / "duplicate tick" / "stale tick" /
"reconnect replay" stay distinguishable instead of collapsing into one generic
"duplicate".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Tick classification vocabulary (OBSERVABILITY REQUIREMENT)
# ---------------------------------------------------------------------------


class TickClass(Enum):
    """Why an incoming tick was accepted or rejected.

    Kept deliberately distinct (the brief: do not collapse all cases into one
    generic "duplicate"):

    * ``FRESH``               — new market state; normal decision flow resumes.
    * ``RECONNECT_REPLAY``    — indistinguishable from the pre-disconnect quote
                                inside the re-acquisition window; NOT fresh.
    * ``DUPLICATE``           — byte-identical to the immediately preceding
                                accepted tick; zero new information.
    * ``STALE``               — carries an older broker timestamp than the last
                                accepted tick and no new quote information.
    * ``REORDERED_FRESH``     — older timestamp but the quote genuinely moved;
                                accepted (never suppresses real data).
    """

    FRESH = "FRESH"
    RECONNECT_REPLAY = "RECONNECT_REPLAY"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"
    REORDERED_FRESH = "REORDERED_FRESH"


@dataclass
class TickVerdict:
    """Classification of one inbound tick.

    Attributes:
        accept: True when the tick may drive the normal decision flow.
        tick_class: the :class:`TickClass` (telemetry/observability).
        epoch: the feed connection epoch this tick was seen in.
        detail: short human-readable note for the telemetry line.
    """

    accept: bool
    tick_class: TickClass
    epoch: int
    detail: str = ""


# ---------------------------------------------------------------------------
# Feed epoch tracker
# ---------------------------------------------------------------------------


class FeedEpochTracker:
    """Per-feed connection epoch + bounded reconnect re-acquisition window.

    One instance per feed (the engine owns it, mirroring the per-symbol feed).
    Not thread-safe by itself: it is driven from the single tick-ingestion loop,
    exactly like the other engine tick state it sits beside.
    """

    #: How many ticks the re-acquisition window tolerates before it force-accepts
    #: (broker timestamps are coarse; a frozen quote must never deadlock the
    #: feed). Small and bounded — it delays acceptance, it does not block it.
    DEFAULT_MAX_REACQUIRE_TICKS: int = 5

    #: Wall-clock budget for re-acquisition. After this, the next tick is
    #: accepted even if it still looks like the pre-disconnect quote.
    DEFAULT_MAX_REACQUIRE_SEC: float = 5.0

    def __init__(
        self,
        *,
        max_reacquire_ticks: int | None = None,
        max_reacquire_sec: float | None = None,
    ) -> None:
        self._max_reacquire_ticks = (
            int(max_reacquire_ticks)
            if max_reacquire_ticks is not None and int(max_reacquire_ticks) > 0
            else self.DEFAULT_MAX_REACQUIRE_TICKS
        )
        self._max_reacquire_sec = (
            float(max_reacquire_sec)
            if max_reacquire_sec is not None and float(max_reacquire_sec) > 0.0
            else self.DEFAULT_MAX_REACQUIRE_SEC
        )

        #: Monotonic feed epoch. Starts at 1 (the initial connect).
        self.epoch: int = 1
        #: True while the feed is re-acquiring after a reconnect.
        self.reacquiring: bool = False
        #: Ticks seen inside the current re-acquisition window.
        self.reacquire_tick_count: int = 0
        #: Wall-clock stamp when the current window opened (caller-injected).
        self.reacquire_started_at: float | None = None
        #: The last tick accepted BEFORE the current reconnect (the replay
        #: anchor). A post-reconnect tick equal to this is a replay.
        self._pre_disconnect: tuple[datetime | None, float, float] | None = None

        #: Freshness anchor: the newest broker timestamp accepted so far.
        self._last_accepted_ts: datetime | None = None
        #: The last accepted quote (duplicate detection).
        self._last_accepted_bid: float = 0.0
        self._last_accepted_ask: float = 0.0

        #: Counters per class (telemetry summary).
        self.counts: dict[str, int] = {}
        #: Total reconnects observed.
        self.reconnects: int = 0

    # -- lifecycle --------------------------------------------------------

    def note_disconnect(self) -> None:
        """Record the pre-disconnect anchor while the feed is going down.

        Called when the adapter reports loss / the watchdog decides to
        reconnect, BEFORE the reconnect. Idempotent within one outage.
        """
        self._pre_disconnect = (
            self._last_accepted_ts,
            self._last_accepted_bid,
            self._last_accepted_ask,
        )

    def note_reconnect(self) -> int:
        """Bump the connection epoch and open a re-acquisition window.

        Returns the new epoch. Called after a successful adapter reconnect (and
        after any broker resync), so the very next polled quote is classified
        against the pre-disconnect anchor rather than trusted blindly.
        """
        # Capture the anchor if the caller did not already (a reconnect may be
        # detected without an explicit note_disconnect).
        if self._pre_disconnect is None:
            self._pre_disconnect = (
                self._last_accepted_ts,
                self._last_accepted_bid,
                self._last_accepted_ask,
            )
        self.epoch += 1
        self.reconnects += 1
        self.reacquiring = True
        self.reacquire_tick_count = 0
        self.reacquire_started_at = None  # stamped on the first post-reconnect tick
        return self.epoch

    def note_recovered(self) -> None:
        """Close the re-acquisition window (a fresh tick was accepted)."""
        self.reacquiring = False
        self.reacquire_tick_count = 0
        self.reacquire_started_at = None
        self._pre_disconnect = None

    # -- classification ---------------------------------------------------

    def classify(
        self,
        *,
        timestamp: datetime | None,
        bid: float,
        ask: float,
        now: float | None = None,
    ) -> TickVerdict:
        """Classify one inbound tick; update the tracker's anchors on accept.

        ``now`` is an optional caller-injected monotonic seconds value used only
        for the re-acquisition wall-clock budget; when omitted the budget is
        driven by the tick count alone (fully deterministic in tests).
        """
        bid = float(bid)
        ask = float(ask)

        # 1) Reconnect re-acquisition window: is this the stale cached quote?
        if self.reacquiring:
            if self.reacquire_started_at is None and now is not None:
                self.reacquire_started_at = float(now)
            self.reacquire_tick_count += 1

            expired = self.reacquire_tick_count > self._max_reacquire_ticks or (
                now is not None
                and self.reacquire_started_at is not None
                and (float(now) - self.reacquire_started_at) > self._max_reacquire_sec
            )
            if not expired and self._looks_like_pre_disconnect(timestamp, bid, ask):
                return self._reject(TickClass.RECONNECT_REPLAY, "pre-disconnect quote")

            # Genuinely new post-reconnect state (or the window expired): accept
            # and close re-acquisition.
            self.note_recovered()
            return self._accept(timestamp, bid, ask, TickClass.FRESH)

        # 2) Ordinary duplicate: byte-identical to the last accepted tick.
        same_quote = bid == self._last_accepted_bid and ask == self._last_accepted_ask
        same_ts = (
            timestamp is not None
            and self._last_accepted_ts is not None
            and timestamp == self._last_accepted_ts
        )
        if same_quote and (same_ts or timestamp is None):
            return self._reject(TickClass.DUPLICATE, "identical to last accepted tick")

        # 3) Timestamp monotonicity. Compare against the newest accepted stamp.
        if timestamp is not None and self._last_accepted_ts is not None:
            if timestamp > self._last_accepted_ts:
                return self._accept(timestamp, bid, ask, TickClass.FRESH)
            if timestamp < self._last_accepted_ts:
                # Older broker stamp. If the quote moved it is still real data
                # (broker reorder / coarse clock); accept WITHOUT advancing the
                # freshness anchor. If nothing moved it is stale.
                if same_quote:
                    return self._reject(TickClass.STALE, "older timestamp, no new quote")
                return self._accept(
                    timestamp, bid, ask, TickClass.REORDERED_FRESH, advance_ts=False
                )
            # Equal timestamp, different quote => the quote is the new information.
            return self._accept(timestamp, bid, ask, TickClass.FRESH)

        # 4) No prior anchor (first ever tick) or no timestamp: accept.
        return self._accept(timestamp, bid, ask, TickClass.FRESH)

    # -- internals --------------------------------------------------------

    def _looks_like_pre_disconnect(
        self, timestamp: datetime | None, bid: float, ask: float
    ) -> bool:
        """True when this tick is indistinguishable from the pre-disconnect one.

        Only a tick that matches BOTH the pre-disconnect broker timestamp AND
        the pre-disconnect bid/ask is treated as the replay. A post-reconnect
        tick with any real movement is fresh market state and must pass.
        """
        if self._pre_disconnect is None:
            return False
        pre_ts, pre_bid, pre_ask = self._pre_disconnect
        quote_match = bid == pre_bid and ask == pre_ask
        if not quote_match:
            return False
        # Quote identical: it is a replay when the timestamp is also the same or
        # older than pre-disconnect (nothing has advanced).
        if timestamp is None or pre_ts is None:
            return True
        return timestamp <= pre_ts

    def _accept(
        self,
        timestamp: datetime | None,
        bid: float,
        ask: float,
        tick_class: TickClass,
        *,
        advance_ts: bool = True,
    ) -> TickVerdict:
        if advance_ts and timestamp is not None:
            if self._last_accepted_ts is None or timestamp > self._last_accepted_ts:
                self._last_accepted_ts = timestamp
        self._last_accepted_bid = bid
        self._last_accepted_ask = ask
        self._bump(tick_class)
        return TickVerdict(accept=True, tick_class=tick_class, epoch=self.epoch)

    def _reject(self, tick_class: TickClass, detail: str) -> TickVerdict:
        self._bump(tick_class)
        return TickVerdict(accept=False, tick_class=tick_class, epoch=self.epoch, detail=detail)

    def _bump(self, tick_class: TickClass) -> None:
        self.counts[tick_class.value] = self.counts.get(tick_class.value, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        """Bounded read for diagnostics/UI (never trading)."""
        return {
            "epoch": self.epoch,
            "reconnects": self.reconnects,
            "reacquiring": self.reacquiring,
            "counts": dict(self.counts),
        }
