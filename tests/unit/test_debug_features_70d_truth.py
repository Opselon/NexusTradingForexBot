"""The 70D debug-features payload must serve the REAL inference tensor.

/api/debug/features used to zero-pad the base-50 feature vector to 70 while
the 70D champion consumed live news/liquidity family values — the panel
reported a ready model as UNAVAILABLE. The endpoint must prefer
engine._last_live_tensor_values (the exact assembled tensor) and expose
per-family source statuses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.web import debug_research_routes as drr


class _FakeFV:
    """Base-50 feature vector (the raw value source the panel used to pad)."""

    timestamp_utc = "2026-11-04T00:00:00+00:00"

    def to_tensor_input(self) -> list[float]:
        return [0.5] * 50


@pytest.fixture()
def client_70d() -> TestClient:
    app = FastAPI()
    engine = SimpleNamespace(
        _last_fv=_FakeFV(),
        effective_feature_dim=70,
        _last_live_tensor_values=[float(i) / 10 for i in range(70)],
        _last_news_family_available=True,
        _last_liquidity_family_available=False,
    )
    drr.register_debug_research_routes(
        app,
        _err=lambda *a, **k: None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
    )
    app.state.engine = engine
    return TestClient(app)


def test_debug_features_serve_live_inference_tensor(client_70d: TestClient) -> None:
    r = client_70d.get("/api/debug/features")
    assert r.status_code == 200
    body = r.json()
    assert body["vector_source"] == "LIVE_INFERENCE_TENSOR"
    assert body["feature_count"] == 70
    # Dim 0 comes from the TENSOR (0.0), not the base fv (0.5).
    assert body["features"][0]["value"] == pytest.approx(0.0)
    # News family (50-59) available; liquidity (60-69) honestly unavailable.
    news = body["features"][55]
    liq = body["features"][65]
    assert news["family"] == "news"
    assert "family_available" not in news  # available -> no unavailability tag
    assert liq["family"] == "liquidity"
    assert liq["family_available"] is False
    assert liq["source_status"] == "FEATURE_UNAVAILABLE"
    assert body["family_status"]["news"]["available"] is True
    assert body["family_status"]["liquidity"]["available"] is False


def test_debug_features_fall_back_to_base_vector_without_tensor(
    client_70d: TestClient,
) -> None:
    client_70d.app.state.engine._last_live_tensor_values = None  # type: ignore[attr-defined]
    r = client_70d.get("/api/debug/features")
    assert r.status_code == 200
    body = r.json()
    assert "vector_source" not in body
    assert body["features"][0]["value"] == pytest.approx(0.5)


def test_debug_features_50d_shape_untouched() -> None:
    app = FastAPI()
    engine = SimpleNamespace(_last_fv=_FakeFV(), effective_feature_dim=50)
    drr.register_debug_research_routes(
        app,
        _err=lambda *a, **k: None,
        _log_err=lambda *a, **k: None,
        serialize_enums=lambda x: x,
    )
    app.state.engine = engine
    client = TestClient(app)
    body = client.get("/api/debug/features").json()
    assert body["feature_count"] == 50
    assert "vector_source" not in body
    assert body["features"][0]["value"] == pytest.approx(0.5)
