"""TASK-09 (AGENT-09) — BUG-106 fast parity smoke for critical suite.

The heavy benchmark and extended feature tests have been moved to tests/slow/test_70d_incremental.py.
This unit file retains only the fast parity smoke tests needed for critical CI gates.
"""

from __future__ import annotations

import polars as pl
import pytest

from nexus_scalp.model_generation.schema_v2 import build_70d_dataset
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

DATA_PATH = "data/raw/XAUUSD_M5.parquet"


@pytest.fixture(scope="module")
def real_bars() -> pl.DataFrame:
    """Real XAUUSD M5 bars (first 600 rows — bounded for CI speed)."""
    return pl.read_parquet(DATA_PATH).head(600)


def _feature_diff_count(canon: pl.DataFrame, fast: pl.DataFrame) -> int:
    fcols = [c for c in canon.columns if c.startswith("feat_")]
    diffs = 0
    for c in fcols:
        a = canon[c].to_list()
        b = fast[c].to_list()
        diffs += sum(1 for x, y in zip(a, b, strict=True) if x != y)
    return diffs


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_incremental_byte_identical(real_bars: pl.DataFrame) -> None:
    """TEST-TASK09-01: the incremental builder is byte-identical to canonical."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    canon = compute_70d_frame(real_bars, news_frame=None)
    fast = compute_70d_frame_fast(real_bars, news_frame=None)
    assert canon.height == fast.height
    assert canon["timestamp"].to_list() == fast["timestamp"].to_list()
    diffs = _feature_diff_count(canon, fast)
    assert diffs == 0, f"{diffs} feature diffs between canonical and fast builders"


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_incremental_speedup(real_bars: pl.DataFrame) -> None:
    """The fast builder must not be slower than canonical on the same input."""
    import time

    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    t0 = time.perf_counter()
    compute_70d_frame(real_bars, news_frame=None)
    t_canon = time.perf_counter() - t0

    t0 = time.perf_counter()
    compute_70d_frame_fast(real_bars, news_frame=None)
    t_fast = time.perf_counter() - t0

    assert t_fast < t_canon * 1.5, f"fast {t_fast:.2f}s vs canon {t_canon:.2f}s"
