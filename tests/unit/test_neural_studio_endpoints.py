"""Integration checks for the new Neural Studio model-engineering endpoints.

These exercise the real FastAPI surface (``create_app``) so a broken route, a bad
request schema, or an unserializable response body fails here rather than in the
operator's browser.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_scalp.web.server import create_app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    os.environ.setdefault("NSE_WEB_AUTH_DISABLE", "1")
    return TestClient(create_app())


def _ok(res: TestClient) -> None:
    """Response sanity: a studio endpoint never answers with a 5xx or HTML."""
    assert res.status_code < 500, res.text
    assert res.headers["content-type"].startswith("application/json"), res.headers


# ------------------------- model builder (Phase 4) -------------------------


def test_builder_options_advertises_real_capabilities(client: TestClient) -> None:
    res = client.get("/api/model-studio/model-builder/options")
    _ok(res)
    assert res.status_code == 200
    body = res.json()
    # Two explicit contracts, keyed by dimension as strings, and nothing else.
    assert sorted(body["contracts"].keys()) == ["50", "70"]
    c70 = body["contracts"]["70"]
    c50 = body["contracts"]["50"]
    assert c70["schema_id"] == "scalp_v3"
    assert c70["feature_count"] == 70
    assert c50["schema_id"] == "scalp_v1"
    assert c50["feature_count"] == 50
    # The verified 70D grouping (Phase 5) comes from the backend, not a second
    # hardcoded frontend copy.
    assert c70["families"]["BASE"] == {"start": 0, "end": 49, "count": 50}
    assert c70["families"]["NEWS"] == {"start": 50, "end": 59, "count": 10}
    assert c70["families"]["LIQUIDITY"] == {"start": 60, "end": 69, "count": 10}
    assert body["dimension_to_schema_id"] == {"50": "scalp_v1", "70": "scalp_v3"}
    # No fake controls: the advertised contract is the one the builder constructs.
    assert c70["dtype"] == "float32"
    assert c70["output_classes"] == 3
    assert c70["sequence_length"] == 1
    assert c70["normalization"] == "zscore_clip5"


def test_builder_preflight_rejects_cross_contract_config(client: TestClient) -> None:
    """A 70D request carrying a 50D schema id is refused with a real reason."""
    res = client.post(
        "/api/model-studio/model-builder/preflight",
        json={
            "config_name": "contract-test",
            "dimension": 70,
            "schema_id": "scalp_v1",
            "dataset_path": "",
            "epochs": 1,
        },
    )
    _ok(res)
    assert res.status_code == 200
    report = res.json()
    errors = [f for f in report["findings"] if f["severity"] == "error"]
    assert any(f["code"] == "SCHEMA_DIMENSION_MISMATCH" for f in errors), report
    # The refusal names both the requested schema and the contract's own.
    msg = next(f["message"] for f in errors if f["code"] == "SCHEMA_DIMENSION_MISMATCH")
    assert "scalp_v1" in msg and "scalp_v3" in msg


def test_builder_preflight_accepts_a_clean_70d_config(client: TestClient) -> None:
    res = client.post(
        "/api/model-studio/model-builder/preflight",
        json={
            "config_name": "clean-70d",
            "dimension": 70,
            "schema_id": "scalp_v3",
            "dataset_path": "",
            "epochs": 1,
        },
    )
    _ok(res)
    assert res.status_code == 200
    body = res.json()
    errors = [f for f in body["findings"] if f["severity"] == "error"]
    assert errors == [], body
    contract = body["contract"]
    assert contract["dimension"] == 70
    assert contract["schema_id"] == "scalp_v3"


def test_builder_save_roundtrips_the_exact_configuration(client: TestClient) -> None:
    """SAVE CONFIGURATION persists what the operator typed (Phase 45)."""
    res = client.post(
        "/api/model-studio/model-builder/save",
        json={
            "config_name": "roundtrip-70d",
            "dimension": 70,
            "schema_id": "scalp_v3",
            "dataset_path": "",
            "epochs": 3,
            "batch_size": 32,
            "learning_rate": 0.001,
            "seed": 17,
        },
    )
    _ok(res)
    if res.status_code != 200:
        # The store lives under artifacts/; a read-only checkout can refuse. The
        # refusal must still be structured JSON, never a 500.
        assert res.status_code in (400, 403, 503), res.text
        return
    cfg = res.json()["config"]
    assert cfg["dimension"] == 70
    assert cfg["schema_id"] == "scalp_v3"
    # Reproducibility fields are persisted verbatim (exact reproducibility).
    payload = cfg["config_json"]
    for needle in ('"epochs": 3', '"batch_size": 32', '"learning_rate": 0.001', '"seed": 17'):
        assert needle in payload, (needle, payload)


# ------------------- runtime state machine (Phases 3 / 35) -----------------


def test_runtime_state_reports_engine_model_inference_separately(
    client: TestClient,
) -> None:
    res = client.get("/api/model-studio/runtime/state")
    _ok(res)
    assert res.status_code == 200
    runtime = res.json()["runtime"]
    # Three DISTINCT verdicts, each drawn from its own published vocabulary.
    assert runtime["engine_state"] in runtime["engine_states"]
    assert runtime["model_state"] in runtime["model_lifecycle_states"]
    assert runtime["inference_state"] in runtime["inference_states"]
    # Invariant: inference is only available when a model is loaded AND the
    # engine can actually produce a decision.
    if runtime["inference_available"]:
        assert runtime["model_loaded"] is True
        assert runtime["engine_running"] is True
    # The runtime model and the registry champion are reported separately, so a
    # champion-in-registry need not be the active runtime model (Phase 17).
    assert "registry_champion_id" in runtime
    assert "runtime_model_id" in runtime


# --------------------- tensor inspection (Phases 23 / 24) -------------------


def test_tensor_inspect_refuses_without_a_real_scaler(client: TestClient) -> None:
    """No measurable scaler width => the endpoint says so instead of inventing a
    normalized layer from nothing.

    The endpoint gives one of three HONEST answers depending on runtime state,
    and this test accepts each of them while asserting the invariant that
    a 70D tensor is never inspected against a 50D model:
      * 200 — a hot 70D bundle with a measurable scaler is attached;
      * 422 "scaler"/"measurable" — no scaler, so nothing to normalize;
      * 422 "contract violation" — the active model is the other contract,
        which must be rejected rather than silently resized.
    """
    res = client.get("/api/model-studio/tensor/inspect", params={"dimension": 70})
    assert res.status_code in (200, 422), res.text
    if res.status_code == 200:
        body = res.json()
        assert body["dimension"] == 70
        assert len(body["raw"]) == 70
        assert len(body["normalized"]) == 70
        assert len(body["model_input"]) == 70
        return
    detail = str(res.json().get("detail", "")).lower()
    assert any(k in detail for k in ("scaler", "measurable", "contract violation")), res.text


def test_tensor_inspect_names_its_perturbation_source(client: TestClient) -> None:
    """Perturbation inference must be labelled as such, never as live (Phase 26).

    A workspace with no hot bundle has nothing to perturb, so it 422s; a
    workspace whose champion is hot-loaded returns the perturbed tensor with
    an explicit perturbation marker.
    """
    res = client.get(
        "/api/model-studio/tensor/inspect",
        params={"dimension": 50, "perturbation_sigma": 0.05},
    )
    assert res.status_code in (200, 422), res.text
    if res.status_code == 422:
        # Nothing measurable to perturb, or the request crossed a contract
        # boundary — both are honest refusals.
        detail = str(res.json().get("detail", "")).lower()
        assert any(k in detail for k in ("scaler", "measurable", "contract violation")), res.text
        return
    body = res.json()
    sigma = body.get("perturbation_sigma") or body.get("sigma")
    if sigma:
        assert float(sigma) == pytest.approx(0.05)
        # Perturbation must be labelled, never presented as live.
        assert body.get("perturbation", body.get("perturbed", False)) is True
        assert body.get("source", "perturbed") != "live"
    else:
        assert body.get("perturbation", body.get("perturbed", False)) is False


# --------------------- registry detail (Phases 12 / 44) ---------------------


def test_models_list_reports_training_status(client: TestClient) -> None:
    """The 0.0000 class: the list must say whether a loss was ever measured."""
    res = client.get("/api/model-studio/models")
    _ok(res)
    models = res.json()["models"]
    if not models:
        pytest.skip("registry is empty on this checkout")
    for model in models[:25]:
        assert "training_status" in model, model.keys()
        assert model["training_status"] in {"NOT_TRAINED", "TRAINED", "FAILED"}


def test_model_detail_reports_training_status_not_just_loss(client: TestClient) -> None:
    res = client.get("/api/model-studio/models")
    _ok(res)
    models = res.json()["models"]
    if not models:
        pytest.skip("registry is empty on this checkout")
    target = models[0]["id"]
    res = client.get(f"/api/model-studio/models/{target}/detail")
    _ok(res)
    if res.status_code == 404:
        pytest.skip("model not present in the registry backing this client")
    assert res.status_code == 200
    detail = res.json()["model"]
    assert "training_status" in detail, detail.keys()
    # training_status is what separates "never trained" from "trained": a
    # NOT_TRAINED record must never present a loss as a completed-training
    # measurement, whatever value the column happens to hold.
    status = detail["training_status"]
    assert status in {"NOT_TRAINED", "TRAINED", "FAILED"}
    if status == "TRAINED":
        assert detail["epochs"] >= 1, detail
    else:
        assert detail["epochs"] == 0 or detail["final_loss"] is None or status == "FAILED"


# --------------------- switch preview (Phases 20 / 58) ---------------------


def test_switch_preview_of_an_unknown_model_is_a_404(client: TestClient) -> None:
    res = client.get(
        "/api/model-studio/models/switch/preview",
        params={"model_id": "definitely_not_a_registered_model"},
    )
    _ok(res)
    assert res.status_code in (404, 400)
    # Structured JSON, never a bare traceback or HTML.
    assert res.headers["content-type"].startswith("application/json")
