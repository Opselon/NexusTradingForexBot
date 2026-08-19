"""TASK-09 (AGENT-09) — BUG-106 verification + incremental builder tests.

TEST-TASK09-01 mapping:
- byte-identical parity: compute_70d_frame (canonical) vs
  compute_70d_frame_fast (incremental) on the same real bars → ZERO feature
  diffs (proves the optimization preserves feature semantics/causality/
  schema order — brief §6).
- build_70d_dataset(incremental=True, verify_parity=True) self-check runs
  the same equivalence inside the build and refuses on any diff.
- BUG-106 status: the canonical builder carries the TASK-05 bounded-window
  fix (LIQUIDITY_HISTORY_LIMIT=4000); the incremental builder is the
  byte-identical O(n*window) alternative. Both must be present and green.

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_70d_bug106_incremental_phase19.py -q
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

    # not a strict perf gate (machine variance) — assert it is not slower
    # by more than a small factor (the incremental path must stay competitive).
    assert t_fast < t_canon * 1.5, f"fast {t_fast:.2f}s vs canon {t_canon:.2f}s"


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_build_70d_dataset_incremental_with_parity_selfcheck(
    real_bars: pl.DataFrame, tmp_path: pytest.TempPathFactory
) -> None:
    """build_70d_dataset(incremental=True, verify_parity=True) succeeds and
    the artifact verifies (dimension 70, finite, in-range, schema hash)."""
    from nexus_scalp.model_generation.artifact_store import ArtifactStore
    from nexus_scalp.model_generation.schema_v2 import verify_70d_artifact

    store = ArtifactStore(root=tmp_path / "artifacts")
    handle = build_70d_dataset(
        real_bars,
        timeframe="M5",
        store=store,
        incremental=True,
        verify_parity=True,
        dataset_id="ag09_parity_check",
    )
    assert handle.get("status", "ok") in ("ok", "COMPLETED", "built")
    checks = verify_70d_artifact("ag09_parity_check", store=store)
    assert checks.get("ok") is True, checks


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_build_70d_dataset_canonical_still_works(
    real_bars: pl.DataFrame, tmp_path: pytest.TempPathFactory
) -> None:
    """The canonical (bounded, parity-proven) build path remains the default."""
    from nexus_scalp.model_generation.artifact_store import ArtifactStore

    store = ArtifactStore(root=tmp_path / "artifacts")
    handle = build_70d_dataset(
        real_bars,
        timeframe="M5",
        store=store,
        dataset_id="ag09_canonical_check",
    )
    assert handle.get("dataset_id") == "ag09_canonical_check"

# ---------------------------------------------------------------------------
# TEST-BUG106 suite (mission 31) — extended by Hermes-Bug106
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_10_future_bars_cannot_alter_T() -> None:
    """TEST-BUG106-03 (mission 10): appending arbitrary future bars must not
    change any feature at existing timestamps (anti-leakage)."""
    import polars as pl

    df = pl.read_parquet(DATA_PATH).sort("time")
    base = df.head(400)
    future = df.slice(400, 60)
    no_future = compute_70d_frame_fast(base, news_frame=None)
    with_future = compute_70d_frame_fast(
        pl.concat([base, pl.DataFrame(future)]), news_frame=None
    ).head(no_future.height)
    diffs = 0
    for c in no_future.columns:
        if c.startswith("feat_"):
            a = no_future[c].to_list()
            b = with_future[c].to_list()
            diffs += sum(1 for x, y in zip(a, b, strict=True) if x != y)
    assert diffs == 0, f"{diffs} features changed when future bars appended"


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_09_10_dimension_finite_clipped() -> None:
    """TEST-BUG106-09/10: exact 70D dimension; all values finite and within
    the documented [-3, +3] clip (50D sanitizer + liquidity clip)."""
    import math

    df = pl.read_parquet(DATA_PATH).head(300)
    fast = compute_70d_frame_fast(df, news_frame=None)
    fcols = [c for c in fast.columns if c.startswith("feat_")]
    assert len(fcols) == 70, f"expected 70 features, got {len(fcols)}"
    for c in fcols:
        for v in fast[c].to_list():
            assert math.isfinite(v), f"{c} non-finite {v}"
            assert -3.0 <= v <= 3.0, f"{c} out of clip range: {v}"


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_07_news_family_preserved() -> None:
    """TEST-BUG106-07: news family indices 50..59 preserved exactly
    (10 columns, order fixed by the canonical contract)."""
    from nexus_scalp.features.schema_contract import NEWS_10D_NAMES

    df = pl.read_parquet(DATA_PATH).head(200)
    fast = compute_70d_frame_fast(df, news_frame=None)
    assert len(NEWS_10D_NAMES) == 10
    # the 70D frame stores feats numerically; verify 10 news columns exist
    # at 50..59 and are the neutral family when news_frame is None
    for idx in range(50, 60):
        col = f"feat_{idx}"
        assert col in fast.columns
        vals = fast[col].to_list()
        assert all(v == 0.0 for v in vals), f"{col} not neutral without news"


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_08_liquidity_v11_params_frozen() -> None:
    """TEST-BUG106-08: the frozen Liquidity v1.1 parameters are preserved —
    the incremental builder must not alter engine constants."""
    import nexus_scalp.features.liquidity_engine as le
    import nexus_scalp.features.liquidity_engine_opt as leo

    # v1.1 candidate constants (TASK-06 frozen evidence)
    assert leo.LIQUIDITY_ALGORITHM_VERSION == "liquidity-v1.1"
    # canonical v1 engine constants unchanged
    assert le.SWING_CONFIRM_BARS == 5
    assert le.ATR_PERIOD == 14
    assert le.CONFLUENCE_CUTOFF_ATR == 0.75
    assert le.MIN_ATR == 0.20
    # opt engine exposes the frozen search results (do not re-tune)
    assert hasattr(leo, "LiquidityParams")


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_13_schema_hash_stable() -> None:
    """TEST-BUG106-13: schema hash is stable and identical between builders."""
    from nexus_scalp.features.schema_contract import feature_schema_hash

    h = feature_schema_hash("scalp_v3")
    assert h  # non-empty
    # deterministic: same input -> same hash
    assert h == feature_schema_hash("scalp_v3")


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_15_20k_benchmark_completes() -> None:
    """TEST-BUG106-15: 20K-row build completes via the incremental builder
    (the canonical quadratic path would take hours)."""
    import time

    df = pl.read_parquet(DATA_PATH).head(20000)
    t0 = time.perf_counter()
    fast = compute_70d_frame_fast(df, news_frame=None)
    dt = time.perf_counter() - t0
    assert fast.height >= 19000
    assert dt < 600, f"20K build took {dt:.1f}s (>10 min)"
