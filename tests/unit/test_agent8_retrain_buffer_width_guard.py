"""AGENT-8 RED regression: BUG-243 mixed-width retrain buffer None-poisoning.

Proves the defect class (before fix): a deque mixing 70D canonical records and
50D fallback records (the documented BUG-185 fallback in _on_new_bar, and the
width-flip restart path) produces a polars frame with None feature columns,
which the trainer's nan_to_num converts into silent 0.0 fabrications.

The fix contract (defensive filter before materialization): after the fix,
the filter drops foreign-width rows so the frame is None-free in feat_* cols.
This test targets the pure data contract (no engine construction needed),
mirroring _trigger_async_online_fine_tune's pl.DataFrame(list(records)).
"""

from __future__ import annotations

from collections import deque

import polars as pl
import pytest


def _make_record(width: int, value: float) -> dict:
    rec = {f"feat_{i}": value for i in range(width)}
    rec.update(close=1.0, high=1.1, low=0.9, open=1.0, spread=0.2, atr_m1=1.5)
    return rec


def _filter_by_width(records: deque, expected_width: int) -> deque:
    """Mirror of the LiveEngine BUG-243 defensive filter (extracted contract).

    Kept in the test to pin the CONTRACT: only rows whose feat_* width equals
    the bound trainer width survive. The engine implements the same predicate
    inline on the hot path (bar-close cadence only, O(n) amortized).
    """
    return deque(
        (r for r in records if sum(1 for k in r if str(k).startswith("feat_")) == expected_width),
        maxlen=len(records) or None,
    )


def test_red_mixed_width_frame_materializes_none_features():
    """RED: proves the None-injection defect class exists without the filter."""
    buf = deque(maxlen=4000)
    buf.append(_make_record(70, 0.1))
    buf.append(_make_record(50, 0.2))  # fallback row after liquidity refusal
    df = pl.DataFrame(list(buf))
    null_counts = df.select(pl.all().null_count()).row(0, named=True)
    poisoned = {k: v for k, v in null_counts.items() if v and k.startswith("feat_")}
    # The defect: 20 columns carry None for the 50D row.
    assert poisoned, "expected None-poisoning (this test pins the raw defect)"


def test_width_filter_removes_foreign_rows_before_materialization():
    """GREEN contract: after the defensive filter, no feat_* column has nulls."""
    buf = deque(maxlen=4000)
    buf.append(_make_record(70, 0.1))
    buf.append(_make_record(50, 0.2))
    buf.append(_make_record(70, 0.3))

    expected_width = 70
    filtered = _filter_by_width(buf, expected_width)
    assert len(filtered) == 2
    df = pl.DataFrame(list(filtered))
    null_counts = df.select(pl.all().null_count()).row(0, named=True)
    poisoned = {k: v for k, v in null_counts.items() if v and k.startswith("feat_")}
    assert not poisoned, f"feature nulls survived the width filter: {poisoned}"


def test_width_filter_keeps_pure_buffer_untouched():
    """A homogeneous buffer (all 50D) must pass through unchanged."""
    buf = deque(maxlen=4000)
    for i in range(5):
        buf.append(_make_record(50, 0.1 * i))
    filtered = _filter_by_width(buf, 50)
    assert len(filtered) == 5
    df = pl.DataFrame(list(filtered))
    null_counts = df.select(pl.all().null_count()).row(0, named=True)
    poisoned = {k: v for k, v in null_counts.items() if v and k.startswith("feat_")}
    assert not poisoned


def test_width_filter_handles_reverse_order_flip():
    """Width flip in the other direction (50D champion restart after 70D)."""
    buf = deque(maxlen=4000)
    buf.append(_make_record(50, 0.5))
    buf.append(_make_record(70, 0.7))
    filtered = _filter_by_width(buf, 50)
    assert len(filtered) == 1
    df = pl.DataFrame(list(filtered))
    null_counts = df.select(pl.all().null_count()).row(0, named=True)
    poisoned = {k: v for k, v in null_counts.items() if v and k.startswith("feat_")}
    assert not poisoned


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-header", "-x"]))
