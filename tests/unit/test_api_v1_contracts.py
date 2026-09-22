"""Contract unit tests for the /api/v1 platform (envelope/pagination/sanitize/runtime)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from nexus_scalp.web.api_v1.common import (
    MAX_PAGE_SIZE,
    build_page,
    jsonable,
    parse_pagination,
    sanitize_config,
)
from nexus_scalp.web.api_v1_wiring import create_v1_app, v1_route_count

# ---------------------------------------------------------------------------
# unit: envelope helpers
# ---------------------------------------------------------------------------


class _FakeState:
    request_id = "req_test12345678"


class _FakeRequest:
    state = _FakeState()
    headers: ClassVar[dict[str, str]] = {}
    url: Any = None
    app: Any = None


def test_parse_pagination_bounds() -> None:
    assert parse_pagination(1, 50) == (1, 50)
    assert parse_pagination(3, MAX_PAGE_SIZE) == (3, MAX_PAGE_SIZE)


def test_parse_pagination_rejects_out_of_range() -> None:
    resp = parse_pagination(0, 50)
    assert not isinstance(resp, tuple)
    assert resp.status_code == 422
    body = json.loads(resp.body)
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]

    resp2 = parse_pagination(1, MAX_PAGE_SIZE + 1)
    assert not isinstance(resp2, tuple)
    assert resp2.status_code == 422


def test_sanitize_config_masks_secret_shaped_keys() -> None:
    payload = {
        "mt5": {"login": "12345", "password": "hunter2", "server": "Demo"},
        "telegram": {"bot_token": "abc", "chat_id": "1"},
        "nested": {"api_key": "xyz", "safe": 1},
        "list": [{"credential": "leak"}, 2],
    }
    out = sanitize_config(payload)
    assert out["mt5"]["login"] == "***"
    assert out["mt5"]["password"] == "***"
    assert out["mt5"]["server"] == "Demo"
    assert out["telegram"]["bot_token"] == "***"
    assert out["nested"]["api_key"] == "***"
    assert out["nested"]["safe"] == 1
    assert out["list"][0]["credential"] == "***"
    assert out["list"][1] == 2


def test_jsonable_converts_datetimes_and_enums() -> None:
    from enum import Enum

    class Color(Enum):
        RED = "red"

    payload = {
        "at": datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
        "naive": datetime(2026, 9, 2, 12, 0),
        "color": Color.RED,
        "deep": {"items": [datetime(2026, 1, 1, tzinfo=UTC), 1]},
    }
    out = jsonable(payload)
    assert out["at"] == "2026-09-02T12:00:00+00:00"
    assert out["naive"] == "2026-09-02T12:00:00+00:00"
    assert out["color"] == "red"
    assert out["deep"]["items"][0] == "2026-01-01T00:00:00+00:00"


def test_build_page_shape() -> None:
    page = build_page([{"i": i} for i in range(3)], page=2, page_size=3, has_more=True)
    assert page == {
        "items": [{"i": 0}, {"i": 1}, {"i": 2}],
        "page": 2,
        "page_size": 3,
        "has_more": True,
    }


# ---------------------------------------------------------------------------
# contract: standalone v1 app (no engine) — envelope + truthful unavailability
# ---------------------------------------------------------------------------


@pytest.fixture()
def v1_client() -> TestClient:
    return TestClient(create_v1_app())


def test_v1_app_route_count_matches_openapi(v1_client: TestClient) -> None:
    app = v1_client.app  # type: ignore[attr-defined]
    spec = app.openapi()
    assert v1_route_count(app) >= 60
    assert len(spec["paths"]) == v1_route_count(app)
    # every path carries the api-v1 prefix and documented operation ids
    for path in spec["paths"]:
        assert path.startswith("/api/v1/")
        for method, op in spec["paths"][path].items():
            if method == "parameters":
                continue
            assert op.get("summary"), f"{method} {path} missing summary"


def test_error_envelope_shape_on_not_found(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/decisions/req_does_not_exist")
    assert r.status_code == 404
    body = r.json()
    err = body["error"]
    assert set(err) == {"code", "message", "details", "request_id", "retryable"}
    assert err["code"] == "RESOURCE_NOT_FOUND"
    assert err["retryable"] is False
    assert err["request_id"].startswith("req_")


def test_unknown_route_uses_v1_envelope(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/definitely/not/a/route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_validation_error_envelope(v1_client: TestClient) -> None:
    r = v1_client.post("/api/v1/runtime/mode/validate", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["errors"]  # bounded field summaries present
    assert isinstance(err["details"]["errors"], list) and len(err["details"]["errors"]) <= 20


def test_request_id_continuation(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/system/version", headers={"X-Request-ID": "req_mytrace001"})
    assert r.status_code == 200
    assert r.json()["meta"]["request_id"] == "req_mytrace001"
    assert r.headers["x-request-id"] == "req_mytrace001"


def test_meta_block_shape(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/system/version")
    meta = r.json()["meta"]
    assert meta["request_id"].startswith("req_")
    assert meta["generated_at"].endswith("+00:00")
    assert meta.get("api_version", "v1") == "v1"  # optional envelope field, v1 when present


def test_engine_unavailable_truthful_503(v1_client: TestClient) -> None:
    for path in (
        "/api/v1/market/quote",
        "/api/v1/positions",
        "/api/v1/risk/status",
        "/api/v1/model/status",
    ):
        r = v1_client.get(path)
        assert r.status_code == 503, path
        err = r.json()["error"]
        assert err["code"] == "ENGINE_UNAVAILABLE"
        assert err["retryable"] is True


def test_db_backed_routes_live_without_engine(v1_client: TestClient) -> None:
    for path in ("/api/v1/system/version", "/api/v1/features/contract", "/api/v1/decisions/stats"):
        r = v1_client.get(path)
        assert r.status_code == 200, path
        assert "data" in r.json()


def test_pagination_validation_on_list_route(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/decisions", params={"page": 0})
    assert r.status_code == 422
    r2 = v1_client.get("/api/v1/decisions", params={"page_size": 5000})
    assert r2.status_code == 422


def test_mode_validate_noop_and_transition_rules(v1_client: TestClient) -> None:
    r = v1_client.post("/api/v1/runtime/mode/validate", json={"mode": "paper"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["valid"] is True
    assert data["proposed_mode"] == "PAPER"
    r2 = v1_client.post("/api/v1/runtime/mode/validate", json={"mode": "LIVE"})
    data2 = r2.json()["data"]
    # LIVE proposals validate but ALWAYS warn (no API mutation path exists)
    assert any("LIVE" in w for w in data2["warnings"])


def test_config_validate_never_applies(v1_client: TestClient) -> None:
    r = v1_client.post("/api/v1/config/validate", json={"risk": {"risk_per_trade_pct": 0.25}})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["valid"] is True
    assert data["applied"] is False


def test_incident_not_found_bounded(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/incidents/INC-DOES-NOT-EXIST")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_no_stack_traces_in_any_error(v1_client: TestClient) -> None:
    r = v1_client.get("/api/v1/decisions/req_x")
    text = r.text
    assert "Traceback" not in text
    assert ".py" not in text


# ---------------------------------------------------------------------------
# OBS-JSON regression (2026-09-21): GET /api/v1/model/identity must never
# return a live in-process object. The serving bundle exposes the scaler as a
# ScalerBundle of numpy arrays; the old attribute loop echoed it straight into
# json.dumps and 500'd the ML panel on every poll. It also read identity off
# the bundle, which carries only (model, scaler, artifact_path), so every
# rendered field was empty. Identity now comes from the model registry.
# ---------------------------------------------------------------------------


class _FakeScalerBundle:
    corrupt = False

    def is_ready(self) -> bool:
        return True

    def dimension(self) -> int:
        return 70


class _FakeBundle:
    model: Any = None
    scaler = _FakeScalerBundle()
    artifact_path = "C:/artifacts/model.pt"


class _FakeProvenance:
    model_id = "primary_scalp_scalp_v3_70d"
    model_version = "v1.0"
    feature_schema_id = "scalp_v3"
    feature_dimension = 70
    artifact_fingerprint = "abc123def4567890"
    model_role = "PRIMARY_SCALP"
    config_version = "9.1.0"


class _FakeRegistry:
    current = _FakeProvenance()


def _client_with_bundle(bundle: Any, registry: Any = None) -> TestClient:
    app = create_v1_app()
    app.state.engine = SimpleNamespace(_bundle=bundle, model_registry=registry)
    return TestClient(app)


def _client_with_app(engine: Any) -> TestClient:
    """Mount a full engine object (accessor methods, not just namespace attrs)."""
    app = create_v1_app()
    app.state.engine = engine
    return TestClient(app)


def test_model_identity_never_returns_non_json_objects() -> None:
    client = _client_with_bundle(_FakeBundle(), _FakeRegistry())
    r = client.get("/api/v1/model/identity")
    assert r.status_code == 200, r.text
    # jsonable() would have raised TypeError before reaching json(); this
    # assertion fails loudly if the route ever re-introduces a raw object.
    assert r.json()["data"]


def test_model_identity_reports_registry_fields() -> None:
    data = (
        _client_with_bundle(_FakeBundle(), _FakeRegistry())
        .get("/api/v1/model/identity")
        .json()["data"]
    )
    assert data["available"] is True
    assert data["registered"] is True
    assert data["model_id"] == "primary_scalp_scalp_v3_70d"
    assert data["model_version"] == "v1.0"
    assert data["schema_id"] == "scalp_v3"
    assert data["artifact_id"] == "abc123def4567890"
    assert data["feature_dimension"] == 70
    # contract FACTS from the scaler, never the object itself
    assert data["scaler_ready"] is True
    assert data["scaler_dimension"] == 70
    assert data["scaler_corrupt"] is False
    assert "scaler" not in data


def test_model_identity_absent_without_bundle() -> None:
    app = create_v1_app()
    app.state.engine = SimpleNamespace(_bundle=None, model_registry=None)
    r = TestClient(app).get("/api/v1/model/identity")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False
    assert data["reason"]


def test_model_identity_reports_unregistered_state_without_placeholders() -> None:
    client = _client_with_bundle(_FakeBundle(), registry=None)
    r = client.get("/api/v1/model/identity")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is True
    assert data["registered"] is False
    assert data.get("model_id") in (None, "", "unregistered")
    # an unregistered model must not emit a row of empty-string fields
    for key in ("artifact_id", "model_role", "config_version"):
        assert key not in data or data[key]


# ---------------------------------------------------------------------------
# OBS-METADATA regression (2026-09-21): GET /api/v1/model/contracts must
# resolve the MODEL side of the contract from the serving runtime, not from
# ModelBundle.manifest. ModelBundle is a frozen dataclass of exactly
# (model, scaler, artifact_path) and has no manifest attribute, so the old
# read was always None and every live deployment reported
# result=UNKNOWN / reason=NO_MODEL_METADATA while serving a validated 70D
# scalp_v3 champion -- a false UNKNOWN that hides a real PASS.
# ---------------------------------------------------------------------------


class _EffectiveEngine:
    """Engine stub exposing the bundle-derived effective contract accessors."""

    _bundle = _FakeBundle()

    def effective_feature_dim(self) -> int:
        return 70

    def effective_feature_schema_id(self) -> str:
        return "scalp_v3"


class _UnregisteredEngine:
    """Bundle loaded but no registered provenance (cold pre-registration boot)."""

    _bundle = _FakeBundle()
    model_registry = None

    def effective_feature_dim(self) -> int:
        return 50

    def effective_feature_schema_id(self) -> str:
        return "scalp_v1"


def test_model_contracts_reports_real_model_metadata() -> None:
    r = _client_with_app(_EffectiveEngine()).get("/api/v1/model/contracts")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["feature_schema_ids"]
    compat = data["compatible_model_schemas"]
    assert compat["model_schema_id"] == "scalp_v3"
    assert compat["model_dimension"] == 70
    assert compat["runtime_schema_id"] == "scalp_v3"
    assert compat["runtime_dimension"] == 70
    assert compat["result"] == "PASS", compat
    assert compat["reason"] == "SCHEMA_DIMENSION_MATCH"
    assert data["serving_bundle_present"] is True


def test_model_contracts_reports_mismatch_not_unknown() -> None:
    """A 50D model under the canonical 70D runtime must BLOCK, not UNKNOWN."""
    r = _client_with_app(_UnregisteredEngine()).get("/api/v1/model/contracts")
    assert r.status_code == 200, r.text
    compat = r.json()["data"]["compatible_model_schemas"]
    assert compat["model_schema_id"] == "scalp_v1"
    assert compat["model_dimension"] == 50
    assert compat["result"] == "BLOCK", compat
    assert compat["reason"] == "SCHEMA_MISMATCH"


def test_model_contracts_absent_engine_is_honest_unknown() -> None:
    """No engine attached -> UNKNOWN with a real reason, never a fabricated PASS."""
    app = create_v1_app()
    app.state.engine = None
    r = TestClient(app).get("/api/v1/model/contracts")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    compat = data["compatible_model_schemas"]
    assert compat["model_schema_id"] is None
    assert compat["model_dimension"] is None
    assert compat["result"] == "UNKNOWN"
    assert compat["reason"] == "NO_MODEL_METADATA"
