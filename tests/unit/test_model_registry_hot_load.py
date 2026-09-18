"""Comprehensive Unit Tests for Model Registry, SQLite Catalog & AI Hub Hot-Loader.

Validates the full 15-feature API-first suite:
- SQLite persistence, schema versioning, audit trail
- Hot-loading PyTorch ScalpNet weights and scaler sidecars into live engine
- Gated fine-tuning and backbone freezing
- Pre-load integrity verification battery (numerical finiteness, variance, smoke inference)
- 1-click champion rollback to previous model from audit history
- REST API endpoints and CLI commands
"""

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from nexus_scalp.cli.main import app as cli_app
from nexus_scalp.model_generation.model_registry import ModelRecord, ModelRegistry
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.web.model_studio_routes import (
    ModelStudioFineTuneRequest,
    ModelStudioHotLoadRequest,
    ModelStudioVerifyRequest,
    execute_active_model,
    execute_benchmark_live,
    execute_canary,
    execute_delete_model,
    execute_drift_check,
    execute_export_model,
    execute_fine_tune,
    execute_get_scaler,
    execute_history,
    execute_hot_load,
    execute_list_models,
    execute_register_model,
    execute_rollback,
    execute_tag_model,
    execute_verify,
    register_model_studio_routes,
)


@pytest.fixture
def isolated_db(tmp_path: Path) -> ModelRegistry:
    db_file = tmp_path / "test_models.db"
    return ModelRegistry(db_file)


