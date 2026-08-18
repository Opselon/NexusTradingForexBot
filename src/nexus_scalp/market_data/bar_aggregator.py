"""
Incremental Candle Bar Aggregator
=================================
Aggregates tick streams into complete OHLC candle bars across timeframes.
Guarantees explicit separation between Completed Bars and Forming Bars.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.market_data.bar_aggregator")


class BarData(BaseModel):
    """
    Immutable representation of an OHLC Bar.
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


class BarAggregator:
    """
    Maintains active forming bars and yields completed bars upon timeframe boundary crossing.
    """

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

    def process_tick(self, tick: TickData) -> BarData | None:
        """
        Processes incoming tick and returns a completed BarData object if a boundary crossed.

        Args:
            tick: New incoming tick.

        Returns:
            Optional[BarData]: Completed bar if period closed, else None.
        """
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
            return None

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
