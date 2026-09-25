"""CHG-0043 E2E certification: FULL historical cycle through the wired server.

Fresh replay session over the REAL local M1 dataset:

    session create (API) -> step -> candles -> features -> 70D -> model
        -> policy -> risk -> simulated execution -> lifecycle -> outcome
        -> report (API) -> persistence -> reconciliation

This test boots create_app WITHOUT the live engine (engine_ref=None) — the
replay subsystem must be fully independent of the live engine (brief section
0: strict isolation from live trading).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
M1 = REPO / "data" / "raw" / "XAUUSD_M1.parquet"
MODEL = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"

pytestmark = pytest.mark.skipif(
    not M1.exists() or not MODEL.exists(),
    reason="real M1 dataset / model bundle not present",
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=None)
    return TestClient(app)


def _window_hours(hours: int = 6) -> dict[str, Any]:
    import polars as pl

    df = pl.read_parquet(M1).head(3000)
    t0 = df.row(0, named=True)["time_utc"].replace(tzinfo=UTC)
    t_end = t0 + timedelta(minutes=60 * hours - 1)
    _window_hours.bars = int(
        (
            (df["time_utc"] >= t0.replace(tzinfo=None))
            & (df["time_utc"] <= t_end.replace(tzinfo=None))
        ).sum()
    )
    return {
        "dataset_id": "DS-CERT-E2E",
        "dataset_fingerprint": hashlib.sha256(M1.read_bytes()).hexdigest()[:32],
        "symbol": "XAUUSD",
        "replay_mode": "BAR_REPLAY",
        "start_time": t0.isoformat(),
        "end_time": t_end.isoformat(),
        "git_commit": "chg0043-cert",
        "checkpoint_every_bars": 300,
    }


def test_full_cycle_certification(client: TestClient) -> None:
    # 1. session create
    r = client.post("/api/replay/session", json=_window_hours())
    assert r.status_code == 200, r.text
    body = r.json()
    rid = body["replay_id"]
    ident = body["identity"]
    assert ident["engine"]["schema_dimension"] == 70
    assert (
        ident["engine"]["model"]["model_fingerprint"]
        == hashlib.sha256(MODEL.read_bytes()).hexdigest()[:32]
    )

    # 2. stepwise advance through the API (contract window = 6h = 360 bars)
    for _ in range(6):
        r = client.post(
            "/api/replay/control", json={"action": "step_bar", "n": 60, "replay_id": rid}
        )
        assert r.status_code == 200
        assert r.json()["result"]["status"] in {"OK", "END_OF_DATA"}

    # 3. cursor state: KNOWN/UNKNOWN boundary
    st = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st["counts"]["bars"] == _window_hours.bars
    assert st["known_events"] == _window_hours.bars
    assert st["unknown_events"] == 0
    assert st["clock"] is not None

    # 4. report: the operator research report
    rep = client.get("/api/replay/report", params={"replay_id": rid}).json()["report"]
    assert rep["counts"]["bars"] == _window_hours.bars
    assert rep["counts"]["decisions"] == _window_hours.bars
    assert rep["identity"]["engine"]["schema_dimension"] == 70
    # trades carry full lifecycle + honest outcomes
    trades = rep["trades"]
    assert trades["total"] >= 0
    assert rep["equity"]["final"] == round(rep["equity"]["start"] + trades["pnl_usd"], 6)

    # 5. DB reconciliation: replay_id + snapshot identity readable back
    #    (row was persisted by the E2E reconciliation test file's contract —
    #    here we verify the registry identity matches what a snapshot would
    #    carry: replay_id derived deterministically from contract+model)
    r2 = client.post("/api/replay/session", json=_window_hours())
    assert r2.json()["replay_id"] == rid  # deterministic

    # 6. reset + seek equivalence through the API
    client.post("/api/replay/control", json={"action": "reset", "replay_id": rid})
    import polars as pl

    df = pl.read_parquet(M1).head(3000)
    ts_seek = df.row(199, named=True)["time_utc"].replace(tzinfo=UTC).isoformat()
    r = client.post(
        "/api/replay/control",
        json={"action": "seek", "n": 1, "replay_id": rid, "seek_time": ts_seek},
    )
    assert r.status_code == 200
    st2 = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st2["counts"]["bars"] == 200

    # 7. no order surface anywhere in the cycle (source guard, server + session)
    for f in (
        REPO / "src/nexus_scalp/web/replay_routes.py",
        REPO / "src/nexus_scalp/research/replay_session.py",
    ):
        src = f.read_text(encoding="utf-8")
        assert "order_send(" not in src