@pytest.fixture
def studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Creates isolated REPO_ROOT and test model checkpoints."""
    repo = tmp_path / "repo"
    ckpt_dir = repo / "artifacts" / "model_generation" / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    (repo / "data" / "raw").mkdir(parents=True, exist_ok=True)

    # Monkeypatch REPO_ROOT in model_studio_routes
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", repo)

    # Create dummy 50D model checkpoint and scaler sidecar
    m50 = ScalpNet(num_features=50, num_classes=3)
    p50 = ckpt_dir / "test_model_50d.pt"
    torch.save(m50.state_dict(), p50)
    sc50 = ckpt_dir / "test_model_50d.scaler.npz"
    np.savez(
        sc50, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32), dimension=50
    )

    # Create dummy 70D model checkpoint and scaler sidecar
    m70 = ScalpNet(num_features=70, num_classes=3)
    p70 = ckpt_dir / "test_model_70d.pt"
    torch.save(m70.state_dict(), p70)
    sc70 = ckpt_dir / "test_model_70d.scaler.npz"
    np.savez(
        sc70,
        mean=np.ones(70, dtype=np.float32) * 0.5,
        std=np.ones(70, dtype=np.float32) * 1.2,
        dimension=70,
    )

    # Isolated SQLite registry
    test_db = tmp_path / "registry.db"
    reg = ModelRegistry(test_db)
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.get_model_registry", lambda: reg)

    return repo, test_db


@pytest.fixture
def api_client(studio_env: tuple[Path, Path]) -> TestClient:
    app = FastAPI()
    register_model_studio_routes(app, lambda *a: None, lambda *a: None)
    return TestClient(app)


# =============================================================================
# 1. SQLite ModelRegistry Persistence & Audit History Tests
# =============================================================================


def test_model_registry_crud(isolated_db: ModelRegistry) -> None:
    """Tests model checkpoint registration, retrieval, listing, and updates."""
    rec = ModelRecord(
        id="model_xauusd_v1",
        name="model_xauusd_v1.pt",
        version="1.0.0",
        dimension=50,
        architecture="ScalpNet",
        weights_path="checkpoints/model_xauusd_v1.pt",
        sha256="abcdef1234567890",
        epochs=5,
        final_loss=0.4521,
        final_val_loss=0.4612,
        dataset_path="data/raw/xauusd_m5.parquet",
        fine_tune_enabled=True,
        stage="STAGING",
    )
    saved = isolated_db.register_model(rec)
    assert saved.id == "model_xauusd_v1"

    # Fetch
    fetched = isolated_db.get_model("model_xauusd_v1")
    assert fetched is not None
    assert fetched.dimension == 50
    assert fetched.final_loss == 0.4521

    # List
    all_models = isolated_db.list_models()
    assert len(all_models) == 1
    assert all_models[0].id == "model_xauusd_v1"

    # Set Active Champion
    isolated_db.set_active_champion("model_xauusd_v1", operator="TEST_USER", fine_tune_enabled=True)
    active = isolated_db.get_active_model()
    assert active is not None
    assert active.id == "model_xauusd_v1"
    assert active.is_active is True

    # Audit history
    hist = isolated_db.get_load_history()
    assert len(hist) == 1
    assert hist[0]["model_id"] == "model_xauusd_v1"
    assert hist[0]["action"] == "HOT_LOAD"
    assert hist[0]["operator"] == "TEST_USER"


def test_model_registry_atomic_rollback(isolated_db: ModelRegistry) -> None:
    """Tests history tracking and rollback to previous active champion."""
    # Register model A and activate
    rec_a = ModelRecord(id="model_A", name="a.pt", dimension=50, weights_path="a.pt", sha256="shaA")
    isolated_db.register_model(rec_a)
    isolated_db.set_active_champion("model_A", operator="OP1")

    # Register model B and activate
    rec_b = ModelRecord(id="model_B", name="b.pt", dimension=50, weights_path="b.pt", sha256="shaB")
    isolated_db.register_model(rec_b)
    isolated_db.set_active_champion("model_B", operator="OP2")

    # Current active must be B
    assert isolated_db.get_active_model().id == "model_B"  # type: ignore

    # Previous active model must be A
    prev = isolated_db.get_previous_active_model()
    assert prev is not None
    assert prev.id == "model_A"


# =============================================================================
# 2. Hot-Load Execution & Live Runtime Integration Tests
# =============================================================================


def test_hot_load_50d_and_70d(studio_env: tuple[Path, Path]) -> None:
    """Verifies hot-loading weights, sidecar scaler attachment, and dimension updates."""
    # Hot-load 50D model
    req50 = ModelStudioHotLoadRequest(
        model_id="test_model_50d",
        fine_tune_enabled=True,
        attach_scaler=True,
        operator="TEST_OPERATOR",
    )
    res50 = execute_hot_load(req50)
    assert res50["status"] == "OK"
    assert res50["model_id"] == "test_model_50d"
    assert res50["dimension"] == 50
    assert res50["scaler_attached"] is True
    assert res50["fine_tune_enabled"] is True
    assert res50["warmup_latency_us"] > 0

    # Verify active runtime reflection
    act = execute_active_model()
    assert act["status"] == "OK"
    assert act["active_model"]["model_id"] == "test_model_50d"
    assert act["active_model"]["dimension"] == 50
    assert act["active_model"]["fine_tune_enabled"] is True

    # Hot-load 70D model
    req70 = ModelStudioHotLoadRequest(
        model_id="test_model_70d",
        fine_tune_enabled=False,
        attach_scaler=True,
        operator="TEST_OPERATOR",
    )
    res70 = execute_hot_load(req70)
    assert res70["status"] == "OK"
    assert res70["model_id"] == "test_model_70d"
    assert res70["dimension"] == 70
    assert res70["scaler_attached"] is True
    assert res70["fine_tune_enabled"] is False

    # Verify active runtime switched to 70D
    act2 = execute_active_model()
    assert act2["active_model"]["model_id"] == "test_model_70d"
    assert act2["active_model"]["dimension"] == 70


def test_hot_load_rollback(studio_env: tuple[Path, Path]) -> None:
    """Verifies 1-click rollback restoring the previous champion in memory."""
    # Hot load model 50d then 70d
    execute_hot_load(ModelStudioHotLoadRequest(model_id="test_model_50d"))
    execute_hot_load(ModelStudioHotLoadRequest(model_id="test_model_70d"))

    assert execute_active_model()["active_model"]["model_id"] == "test_model_70d"

    # Rollback
    rb_res = execute_rollback()
    assert rb_res["status"] == "OK"
    assert rb_res["model_id"] == "test_model_50d"

    # Active model must now be 50d
    assert execute_active_model()["active_model"]["model_id"] == "test_model_50d"


# =============================================================================
# 3. Verification Battery & Pre-Load Quality Checks
# =============================================================================


def test_verify_checkpoint_integrity(studio_env: tuple[Path, Path]) -> None:
    """Tests the 5-point verification battery on valid checkpoint."""
    req = ModelStudioVerifyRequest(model_id="test_model_50d")
    res = execute_verify(req)
    assert res["status"] == "OK"
    assert res["all_passed"] is True
    assert res["dimension"] == 50

    check_names = [c["name"] for c in res["checks"]]
    assert "FILE_EXISTS" in check_names
    assert "SAFE_DESERIALIZATION" in check_names
    assert "NUMERICAL_FINITENESS" in check_names
    assert "WEIGHT_VARIANCE" in check_names
    assert "ARCHITECTURE_LOAD" in check_names
    assert "SMOKE_INFERENCE" in check_names
    assert "SCALER_SIDECAR" in check_names


def test_inspect_scaler_vectors(studio_env: tuple[Path, Path]) -> None:
    """Tests vector extraction of mean, std, clamping, and zero variance indicators."""
    res = execute_get_scaler("test_model_70d")
    assert res["status"] == "OK"
    assert res["dimension"] == 70
    assert res["features_count"] == 70
    assert len(res["features"]) == 70
    assert res["features"][0]["mean"] == 0.5
    assert res["features"][0]["std"] == 1.2
    assert res["features"][0]["zero_variance"] is False


# =============================================================================
# 4. Fine-Tuning & Backbone Freezing
# =============================================================================


def test_fine_tune_with_frozen_backbone(studio_env: tuple[Path, Path]) -> None:
    """Tests fine-tuning from base checkpoint with classifier-only training."""
    # Activate base model
    execute_hot_load(ModelStudioHotLoadRequest(model_id="test_model_50d", fine_tune_enabled=True))

    ft_req = ModelStudioFineTuneRequest(
        base_model_id="test_model_50d",
        epochs=2,
        learning_rate=0.001,
        freeze_backbone=True,
    )
    ft_res = execute_fine_tune(ft_req)
    assert ft_res["status"] == "OK"
    assert ft_res["parent_model_id"] == "test_model_50d"
    assert ft_res["frozen_parameters"] > 0
    assert ft_res["trainable_parameters"] > 0
    assert ft_res["final_loss"] > 0

    # Ensure fine-tuned model checkpoint file was written
    repo, _ = studio_env
    assert (repo / ft_res["weights_path"]).is_file()
    assert (repo / ft_res["scaler_path"]).is_file()


# =============================================================================
# 5. REST API Endpoints (15 Capabilities)
# =============================================================================


def test_api_models_endpoints(api_client: TestClient) -> None:
    """Tests REST endpoints for models list, hot-load, active, verify, canary, history, export, tag, and drift."""
    # 1. List
    r_list = api_client.get("/api/model-studio/models")
    assert r_list.status_code == 200
    assert r_list.json()["status"] == "OK"

    # 2. Hot-Load
    r_load = api_client.post(
        "/api/model-studio/models/hot-load",
        json={"model_id": "test_model_50d", "fine_tune_enabled": True},
    )
    assert r_load.status_code == 200
    assert r_load.json()["model_id"] == "test_model_50d"

    # 3. Active
    r_act = api_client.get("/api/model-studio/models/active")
    assert r_act.status_code == 200
    assert r_act.json()["active_model"]["model_id"] == "test_model_50d"

    # 4. Verify
    r_ver = api_client.post(
        "/api/model-studio/models/verify",
        json={"model_id": "test_model_50d"},
    )
    assert r_ver.status_code == 200
    assert r_ver.json()["all_passed"] is True

    # 5. Scaler
    r_sc = api_client.get("/api/model-studio/models/test_model_50d/scaler")
    assert r_sc.status_code == 200
    assert r_sc.json()["dimension"] == 50

    # 6. Canary
    r_can = api_client.post(
        "/api/model-studio/models/canary",
        json={"model_id": "test_model_70d"},
    )
    assert r_can.status_code == 200
    assert r_can.json()["canary_model_id"] == "test_model_70d"

    # 7. History
    r_hist = api_client.get("/api/model-studio/models/history")
    assert r_hist.status_code == 200
    assert len(r_hist.json()["history"]) >= 1

    # 8. Export
    r_exp = api_client.post(
        "/api/model-studio/models/export",
        json={"model_id": "test_model_50d"},
    )
    assert r_exp.status_code == 200
    assert r_exp.json()["size_bytes"] > 0

    # 9. Tag
    r_tag = api_client.post(
        "/api/model-studio/models/tag",
        json={"model_id": "test_model_50d", "stage": "CHAMPION", "fine_tune_enabled": True},
    )
    assert r_tag.status_code == 200
    assert r_tag.json()["model"]["stage"] == "CHAMPION"

    # 10. Benchmark Live
    r_bench = api_client.post("/api/model-studio/models/benchmark-live")
    assert r_bench.status_code == 200
    assert r_bench.json()["p50_latency_us"] > 0

    # 11. Drift Check
    r_drift = api_client.post(
        "/api/model-studio/models/drift-check",
        json={"dimension": 50, "max_rows": 200},
    )
    assert r_drift.status_code == 200
    assert "overall_drift_score" in r_drift.json()

    # 12. Delete Active Champion Protection (must fail with 400)
    r_del = api_client.delete("/api/model-studio/models/test_model_50d")
    assert r_del.status_code == 400


# =============================================================================
# 6. CLI Commands Tests
# =============================================================================


def test_cli_model_commands(studio_env: tuple[Path, Path]) -> None:
    """Verifies Typer CLI commands for model management."""
    runner = CliRunner()

    # List
    res_list = runner.invoke(cli_app, ["model-list", "--json"])
    assert res_list.exit_code == 0
    data_list = json.loads(res_list.output)
    assert data_list["status"] == "OK"

    # Hot Load
    res_load = runner.invoke(cli_app, ["model-hot-load", "test_model_50d", "--fine-tune", "--json"])
    assert res_load.exit_code == 0
    data_load = json.loads(res_load.output)
    assert data_load["model_id"] == "test_model_50d"
    assert data_load["fine_tune_enabled"] is True

    # Active
    res_act = runner.invoke(cli_app, ["model-active", "--json"])
    assert res_act.exit_code == 0
    data_act = json.loads(res_act.output)
    assert data_act["active_model"]["model_id"] == "test_model_50d"

    # Verify
    res_ver = runner.invoke(cli_app, ["model-verify", "test_model_50d", "--json"])
    assert res_ver.exit_code == 0
    data_ver = json.loads(res_ver.output)
    assert data_ver["all_passed"] is True
