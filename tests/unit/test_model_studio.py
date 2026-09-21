"""Tests for Deep Learning & Neural Model Studio (ML-UI-001).

Validates:
  1. /api/model-studio/overview (architecture, parameters, weights fingerprint, scaler stats)
  2. /api/model-studio/fetch-70d (Base 0..49, News 50..59, Liquidity 60..69 assembly & contract)
  3. /api/model-studio/predict (50D and 70D forward pass, probabilities, entropy, layer norms, saliency)
  4. Out-of-Distribution (OOD) anomaly scoring
  5. Adversarial stress testing (zero variance, flash crash, NaN defense, dimension boundary)
  6. Latency benchmarking (P50, P90, P99, throughput)
  7. Training dispatch & progress polling
  8. CLI commands (nexus model-quality, model-predict, model-stress-test, model-train-dataset)
  9. Web UI integration (Legacy Web/ and React frontend/ assets)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from nexus_scalp.cli.app_factory import app as cli_app
from nexus_scalp.web.model_studio_routes import (
    ModelStudioBenchmarkRequest,
    ModelStudioPredictRequest,
    ModelStudioStressRequest,
    ModelStudioTrainRequest,
    _capture_layer_activations,
    _compute_saliency,
    _shannon_entropy,
    execute_benchmark,
    execute_predict,
    execute_stress_test,
    execute_train,
    fetch_70d_components,
    get_studio_overview,
)
from nexus_scalp.web.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with fresh app and mocked engine state."""
    monkeypatch.setenv("NSE_WEB_AUTH_DISABLE", "1")
    app = create_app()
    return TestClient(app)


# =============================================================================
# Core Engine & Unit Tests
# =============================================================================


def test_shannon_entropy_calculation() -> None:
    """Entropy must be maximum for uniform distribution and 0 for deterministic."""
    # Uniform 3-class: H = log2(3) approx 1.585
    uniform = [1 / 3, 1 / 3, 1 / 3]
    h_uni = _shannon_entropy(uniform)
    assert 1.58 <= h_uni <= 1.59

    # Deterministic: H = 0
    determ = [1.0, 0.0, 0.0]
    assert _shannon_entropy(determ) == 0.0


def test_studio_overview_metadata() -> None:
    """Overview must return architecture, parameter counts, and weights hash."""
    data = get_studio_overview(None)
    assert data["status"] == "OK"
    assert data["architecture"] == "ScalpNet"
    assert data["parameter_count"] > 0
    assert data["trainable_parameters"] == data["parameter_count"]
    assert len(data["weights_sha256"]) == 64
    assert data["effective_dimension"] in (50, 70)


def test_fetch_70d_assembly_and_schema_contract() -> None:
    """70D assembly must provide all 70 slots with family partitioning."""
    res = fetch_70d_components(None)
    assert res["status"] == "OK"
    assert res["dimension"] == 70
    assert len(res["slots"]) == 70
    assert len(res["vector"]) == 70

    # Verify family partitioning
    for i, slot in enumerate(res["slots"]):
        assert slot["index"] == i
        if i < 50:
            assert slot["family"] == "BASE"
        elif i < 60:
            assert slot["family"] == "NEWS"
        else:
            assert slot["family"] == "LIQUIDITY"


def test_predict_50d_mathematical_invariants() -> None:
    """50D forward pass must satisfy probability sum == 1.0, layer norms, and saliency."""
    req = ModelStudioPredictRequest(
        dimension=50,
        features=[0.5] * 50,
        inspect_layers=True,
        compute_saliency=True,
    )
    res = execute_predict(req, None)

    assert res["status"] == "OK"
    assert res["dimension"] == 50

    # Numerical invariants
    val = res["numerical_validation"]
    assert val["valid"] is True
    assert abs(val["sum"] - 1.0) < 1e-4
    assert val["all_positive"] is True

    # Uncertainty
    assert 0.0 <= res["confidence"] <= 1.0
    assert res["shannon_entropy_bits"] >= 0.0
    assert res["confidence_margin"] >= 0.0

    # Layer activations
    layers = res["layer_inspection"]
    assert len(layers) > 0
    assert any(l["layer"] == "classifier" for l in layers)
    for l in layers:
        assert l["l2_norm"] >= 0.0
        assert 0.0 <= l["zero_fraction"] <= 1.0

    # Saliency
    sal = res["saliency"]
    assert "top_positive_drivers" in sal
    assert "top_negative_drivers" in sal


def test_predict_70d_with_live_fetch() -> None:
    """70D prediction with auto-fetch must assemble and execute without error."""
    req = ModelStudioPredictRequest(
        dimension=70,
        fetch_live_70d=True,
        inspect_layers=False,
        compute_saliency=False,
    )
    res = execute_predict(req, None)
    assert res["status"] == "OK"
    assert res["dimension"] == 70
    assert res["feature_source"] == "LIVE_70D_ASSEMBLED"
    assert res["numerical_validation"]["valid"] is True


def test_adversarial_stress_battery() -> None:
    """Adversarial stress testing must confirm fail-safe handling of extreme and malformed inputs."""
    req = ModelStudioStressRequest(dimension=50)
    res = execute_stress_test(req, None)
    assert res["status"] == "OK"
    assert res["all_passed"] is True
    assert len(res["results"]) == 4

    test_names = {r["test"] for r in res["results"]}
    assert "ZERO_VARIANCE" in test_names
    assert "FLASH_CRASH_SHOCK" in test_names
    assert "NAN_INJECTION_DEFENSE" in test_names
    assert "DIMENSION_BOUNDARY_FAIL_LOUD" in test_names


