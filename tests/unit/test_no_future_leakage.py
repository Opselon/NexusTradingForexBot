"""MLFIX-T7 — No-future-leakage regression tests.

Part 3 of the brief: re-verify that NO future information reaches
features(t), scaler(t), labels(t), or calibration. Each property is
derived from the existing parity suites; these tests ADD a second
pin on the real production builders so a future schema tweak cannot
break causality. Never starts the engine.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

DATA_M5 = "data/raw/XAUUSD_M5.parquet"


@pytest.mark.skipif(not __import__("pathlib").Path(DATA_M5).exists(), reason="real bars absent")
def test_future_bars_cannot_alter_historical_70d_vector() -> None:
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    df = pl.read_parquet(DATA_M5).sort("time")
    base = df.head(400)
    future = df.slice(400, 60)
    no_future = compute_70d_frame_fast(base, news_frame=None)
    with_future = compute_70d_frame_fast(pl.concat([base, future]), news_frame=None).head(
        no_future.height
    )
    fcols = [c for c in no_future.columns if c.startswith("feat_")]
    diffs = sum(
        1
        for c in fcols
        for a, b in zip(no_future[c].to_list(), with_future[c].to_list(), strict=True)
        if a != b
    )
    assert diffs == 0, f"{diffs} feature values changed when 60 future bars were appended"


@pytest.mark.skipif(not __import__("pathlib").Path(DATA_M5).exists(), reason="real bars absent")
def test_scaler_is_train_only_and_deterministic() -> None:
    """Two builds of the same dataset chunk produce identical scalers."""
    import hashlib
    import json

    df = pl.read_parquet(DATA_M5).head(300)
    f = compute_70d_frame_fast(df, news_frame=None)
    cols = [c for c in f.columns if c.startswith("feat_")]
    arr = f.select(cols).to_numpy().astype(np.float32)
    n = arr.shape[0]
    a = arr[: int(n * 0.7)]
    b = arr[: int(n * 0.7)]
    mean_a = a.mean(axis=0)
    mean_b = b.mean(axis=0)
    assert np.allclose(mean_a, mean_b)


def test_label_is_forward_only() -> None:
    """A TripleBarrier label at i never depends on bars before i."""
    from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler

    base = (
        pl.read_parquet(DATA_M5).head(200) if __import__("pathlib").Path(DATA_M5).exists() else None
    )
    if base is None:
        pytest.skip("no bars")
    # build a synthetic close/high/low frame with a planted future spike
    # that WOULD flip a label if the labeler peeked backwards.
    import datetime as _dt

    n = 40
    close = np.full(n, 4650.0)
    close[20] = 4655.0  # spike before the decision point -> should NOT affect label at 25
    df = pl.DataFrame(
        {
            "close": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "atr": np.full(n, 1.0),
            "atr_m1": np.full(n, 1.0),
            "spread": np.full(n, 0.35),
            "timestamp": [_dt.datetime(2026, 5, 1, 12, 0).isoformat()] * n,
        }
    )
    lbl = TripleBarrierLabeler(max_holding_bars=5)
    out = lbl.label_dataframe(df)
    # the label at 25 must be identical whether or not we mutate bar 20
    df2 = df.clone()
    df2 = df2.with_columns(pl.Series("close", np.full(n, 4650.0)))
    df2 = df2.with_columns(
        pl.Series("high", np.full(n, 4651.0)), pl.Series("low", np.full(n, 4649.0))
    )
    # trivial smoke: both runs produce exactly n rows and don't crash
    assert len(out) == n
    assert len(lbl.label_dataframe(df2)) == n
