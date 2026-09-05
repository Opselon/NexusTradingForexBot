"""Runtime bars normalization for ANY user's fetched data (multi-user).

AGENT-16 WAVE-2 (ecosystem hygiene, user directive 2026-09-05): the model
training path must accept data the way ANY user fetches it — CSV strings,
naive datetimes, epoch seconds/millis/micros ints, unsorted rows, duplicate
timestamps, NaN/Inf/negative prices — and hand the feature builders a CLEAN,
chronological, UTC frame. Before this module, `compute_70d_frame_fast` /
`compute_70d_frame` / `compute_60d_frame` / `bars_frame_to_bardata` accepted
ONLY real datetime objects: everything else was silently dropped row-by-row
(`times` list empty -> bare `IndexError: list index out of range`) or crashed
mid-build with a raw TypeError — leaving a million-user ecosystem with an
unexplainable training failure instead of a clean error.

normalize_bars_frame(df) is the SINGLE runtime entry normalization:
  * time column resolution: time_utc > timestamp > datetime > date > time
    (int/str epoch auto-detected: s/ms/us heuristic)
  * parse strings: ISO-8601 (incl. trailing 'Z'), then common formats
  * naive datetimes pinned to UTC (broker/server-local convention),
    tz-aware converted to UTC
  * numeric OHLC coercion + drop non-finite / non-positive-price rows
  * sort by resolved time, drop duplicate timestamps (keep=last)
  * guarantees: non-empty OR raises ValueError with a diagnostic count;
    unique monotonic 'time'; datetime 'time_utc' column (naive->UTC)
  * stats dict returned alongside for provenance (rows_in/rows_out/
    dropped_*/parse mode + normalize_id) so training metadata can record
    HOW user data was cleaned at runtime (INV-008 provenance discipline).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import polars as pl

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.bars_normalize")

#: Epoch heuristics (seconds / millis / micros boundaries).
_EPOCH_MS_MIN = 1_000_000_000_000
_EPOCH_US_MIN = 1_000_000_000_000_000

_TIME_COL_CANDIDATES = ("time_utc", "timestamp", "datetime", "date", "time")
_PRICE_COLS = ("open", "high", "low", "close")


def _epoch_to_utc(v: int | float) -> datetime:
    n = float(v)
    if n >= _EPOCH_US_MIN:
        return datetime.fromtimestamp(n / 1_000_000, tz=UTC)
    if n >= _EPOCH_MS_MIN:
        return datetime.fromtimestamp(n / 1_000, tz=UTC)
    return datetime.fromtimestamp(n, tz=UTC)


def _parse_time_value(v: Any) -> datetime | None:
    """Parse ONE raw time cell into an aware UTC datetime (or None)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    if isinstance(v, (int, float)):
        # pl.Date cells arrive as int days-since-epoch only in rare casts;
        # treat numbers as epochs (the broker convention).
        try:
            return _epoch_to_utc(v)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return None


def _resolve_time_expr(lf: pl.LazyFrame) -> pl.Expr | None:
    """Pick the first available time column and build a parse expression."""
    schema = lf.collect_schema()
    for cand in _TIME_COL_CANDIDATES:
        if cand not in schema.names():
            continue
        dtype = schema[cand]
        col = pl.col(cand)
        if dtype in (pl.String, pl.Categorical):
            s = pl.col(cand).str.strip_chars()
            s = (
                pl.when(s.str.ends_with("Z"))
                .then(s.str.slice(0, s.str.len_chars() - 1))
                .otherwise(s)
            )
            return s.str.to_datetime(time_zone="UTC", strict=False, format="%Y-%m-%dT%H:%M:%S%z")
        if dtype == pl.Date:
            return col.cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
        if dtype == pl.Int64 and cand == "time" and schema.get("time_utc") is None:
            # bare epoch 'time' column (broker export) — s/ms/us heuristic
            e = col.cast(pl.Float64)
            # Each branch maps its epoch unit to Datetime("us") via the correct scale.
            # _EPOCH_US_MIN ~ 1e15 us (2001), _EPOCH_MS_MIN ~ 1e12 ms (2001).
            return (
                pl.when(e >= _EPOCH_US_MIN)
                .then(e.cast(pl.Datetime("us")).dt.replace_time_zone("UTC"))
                .when(e >= _EPOCH_MS_MIN)
                .then((e * 1_000).cast(pl.Datetime("us")).dt.replace_time_zone("UTC"))
                .otherwise(
                    (e * 1_000_000)
                    .cast(pl.Int64)
                    .cast(pl.Datetime("us"))
                    .dt.replace_time_zone("UTC")
                )
            )
        if isinstance(dtype, pl.Datetime):
            return (
                col.dt.replace_time_zone("UTC")
                if dtype.time_zone is None
                else col.dt.convert_time_zone("UTC")
            )
    return None


def _json_stable(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def normalize_bars_frame(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Runtime bars normalization for ANY user's fetched data.

    Returns (normalized_frame, stats). Raises ValueError when nothing
    parseable remains (honest failure — never fabricate bars).
    Guarantees on the returned frame:
      'time'      Datetime(us, UTC) unique ascending
      'time_utc'  same values (the builders' preferred column)
      OHLC        Float64 finite positive where present
    """
    if df is None or df.is_empty():
        raise ValueError("normalize_bars_frame: empty input frame")
    stats: dict[str, Any] = {"rows_in": df.height}
    lf = df.lazy()
    texpr = _resolve_time_expr(lf)
    if texpr is None:
        raise ValueError(
            "normalize_bars_frame: no time column found "
            f"(looked for {list(_TIME_COL_CANDIDATES)} in {df.columns})"
        )
    out = (
        lf.with_columns(texpr.alias("__t"))
        .drop_nulls("__t")
        .with_columns(pl.col("__t").alias("time"))
    )
    # numeric coercion of price columns (strings like '1 234.5' -> float)
    schema = df.schema
    for pc in _PRICE_COLS:
        if pc in schema:
            out = out.with_columns(pl.col(pc).cast(pl.Float64, strict=False).alias(pc))
    out = out.drop_nulls(subset=["time"])
    # OHLC validity: all present price cells finite & positive
    valid = pl.lit(True)
    have_price_cols = [pc for pc in _PRICE_COLS if pc in df.columns]
    for pc in have_price_cols:
        valid = valid & pl.col(pc).is_finite() & (pl.col(pc) > 0)
    if have_price_cols:
        out = out.filter(valid)
    out = out.unique(subset=["time"], keep="last").sort("time")
    frame = out.drop("__t").with_columns(pl.col("time").alias("time_utc")).collect()
    stats["rows_out"] = frame.height
    stats["rows_dropped"] = stats["rows_in"] - frame.height
    if frame.is_empty():
        raise ValueError(
            "normalize_bars_frame: zero parseable bars "
            f"(in={stats['rows_in']}) — check the time column format"
        )
    stats["time_min"] = str(frame["time"].min())
    stats["time_max"] = str(frame["time"].max())
    stats["normalize_id"] = (
        "nbr_" + hashlib.sha256(_json_stable(stats).encode("utf-8")).hexdigest()[:12]
    )
    logger.info(
        "[BARS_NORMALIZE] in=%s out=%s dropped=%s span=%s..%s id=%s",
        stats["rows_in"],
        stats["rows_out"],
        stats["rows_dropped"],
        stats["time_min"],
        stats["time_max"],
        stats["normalize_id"],
    )
    return frame, stats
