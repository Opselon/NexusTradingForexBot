"""BUG-106 extended contracts on synthetic bars (tests/slow migration).

``tests/slow/test_70d_incremental.py`` carried ten BUG-106/incremental
contracts behind ``skipif(data/raw/XAUUSD_M5.parquet.exists())`` — a file
git never carries (download-only, data/raw gitignored), so in CI the whole
module collected-and-skipped forever: ten green-looking skips, zero live
coverage. Two of its contracts already run for real in
``tests/unit/test_70d_bug106_incremental_phase19.py`` (byte-identical
parity + the CPU-ratio speedup on synthetic bars); the schema-hash
determinism contract is covered by
``test_qa_deep_70d_contract_properties.py::test_schema_hash_stable_across_calls``.

THIS file ports the contracts that existed nowhere else, replacing the
git-absent parquet with the same deterministic synthetic generator the
phase19 remediation uses. Frame sizes are bounded so the file stays inside
a few seconds locally / a few tens on the 2-core CI runner:

* verify_parity self-check + canonical build path (the builder-level
  contracts) run on a 500-bar frame — the parity machinery itself decides
  equivalence, no assertion is weakened.
* anti-leakage, finite/clip, news-family and the frozen-liquidity-constant
  pins run on the 600-bar frame.
* the old 4K/20K wall-clock benchmark's regression value (the fast path
  must not silently go quadratic) is already carried by the phase19
  CPU-ratio budget; only the larger-input completion is re-proven here,
  without a wall-clock gate (a fixed second figure measures the runner,
  not the code — the same defect class ML-QA-019 remediated).
"""

from __future__ import annotations

import math

import polars as pl
import pytest

from nexus_scalp.features.schema_contract import feature_schema_hash
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.schema_v2 import build_70d_dataset, verify_70d_artifact
from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
from scripts.data.ingest_historical_candles import generate_synthetic_bars

_FRAME_COUNT = 600  # feature-contract frames; yields 600 - 54 warm-up rows
_BUILD_COUNT = 500  # builder-level contracts (parity self-check runs canon too)
_LARGE_COUNT = 1000  # larger-input completion proof
_SEED = 4321


@pytest.fixture(scope="module")
def frame_bars() -> pl.DataFrame:
    """Deterministic synthetic M1 bars for the feature-contract pins."""
    return generate_synthetic_bars(symbol="XAUUSD", count=_FRAME_COUNT, seed=_SEED)


@pytest.fixture(scope="module")
def small_bars() -> pl.DataFrame:
    """Deterministic synthetic M1 bars for the builder-level contracts."""
    return generate_synthetic_bars(symbol="XAUUSD", count=_BUILD_COUNT, seed=_SEED + 1)


def test_incremental_build_with_parity_selfcheck_verifies(small_bars, tmp_path) -> None:
    """build_70d_dataset(incremental=True, verify_parity=True) succeeds and
    the artifact verifies (dimension 70, schema hash) — the self-check the
    builder runs internally must accept the incremental frame."""
    from nexus_scalp.model_generation.schema_v2 import verify_70d_artifact as _verify

    store = ArtifactStore(root=tmp_path / "artifacts")
    handle = build_70d_dataset(
        small_bars,
        timeframe="M1",
        store=store,
        incremental=True,
        verify_parity=True,
        dataset_id="synthetic_parity_selfcheck",
    )
    assert handle.get("status", "ok") in ("ok", "COMPLETED", "built")
    checks = _verify("synthetic_parity_selfcheck", store=store)
    # The PARITY self-check ran inside the build and logged diffs=0. The
    # verifier's top-level ``ok`` is deliberately False for a news_frame=None
    # build (news dims 50..59 are contract zeros with no news source — the
    # verifier rejects it as "a base-50 dataset wearing a 70D label"), so —
    # exactly like test_70d_parity_task3.py and test_ml_phase1_data_contract
    # #.py — the ported contract asserts the structural sub-checks plus the
    # news-disabled shape itself, never a bare ok=True.
    assert int(checks.get("feature_count", 0)) == 70
    assert checks["dimension_ok"] is True
    assert checks["schema_id_ok"] is True
    assert checks["schema_hash_ok"] is True
    assert checks["all_finite"] is True
    assert checks["all_in_range"] is True
    assert checks["timestamp_sane"] is True
    assert checks["duplicate_timestamps"] == 0
    assert checks["duplicate_sample_ids"] == 0
    nc = checks["news_coverage"]
    assert nc["ok"] is False
    assert nc["nonzero_rows"] == 0
    assert nc["news_status"] == "FEATURE_DISABLED"


