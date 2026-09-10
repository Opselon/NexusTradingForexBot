"""AGENT-5 replay/shadow validation — live sequence gap parity (REPLAY-01..03).

Proves the LIVE sequence deque (LiveSequenceService) matches the dataset-side
SequenceBuilder semantics on the SAME bar history:

  REPLAY-01  a >max_gap_us hole invalidates exactly the windows that contain
             it; post-gap windows rebuild with L fresh bars (parity with
             SequenceBuilder, which re-validates after the boundary).
  REPLAY-02  within-window bars (same timestamp ticks) update nothing —
             one entry per completed M1 bar (bar-aligned window).
  REPLAY-03  a 2D-trained artifact NEVER builds a sequence tensor even with
             bar timestamps present (trained-mode parity gate).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

torch = pytest.importorskip("torch")

from nexus_scalp.application.live_sequence import (  # noqa: E402
    LiveSequenceService,
    LiveSequenceState,
)
from nexus_scalp.model_generation.sequence import (  # noqa: E402
    SEQUENCE_CONTRACT,
    SequenceBuilder,
)

L = 8
T0 = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _history(hole_minutes: int = 30):
    """40 steady 1-min bars, a hole, 40 more steady bars (row-id == order)."""
    rows, ts = [], T0
    for i in range(40):
        rows.append({"timestamp": ts, "label": i})
        ts += timedelta(minutes=1)
    hole_end = ts + timedelta(minutes=hole_minutes)
    for i in range(40):
        rows.append({"timestamp": hole_end + timedelta(minutes=i), "label": 40 + i})
    return rows


def _live_state(seq_len: int = L) -> LiveSequenceState:
    return LiveSequenceState(
        buffer=__import__("collections").deque(maxlen=64),
        seq_len=seq_len,
        max_gap_us=SEQUENCE_CONTRACT.max_gap_us,
        last_bar_ts_us=None,
        gap_invalid=False,
        trained_mode="sequence",
    )


def test_replay01_post_gap_window_rebuilds_like_dataset():
    rows = _history()
    frame = pl.DataFrame(
        [
            {**r, "symbol": "XAUUSD", "timeframe": "M1", **{f"feat_{j}": 0.0 for j in range(70)}}
            for r in rows
        ]
    )
    seq = SequenceBuilder(seq_len=L, max_gap_us=SEQUENCE_CONTRACT.max_gap_us).build(
        frame, news_enabled=False
    )
    # dataset: first valid window ENDING at a post-gap row
    dataset_first = next(
        r["label"] for i, r in enumerate(rows) if r["label"] >= 40 and bool(seq["valid"][i - L + 1])
    )
    assert dataset_first == 47  # first window with L fully post-gap bars (rows 40..47)

    st = _live_state()
    live_first = None
    for r in rows:
        out = LiveSequenceService.maybe_build_sequence_tensor(st, [0.0] * 70, bar_ts=r["timestamp"])
        if out is not None and r["label"] >= 40 and live_first is None:
            live_first = r["label"]
    assert live_first == dataset_first, (
        "live window re-arm diverges from dataset gap semantics: "
        f"live={live_first} dataset={dataset_first}"
    )


def test_replay02_intra_bar_ticks_do_not_fill_window():
    st = _live_state(seq_len=4)
    base = T0
    # 4 distinct bars
    for m in range(4):
        LiveSequenceService.maybe_build_sequence_tensor(
            st, [0.0] * 70, bar_ts=base + timedelta(minutes=m)
        )
    assert len(st.buffer) == 4
    # intra-bar ticks (same timestamp as the last bar) change nothing
    for _ in range(5):
        LiveSequenceService.maybe_build_sequence_tensor(
            st, [0.5] * 70, bar_ts=base + timedelta(minutes=3)
        )
    assert len(st.buffer) == 4
    assert st.buffer[-1][0] == 0.0  # the tick value never entered


def test_replay03_2d_trained_never_builds_sequence():
    st = LiveSequenceService.defaults()  # trained_mode="2d" (canonical trainer)
    st.seq_len = 2
    out = None
    for m in range(4):
        out = LiveSequenceService.maybe_build_sequence_tensor(
            st, [0.1] * 70, bar_ts=T0 + timedelta(minutes=m)
        )
    assert out is None
    assert len(st.buffer) == 0
