"""Snapshot provider — shared Indicator Engine -> ML feature snapshots.

TASK-ML-CTRL §5/§11/§22: ONE place that turns the live bar cache into
timestamped per-timeframe IndicatorSnapshots for the ML tensorizer (and any
future consumer). Snapshots are cached per (symbol, timeframe, bar-count)
so the AI Analysis page, the ML controller, and training all reuse the SAME
computation instead of recomputing RSI/EMA/pivots per consumer (§22).

Look-ahead policy (§11): M1 uses the completed-bar policy the caller
declares; M5/M15 are resampled from the same completed M1 bars — a snapshot
is stamped with the source candle timestamp (the M1 close time the resample
bucket covers), never a wall-clock time after the decision.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.position_adviser.snapshots")

__all__ = ["SnapshotProvider", "TimedSnapshot"]


@dataclass(frozen=True)
class TimedSnapshot:
    """IndicatorSnapshot + the source-candle timestamp (§11 provenance)."""

    snapshot: Any  # indicators.service.IndicatorSnapshot
    snapshot_time: datetime
    timeframe: str

    def __getattr__(self, name: str) -> Any:  # transparent delegation
        return getattr(self.snapshot, name)


class SnapshotProvider:
    """Builds/caches timestamped indicator snapshots for the ML controller."""

    def __init__(self, bar_source: Any) -> None:
        self._bars = bar_source  # engine.aggregator (get_completed_bars)
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, int], TimedSnapshot] = {}

    def snapshot(self, symbol: str, timeframe: str) -> TimedSnapshot:
        """Timestamped snapshot for one timeframe (cached per bar count)."""
        from nexus_scalp.indicators.service import IndicatorService

        bars = list(self._bars.get_completed_bars())
        key = (symbol.upper(), timeframe.upper(), len(bars))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        svc = IndicatorService()
        snap = svc.snapshot(symbol, bars, timeframe=timeframe)
        # Source-candle stamp: the LAST completed bar's close time (M1 bars
        # carry their bucket close in `time`/`timestamp`; fall back to now).
        ts = _last_bar_close_time(bars)
        timed = TimedSnapshot(snapshot=snap, snapshot_time=ts, timeframe=timeframe.upper())
        with self._lock:
            # keep the cache bounded: one entry per (sym, tf, bar-count), and
            # drop entries whose bar count is stale (cache hit rate is high
            # within a management pass; the dict self-trims via key churn).
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = timed
        return timed

    def snapshot_triple(self, symbol: str) -> dict[str, TimedSnapshot]:
        """M1 + M5 + M15 in one pass, sharing the same bar window (§22)."""
        return {tf: self.snapshot(symbol, tf) for tf in ("M1", "M5", "M15")}

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


def _last_bar_close_time(bars: list[Any]) -> datetime:
    """Close time of the last completed bar (the snapshot's decision time)."""
    if not bars:
        return datetime.now(UTC)
    last = bars[-1]
    for attr in ("time", "timestamp", "close_time", "dt"):
        v = getattr(last, attr, None)
        if v is None:
            continue
        try:
            if isinstance(v, datetime):
                return v if v.tzinfo else v.replace(tzinfo=UTC)
            return datetime.fromtimestamp(float(v), tz=UTC)
        except (TypeError, ValueError, OSError):
            continue
    return datetime.now(UTC)
