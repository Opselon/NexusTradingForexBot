"""Unit tests for Model Studio End-to-End Dataset, Training & Position-Dataset Pipeline."""

import json
from pathlib import Path

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.cli.app_factory import app as cli_app
from nexus_scalp.model_generation.position_dataset_generator import generate_position_dataset
from nexus_scalp.web.model_studio_routes import (
    ModelStudioDownloadRequest,
    ModelStudioInspectFeaturesRequest,
    ModelStudioPositionDatasetRequest,
    ModelStudioTrainRequest,
    execute_download,
    execute_generate_position_dataset,
    execute_inspect_features,
    execute_train,
    register_model_studio_routes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def api_client() -> TestClient:
    app = FastAPI()
    register_model_studio_routes(app, lambda *a: None, lambda *a: None)
    return TestClient(app)


# =============================================================================
# 1. Dataset Download & Ingestion Tests
# =============================================================================


def test_execute_download_synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies downloading synthetic candles with timeframe support and schema validation."""
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    for tf in ["M1", "M3", "M5", "M15"]:
        req = ModelStudioDownloadRequest(
            symbol="XAUUSD",
            timeframe=tf,
            bars=1000,
            source="synthetic",
        )
        res = execute_download(req)
        assert res["status"] == "OK"
        assert res["rows"] == 1000
        assert res["symbol"] == "XAUUSD"
        assert res["timeframe"] == tf
        assert res["bytes_written"] > 0
        assert (tmp_path / res["dataset_path"]).is_file()

        # Check parquet columns
        df = pl.read_parquet(tmp_path / res["dataset_path"])
        assert set(df.columns) >= {"time", "open", "high", "low", "close", "tick_volume"}


def test_api_route_dataset_download(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests POST /api/model-studio/datasets/download endpoint."""
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    resp = api_client.post(
        "/api/model-studio/datasets/download",
        json={"symbol": "EURUSD", "timeframe": "M5", "bars": 1000, "source": "synthetic"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["symbol"] == "EURUSD"
    assert data["timeframe"] == "M5"
    assert data["rows"] == 1000


# =============================================================================
# 2. Feature Inspection & Normalization Tests (50D & 70D)
# =============================================================================


def test_execute_inspect_features_50d_and_70d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies feature inspection, stats, and normalization for both 50D and 70D."""
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)

    # 50D Inspection
    req_50 = ModelStudioInspectFeaturesRequest(dataset_path="", dimension=50, max_rows=300)
    res_50 = execute_inspect_features(req_50)
    assert res_50["status"] == "OK"
    assert res_50["dimension"] == 50
    assert res_50["total_features"] == 50
    assert res_50["healthy_features"] > 35
    assert len(res_50["features"]) == 50
    assert res_50["scaler_ready"] is True

    # 70D Inspection
    req_70 = ModelStudioInspectFeaturesRequest(dataset_path="", dimension=70, max_rows=300)
    res_70 = execute_inspect_features(req_70)
    assert res_70["status"] == "OK"
    assert res_70["dimension"] == 70
    assert res_70["total_features"] == 70
    assert len(res_70["features"]) == 70

    # Ensure news (50..59) and liquidity (60..69) families exist in 70D
    families = {f["family"] for f in res_70["features"]}
    assert families == {"BASE", "NEWS", "LIQUIDITY"}


def test_api_route_inspect_features(api_client: TestClient) -> None:
    """Tests POST /api/model-studio/datasets/inspect-features endpoint."""
    resp = api_client.post(
        "/api/model-studio/datasets/inspect-features",
        json={"dimension": 50, "max_rows": 200},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["dimension"] == 50
    assert len(data["features"]) == 50


# =============================================================================
# 3. Layer-2 Position Management Dataset Generation Tests
# =============================================================================


def test_generate_position_dataset_generator(tmp_path: Path) -> None:
    """Verifies generation of position dataset with mathematical labeling and anti-leakage."""
    out_file = tmp_path / "test_positions.parquet"
    res = generate_position_dataset(
        bars_limit=500,
        max_holding_bars=20,
        target_atr_multiplier=2.0,
        stop_loss_atr_multiplier=1.5,
        friction_pips=0.25,
        output_path=out_file,
    )
    assert res.status == "OK"
    assert res.total_samples > 0
    assert res.simulated_trades > 0
    assert out_file.is_file()

    # Verify Parquet content & anti-leakage schemas
    df = pl.read_parquet(out_file)
    assert set(df.columns) >= {
        "timestamp",
        "direction",
        "entry_price",
        "current_price",
        "unrealized_pnl_r",
        "position_age_bars",
        "continuation_value",
        "best_future_r",
        "worst_future_r",
        "optimal_action",
        "split",
    }

    # Verify action labels are within domain
    actions = set(df["optimal_action"].unique().to_list())
    assert actions.issubset({"KEEP", "CLOSE", "REDUCE"})

    # Verify chronological splits
    splits = set(df["split"].unique().to_list())
    assert "train" in splits


def test_api_route_position_dataset_generate(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests POST /api/model-studio/position-dataset/generate endpoint."""
    monkeypatch.setattr(
        "nexus_scalp.model_generation.position_dataset_generator.Path.cwd", lambda: tmp_path
    )

    req = {
        "bars_limit": 400,
        "max_holding_bars": 15,
        "target_atr_multiplier": 2.0,
        "friction_pips": 0.2,
    }
    resp = api_client.post("/api/model-studio/position-dataset/generate", json=req)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["total_samples"] > 0
    assert "KEEP" in data["actions_distribution"]


# =============================================================================
# 4. Real Model Training Dispatch & Progress Tests
# =============================================================================


def test_execute_train_real_pytorch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests real PyTorch model training loop and checkpoint persistence."""
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    (tmp_path / "artifacts" / "model_generation" / "checkpoints").mkdir(parents=True, exist_ok=True)

    req = ModelStudioTrainRequest(
        dimension=50,
        epochs=2,
        batch_size=64,
        learning_rate=0.001,
        seed=123,
    )
    res = execute_train(req)
    assert res["status"] == "OK"
    assert res["epochs_completed"] == 2
    assert "loss" in res["state"]
    assert res["state"]["status"] == "DONE"
    assert Path(tmp_path / res["checkpoint_path"]).is_file()


def test_api_route_train_and_progress(
    api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests POST /api/model-studio/train and GET /api/model-studio/train/progress."""
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    (tmp_path / "artifacts" / "model_generation" / "checkpoints").mkdir(parents=True, exist_ok=True)

    train_resp = api_client.post(
        "/api/model-studio/train",
        json={"dimension": 50, "epochs": 2, "batch_size": 32, "learning_rate": 0.001},
    )
    assert train_resp.status_code == 200
    train_data = train_resp.json()
    assert train_data["status"] == "OK"

    prog_resp = api_client.get("/api/model-studio/train/progress")
    assert prog_resp.status_code == 200
    prog_data = prog_resp.json()
    assert prog_data["status"] == "OK"
    assert prog_data["progress"]["status"] == "DONE"


# =============================================================================
# 5. CLI Commands Integration Tests
# =============================================================================


def test_cli_dataset_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests CLI commands dataset-download, dataset-inspect, and position-dataset-generate."""
    from typer.testing import CliRunner

    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()

    # 1. Download
    dl_res = runner.invoke(
        cli_app,
        ["dataset-download", "--symbol", "XAUUSD", "--timeframe", "M5", "--bars", "500", "--json"],
    )
    assert dl_res.exit_code == 0
    raw_dl = dl_res.stdout[dl_res.stdout.find("{") :]
    dl_data = json.loads(raw_dl)
    assert dl_data["status"] == "OK"
    ds_path = str(tmp_path / dl_data["dataset_path"])

    # 2. Inspect
    ins_res = runner.invoke(
        cli_app,
        ["dataset-inspect", "--dataset", ds_path, "--dim", "50", "--json"],
    )
    assert ins_res.exit_code == 0
    raw_ins = ins_res.stdout[ins_res.stdout.find("{") :]
    ins_data = json.loads(raw_ins)
    assert ins_data["status"] == "OK"
    assert ins_data["dimension"] == 50

    # 3. Position Dataset Generate
    pos_res = runner.invoke(
        cli_app,
        ["position-dataset-generate", "--dataset", ds_path, "--bars", "400", "--json"],
    )
    assert pos_res.exit_code == 0
    raw_pos = pos_res.stdout[pos_res.stdout.find("{") :]
    pos_data = json.loads(raw_pos)
    assert pos_data["status"] == "OK"
    assert pos_data["total_samples"] > 0