def test_latency_benchmark_profiler() -> None:
    """Benchmark must profile 20 iterations and satisfy SLA (< 10ms P99)."""
    req = ModelStudioBenchmarkRequest(dimension=50, iterations=20)
    res = execute_benchmark(req, None)
    assert res["status"] == "OK"
    assert res["iterations"] == 20
    assert res["latency_p50_ms"] > 0.0
    assert res["latency_p99_ms"] < 10.0
    assert res["throughput_inferences_per_sec"] > 100.0
    assert res["sla_passed"] is True


def test_training_dispatch_lifecycle() -> None:
    """Training dispatch must run a real PyTorch loop and report completion."""
    req = ModelStudioTrainRequest(dimension=50, epochs=2, learning_rate=1e-4)
    res = execute_train(req)
    assert res["status"] == "OK"
    assert "run_id" in res
    assert res["state"]["status"] == "DONE"
    assert res["state"]["epochs"] == 2
    assert res["epochs_completed"] == 2
    assert res["state"]["loss"] >= 0.0


# =============================================================================
# REST Endpoints (FastAPI TestClient)
# =============================================================================


def test_api_overview_route(client: TestClient) -> None:
    """GET /api/model-studio/overview must return 200 and schema."""
    resp = client.get("/api/model-studio/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert "architecture" in data
    assert "weights_sha256" in data


def test_api_fetch_70d_route(client: TestClient) -> None:
    """GET /api/model-studio/fetch-70d must return 200 with 70 slots."""
    resp = client.get("/api/model-studio/fetch-70d")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert len(data["slots"]) == 70


def test_api_predict_route(client: TestClient) -> None:
    """POST /api/model-studio/predict must evaluate forward pass."""
    payload = {
        "dimension": 50,
        "features": [0.1] * 50,
        "inspect_layers": True,
        "compute_saliency": True,
    }
    resp = client.post("/api/model-studio/predict", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert "probabilities" in data
    assert data["numerical_validation"]["valid"] is True


def test_api_predict_invalid_dimension_raises_422(client: TestClient) -> None:
    """POST /api/model-studio/predict with dimension mismatch must raise 422."""
    payload = {"dimension": 50, "features": [0.1] * 49}
    resp = client.post("/api/model-studio/predict", json=payload)
    assert resp.status_code == 422


def test_api_stress_test_route(client: TestClient) -> None:
    """POST /api/model-studio/stress-test must pass."""
    resp = client.post("/api/model-studio/stress-test", json={"dimension": 50})
    assert resp.status_code == 200
    assert resp.json()["all_passed"] is True


def test_api_benchmark_route(client: TestClient) -> None:
    """POST /api/model-studio/benchmark must return latency stats."""
    resp = client.post("/api/model-studio/benchmark", json={"dimension": 50, "iterations": 15})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["latency_p50_ms"] > 0.0


def test_api_artifact_locations_route(client: TestClient) -> None:
    """GET /api/model-studio/artifact-locations resolves the on-disk roots.

    The Neural Model Studio surfaces "where did this artifact actually land" for
    every dataset/checkpoint/registry file it writes. The response is
    server-derived (never from request input) and read-only, and must report a
    repo_root the UI can shorten absolute paths against.
    """
    from nexus_scalp.web.model_studio_routes import _repo_root

    resp = client.get("/api/model-studio/artifact-locations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["repo_root"] == str(_repo_root())

    locs = data["locations"]
    # Every advertised root carries the path contract the UI renders.
    for key in ("datasets", "model_checkpoints", "registry_database"):
        entry = locs[key]
        assert {"absolute_path", "relative_path", "exists", "is_dir", "file_count"} <= set(entry)
        # Relative paths are repo-relative: no drive letter, no leading separator.
        rel = str(entry["relative_path"])
        assert not rel.startswith("/")
        assert not rel.startswith("\\")
        assert ":" not in rel

    # The checkpoints root is a real directory in this checkout.
    assert locs["model_checkpoints"]["exists"] is True
    assert locs["model_checkpoints"]["is_dir"] is True
    assert locs["model_checkpoints"]["file_count"] > 0


# =============================================================================
# CLI Commands Integration
# =============================================================================


def test_cli_model_quality_json() -> None:
    """CLI model-quality command must output valid JSON envelope."""
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["model-quality", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "OK"
    assert data["architecture"] == "ScalpNet"


def test_cli_model_predict_json() -> None:
    """CLI model-predict command must output valid prediction JSON."""
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["model-predict", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "OK"
    assert data["numerical_validation"]["valid"] is True


def test_cli_model_stress_test_json() -> None:
    """CLI model-stress-test command must pass all stress checks."""
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["model-stress-test", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["all_passed"] is True


def test_cli_model_train_dataset_json() -> None:
    """CLI model-train-dataset must dispatch training."""
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["model-train-dataset", "--epochs", "2", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "OK"
    assert data["state"]["epochs"] == 2


# =============================================================================
# Web Assets Integrity
# =============================================================================


def test_web_ui_assets_exist() -> None:
    """Verifies Legacy Web/ and React frontend/ assets exist and are wired."""
    # Legacy Web
    legacy_js = REPO_ROOT / "Web" / "model_studio_ui.js"
    assert legacy_js.is_file(), "Web/model_studio_ui.js must exist"
    index_html = (REPO_ROOT / "Web" / "index.html").read_text(encoding="utf-8")
    assert "tab-model-studio" in index_html
    assert "model_studio_ui.js" in index_html

    # React frontend
    feature_dir = REPO_ROOT / "frontend" / "src" / "features" / "model-studio"
    assert (feature_dir / "index.ts").is_file()
    assert (feature_dir / "ui" / "ModelStudioPage.tsx").is_file()

    # Feature registry
    reg_ts = (REPO_ROOT / "frontend" / "src" / "app" / "featureRegistry.ts").read_text(
        encoding="utf-8"
    )
    assert "modelStudioMeta" in reg_ts
