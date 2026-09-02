"""Deterministic Sequence Builder (PHASE 13B).

Temporal data contract (spec 5):

    * a model sequence satisfies timestamp_0 < timestamp_1 < ... < timestamp_N
    * every timestep shares the same symbol / timeframe / feature schema /
      news schema
    * NO sequence crosses a symbol boundary, timeframe boundary, or an
      invalid market-data gap (configurable max gap)
    * deterministic ordering

Sequences are built from an already-labeled, chronologically-sorted
dataset frame using ONLY the artifacts' stored sample ordering — no future
information and no cross-boundary contamination.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


class SequenceBuilder:
    """Builds fixed-length causal sequences from a labeled dataset frame."""

    def __init__(
        self,
        seq_len: int = 16,
        max_gap_us: int | None = None,
    ) -> None:
        self.seq_len = max(2, int(seq_len))
        #: max allowed inter-bar gap in microseconds; None = no gap check
        self.max_gap_us = max_gap_us

    def build(
        self,
        frame: pl.DataFrame,
        *,
        label_col: str = "label",
        timestamp_col: str = "timestamp",
        symbol_col: str = "symbol",
        timeframe_col: str = "timeframe",
        news_enabled: bool = True,
    ) -> dict[str, np.ndarray]:
        """Returns {X: (N, seq_len, F), y: (N,), valid: (N,) bool}.

        Only rows where a FULL causal history of `seq_len` exists within the
        same symbol/timeframe boundary and within the max gap become
        sequence samples (valid=True). Rows at the start of a boundary are
        excluded — never padded with foreign/borrowed data.

        ``news_enabled=False`` EXCLUDES news_* columns from the sequence
        feature vector (news OFF ablation removes NewsContext entirely).
        """
        if frame is None or frame.is_empty():
            return {
                "X": np.zeros((0, self.seq_len, 0), dtype=np.float32),
                "y": np.zeros(0, dtype=np.int64),
                "valid": np.zeros(0, dtype=bool),
            }

        # FRAME-ORDER columns (matches the 2D path + DatasetFactory output;
        # lexicographic sort would silently reorder feat_10 before feat_2)
        feat_cols = [c for c in frame.columns if c.startswith("feat_")]
        news_cols = [
            c for c in frame.columns if c.startswith("news_") and c != "news_context_schema_id"
        ]
        if not news_enabled:
            news_cols = []
        input_cols = feat_cols + news_cols
        if not feat_cols:
            raise ValueError("SequenceBuilder: no feat_* columns in frame")

        rows = [r for r in frame.iter_rows(named=True)]
        # chronological order within the frame (already sorted by the
        # dataset factory; re-sort defensively)
        rows.sort(key=lambda r: str(r.get(timestamp_col, "")))

        X_list: list[np.ndarray] = []
        y_list: list[int] = []
        valid_list: list[bool] = []

        i = self.seq_len - 1
        while i < len(rows):
            window = rows[i - self.seq_len + 1 : i + 1]
            symbol = str(window[-1].get(symbol_col, ""))
            timeframe = str(window[-1].get(timeframe_col, ""))
            boundary_ok = all(
                str(r.get(symbol_col, "")) == symbol and str(r.get(timeframe_col, "")) == timeframe
                for r in window
            )
            gap_ok = True
            if self.max_gap_us is not None:
                ts_prev = _ts_us(window[0])
                for r in window[1:]:
                    ts_cur = _ts_us(r)
                    if ts_cur - ts_prev > self.max_gap_us:
                        gap_ok = False
                        break
                    ts_prev = ts_cur

            vec = np.array(
                [[float(r.get(c, 0.0)) for c in input_cols] for r in window],
                dtype=np.float32,
            )
            X_list.append(vec)
            y_list.append(int(window[-1].get(label_col, 0)))
            valid_list.append(boundary_ok and gap_ok)
            i += 1

        if not X_list:
            return {
                "X": np.zeros((0, self.seq_len, len(input_cols)), dtype=np.float32),
                "y": np.zeros(0, dtype=np.int64),
                "valid": np.zeros(0, dtype=bool),
            }
        return {
            "X": np.stack(X_list),
            "y": np.array(y_list, dtype=np.int64),
            "valid": np.array(valid_list, dtype=bool),
        }


def _ts_us(value: Any) -> int:
    """Parses a timestamp cell to epoch microseconds."""
    if value is None:
        return 0
    if hasattr(value, "timestamp"):  # datetime-like
        return int(value.timestamp() * 1_000_000)
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000)
    except ValueError:
        return 0