def test_canonical_build_path_still_default(small_bars, tmp_path) -> None:
    """The canonical (bounded, parity-proven) build path remains usable."""
    store = ArtifactStore(root=tmp_path / "artifacts")
    handle = build_70d_dataset(
        small_bars,
        timeframe="M1",
        store=store,
        dataset_id="synthetic_canonical_check",
    )
    assert handle.get("dataset_id") == "synthetic_canonical_check"


def test_future_bars_cannot_alter_T(frame_bars) -> None:
    """Appending arbitrary future bars must not change any feature at
    existing timestamps (anti-leakage, TEST-BUG106-03)."""
    base = frame_bars.head(400)
    future = frame_bars.slice(400, 60)
    no_future = compute_70d_frame_fast(base, news_frame=None)
    with_future = compute_70d_frame_fast(pl.concat([base, future]), news_frame=None).head(
        no_future.height
    )
    diffs = 0
    for c in no_future.columns:
        if c.startswith("feat_"):
            a = no_future[c].to_list()
            b = with_future[c].to_list()
            diffs += sum(1 for x, y in zip(a, b, strict=True) if x != y)
    assert diffs == 0, f"{diffs} features changed when future bars appended"


def test_70d_output_is_finite_and_clipped(frame_bars) -> None:
    """Exact 70D dimension; all values finite and within the documented
    [-3, +3] clip (TEST-BUG106-09/10)."""
    df = frame_bars.head(300)
    fast = compute_70d_frame_fast(df, news_frame=None)
    fcols = [c for c in fast.columns if c.startswith("feat_")]
    assert len(fcols) == 70, f"expected 70 features, got {len(fcols)}"
    for c in fcols:
        for v in fast[c].to_list():
            assert math.isfinite(v), f"{c} non-finite {v}"
            assert -3.0 <= v <= 3.0, f"{c} out of clip range: {v}"


def test_news_family_neutral_without_news(frame_bars) -> None:
    """News family indices 50..59 preserved exactly and neutral when
    news_frame is None (TEST-BUG106-07)."""
    from nexus_scalp.features.schema_contract import NEWS_10D_NAMES

    df = frame_bars.head(200)
    fast = compute_70d_frame_fast(df, news_frame=None)
    assert len(NEWS_10D_NAMES) == 10
    for idx in range(50, 60):
        col = f"feat_{idx}"
        assert col in fast.columns
        vals = fast[col].to_list()
        assert all(v == 0.0 for v in vals), f"{col} not neutral without news"


def test_liquidity_v11_constants_frozen() -> None:
    """The frozen Liquidity v1.1 parameters are preserved — neither builder
    may alter engine constants (TEST-BUG106-08)."""
    import nexus_scalp.features.liquidity_engine as le
    import nexus_scalp.features.liquidity_engine_opt as leo

    assert leo.LIQUIDITY_ALGORITHM_VERSION == "liquidity-v1.1"
    assert le.SWING_CONFIRM_BARS == 5
    assert le.ATR_PERIOD == 14
    assert le.CONFLUENCE_CUTOFF_ATR == 0.75
    assert le.MIN_ATR == 0.20
    assert hasattr(leo, "LiquidityParams")


def test_scalp_v3_schema_hash_is_deterministic() -> None:
    """Companion to test_schema_hash_stable_across_calls: the 70D contract
    id's hash is stable and identical across calls (TEST-BUG106-13)."""
    h = feature_schema_hash("scalp_v3")
    assert h
    assert h == feature_schema_hash("scalp_v3")


def test_incremental_completes_at_larger_input() -> None:
    """The O(n*window) fast path still completes well past the warm-up at a
    larger input (bounded 1000-bar proof of the old 4K benchmark's
    completion contract; the complexity regression itself is gated by the
    phase19 CPU-ratio budget, not by a wall clock here)."""
    big = generate_synthetic_bars(symbol="XAUUSD", count=_LARGE_COUNT, seed=_SEED + 2)
    fast = compute_70d_frame_fast(big, news_frame=None)
    assert fast.height >= _LARGE_COUNT - 60
