"""Position Adviser API contract tests (mission §36/§37).

Exercises the new routes through a real FastAPI TestClient against the REAL
service and on-disk artifacts: load response contract, tensor inspector,
settings round-trip, rollback, and the honest 404s that prove the endpoints do
not fabricate state.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.position_adviser.service import PositionAdviserService
from nexus_scalp.position_adviser.settings_store import AdviserSettingsStore
from nexus_scalp.position_adviser.trainer import AdviserScaler, PositionAdviserNet
from nexus_scalp.web import position_adviser_routes as par

FEATURE_DIM = 12
ACTIONS = ("KEEP", "CLOSE", "REDUCE")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Mount the real router over a service the test owns.

    The router resolves the service through get_position_adviser_service(), so
    the test swaps that accessor for one returning the test's instance — the
    routes are exercised end to end, not stubbed.
    """
    svc = PositionAdviserService()
    monkeypatch.setattr(par, "get_position_adviser_service", lambda: svc)
    # Point the settings store at an isolated DB so the round-trip is real but
    # never touches the operator's settings.
    import sqlite3

    db_path = tmp_path / "settings.db"
    monkeypatch.setattr(par, "_settings_database", lambda: _StubSettings(db_path))
    api = FastAPI()
    api.include_router(par.router)
    return api


