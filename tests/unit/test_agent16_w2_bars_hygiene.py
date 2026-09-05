"""Agent 16 Wave-2 — ecosystem runtime bars hygiene (multi-user).

User directive 2026-09-05: "when the user fetches data it becomes clean at
RUNTIME when the model wants to train — not only locally on my system, it's
an ecosystem for millions of users."

Before this wave, the canonical bars producers accepted ONLY real datetime
objects for time: CSV strings, epoch ints (s/ms/us), naive vs tz-aware rows,
unsorted frames, duplicate timestamps, NaN/Inf/negative OHLC were silently
skipped row-by-row (`times` list shorter than bars list) and then crashed
with a bare `IndexError: list index out of range` (string times) or
propagated non-finite rows. Any user fetch shape that was not a perfect raw
frame could never train — an ecosystem of millions would each hit a spurious
crash.

normalize_bars_frame(df) — the SINGLE runtime entry — fixes this by
normalizing ANY user's fetched frame to a CLEAN, chronological UTC frame with
dedup'd timestamps and validated OHLC before the feature builders run. This
suite is the pinned regression: dirty user fetch shapes -> CLEAN runtime
frame -> trainable labeled row, on the EXACT build_feature_frame() the
runtime path calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame
from nexus_scalp.model_generation.three_model import build_feature_frame

EPOCH = int(datetime(2026, 9, 1, 10, 0, tzinfo=UTC).timestamp())


def _base_frame(n: int = 200) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "time": [EPOCH + 60 * i for i in range(n)],
            "open": [4600.0 + i * 0.1 for i in range(n)],
            "high": [4601.0 + i * 0.1 for i in range(n)],
            "low": [4599.0 + i * 0.1 for i in range(n)],
            "close": [4600.5 + i * 0.1 for i in range(n)],
            "tick_volume": [100 + i for i in range(n)],
            "spread": [4] * n,
            "real_volume": [0] * n,
            "time_utc": [
                datetime(2026, 9, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(n)
            ],
        }
    )


# ---------------------------------------------------------------------------
# Contract A — normalize_bars_frame itself: every known-bad user fetch shape
# ---------------------------------------------------------------------------


def test_normalize_string_time_utc_does_not_crash() -> None:
    """CSV strings for time_utc (no tz) -> clean UTC datetimes (no bare IndexError)."""
    f = _base_frame().with_columns(
        pl.col("time_utc").dt.to_string("%Y-%m-%dT%H:%M:%S").alias("time_utc")
    )
    norm, stats = normalize_bars_frame(f)
    assert norm.height == 200
    assert norm["time"].dtype == pl.Datetime("us", "UTC")
    assert stats["rows_dropped"] == 0


def test_normalize_epoch_int_heuristic() -> None:
    f = _base_frame().select(
        ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    )
    norm, stats = normalize_bars_frame(f)
    assert norm.height == 200
    assert stats["rows_dropped"] == 0


def test_normalize_nan_price_bar_dropped_not_propagated() -> None:
    f = _base_frame().with_columns(
        pl.when(pl.int_range(200) == 150).then(None).otherwise(pl.col("open")).alias("open")
    )
    norm, stats = normalize_bars_frame(f)
    assert stats["rows_dropped"] >= 1
    assert norm.filter(pl.col("open").is_null()).height == 0


def test_normalize_inf_price_bar_dropped() -> None:
    f = _base_frame().with_columns(
        pl.when(pl.int_range(200) == 150)
        .then(float("inf"))
        .otherwise(pl.col("close"))
        .alias("close")
    )
    norm, stats = normalize_bars_frame(f)
    assert stats["rows_dropped"] >= 1


def test_normalize_negative_price_bar_dropped() -> None:
    f = _base_frame().with_columns(
        pl.when(pl.int_range(200) == 150).then(-4600.0).otherwise(pl.col("open")).alias("open")
    )
    norm, stats = normalize_bars_frame(f)
    assert stats["rows_dropped"] >= 1


def test_normalize_unsorted_rows_sorted_utc() -> None:
    rows = _base_frame().to_dicts()
    rows[100], rows[180] = rows[180], rows[100]
    f = pl.DataFrame(rows)
    norm, _ = normalize_bars_frame(f)
    assert norm.height == 200
    assert bool(norm["time"].is_sorted())


def test_normalize_duplicate_timestamps_keep_last() -> None:
    f = pl.concat([_base_frame(), _base_frame().tail(1)])
    norm, stats = normalize_bars_frame(f)
    assert stats["rows_dropped"] >= 1
    assert norm["time"].n_unique() == norm.height


def test_normalize_empty_raises_not_silent() -> None:
    import pytest

    f = pl.DataFrame({"time": [], "open": [], "high": [], "low": [], "close": []})
    with pytest.raises(ValueError, match="empty input"):
        normalize_bars_frame(f)


# ---------------------------------------------------------------------------
# Contract B — full training spine: dirty user fetch -> normalize (via
#    producers) -> 70D features -> Triple-Barrier -> research-trainable frame
# ---------------------------------------------------------------------------


def test_full_spine_dirty_string_times_to_trainable() -> None:
    f = _base_frame().with_columns(
        pl.col("time_utc").dt.to_string("%Y-%m-%dT%H:%M:%S").alias("time_utc")
    )
    feat = build_feature_frame("70d_liquidity", f, None)
    lbl = TripleBarrierLabeler().label_dataframe(feat)
    trainable = lbl.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
    assert feat.height >= 100
    assert trainable.height >= 1


def test_full_spine_dirty_single_bad_price_to_trainable() -> None:
    f = _base_frame().with_columns(
        pl.when(pl.int_range(200) == 150).then(None).otherwise(pl.col("open")).alias("open")
    )
    feat = build_feature_frame("70d_liquidity", f, None)
    TripleBarrierLabeler().label_dataframe(feat)
    assert feat.height >= 100
    feat_cols = [c for c in feat.columns if c.startswith("feat_")]
    arr = feat.select(feat_cols).to_numpy()
    assert bool(np.isfinite(arr).all())


def test_full_spine_unsorted_dirty_to_sorted_features() -> None:
    rows = _base_frame().to_dicts()
    rows[50], rows[170] = rows[170], rows[50]
    f = pl.DataFrame(rows)
    feat = build_feature_frame("70d_liquidity", f, None)
    assert bool(feat["timestamp"].is_sorted())
    assert feat.height >= 100


def test_full_spine_duplicate_ts_to_trainable() -> None:
    f = pl.concat([_base_frame(), _base_frame().tail(1)])
    feat = build_feature_frame("70d_liquidity", f, None)
    lbl = TripleBarrierLabeler().label_dataframe(feat)
    trainable = lbl.filter(pl.col("is_eval_sample") & ~pl.col("is_purged"))
    assert feat.height >= 100
    assert trainable.height >= 1


# ---------------------------------------------------------------------------
# Contract C — producers: every canonical builder threads normalize
# ---------------------------------------------------------------------------


def test_all_canonical_builders_thread_normalize() -> None:
    """Seam pin: each canonical producer now weaves normalize_bars_frame at the top."""
    from pathlib import Path

    for path in (
        "src/nexus_scalp/model_generation/schema_v2.py",
        "src/nexus_scalp/model_generation/schema_v2_incremental.py",
    ):
        src = Path(path).read_text(encoding="utf-8")
        assert src.count("normalize_bars_frame(df)") >= 1, (
            f"{path} must call normalize_bars_frame(df)"
        )
