"""CodeQL py/stack-trace-exposure regression (alerts #1178/#1179).

The ML control-plane routes must never return the raw exception text the
lifecycle records in ``last_error`` / ``reason`` (filesystem paths, internal
service names and source locations can be embedded there). The full detail
goes to the structured logger; the HTTP body carries a stable error code plus
a request id.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.position_adviser.ml_lifecycle import MLLifecycleState
from nexus_scalp.web.position_ml_routes import (
    _public_error_code,
    register_position_ml_routes,
)

# Strings that must never appear in a response body.
SECRET_MARKERS = (
    "C:/secrets/model.pt",
    "FileNotFoundError",
    "OSError",
    "Errno 2",
    "Traceback",
)


class _FakeGate:
    def status(self) -> dict[str, Any]:
        return {"ml_active": False}

    def audit_tail(self, limit: int) -> list[Any]:
        return []


class _FailedLifecycle:
    """Reproduces both taint paths: status().last_error and activate().reason."""

    def __init__(self) -> None:
        self._gate = _FakeGate()
        self.state = MLLifecycleState.FAILED
        self.last_error = "MODEL_LOAD_FAILED: FileNotFoundError [Errno 2] C:/secrets/model.pt"
        self._loaded_model = None

    def status(self) -> dict[str, Any]:
        return {
            "lifecycle_state": str(self.state),
            "persisted": {"model_id": "m1", "model_path": "", "scaler_path": ""},
            "loaded_model_id": "",
            "load_timestamp": 0.0,
            "last_inference_timestamp": 0.0,
            "last_error": self.last_error,
            "restart_required": False,
            "controller_mode": "legacy",
        }

    def activate(self, model_id: str, model_path: str, scaler_path: str) -> dict[str, Any]:
        return {
            "status": "FAILED",
            "reason": "MODEL_LOAD_FAILED: OSError [Errno 2] C:/secrets/model.pt",
        }

    def disable(self) -> dict[str, Any]:
        return {"status": "OK", "state": "DISABLED"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_position_ml_routes(app, lifecycle=_FailedLifecycle())
    return TestClient(app)


def test_public_error_code_maps_known_failures() -> None:
    assert _public_error_code("MODEL_LOAD_FAILED: boom") == "MODEL_LOAD_FAILED"
    assert _public_error_code("MODEL_LOAD_REJECTED: mismatch") == "MODEL_LOAD_REJECTED"
    assert _public_error_code("MODEL_LOAD_FAILED on restart: x") == "MODEL_LOAD_FAILED"
    assert _public_error_code("") == ""
    assert _public_error_code(None) == ""
    assert _public_error_code("anything else") == "FAILED"


def test_status_route_leaks_no_exception_text(client: TestClient) -> None:
    r = client.get("/api/position-ml/status")
    assert r.status_code == 200
    for marker in SECRET_MARKERS:
        assert marker not in r.text, f"leaked {marker!r} in /status"
    body = r.json()
    assert body["last_error"] == "MODEL_LOAD_FAILED"
    assert body["status"] == "OK"


def test_activate_route_leaks_no_exception_text(client: TestClient) -> None:
    r = client.post("/api/position-ml/activate", json={"model_id": "m1"})
    for marker in SECRET_MARKERS:
        assert marker not in r.text, f"leaked {marker!r} in /activate"
    body = r.json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "MODEL_LOAD_FAILED"
    assert body["request_id"]


def test_activate_ok_branch_carries_no_reason() -> None:
    class _OKLifecycle(_FailedLifecycle):
        def __init__(self) -> None:
            super().__init__()
            self.state = MLLifecycleState.ACTIVE

        def activate(self, model_id: str, model_path: str, scaler_path: str) -> dict[str, Any]:
            # Real OK payload includes no "reason", but the route must not
            # echo the lifecycle dict even if one sneaks in.
            return {
                "status": "OK",
                "state": "ACTIVE",
                "model_id": model_id,
                "reason": "MODEL_LOAD_FAILED: OSError C:/secrets/model.pt",
            }

    app = FastAPI()
    register_position_ml_routes(app, lifecycle=_OKLifecycle())
    r = TestClient(app).post("/api/position-ml/activate", json={"model_id": "m9"})
    for marker in SECRET_MARKERS:
        assert marker not in r.text, f"leaked {marker!r} on the OK branch"
    body = r.json()
    assert body == {"status": "OK", "state": "ACTIVE", "model_id": "m9"}
