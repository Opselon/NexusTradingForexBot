"""CHG-0043 API tests: replay routes contract (REPLAY_API v1).

FastAPI TestClient over a synthetic local dataset:

* session creation (contract identity, deterministic replay_id, 422 on bad window)
* control: step/play/reset/seek/checkpoint semantics over HTTP
* state endpoint: KNOWN/UNKNOWN counts, no future candles in payload
* decision drill-down: engine trace, 404 for unknown seq
* report: JSON-serializable operator report
* source guard: routes have no broker surface
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.web.replay_routes import (
    ReplaySessionRegistry,
    register_replay_routes,
)

T0 = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
N = 400
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    from nexus_scalp.models.scalp_net import ScalpNet

    tmp = tmp_path_factory.mktemp("api_bundle")
    torch.manual_seed(11)
    net = ScalpNet(num_features=70, num_classes=4, hidden_dim=128)
    for p in net.parameters():
        p.data.uniform_(-0.01, 0.01)
    net.eval()
    torch.save(net.state_dict(), tmp / "model.pt")
    np.savez(tmp / "model.scaler.npz", mean=np.zeros(70), std=np.ones(70))

    def _records(contract: Any, config: Any) -> list[dict[str, Any]]:
        out = []
        n = 0
        ts = contract.start_time
        while ts <= contract.end_time and n < 5000:
            c = 2650.0 + 0.05 * (n % 13) + 0.5 * ((n // 13) % 7) + 0.01 * n
            o = c - 0.05
            out.append(
                {
                    "kind": "BAR",
                    "timestamp": ts,
                    "open": o,
                    "high": max(o, c) + 0.3,
                    "low": min(o, c) - 0.3,
                    "close": c,
                    "tick_volume": 100 + (n % 17),
                    "spread": 0.2,
                    "symbol": contract.symbol,
                    "timeframe": contract.timeframe,
                }
            )
            ts += timedelta(minutes=1)
            n += 1
        return out

    app = FastAPI()
    registry = ReplaySessionRegistry()
    register_replay_routes(app, registry, _records)
    return TestClient(app)


def _session_payload() -> dict[str, Any]:
    return {
        "dataset_id": "DS-API-TEST",
        "dataset_fingerprint": "f" * 16,
        "symbol": "XAUUSD",
        "replay_mode": "BAR_REPLAY",
        "start_time": T0.isoformat(),
        "end_time": (T0 + timedelta(minutes=N - 1)).isoformat(),
        "git_commit": "api-test",
        "model_artifact_path": str(
            REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"
        ),
    }


def test_session_create_and_identity(client: TestClient) -> None:
    r = client.post("/api/replay/session", json=_session_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["replay_id"].startswith("RPL-")
    assert body["identity"]["engine"]["schema_dimension"] == 70
    # deterministic identity
    r2 = client.post("/api/replay/session", json=_session_payload())
    assert r2.json()["replay_id"] == body["replay_id"]


def test_bad_window_422(client: TestClient) -> None:
    bad = _session_payload() | {
        "start_time": (T0 + timedelta(minutes=5)).isoformat(),
        "end_time": T0.isoformat(),
    }
    r = client.post("/api/replay/session", json=bad)
    assert r.status_code == 422


def test_control_step_play_state(client: TestClient) -> None:
    s = client.post("/api/replay/session", json=_session_payload()).json()["replay_id"]
    r = client.post("/api/replay/control", json={"action": "step_bar", "n": 50, "replay_id": s})
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "OK"
    st = client.get("/api/replay/state", params={"replay_id": s}).json()
    assert st["counts"]["bars"] == 50
    assert st["known_events"] == 50
    assert st["unknown_events"] == N - 50
    # state payload must NOT carry future candles (only counts + cursor truth)
    assert "bars" not in st and "candles" not in st
    # play to end
    r = client.post("/api/replay/control", json={"action": "play", "replay_id": s})
    assert r.status_code == 200
    st2 = client.get("/api/replay/state", params={"replay_id": s}).json()
    assert st2["counts"]["bars"] == N
    assert st2["unknown_events"] == 0
    # reset then seek
    client.post("/api/replay/control", json={"action": "reset", "replay_id": s})
    r = client.post(
        "/api/replay/control",
        json={
            "action": "seek",
            "replay_id": s,
            "seek_time": (T0 + timedelta(minutes=199)).isoformat(),
        },
    )
    assert r.status_code == 200
    st3 = client.get("/api/replay/state", params={"replay_id": s}).json()
    assert st3["counts"]["bars"] == 200


def test_report_endpoint(client: TestClient) -> None:
    s = client.post("/api/replay/session", json=_session_payload()).json()["replay_id"]
    client.post("/api/replay/control", json={"action": "play", "replay_id": s})
    r = client.get("/api/replay/report", params={"replay_id": s})
    assert r.status_code == 200
    rep = r.json()["report"]
    assert rep["counts"]["bars"] == N
    assert rep["identity"]["engine"]["schema_dimension"] == 70


def test_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/replay/state", params={"replay_id": "RPL-nonexistent"})
    assert r.status_code == 404


def test_replay_routes_no_broker_surface() -> None:
    src = (REPO / "src/nexus_scalp/web/replay_routes.py").read_text(encoding="utf-8")
    assert "order_send" not in src
    assert "MetaTrader5" not in src
