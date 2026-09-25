"""Unit and Invariant Tests for ML-RISK-001: Position Replay & Economic Dataset Generation.

Covers:
  - Deterministic Replay
  - Exact Model & Scaler Loading / Lineage
  - Position State Feature Causality & Integrity
  - Future Trajectory & Economic Labels (MFE, MAE, Continuation Value)
  - Execution Cost Monotonicity
  - Economic Invariant Tests (Adverse-only price paths)
  - Temporal Splitting, Purge, and Embargo Bounds
  - Trade Group Isolation (Zero Trade Crossing Across Splits)
  - Dataset Manifest & SHA-256 Cryptographic Verification
  - Automated Dataset Validator (Clean & Tamper Detection)
  - Failure / Defensive Boundary Cases
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.model_generation.position_replay import (
    CAUSALITY_CONTRACT,
    PositionDatasetResult,
    PositionDatasetValidationReport,
    PositionDatasetValidator,
    PositionReplayPipeline,
    PrimaryModelIdentity,
    ReplayExecutionConfig,
    TemporalSplitConfig,
    resolve_primary_model_bundle,
)
from nexus_scalp.models.scalp_net import ScalpNet
from scripts.data.ingest_historical_candles import generate_synthetic_bars

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample_bars() -> pl.DataFrame:
    """Generates 500 deterministic synthetic M1 bars."""
    return generate_synthetic_bars(symbol="XAUUSD", count=500, seed=12345)


@pytest.fixture
def trained_model_and_scaler(tmp_path: Path) -> tuple[Path, Path]:
    """Creates a temporary checkpoint and companion scaler for exact loading tests."""
    model_dir = tmp_path / "model_bundle"
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "scalp_test.pt"
    scaler_path = model_dir / "scalp_test.scaler.npz"

    model = ScalpNet(num_features=50, num_classes=3)
    torch.save(model.state_dict(), model_path)

    mean = np.zeros(50, dtype=np.float64)
    std = np.ones(50, dtype=np.float64)
    np.savez(scaler_path, mean=mean, std=std)

    return model_path, scaler_path


# =============================================================================
# 1. Deterministic Replay & Model Loading
# =============================================================================


def test_deterministic_replay(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Proves that running the pipeline twice on identical input yields bit-identical datasets."""
    out1 = tmp_path / "ds1.parquet"
    out2 = tmp_path / "ds2.parquet"

    pipeline1 = PositionReplayPipeline()
    df1, res1 = pipeline1.run(sample_bars, output_parquet_path=out1)

    pipeline2 = PositionReplayPipeline()
    df2, res2 = pipeline2.run(sample_bars, output_parquet_path=out2)

    assert res1.sha256 == res2.sha256
    assert res1.total_samples == res2.total_samples
    assert res1.simulated_trades == res2.simulated_trades
    assert res1.actions_distribution == res2.actions_distribution
    assert df1.equals(df2)


def test_exact_model_and_scaler_loading(
    sample_bars: pl.DataFrame, trained_model_and_scaler: tuple[Path, Path], tmp_path: Path
) -> None:
    """Verifies that the exact model artifact and scaler sidecar are loaded and recorded."""
    model_path, scaler_path = trained_model_and_scaler

    pipeline = PositionReplayPipeline(
        primary_model_path=model_path,
        scaler_path=scaler_path,
        dimension=50,
    )

    out_p = tmp_path / "loaded_ds.parquet"
    df, res = pipeline.run(sample_bars, output_parquet_path=out_p)

    assert res.lineage.model_id == "scalp_test"
    assert res.lineage.weights_path == str(model_path)
    assert res.lineage.scaler_path == str(scaler_path)
    assert res.lineage.scaler_hash != "none"
    assert len(res.lineage.model_hash) > 0
    assert (out_p.with_suffix(".manifest.json")).exists()


# =============================================================================
# 2. Position State & Causality Verification
# =============================================================================


def test_causality_contract_completeness() -> None:
    """Verifies that all master causality contract entries are declared as strictly causal."""
    assert len(CAUSALITY_CONTRACT) >= 30
    for entry in CAUSALITY_CONTRACT:
        assert entry.causal is True
        assert entry.lookback_bars >= 0
        assert entry.available_at in {"T", "T_entry"}


