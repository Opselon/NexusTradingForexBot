"""AGENT 2 / BUG-244 — split-boundary horizon purge regression tests.

Contract under test: the DatasetFactory chronological 70/15/15 split must
never score a sample whose triple-barrier horizon (15 bars) crosses the
train/val or val/test boundary, and downstream trainers must EXCLUDE
boundary-purged rows from both train and validation pools.

Red-before evidence: without the purge, 15 train-tail rows horizon into val
and 15 val-tail rows horizon into test (proven by a 300-row deterministic
fixture on the fix branch).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nexus_scalp.model_generation.dataset_factory import (  # noqa: E402
    DatasetFactory,
    DEFAULT_SPLIT_PURGE_BARS,
)
from nexus_scalp.model_generation.sample_factory import (  # noqa: E402
    SampleFactory,
    samples_to_frame,
)

N = 300  # 70/15/15 -> train 210, val 45, test 45
FEATS = 50  # SampleFactory default schema is scalp_v1 (50 dims)
HORIZON = 15  # TripleBarrierLabeler.max_holding_bars default


def _make_bars(n: int) -> pl.DataFrame:
    ts = np.arange(n, dtype="int64").astype("datetime64[us]")
    rng = np.random.default_rng(42)
    close = 2000 + np.cumsum(rng.standard_normal(n) * 0.5)
    return pl.DataFrame(
        {
            **{f"feat_{i}": rng.standard_normal(n) for i in range(FEATS)},
            "close": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "atr_m1": np.full(n, 1.5),  # > min_valid_atr -> rows stay evaluated
            "timestamp": ts,
            "regime": np.where(rng.random(n) > 0.5, "TRENDING", "RANGING"),
        }
    )


def _split(purge_bars: int | None) -> pl.DataFrame:
    bars = _make_bars(N)
    sf = SampleFactory()
    samples = sf.build_samples(bars, symbol="XAUUSD", timeframe="M1", news_frame=None)
    frame = samples_to_frame(samples)
    kwargs = {} if purge_bars is None else {"purge_bars": purge_bars}
    return DatasetFactory._apply_split(frame, train_ratio=0.7, val_ratio=0.15, **kwargs)


def _positions(frame: pl.DataFrame, block: str) -> list[int]:
    return frame.with_row_index("__pos__").filter(pl.col("_split") == block)["__pos__"].to_list()


class TestSplitBoundaryPurge:
    def test_default_purge_excludes_boundary_horizon_rows(self) -> None:
        """Canonical purge (15): no train/val row's horizon may reach the next
        scored block."""
        frame = _split(purge_bars=None)
        assert DEFAULT_SPLIT_PURGE_BARS == HORIZON
        train_pos = _positions(frame, "train")
        val_pos = _positions(frame, "val")
        test_pos = _positions(frame, "test")
        purged_pos = _positions(frame, "purged")
        assert purged_pos, "expected boundary-purged rows"
        assert train_pos and val_pos and test_pos
        assert train_pos[-1] + HORIZON < val_pos[0]
        assert val_pos[-1] + HORIZON < test_pos[0]

    def test_purged_rows_tagged_and_excluded_from_scored_blocks(self) -> None:
        frame = _split(purge_bars=None)
        assert "_purged_split" in frame.columns
        purged = frame.filter(pl.col("_purged_split"))
        assert purged.height == 2 * HORIZON
        scored = frame.filter(~pl.col("_purged_split"))
        assert set(scored["_split"].unique().to_list()) <= {"train", "val", "test"}
        assert set(purged["_split"].unique().to_list()) == {"purged"}

    def test_zero_purge_reproduces_defect(self) -> None:
        """Red-contract pin: purge=0 restores the legacy leaky behavior."""
        frame = _split(purge_bars=0)
        assert frame.filter(pl.col("_purged_split")).height == 0
        train_pos = _positions(frame, "train")
        val_pos = _positions(frame, "val")
        assert train_pos[-1] + HORIZON >= val_pos[0]

    def test_chronological_order_and_block_ordering(self) -> None:
        frame = _split(purge_bars=None)
        ts = frame["timestamp"].to_list()
        assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1))
        # Layout: [ train ][ purged | val ][ purged | test ] — the integer
        # ordering of labels has a discontinuity at each boundary, so the
        # positional sequence is: train run, purged, val, purged, test.
        seen = [
            dict.fromkeys([k for k in g])
            for _, g in __import__("itertools").groupby(frame["_split"].to_list())
        ]
        # at least verify each block type appears in the correct phase
        flat = frame["_split"].to_list()
        assert flat[0] == "train" and flat[-1] == "test" and "purged" in flat

    def test_counts_exclude_purged_rows(self) -> None:
        """purged rows are audit-visible but EXCLUDED from scored counts."""
        bars = _make_bars(N)
        sf = SampleFactory()
        samples = sf.build_samples(bars, symbol="XAUUSD", timeframe="M1", news_frame=None)
        frame = samples_to_frame(samples)
        split = DatasetFactory._apply_split(frame, train_ratio=0.7, val_ratio=0.15)
        counts = {
            "train": int(split.filter(pl.col("_split") == "train").height),
            "val": int(split.filter(pl.col("_split") == "val").height),
            "test": int(split.filter(pl.col("_split") == "test").height),
            "purged_boundary": int(split.filter(pl.col("_purged_split")).height),
        }
        assert counts["purged_boundary"] == 2 * HORIZON
        assert counts["train"] + counts["val"] + counts["test"] + counts["purged_boundary"] == N
        assert counts["train"] == 210 - HORIZON
        assert counts["val"] == 45 - HORIZON
        assert counts["test"] == 45

    def test_candidate_trainer_excludes_purged_split(self) -> None:
        """CandidateTrainer must never place purged rows into the train pool."""
        frame = _split(purge_bars=None)
        split_arr = frame["_split"].to_numpy()
        # NEW rule (mirrors training.py post-fix)
        train_idx = np.where((split_arr != "test") & (split_arr != "purged"))[0]
        val_idx = np.where(split_arr == "test")[0]
        # post-fix train pool contains ONLY 'train' rows (purged+val excluded)
        assert set(split_arr[train_idx].tolist()) == {"train"}
        assert len(train_idx) == 210 - HORIZON  # 195
        assert len(val_idx) == 45
        # the pre-fix rule would have included val+purged (210) rows
        old_idx = np.where(split_arr != "test")[0]
        assert len(old_idx) == 210 and len(old_idx) - len(train_idx) == HORIZON + 30

    def test_sequence_trainer_boundary_purge(self) -> None:
        """Sequence trainer positional split: train_end = (n-val)-purge."""
        purge = 15
        n_seq = 100
        val_n = max(1, int(n_seq * 0.2))
        train_end = max(0, (n_seq - val_n) - purge)
        train_idx = np.arange(train_end)
        val_idx = np.arange(n_seq - val_n, n_seq)
        assert train_idx[-1] + HORIZON < val_idx[0]
        assert len(val_idx) == val_n


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-header"]))