class _StubSettings:
    """Minimal stand-in exposing the two methods the store calls.

    The real SettingsDatabase is a SQLite-backed key/value store with its own
    provisioning; reproducing it here would re-implement it. This keeps the
    contract test honest about the ROUTE while the store's own tests cover the
    database itself.
    """

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS application_settings "
            "(key TEXT PRIMARY KEY, value TEXT, value_type TEXT, source TEXT, actor TEXT)"
        )
        self._conn.commit()

    def get(self, key: str):
        row = self._conn.execute(
            "SELECT value FROM application_settings WHERE key = ?", (key,)
        ).fetchone()
        return _StubValue(row[0]) if row else None

    def set(self, key, value, *, value_type=None, source=None, actor=None):
        self._conn.execute(
            "INSERT OR REPLACE INTO application_settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()


class _StubValue:
    def __init__(self, value: str) -> None:
        self.value = value


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def artifact_dir() -> Path:
    out = Path("artifacts") / "position_adviser" / "pa_api_tests"
    out.mkdir(parents=True, exist_ok=True)
    yield out
    for child in sorted(out.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
    out.rmdir()


def _write_model(artifact_dir: Path, model_id: str) -> tuple[Path, Path]:
    net = PositionAdviserNet()
    weights = artifact_dir / f"{model_id}.pt"
    torch.save(net.state_dict(), weights)
    rng = np.random.default_rng(seed=abs(hash(model_id)) % (2**32))
    scaler = AdviserScaler(
        mean=rng.normal(size=FEATURE_DIM).astype(np.float32),
        std=np.full(FEATURE_DIM, 0.5, dtype=np.float32),
        feature_dim=FEATURE_DIM,
    )
    scaler_path = artifact_dir / f"{model_id}.scaler.npz"
    np.savez(scaler_path, **scaler.to_arrays())
    (artifact_dir / f"{model_id}.meta.json").write_text(
        json.dumps(
            {
                "model_id": model_id,
                "adviser_actions": list(ACTIONS),
                "input_dim": FEATURE_DIM,
                "source_dataset_hash": f"ds_{model_id}",
            }
        )
    )
    return weights, scaler_path


# ================================================== load API contract


def test_load_response_carries_the_full_contract(client: TestClient, artifact_dir: Path) -> None:
    weights, scaler = _write_model(artifact_dir, "pa_api_load_001")
    r = client.post(
        "/api/position-adviser/load",
        json={"weights_path": str(weights), "scaler_path": str(scaler)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OK"
    assert body["model_id"] == "pa_api_load_001"
    # Mission §37: every field the UI's load card needs, from the backend.
    for key in (
        "model_id",
        "model_version",
        "weights_sha256",
        "feature_dim",
        "feature_schema_id",
        "loaded",
        "active",
        "device",
        "restart_required",
        "loaded_at",
    ):
        assert key in body, f"load response missing {key}"
    assert body["loaded"] is True
    assert body["restart_required"] is False


def test_load_of_missing_artifact_is_400(client: TestClient, artifact_dir: Path) -> None:
    r = client.post(
        "/api/position-adviser/load",
        json={
            "weights_path": str(artifact_dir / "ghost.pt"),
            "scaler_path": str(artifact_dir / "ghost.scaler.npz"),
        },
    )
    assert r.status_code == 400


# ================================================= tensor inspector


def _seed_evaluation(client: TestClient, artifact_dir: Path, model_id: str) -> None:
    weights, scaler = _write_model(artifact_dir, model_id)
    client.post(
        "/api/position-adviser/load",
        json={"weights_path": str(weights), "scaler_path": str(scaler)},
    )
    client.post("/api/position-adviser/activate", json={"activation": "PAPER"})
    svc = position_adviser_service_of(client)
    svc.evaluate(*_ticket_of_state(_live_state()))


def _ticket_of_state(state: dict) -> tuple[int, dict]:
    return int(state["ticket"]), state


def _live_state(ticket: int = 424242) -> dict:
    import hashlib
    import time

    at = time.monotonic()
    state = {
        "ticket": ticket,
        "unrealized_pnl_r": 1.10,
        "current_r_net": 0.90,
        "current_return": 0.0030,
        "distance_to_stop_r": -1.60,
        "distance_to_target_r": 2.10,
        "position_age_bars": 180.0,
        "atr": 0.0009,
        "spread": 0.00015,
        "estimated_slippage": 0.00007,
        "model_probability": 0.60,
        "model_confidence": 0.52,
        "signal_age": 240.0,
    }
    state["snapshot_observed_at"] = at
    state["snapshot_id"] = hashlib.sha1(f"{ticket}:{at}".encode()).hexdigest()[:12]
    return state


def position_adviser_service_of(client: TestClient) -> PositionAdviserService:
    return par.get_position_adviser_service()


def test_tensor_inspector_404_before_any_evaluation(client: TestClient) -> None:
    r = client.get("/api/position-adviser/tensor/current-input")
    assert r.status_code == 404
    assert "no evaluation has run yet" in r.json()["detail"]


def test_tensor_inspector_shows_raw_and_normalized(
    client: TestClient, artifact_dir: Path
) -> None:
    _seed_evaluation(client, artifact_dir, "pa_api_tensor_001")
    r = client.get("/api/position-adviser/tensor/current-input")
    assert r.status_code == 200, r.text
    t = r.json()["tensor"]
    # Mission §12: raw and normalized are both present and agree in dimension.
    assert t["feature_dimension"] == FEATURE_DIM
    assert t["tensor_shape"] == [1, FEATURE_DIM]
    assert t["model_id"] == "pa_api_tensor_001"
    rows = t["features"]
    assert len(rows) == FEATURE_DIM
    names = [f["name"] for f in rows]
    assert len(set(names)) == FEATURE_DIM  # no duplicated feature
    # Per-feature raw + normalized, both finite.
    assert all("raw" in f and "normalized" in f for f in rows)
    assert all(f["finite"] for f in rows)
    # Aggregate validity counters the inspector computes itself.
    assert t["validity"]["nan_count"] == 0
    assert t["validity"]["inf_count"] == 0


def test_decision_trace_404_without_a_decision(client: TestClient) -> None:
    r = client.get("/api/position-adviser/decision/current")
    assert r.status_code == 404


def test_decision_trace_reports_measured_latencies(
    client: TestClient, artifact_dir: Path
) -> None:
    _seed_evaluation(client, artifact_dir, "pa_api_decision_001")
    r = client.get("/api/position-adviser/decision/current")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["action"] in ACTIONS
    assert set(body["decision"]["probabilities"]) == set(ACTIONS)
    lat = body["latency"]
    assert lat["total_ms"] >= lat["inference_ms"]
    assert lat["feature_ms"] is not None


# ================================================= settings round-trip


def test_settings_round_trip_through_the_api(client: TestClient) -> None:
    r = client.get("/api/position-adviser/settings")
    assert r.status_code == 200
    assert r.json()["settings"]["activation"] == "DISABLED"

    r = client.put(
        "/api/position-adviser/settings",
        json={"activation": "PAPER", "auto_load": True},
    )
    assert r.status_code == 200, r.text
    persisted = r.json()["persisted"]
    assert "activation" in persisted
    assert "auto_load" in persisted

    r = client.get("/api/position-adviser/settings")
    assert r.json()["settings"]["activation"] == "PAPER"
    assert r.json()["settings"]["auto_load"] is True


def test_settings_partial_edit_is_rejected_for_unknown_key(client: TestClient) -> None:
    r = client.put(
        "/api/position-adviser/settings",
        json={"config_key": "not_a_real_key", "config_value": 3.0},
    )
    assert r.status_code == 200
    # An unknown key is ignored, never written.
    assert r.json()["persisted"] == []


# ================================================= rollback route


def test_rollback_route_reports_nothing_to_restore(client: TestClient) -> None:
    r = client.post("/api/position-adviser/rollback")
    assert r.status_code == 409


def test_load_then_rollback_round_trip(client: TestClient, artifact_dir: Path) -> None:
    w_a, s_a = _write_model(artifact_dir, "pa_api_rb_a")
    w_b, s_b = _write_model(artifact_dir, "pa_api_rb_b")
    client.post(
        "/api/position-adviser/load",
        json={"weights_path": str(w_a), "scaler_path": str(s_a)},
    )
    client.post(
        "/api/position-adviser/load",
        json={"weights_path": str(w_b), "scaler_path": str(s_b)},
    )
    assert client.get("/api/position-adviser/status").json()["model_id"] == "pa_api_rb_b"

    r = client.post("/api/position-adviser/rollback")
    assert r.status_code == 200, r.text
    assert r.json()["model_id"] == "pa_api_rb_a"
    # The active model really did move back.
    assert client.get("/api/position-adviser/status").json()["model_id"] == "pa_api_rb_a"