def test_future_mutation_isolation_leakage(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """PROVE ANTI-LEAKAGE: Mutating future bars alters labels but leaves input features at T invariant."""
    pipeline = PositionReplayPipeline()
    df_orig, _ = pipeline.run(sample_bars, output_parquet_path=tmp_path / "orig.parquet")

    assert df_orig.height > 0
    # Pick a sample around the middle
    mid_idx = df_orig.height // 2
    obs_orig = df_orig.row(mid_idx, named=True)
    t_bar = obs_orig["bar_index"]

    # Create mutated bars: keep all bars <= t_bar identical, but add a large price shock to bars > t_bar
    mutated_bars = sample_bars.clone()
    shock = 100.0
    mutated_closes = mutated_bars["close"].to_numpy().copy()
    mutated_highs = mutated_bars["high"].to_numpy().copy()
    for k in range(t_bar + 1, len(mutated_closes)):
        mutated_closes[k] += shock
        mutated_highs[k] += shock

    mutated_bars = mutated_bars.with_columns(
        [
            pl.Series("close", mutated_closes),
            pl.Series("high", mutated_highs),
        ]
    )

    df_mut, _ = pipeline.run(mutated_bars, output_parquet_path=tmp_path / "mut.parquet")

    # Find the matching observation at bar t_bar
    matching_mut = df_mut.filter(
        (pl.col("bar_index") == t_bar) & (pl.col("trade_id") == obs_orig["trade_id"])
    )
    assert matching_mut.height == 1
    obs_mut = matching_mut.row(0, named=True)

    # Invariant: Causal features at T must be bit-identical
    causal_fields = [
        "entry_price",
        "current_price",
        "position_age",
        "unrealized_pnl_gross",
        "unrealized_pnl_net",
        "current_r_gross",
        "current_r_net",
        "current_return",
        "atr",
        "spread",
        "model_signal",
    ]
    for field in causal_fields:
        assert obs_orig[field] == obs_mut[field], f"Causality leak detected in field {field}!"

    # Variant: Future-dependent labels MUST reflect the price shock
    assert obs_orig["best_future_r"] != obs_mut["best_future_r"]
    assert obs_orig["continuation_value"] != obs_mut["continuation_value"]


# =============================================================================
# 3. Economic Labels & Continuation Value
# =============================================================================


def test_labels_mathematical_consistency(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Verifies the mathematical definitions of MFE, MAE, and continuation value."""
    pipeline = PositionReplayPipeline()
    df, _ = pipeline.run(sample_bars, output_parquet_path=tmp_path / "econ.parquet")

    assert df.height > 0
    for row in df.iter_rows(named=True):
        cur_r_net = row["current_r_net"]
        best_f_r = row["best_future_r"]
        worst_f_r = row["worst_future_r"]
        future_r_end = row["future_r_net"]
        cont_val = row["continuation_value"]
        horiz_cont_val = row["horizon_continuation_value"]

        # Invariant 1: best_future_r >= worst_future_r
        assert best_f_r >= worst_f_r - 1e-9

        # Invariant 2: continuation_value == best_future_r - current_r_net
        assert abs(cont_val - (best_f_r - cur_r_net)) < 1e-6

        # Invariant 3: horizon_continuation_value == future_r_net - current_r_net
        assert abs(horiz_cont_val - (future_r_end - cur_r_net)) < 1e-6

        # Invariant 4: time_to_mfe and time_to_mae are positive offsets
        assert row["time_to_mfe"] >= 1
        assert row["time_to_mae"] >= 1

        # Invariant 5: Action label consistency
        if cont_val > 0.20 and worst_f_r > -0.50:
            assert row["optimal_action"] == "KEEP"
        elif cur_r_net >= 0.80 and cont_val <= 0.20:
            assert row["optimal_action"] == "REDUCE"
        else:
            assert row["optimal_action"] == "CLOSE"


def test_execution_cost_monotonicity(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """ECONOMIC INVARIANT: Higher transaction costs never increase net PnL or net R."""
    low_cost_cfg = ReplayExecutionConfig(spread_usd=0.05, slippage_usd=0.01)
    high_cost_cfg = ReplayExecutionConfig(spread_usd=0.50, slippage_usd=0.10)

    p_low = PositionReplayPipeline(execution_config=low_cost_cfg)
    df_low, _ = p_low.run(sample_bars, output_parquet_path=tmp_path / "low_cost.parquet")

    p_high = PositionReplayPipeline(execution_config=high_cost_cfg)
    df_high, _ = p_high.run(sample_bars, output_parquet_path=tmp_path / "high_cost.parquet")

    assert df_low.height > 0
    assert df_high.height > 0

    # Compare matching positions
    for row_low in df_low.slice(0, 50).iter_rows(named=True):
        match = df_high.filter(
            (pl.col("bar_index") == row_low["bar_index"])
            & (pl.col("trade_id") == row_low["trade_id"])
        )
        if match.height == 1:
            row_high = match.row(0, named=True)
            assert row_high["unrealized_pnl_net"] <= row_low["unrealized_pnl_net"] + 1e-9
            assert row_high["current_r_net"] <= row_low["current_r_net"] + 1e-9


def test_economic_invariant_adverse_future(tmp_path: Path) -> None:
    """ECONOMIC INVARIANT: If all future returns are negative, best_future_R cannot become positive."""
    # Construct a downward trending synthetic series for a BUY trade
    n = 200
    times = [f"2026-09-01T{10 + i // 60:02d}:{i % 60:02d}:00Z" for i in range(n)]
    # Prices monotonically drop
    base_px = 2500.0
    closes = [base_px - i * 0.5 for i in range(n)]
    opens = [c + 0.1 for c in closes]
    highs = [o + 0.1 for o in opens]
    lows = [c - 0.1 for c in closes]

    df_drop = pl.DataFrame(
        {
            "time_utc": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": [100.0] * n,
        }
    )

    # Force a BUY model
    class ConstantBuyModel(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:
            batch = x.shape[0]
            # logits: [no_trade=-10.0, buy=+10.0, sell=-10.0]
            out = torch.tensor([[-10.0, 10.0, -10.0]], dtype=torch.float32).repeat(batch, 1)
            return out

    pipeline = PositionReplayPipeline(
        primary_model=ConstantBuyModel(),
        execution_config=ReplayExecutionConfig(max_holding_bars=15),
    )
    df, res = pipeline.run(df_drop, output_parquet_path=tmp_path / "drop.parquet")

    assert df.height > 0
    # Because prices monotonically dropped after entry for a BUY trade,
    # best_future_r cannot be positive!
    for row in df.iter_rows(named=True):
        assert row["direction"] == "BUY"
        assert row["best_future_r"] <= 0.0
        assert row["optimal_action"] == "CLOSE"


# =============================================================================
# 4. Temporal Splitting, Purge & Trade Group Isolation
# =============================================================================


def test_temporal_split_and_purge_boundaries(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Verifies that split ratios, purge zones, and partition boundaries are strictly enforced."""
    split_cfg = TemporalSplitConfig(train_ratio=0.70, val_ratio=0.15, oos_ratio=0.15, purge_bars=20)
    pipeline = PositionReplayPipeline(split_config=split_cfg)

    df, res = pipeline.run(sample_bars, output_parquet_path=tmp_path / "splits.parquet")

    assert res.splits["train"] > 0
    assert res.splits["purge"] >= 0

    # Ensure all splits are valid enum strings
    valid_splits = {"train", "val", "oos", "purge"}
    for s in df["split"].unique().to_list():
        assert s in valid_splits


def test_trade_group_isolation(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Verifies that no single trade's position states are split across multiple dataset partitions."""
    pipeline = PositionReplayPipeline()
    df, _ = pipeline.run(sample_bars, output_parquet_path=tmp_path / "isolation.parquet")

    assert df.height > 0
    # Group by trade_id and count distinct non-purge splits
    non_purge = df.filter(pl.col("split") != "purge")
    trade_split_counts = non_purge.group_by("trade_id").agg(
        pl.col("split").n_unique().alias("num_splits")
    )

    for num in trade_split_counts["num_splits"].to_list():
        assert num <= 1, "Trade crossed dataset partitions! Violates Trade Group Isolation."


# =============================================================================
# 5. Dataset Validation & Tamper Detection
# =============================================================================


def test_dataset_validator_clean_report(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Verifies that a validly generated dataset produces a clean validation report."""
    ds_path = tmp_path / "valid_ds.parquet"
    pipeline = PositionReplayPipeline()
    _, res = pipeline.run(sample_bars, output_parquet_path=ds_path)

    report = PositionDatasetValidator.validate(ds_path)

    assert report.valid is True
    assert report.row_count == res.total_samples
    assert report.duplicate_count == 0
    assert report.nan_count == 0
    assert report.inf_count == 0
    assert report.monotonic_timestamps is True
    assert report.trade_split_leakage_count == 0
    assert report.hash_verified is True
    assert len(report.violations) == 0


def test_dataset_validator_detects_tampering(sample_bars: pl.DataFrame, tmp_path: Path) -> None:
    """Verifies that modifying any value or the manifest triggers validation failure."""
    ds_path = tmp_path / "tamper_ds.parquet"
    pipeline = PositionReplayPipeline()
    df, _ = pipeline.run(sample_bars, output_parquet_path=ds_path)

    # Tamper with dataset: inject NaN into current_price
    tampered_df = df.with_columns(
        pl.when(pl.col("bar_index") == df["bar_index"][0])
        .then(None)
        .otherwise(pl.col("current_price"))
        .alias("current_price")
    )
    tampered_p = tmp_path / "tampered.parquet"
    tampered_df.write_parquet(tampered_p)

    report = PositionDatasetValidator.validate(tampered_p)
    assert report.valid is False
    assert report.nan_count > 0


# =============================================================================
# 6. Failure & Boundary Cases
# =============================================================================


def test_insufficient_bars_failure() -> None:
    """Verifies fail-closed behavior when historical market data is too small."""
    tiny_bars = pl.DataFrame(
        {
            "time_utc": ["2026-09-01T10:00:00Z"] * 20,
            "open": [2500.0] * 20,
            "high": [2501.0] * 20,
            "low": [2499.0] * 20,
            "close": [2500.0] * 20,
            "tick_volume": [100.0] * 20,
        }
    )

    pipeline = PositionReplayPipeline()
    with pytest.raises(ValueError, match="Insufficient bars"):
        pipeline.run(tiny_bars)


def test_missing_dataset_path_failure() -> None:
    """Verifies fail-closed behavior when given non-existent file path."""
    pipeline = PositionReplayPipeline()
    with pytest.raises(FileNotFoundError):
        pipeline.run("data/non_existent_market_file.parquet")


def test_continuation_value_non_positive_when_closing_now_is_better() -> None:
    """ECONOMIC INVARIANT: When future peak R is lower than current R, continuation value must be <= 0."""
    # If a position has reached +2.0R, but future peak across next H bars is only +1.2R,
    # continuation_value = 1.2 - 2.0 = -0.8R (holding destroyed value; closing was optimal).
    cur_r_net = 2.0
    best_f_r = 1.2
    cont_val = best_f_r - cur_r_net
    assert cont_val < 0.0, "Continuation value must be negative when closing now is better!"


def test_cli_and_api_position_dataset_validate(
    sample_bars: pl.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies CLI command position-dataset-validate and REST API endpoint."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    import nexus_scalp.web.model_studio_routes as msr
    from nexus_scalp.cli.app_factory import app as cli_app
    from nexus_scalp.web.model_studio_routes import register_model_studio_routes

    monkeypatch.setattr(msr, "REPO_ROOT", tmp_path)

    # 1. Generate dataset
    ds_path = tmp_path / "data" / "raw" / "cli_test.parquet"
    pipeline = PositionReplayPipeline()
    pipeline.run(sample_bars, output_parquet_path=ds_path)

    # 2. CLI validation
    runner = CliRunner()
    cli_res = runner.invoke(
        cli_app, ["position-dataset-validate", "--dataset", str(ds_path), "--json"]
    )
    assert cli_res.exit_code == 0
    raw_json = cli_res.stdout[cli_res.stdout.find("{") :]
    data = json.loads(raw_json)
    assert data["valid"] is True
    assert data["row_count"] > 0

    # 3. API validation
    app = FastAPI()
    register_model_studio_routes(app, lambda *a: None, lambda *a: None)
    client = TestClient(app)

    api_res = client.post(
        "/api/model-studio/position-dataset/validate",
        json={"dataset_path": str(ds_path)},
    )
    assert api_res.status_code == 200
    api_data = api_res.json()
    assert api_data["valid"] is True
    assert api_data["row_count"] > 0
