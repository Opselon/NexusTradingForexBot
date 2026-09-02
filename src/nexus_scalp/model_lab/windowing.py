"""MODEL LAB — strict causal windowing for temporal experiments.

sample at t  ->  features from rows t-window+1 .. t  ONLY.
No future observation can enter a window (verified by tests).
Windows are materialized as (n_samples, window, dim) float32 arrays with a
per-sample label/features from the LAST row of the window.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


def build_windows(
    frame: pl.DataFrame,
    feature_cols: list[str],
    window: int,
    *,
    split_col: str = "_split",
) -> dict[str, dict[str, np.ndarray]]:
    """Causal sliding windows per split.

    The first window of each split needs window-1 history rows from the
    PREVIOUS split's tail — that is legitimate causality (the past is always
    available) but it must not CROSS the train/val/oos boundary for LABEL
    contamination purposes. Labels come from each window's last row only, and
    the label horizon (15 bars) is purged at dataset build time, so crossing
    windows carry no future information.
    """
    X_all = frame.select(feature_cols).to_numpy().astype(np.float32)
    y_all = frame["label"].to_numpy().astype(np.int64)
    ts_all = frame["timestamp"].to_list()
    splits = frame[split_col].to_list() if split_col in frame.columns else ["all"] * frame.height

    out: dict[str, dict[str, list[Any]]] = {}
    n = frame.height
    if n < window:
        raise ValueError(f"frame has {n} rows; window={window} too large")

    starts = range(window - 1, n)
    for s in starts:
        key = splits[s]
        Xw = X_all[s - window + 1 : s + 1]
        item = out.setdefault(key, {"X": [], "y": [], "ts": []})
        item["X"].append(Xw)
        item["y"].append(y_all[s])
        item["ts"].append(ts_all[s])

    return {
        k: {
            "X": np.stack(v["X"]),
            "y": np.asarray(v["y"], dtype=np.int64),
            "ts": np.asarray(v["ts"], dtype=object),
        }
        for k, v in out.items()
    }
