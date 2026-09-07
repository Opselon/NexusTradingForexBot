"""Multi-timeframe bar resampling (SOLID, pure).

Turns the engine's canonical M1 completed-bar stream into ANY higher
timeframe (M5/M15/M30/H1/H2/H4/D1/W1/MN1) with strict OHLCV aggregation.
Single responsibility: time-bucketing only. No indicator logic here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

_TF_MINUTES: dict[str, int | None] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H4": 240,
    "D1": 1440,
    "W1": None,  # ISO week bucketing
    "MN1": None,  # calendar month bucketing
}


def bucket_start(ts: datetime, tf: str) -> datetime:
    """Floor a timestamp to its timeframe bucket start (UTC assumed naive/aware consistent)."""
    minutes = _TF_MINUTES.get(tf, 1)
    if minutes is None:
        if tf == "W1":
            # ISO week: Monday 00:00
            day = ts - timedelta(days=ts.weekday())
            return day.replace(hour=0, minute=0, second=0, microsecond=0)
        # MN1: first of month
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    minutes = int(minutes)
    epoch = int(ts.timestamp())
    bucket = (epoch // (minutes * 60)) * (minutes * 60)
    return datetime.fromtimestamp(bucket, tz=ts.tzinfo)


class BarResampler:
    """Aggregates an ordered M1 bar sequence into a higher timeframe."""

    def __init__(self, timeframe: str) -> None:
        if timeframe not in _TF_MINUTES:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        self.timeframe = timeframe

    def resample(self, bars: Sequence[Any]) -> list[dict[str, Any]]:
        """Bars (oldest→newest, is_complete only) → list of OHLC bucket dicts."""
        if self.timeframe == "M1":
            return [
                {
                    "timestamp": getattr(b, "timestamp", None),
                    "open": float(getattr(b, "open", 0.0)),
                    "high": float(getattr(b, "high", 0.0)),
                    "low": float(getattr(b, "low", 0.0)),
                    "close": float(getattr(b, "close", 0.0)),
                    "volume": float(getattr(b, "tick_volume", 0) or 0),
                    "open_val": float(getattr(b, "open", 0.0)),
                }
                for b in bars
                if getattr(b, "is_complete", True)
            ]

        out: list[dict[str, Any]] = []
        cur_key: Any = None
        o = h = lo = c = 0.0
        vol = 0.0
        cur_ts: datetime | None = None
        for b in bars:
            if getattr(b, "is_complete", True) is False:
                continue
            ts = getattr(b, "timestamp", None)
            if ts is None:
                continue
            key = bucket_start(ts, self.timeframe)
            close_v = float(getattr(b, "close", 0.0))
            high_v = float(getattr(b, "high", 0.0))
            low_v = float(getattr(b, "low", 0.0))
            open_v = float(getattr(b, "open", 0.0))
            tv = float(getattr(b, "tick_volume", 0) or 0)
            if cur_key is None or key != cur_key:
                if cur_key is not None:
                    out.append(
                        {
                            "timestamp": cur_ts,
                            "open": o,
                            "high": h,
                            "low": lo,
                            "close": c,
                            "volume": vol,
                            "open_val": o,
                        }
                    )
                cur_key = key
                cur_ts = key
                o, h, lo, c, vol = open_v, high_v, low_v, close_v, tv
            else:
                h = max(h, high_v)
                lo = min(lo, low_v)
                c = close_v
                vol += tv
        if cur_key is not None:
            out.append(
                {
                    "timestamp": cur_ts,
                    "open": o,
                    "high": h,
                    "low": lo,
                    "close": c,
                    "volume": vol,
                    "open_val": o,
                }
            )
        return out
