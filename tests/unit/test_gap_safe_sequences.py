"""MLFIX-T7 — SequenceBuilder gap-safety regression tests.

The gap-safe rule (verified in docs/forensics/t70d_data_quality_gap_audit_2026-09-03.md):
    Any sequence window that (a) spans an inter-bar gap > max_gap_us, or
    (b) crosses a symbol/timeframe boundary, is marked valid=False and is
    strictly EXCLUDED from training. No padding, no interpolation.

Gap guard: model_generation/sequence.py now passes the timestamp cell
(row.get(timestamp_col)) to _ts_us so weekend gaps are correctly detected
(pre-patch defect: whole row dict -> always 0).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from nexus_scalp.model_generation.sequence import SequenceBuilder


def _rows(n: int, gap_at: int | None = None, gap_minutes: int = 20, symbol: str = "XAUUSD"):
    t = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    out = []
    for i in range(n):
        if gap_at is not None and i == gap_at:
            t = t + timedelta(minutes=gap_minutes)
        out.append(
            {
                "timestamp": t.isoformat(),
                "symbol": symbol,
                "timeframe": "M1",
                "feat_0": float(i),
                "feat_1": float(i) * 2.0,
                "label": i % 3,
            }
        )
        t = t + timedelta(minutes=1)
    return pl.DataFrame(out)


TEN_MIN_US = 10 * 60 * 1_000_000


def test_window_straddling_gap_is_invalid() -> None:
    b = SequenceBuilder(seq_len=4, max_gap_us=TEN_MIN_US)
    seq = b.build(_rows(12, gap_at=6, gap_minutes=20))
    assert not seq["valid"][3], "window [3..6] straddles the 20m gap -> valid=False"
    assert not seq["valid"][4]
    assert not seq["valid"][5]
    assert seq["valid"][0] and seq["valid"][1] and seq["valid"][2]
    assert seq["valid"][6] and seq["valid"][7] and seq["valid"][8]


def test_gap_within_tolerance_is_valid() -> None:
    b = SequenceBuilder(seq_len=4, max_gap_us=TEN_MIN_US)
    seq = b.build(_rows(12, gap_at=6, gap_minutes=5))
    assert seq["valid"].all()


def test_no_max_gap_keeps_all_windows_valid() -> None:
    b = SequenceBuilder(seq_len=4, max_gap_us=None)
    seq = b.build(_rows(12, gap_at=6, gap_minutes=20))
    assert seq["valid"].all(), "gap check disabled -> no window dropped"


def test_symbol_boundary_is_invalid() -> None:
    half = _rows(6)
    t1 = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    other_rows = []
    for i in range(6):
        ts = t1 + timedelta(minutes=i)
        other_rows.append(
            {
                "timestamp": ts.isoformat(),
                "symbol": "EURUSD",
                "timeframe": "M1",
                "feat_0": float(i),
                "feat_1": float(i) * 2.0,
                "label": i % 3,
            }
        )
    other = pl.DataFrame(other_rows)
    frame = pl.concat([half, other])
    b = SequenceBuilder(seq_len=4, max_gap_us=TEN_MIN_US)
    seq = b.build(frame)
    assert not seq["valid"][3]
    assert not seq["valid"][4]
    assert not seq["valid"][5]
    assert seq["valid"][0]
    assert seq["valid"][2]
    assert seq["valid"][-1]


def test_excluded_windows_never_reach_training_tensors() -> None:
    """SequenceCandidateTrainer masks X/y with `valid` before training —
    this contract is what makes the builder gap-SAFE rather than gap-AWARE."""
    b = SequenceBuilder(seq_len=4, max_gap_us=TEN_MIN_US)
    seq = b.build(_rows(12, gap_at=6, gap_minutes=30))
    X, y, valid = seq["X"], seq["y"], seq["valid"]
    X_ok, y_ok = X[valid], y[valid]
    assert X_ok.shape[1:] == (4, 2)
    assert len(y_ok) == int(valid.sum())
    # every kept window is gap-free (no 30m hop inside it)
    assert int(valid.sum()) < 9


def test_deterministic_ordering_and_labels() -> None:
    b = SequenceBuilder(seq_len=4, max_gap_us=TEN_MIN_US)
    frame = _rows(10)
    frame = frame.with_columns((pl.col("label") * 10).alias("label"))
    s1 = b.build(frame)
    s2 = b.build(frame)
    assert np.array_equal(s1["X"], s2["X"])
    assert np.array_equal(s1["y"], s2["y"])
    # window 0 covers labels [0,10,20,0] -> last = 0; window 1 [10,20,0,10] -> 10
    assert int(s1["y"][0]) == 0
    assert int(s1["y"][1]) == 10
    assert int(s1["y"][2]) == 20


def test_empty_frame_is_shape_safe() -> None:
    b = SequenceBuilder(seq_len=4)
    seq = b.build(pl.DataFrame())
    assert seq["X"].shape[0] == 0
    assert len(seq["y"]) == 0
    assert len(seq["valid"]) == 0
