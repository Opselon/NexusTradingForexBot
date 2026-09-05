"""AGENT-16 WAVE-2 regression: runtime bars normalization (multi-user fetch).

LEARNFIX-3 — the user brief: "when user fetch data it becomes clean at
RUNTIME when the model wants training, not only local on my system — it's
an ecosystem for millions of users."

Before this module the feature builders accepted ONLY real datetime objects.
Any other shape (broker CSV strings, epoch ints, naive datetimes, unsorted,
duplicate, NaN/Inf prices) silently dropped every row or crashed with
bare IndexError/TypeError deep in the builder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

pytest.importorskip("polars")

N = 120
_T0 = datetime(2026, 5, 1, 17, 15, tzinfo=UTC)


def _bars_shape(shape: str) -> pl.DataFrame:
    rng = np.random.default_rng(7)
    ts = [_T0 + timedelta(minutes=i) for i in range(N)]
    base = {
        "time": [int(t.timestamp()) for t in ts],
        "open": (3300 + rng.normal(0, 1, N)).tolist(),
        "high": (3301 + rng.normal(0, 1, N)).tolist(),
        "low": (3299 + rng.normal(0, 1, N)).tolist(),
        "close": (3300.5 + rng.normal(0, 1, N)).tolist(),
        "tick_volume": rng.integers(80, 400, N).tolist(),
        "spread": [0.20] * N,
        "real_volume": [0] * N,
    }
    if shape == "datetime":
        return pl.DataFrame({**base, "time_utc": ts})
    if shape == "csv_string":
        return pl.DataFrame({**base, "time_utc": [t.isoformat() for t in ts]})
    if shape == "csv_string_z":
        return pl.DataFrame({**base, "time_utc": [t.isoformat() + "Z" for t in ts]})
    if shape == "epoch_int_only":
        return pl.DataFrame(base)
    if shape == "epoch_ms_only":
        return pl.DataFrame({**base, "time": [int(t.timestamp() * 1000) for t in ts]})
    if shape == "naive_datetime":
        return pl.DataFrame({**base, "time_utc": [t.replace(tzinfo=None) for t in ts]})
    raise AssertionError(shape)


def test_all_user_shapes_normalize_identically() -> None:
    ref, ref_stats = normalize_bars_frame(_bars_shape("datetime"))
    for shape in (
        "csv_string",
        "csv_string_z",
        "epoch_int_only",
        "epoch_ms_only",
        "naive_datetime",
    ):
        frame, stats = normalize_bars_frame(_bars_shape(shape))
        assert frame.height == ref.height, shape
        assert frame["time"].to_list() == ref["time"].to_list(), shape
        # dtype equality: time-unit may differ (us vs ms) but tz must be UTC
        assert "UTC" in str(frame["time"].dtype), shape
        assert str(frame["time"].dtype).startswith("Datetime"), shape
        assert stats["rows_out"] == ref_stats["rows_out"]


def test_output_invariants_sorted_deduped_utc() -> None:
    ts = [_T0 + timedelta(minutes=i) for i in range(N)]
    frame = pl.DataFrame(
        {
            "time_utc": [t.isoformat() for t in ts] + [ts[5].isoformat()],
            "time": [int(t.timestamp()) for t in ts] + [int(ts[5].timestamp())],
            "open": [3300.0] * (N + 1),
            "high": [3300.3] * (N + 1),
            "low": [3299.9] * (N + 1),
            "close": [3300.1] * (N + 1),
            "tick_volume": [100] * (N + 1),
        }
    ).sample(fraction=1.0, shuffle=True, seed=11)  # unsorted on purpose
    out, stats = normalize_bars_frame(frame)
    assert stats["rows_in"] == N + 1
    assert stats["rows_out"] == N  # duplicate timestamp collapsed
    assert stats["rows_dropped"] == 1
    t = out["time"].to_list()
    assert t == sorted(t)
    assert len(set(t)) == len(t)
    assert str(out["time"].dtype).startswith("Datetime")
    assert "UTC" in str(out["time"].dtype)
    assert out["time_utc"].to_list() == out["time"].to_list()
    assert stats["normalize_id"].startswith("nbr_")


def test_dirty_prices_dropped() -> None:
    ts = [_T0 + timedelta(minutes=i) for i in range(N)]
    rows = {
        "time_utc": [t.isoformat() for t in ts],
        "open": [3300.0] * N,
        "high": [3300.3] * N,
        "low": [3299.9] * N,
        "close": [3300.1] * N,
        "tick_volume": [100] * N,
    }
    rows["close"][3] = float("nan")
    rows["close"][7] = float("inf")
    rows["close"][11] = -1.0
    rows["close"][13] = 0.0
    out, stats = normalize_bars_frame(pl.DataFrame(rows))
    assert stats["rows_out"] == N - 4
    assert float(out["close"].min()) > 0
    assert bool(out["close"].is_finite().all())


def test_honest_failure_on_garbage() -> None:
    with pytest.raises(ValueError, match="no time column"):
        normalize_bars_frame(pl.DataFrame({"price": [1, 2, 3]}))
    with pytest.raises(ValueError, match=r"failed to parse time column|zero parseable bars"):
        normalize_bars_frame(pl.DataFrame({"time_utc": ["garbage", "junk"], "open": [1, 1]}))
    with pytest.raises(ValueError, match="empty input"):
        normalize_bars_frame(pl.DataFrame({"time_utc": []}))


def test_builders_shape_transparent_70d() -> None:
    """70D builder must produce the SAME frame regardless of the fetch shape."""
    from nexus_scalp.model_generation.schema_v2_incremental import (
        compute_70d_frame_fast,
    )

    ref = compute_70d_frame_fast(_bars_shape("datetime"))
    for shape in ("csv_string", "epoch_int_only", "naive_datetime"):
        got = compute_70d_frame_fast(_bars_shape(shape))
        assert got.height == ref.height, shape
        cols = [c for c in got.columns if c.startswith("feat_")]
        a = ref.sort("timestamp").select(cols).to_numpy()
        b = got.sort("timestamp").select(cols).to_numpy()
        assert float(np.abs(a - b).max()) < 1e-9, shape


def test_canonical_vs_fast_identity_preserved_after_cleaning() -> None:
    """Cleaning must not break the fast/canonical byte-identity contract."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
    from nexus_scalp.model_generation.schema_v2_incremental import (
        compute_70d_frame_fast,
    )

    dirty = _bars_shape("csv_string")
    dirty = pl.concat([dirty, dirty.tail(5)]).sample(fraction=1.0, shuffle=True, seed=3)
    canon = compute_70d_frame(dirty)
    fast = compute_70d_frame_fast(dirty)
    assert canon.height == fast.height
    cols = [c for c in canon.columns if c.startswith("feat_")]
    a = canon.sort("timestamp").select(cols).to_numpy()
    b = fast.sort("timestamp").select(cols).to_numpy()
    assert float(np.abs(a - b).max()) < 1e-9
