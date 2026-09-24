"""
Incremental Candle Bar Aggregator
=================================
Aggregates tick streams into complete OHLC candle bars across timeframes.
Guarantees explicit separation between Completed Bars and Forming Bars.
"""

import math
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, field_validator

from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.market_data.bar_aggregator")


class BarData(BaseModel):
    """
    Immutable representation of an OHLC Bar.

    BUG-285 (wave 2026-09-14, input-validation lane L11-1/L11-2): the bar is
    the canonical price-truth object consumed by features, replay, training
    and the UI. NaN/inf prices previously passed construction silently
    (lane-11 probe: a NaN forming-bar seed flowed into the canonical series).
    Corrupted price is MUST-FAIL-CLOSED: non-finite values are refused at the
    model. OHLC *geometry* and plausibility (non-positive / scale-implausible,
    the 2026-09-02 EUR-on-XAUUSD contamination class) are enforced at the
    broker-boundary readers (mt5_adapter.get_historical_bars drops malformed
    rows loudly), because only those sites know the symbol's price scale.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    is_complete: bool

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _finite_price(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError(f"non-finite price: {v}")
        return v


class BarAggregator:
    """
    Maintains active forming bars and yields completed bars upon timeframe boundary crossing.

    MEMORY BOUND (H-05, MT5-PARITY-FORENSICS lane H): the completed-bar series
    is a bounded ring, not an unbounded list. Every completed M1 bar used to be
    appended to ``_completed_bars`` and never released, so a long live session
    grew it linearly (one bar/minute ≈ 1440/day ≈ 525k/year) *and* paid an O(n)
    full copy on every ``get_completed_bars()`` call — the hot path reads it on
    every tick. The bound is set from the largest legitimate consumer window:

    * the liquidity governor runs on the FULL window by deliberate contract
      (``features/liquidity_runtime.py:537-548`` — a cap was probed and
      REJECTED because old daily pools fell outside it, 0/594 parity loss;
      its lifecycle loop is vectorized instead), and
    * ``_resync_from_broker`` (``live_engine.py:2916-2921``) reseeds with up to
      ``chart_count = 20000`` broker M1 bars (~14 days), and
    * ``HTF_HISTORY_BARS = 4000`` (``features/scalp_features.py:50``) is the
      shared train==live HTF contract the dataset builder mirrors.

    ``COMPLETED_BARS_MAXLEN = 20000`` therefore admits the largest legitimate
    reseed whole and keeps every consumer's semantics identical; the live tick
    path additionally trims to 4000 (``tick_pipeline.py:463-464``), so in steady
    state the ring sits far below the cap. A reseed that exceeds the cap keeps
    the NEWEST bars (the causal tail every consumer reads) and counts the
    dropped prefix in ``dropped_completed_bars``. Nothing is dropped under any
    observed production path; the counter exists so the API can never silently
    under-report history (see ``dropped_completed_bars`` below).

    Market-data integrity contract (Agent-13, 2026-09-09):
      * Symbol identity: a tick whose ``symbol`` differs from the aggregator's
        own symbol is rejected (ValueError, fail closed) — a foreign-symbol
        quote must never be able to mint or mutate a bar of this instrument.
      * Tick timestamp monotonicity: a tick OLDER than the last ACCEPTED tick
        is dropped (returns ``None``) — an out-of-order / replayed quote must
        never mutate a bar that the market has already moved past.

    Deliberately NO future-stamp guard: a large POSITIVE timestamp jump is a
    legitimate market gap (feed outage, session break, weekend) and MT5
    itself seals the previous bar on the next quote in that case. Clock-
    offset-scale timebase defects are handled fail-closed at the freshness
    layer (LiveFreshnessService.stage_freshness reports a future stamp as
    STALE) and the G29 freshness gate downgrades proposals while the skew
    persists.

    Dropped ticks never touch any bar state; they carry zero bar information.
    """

    #: H-05: hard ceiling on the retained completed-bar series (see the class
    #: MEMORY BOUND docstring for the sizing derivation). Chosen as the largest
    #: legitimate consumer window: ``_resync_from_broker`` reseeds with up to
    #: 20000 broker M1 bars, and the liquidity governor deliberately runs on the
    #: FULL window (a cap was probed and rejected for losing 0/594 parity).
    COMPLETED_BARS_MAXLEN: int = 20000

    def __init__(self, symbol: str, timeframe_minutes: int = 1) -> None:
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.timeframe_str = f"M{timeframe_minutes}"
        self._current_bar_time: datetime | None = None
        self._open: float = 0.0
        self._high: float = 0.0
        self._low: float = 0.0
        self._close: float = 0.0
        self._volume: int = 0
        self._completed_bars: list[BarData] = []
        #: H-05: monotonic count of completed bars evicted from the head of the
        #: ring to keep it at ``COMPLETED_BARS_MAXLEN``. Zero on every observed
        #: production path (the cap admits the 20000-bar broker reseed whole);
        #: exposed so a consumer that assumes complete history can detect that
        #: the retained window is a tail and not the whole series.
        self.dropped_completed_bars: int = 0
        # Monotonic stamp of the last ACCEPTED tick (UTC-aware). None until
        # the first valid tick arrives; rebased by reseed() from broker bars.
        self._last_accepted_ts: datetime | None = None

    def process_tick(self, tick: TickData) -> BarData | None:
        """
        Processes incoming tick and returns a completed BarData object if a boundary crossed.

        Args:
            tick: New incoming tick.

        Returns:
            Optional[BarData]: Completed bar if period closed, else None.
        """
        # ------------------------------------------------------------------
        # INTEGRITY GUARD 1 — symbol identity (fail closed).
        # A foreign-symbol tick must never contribute to this instrument's
        # bars (wrong-price contamination at 1e4 price-scale distance).
        # ------------------------------------------------------------------
        if str(tick.symbol) != self.symbol:
            raise ValueError(
                f"BarAggregator[{self.symbol}]: tick symbol mismatch "
                f"(got '{tick.symbol}'); bar state left untouched"
            )

        tick_ts = tick.timestamp
        if tick_ts.tzinfo is None:  # defensive: TickData already normalizes
            from datetime import UTC as _UTC

            tick_ts = tick_ts.replace(tzinfo=_UTC)

        # ------------------------------------------------------------------
        # INTEGRITY GUARD — out-of-order / replayed tick (drop, no raise):
        # the market has already built state past this timestamp; mutating
        # the current bar with it would inject a stale price. The lower
        # bound is the monotonic MARKET clock (last accepted tick).
        #
        # Deliberately NO future-stamp guard here: a large POSITIVE jump is
        # a legitimate market gap (feed outage, session break, weekend) and
        # MT5 itself seals the previous bar on the next quote in that case.
        # Clock-offset-scale timebase defects are handled fail-closed at the
        # freshness layer (LiveFreshnessService.stage_freshness now reports
        # a future stamp as STALE) and the G29 freshness gate downgrades
        # proposals to NO_TRADE while the skew persists.
        # ------------------------------------------------------------------
        if self._last_accepted_ts is not None and tick_ts < self._last_accepted_ts:
            logger.warning(
                "Out-of-order tick dropped",
                symbol=self.symbol,
                timeframe=self.timeframe_str,
                tick_ts=tick_ts.isoformat(),
                last_accepted=self._last_accepted_ts.isoformat(),
            )
            return None

        self._last_accepted_ts = tick_ts

        price = (tick.bid + tick.ask) / 2.0
        tick_minute = tick.timestamp.minute
        bar_minute = (tick_minute // self.timeframe_minutes) * self.timeframe_minutes
        bar_start = tick.timestamp.replace(minute=bar_minute, second=0, microsecond=0)

        completed_bar: BarData | None = None

        if self._current_bar_time is None:
            self._current_bar_time = bar_start
            self._open = price
            self._high = price
            self._low = price
            self._close = price
            self._volume = 1
        elif bar_start > self._current_bar_time:
            completed_bar = BarData(
                symbol=self.symbol,
                timeframe=self.timeframe_str,
                timestamp=self._current_bar_time,
                open=self._open,
                high=self._high,
                low=self._low,
                close=self._close,
                tick_volume=self._volume,
                is_complete=True,
            )
            self._completed_bars.append(completed_bar)
            # H-05: bound the completed-bar series. O(1) amortized (the trim
            # runs only when the ring is exactly one over the cap, and the live
            # tick path already trims to 4000 at tick_pipeline.py:463-464 so
            # this is defense-in-depth for non-engine consumers of the
            # aggregator). Keep the NEWEST bars: every consumer reads the
            # causal tail (55/60/900-bar windows, HTF aggregation).
            if len(self._completed_bars) > self.COMPLETED_BARS_MAXLEN:
                overflow = len(self._completed_bars) - self.COMPLETED_BARS_MAXLEN
                self._completed_bars = self._completed_bars[overflow:]
                self.dropped_completed_bars += overflow
            logger.info(
                "Bar completed",
                symbol=self.symbol,
                timeframe=self.timeframe_str,
                time=self._current_bar_time.isoformat(),
                close=self._close,
            )

            self._current_bar_time = bar_start
            self._open = price
            self._high = price
            self._low = price
            self._close = price
            self._volume = 1
        else:
            self._high = max(self._high, price)
            self._low = min(self._low, price)
            self._close = price
            self._volume += 1

        return completed_bar

    def get_completed_bars(self) -> list[BarData]:
        """Returns copy of all historical completed bars in memory."""
        return list(self._completed_bars)

    def reseed(self, completed_bars: list[BarData]) -> BarData | None:
        """Atomically replace history with broker-authoritative completed bars.

        Used after downtime / cold start so the aggregator and the live tick
        stream can never diverge from the real broker candles:

        * Drops any in-memory history (they may be stale / duplicate the
          still-forming broker minute).
        * Keeps only strictly-ascending, unique, completed bars.
        * Aligns the forming bar to the LATEST historical bar so the first
          live tick of the same minute continues the broker bar instead of
          minting a duplicate (same timestamp) bar.
        * Returns the last seeded bar (used by the warmup / reseed paths to
          rebuild feature records) or None when nothing was seeded.

        This is a bounded rebuild: the caller passes at most a few thousand
        bars and the replacing assignment is O(1).
        """
        if not completed_bars:
            self._completed_bars = []
            self._current_bar_time = None
            self._last_accepted_ts = None
            return None

        # Deterministic dedupe + ascending order (never trust caller order).
        seen: set[datetime] = set()
        deduped: list[BarData] = []
        for b in sorted(completed_bars, key=lambda x: x.timestamp):
            if b.timestamp in seen:
                continue
            seen.add(b.timestamp)
            deduped.append(b)

        # Only completed bars are allowed into the historical series.
        deduped = [b for b in deduped if b.is_complete]
        if not deduped:
            self._completed_bars = []
            self._current_bar_time = None
            self._last_accepted_ts = None
            return None

        last_bar = deduped[-1]
        # H-05: bound the reseeded series the same way as the live append path
        # (the caller fetches at most 20000 broker M1 bars, so this normally
        # trims nothing). Keep the NEWEST bars: every consumer reads the causal
        # tail. reseed() is an atomic replace, so the head we drop here is the
        # oldest broker history, never live-minted bars.
        if len(deduped) > self.COMPLETED_BARS_MAXLEN:
            overflow = len(deduped) - self.COMPLETED_BARS_MAXLEN
            deduped = deduped[overflow:]
            self.dropped_completed_bars += overflow
            last_bar = deduped[-1]
        self._completed_bars = deduped

        # Seed the forming bar at the NEXT minute boundary after the last
        # COMPLETED broker bar. The history bars are all complete, so the
        # current forming minute is the one AFTER the last completed bar —
        # seeding at last_bar.timestamp itself makes the first live tick of
        # that same minute mint a DUPLICATE complete bar at an already-sealed
        # timestamp (observed: "Bar completed 01:46" right after a 01:46
        # reseed on a 02:16 restart). +1min keeps the broker series exact.
        next_minute = last_bar.timestamp + timedelta(minutes=self.timeframe_minutes)
        self._current_bar_time = next_minute
        self._open = last_bar.close
        self._high = last_bar.close
        self._low = last_bar.close
        self._close = last_bar.close
        self._volume = 0
        # Rebase the monotonic tick stamp to the broker clock. The last
        # bar's OPEN time is the correct strict lower bound for live ticks:
        # a tick stamped within the last (still forming) broker minute must
        # be accepted (it continues that bar), while anything at/before the
        # last COMPLETED bar's open is provably stale. Anchor at the last
        # bar's open (not close) so the first tick of the forming minute —
        # stamped anywhere inside [last_open+1m, next_minute] — survives.
        anchor = last_bar.timestamp + timedelta(minutes=self.timeframe_minutes)
        # BUG-308 (2026-09-21): refuse to point the monotonic tick clock into
        # the future. A boundary reader that hands the still-forming current
        # minute in as a COMPLETED bar makes the anchor (last_bar + 1m) sit up
        # to a full minute ahead of the real clock; the out-of-order guard
        # then drops every live tick of that minute — the live feed starves
        # silently and only NO_TRADE can ever be produced. Clamp the anchor to
        # the last completed bar's own open (still strictly after every sealed
        # bar, still inside the forming minute) so the first real tick always
        # survives. The forming bar's own timestamp stays at next_minute so
        # the series geometry is unchanged; only the acceptance floor moves.
        _now = datetime.now(UTC)
        if anchor > _now:
            logger.warning(
                "Reseed anchor is in the future; clamping to the last completed bar",
                symbol=self.symbol,
                timeframe=self.timeframe_str,
                anchor=anchor.isoformat(),
                now=_now.isoformat(),
                clamped_to=last_bar.timestamp.isoformat(),
            )
            anchor = last_bar.timestamp
        self._last_accepted_ts = anchor

        logger.info(
            "BarAggregator reseeded",
            symbol=self.symbol,
            timeframe=self.timeframe_str,
            bars=len(deduped),
            first=deduped[0].timestamp.isoformat(),
            last=last_bar.timestamp.isoformat(),
        )
        return last_bar

    def get_current_forming_bar(self) -> BarData | None:
        """
        Returns the currently active forming (uncompleted) bar as a BarData object.
        Returns None if no tick has been processed yet.
        """
        if self._current_bar_time is None:
            return None
        return BarData(
            symbol=self.symbol,
            timeframe=self.timeframe_str,
            timestamp=self._current_bar_time,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            tick_volume=self._volume,
            is_complete=False,
        )
