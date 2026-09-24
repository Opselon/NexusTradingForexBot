"""MT5-PARITY-FORENSICS IMPL-D: replay web/API surface contract tests.

Covers the lane-D findings (ids from reports 03_laneC / 05_laneE / 06_laneF /
08_laneH), each asserting the contract the fix established:

* C-3  GET /api/replay/decision resolves against the engine run that ACTUALLY
       EXECUTED, not the never-run long-lived session.engine (always-404 bug).
* C-4  regime_enabled=True is one truth end to end: the engine config carries
       it and /api/replay/state answers 200 (no AttributeError path).
* C-9  control responses expose an explicit END_OF_DATA once the cursor is
       exhausted; a step/seek past the end never reports OK.
* H-15/F-11 no exception text on the wire: 4xx/5xx bodies carry a stable
       error code + request_id, never str(exc) (filesystem paths, module
       internals).
* UI-M1 a fabricated dataset_id is rejected by the LOCAL-DATA loader with a
       4xx that names the bad id; the canonical id of the served window is
       the only accepted one (fail closed).

Two apps are exercised on purpose:

* ``client`` — routes wired to a synthetic records loader (fast, offline) for
  the C-3/C-4/C-9/H-15 contracts that belong to the ROUTE layer.
* ``server_client`` — the real ``create_app`` (engine_ref=None) whose
  ``_replay_records_loader`` is the ONLY authority on dataset identity, so the
  UI-M1 contract is proven against the code path that actually enforces it.

PURPOSE: regression gate for the replay REST surface.
OWNER: IMPL-D (mt5-parity-d). Consumes web/replay_routes.py + web/server.py's
       local-dataset records loader. PROVIDES: REPLAY_API v1 contract proof.
INVARIANTS: no broker surface, no network, no future data in any payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.web.replay_routes import (
    ReplaySessionRegistry,
    register_replay_routes,
)

REPO = Path(__file__).resolve().parents[2]
M1 = REPO / "data" / "raw" / "XAUUSD_M1.parquet"
MODEL = REPO / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity" / "model.pt"
T0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
N = 300  # synthetic bars; >= FEATURE_WARMUP_BARS so decisions really trace
WEB_TOKEN = "impld-replay-contract-token"


def _synth_records(contract: Any, config: Any) -> list[dict[str, Any]]:
    """Deterministic synthetic M1 bars for the requested window (offline)."""
    out: list[dict[str, Any]] = []
    ts = contract.start_time
    n = 0
    while ts <= contract.end_time and n < 5000:
        close = 2650.0 + 0.05 * (n % 13) + 0.01 * n
        out.append(
            {
                "kind": "BAR",
                "timestamp": ts,
                "open": close - 0.05,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "tick_volume": 100 + (n % 17),
                "spread": 0.2,
                "symbol": contract.symbol,
                "timeframe": contract.timeframe,
            }
        )
        ts += timedelta(minutes=1)
        n += 1
    return out


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """Routes + synthetic loader: route-layer contracts (C-3/C-4/C-9/H-15)."""
    app = FastAPI()
    registry = ReplaySessionRegistry()
    register_replay_routes(app, registry, _synth_records)
    return TestClient(app)


@pytest.fixture(scope="module")
def server_client() -> Any:
    """The REAL app, so UI-M1 is proven against the enforcing loader.

    Authenticated the same way tests/unit/test_live_state_contract.py does
    (env token resolved lazily on the first request + bearer header).
    """
    import os

    os.environ["NSE_WEB_AUTH_TOKEN"] = WEB_TOKEN
    try:
        from nexus_scalp.web.server import create_app

        app = create_app(engine_ref=None)
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {WEB_TOKEN}"})
        yield c
    finally:
        os.environ.pop("NSE_WEB_AUTH_TOKEN", None)


def _payload(**over: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "dataset_id": "DS-IMPLD-TEST",
        "dataset_fingerprint": "0" * 32,
        "symbol": "XAUUSD",
        "replay_mode": "BAR_REPLAY",
        "start_time": T0.isoformat(),
        "end_time": (T0 + timedelta(minutes=N - 1)).isoformat(),
        "git_commit": "impld-contract",
        "model_artifact_path": str(MODEL),
    }
    p.update(over)
    return p


def _make_session(c: TestClient, **over: Any) -> str:
    r = c.post("/api/replay/session", json=_payload(**over))
    assert r.status_code == 200, r.text
    return r.json()["replay_id"]


# ---------------------------------------------------------------------------
# H-15 / F-11: no exception text on the wire
# ---------------------------------------------------------------------------


def test_h15_route_failures_never_leak_exception_text(client: TestClient) -> None:
    """A forced failure answers the safe envelope, never str(exc).

    The model artifact path is impossible, so session create fails inside the
    engine binding; the 500 body must carry a stable code + request_id and
    must NOT contain the filesystem path (str(e) would have leaked it).
    """
    bad = _payload(model_artifact_path="definitely/not/a/model.pt")
    r = client.post("/api/replay/session", json=bad)
    assert r.status_code == 500, r.text
    body = r.json()
    assert "error" in body and "code" in body["error"]
    assert "request_id" in body["error"]
    blob = repr(body)
    assert "definitely/not/a/model.pt" not in blob
    assert "Traceback" not in blob
    # The vocabulary is the curated one from web/errors.py, not exception text.
    assert body["error"]["code"] in {
        "OPERATION_FAILED",
        "INTERNAL_ERROR",
        "RESOURCE_UNAVAILABLE",
        "VALIDATION_ERROR",
    }


def test_h15_unknown_session_4xx_is_named_not_exception(client: TestClient) -> None:
    r = client.get("/api/replay/state", params={"replay_id": "RPL-DOES-NOT-EXIST"})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_h15_domain_rejection_carries_request_id(client: TestClient) -> None:
    """A deliberate 422 names the problem AND a correlation id (no str(e))."""
    r = client.post(
        "/api/replay/control",
        json={"action": "seek", "replay_id": _make_session(client)},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "seek_time" in detail
    assert "request_id=" in detail


# ---------------------------------------------------------------------------
# C-3: decision trace resolves against the ACTUALLY RUNNING engine
# ---------------------------------------------------------------------------


def test_c3_decision_trace_resolves_after_a_run(client: TestClient) -> None:
    """C-3: the endpoint 404'd by construction before the fix.

    The controller rebuilds a fresh engine per run and the long-lived
    session.engine is never executed, so reading session.engine's
    _last_decision_trace was dead code. The route now serves the trace of the
    run that actually executed. Trace rows only exist past the feature
    warmup (FEATURE_WARMUP_BARS), so the cursor is advanced past it first.
    """
    rid = _make_session(client)
    r = client.post("/api/replay/control", json={"action": "step_bar", "n": 120, "replay_id": rid})
    assert r.status_code == 200, r.text

    hit: dict[str, Any] | None = None
    for seq in range(0, 121):
        d = client.get("/api/replay/decision", params={"seq": seq, "replay_id": rid})
        if d.status_code == 200:
            hit = d.json()["decision"]
            break
    assert hit is not None, "no reachable decision row after a real run (C-3 regression)"
    assert isinstance(hit["decision_index"], int)
    assert "ts" in hit and "action" in hit and "bid" in hit

    # A seq that no run ever produced still 404s honestly.
    miss = client.get("/api/replay/decision", params={"seq": 99999, "replay_id": rid})
    assert miss.status_code == 404
    assert "not found" in miss.json()["detail"]


def test_c3_decision_404_when_no_run_yet(client: TestClient) -> None:
    """Fresh session: no trace from any executed run, so 404 either way."""
    rid = _make_session(client)
    r = client.get("/api/replay/decision", params={"seq": 0, "replay_id": rid})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"] or "no decision trace" in r.json()["detail"]


# ---------------------------------------------------------------------------
# C-4: regime_enabled is one truth end to end (no 500 path)
# ---------------------------------------------------------------------------


def test_c4_regime_enabled_state_never_500s(client: TestClient) -> None:
    """C-4: regime_enabled=True used to 500 /api/replay/state with
    AttributeError (session classifier built but never run; current_regime()
    derefs a None _stable_regime). The state must answer 200 in every phase —
    before any run, mid-run, and exhausted.
    """
    rid = _make_session(client, regime_enabled=True)
    # Fresh session: clock is None, so the state is the compact early shape.
    r0 = client.get("/api/replay/state", params={"replay_id": rid})
    assert r0.status_code == 200, r0.text
    st0 = r0.json()
    assert st0["clock"] is None and "counts" in st0

    # Mid-run: full cursor shape, regime block present (value may be null
    # while the classifier is in warmup — explicit null, never an exception).
    client.post("/api/replay/control", json={"action": "step_bar", "n": 40, "replay_id": rid})
    r1 = client.get("/api/replay/state", params={"replay_id": rid})
    assert r1.status_code == 200, r1.text
    st1 = r1.json()
    assert "regime" in st1
    assert st1["counts"]["bars"] == 40

    # Exhausted: still 200 — never the AttributeError path.
    client.post("/api/replay/control", json={"action": "play", "replay_id": rid})
    r2 = client.get("/api/replay/state", params={"replay_id": rid})
    assert r2.status_code == 200, r2.text
    assert r2.json()["counts"]["bars"] == N


def test_c4_regime_reaches_engine_config(client: TestClient) -> None:
    """C-4 plumbing half: the flag must reach THE CONFIG THE ENGINE READS.

    The session flag and the engine config flag used to diverge (split-brain:
    identity claimed regime_enabled while the engine classifier stayed
    disabled). identity now reports both, so the lie is visible.
    """
    rid = _make_session(client, regime_enabled=True)
    ident = client.get("/api/replay/report", params={"replay_id": rid}).json()["report"]["identity"]
    assert ident.get("regime_enabled") is True
    assert ident.get("engine_regime_enabled") is True

    rid2 = _make_session(client, regime_enabled=False)
    ident2 = client.get("/api/replay/report", params={"replay_id": rid2}).json()["report"][
        "identity"
    ]
    assert ident2.get("regime_enabled") is False
    assert ident2.get("engine_regime_enabled") is False


def test_c4_regime_off_is_consistent(client: TestClient) -> None:
    """The off default behaves identically: no classifier, explicit null."""
    rid = _make_session(client, regime_enabled=False)
    client.post("/api/replay/control", json={"action": "step_bar", "n": 10, "replay_id": rid})
    st = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st["counts"]["bars"] == 10
    assert st["regime"] is None  # explicitly null, not missing


# ---------------------------------------------------------------------------
# C-9: control status strings do not lie
# ---------------------------------------------------------------------------


def test_c9_step_past_end_reports_end_of_data(client: TestClient) -> None:
    """C-9: stepping past the last bar must answer END_OF_DATA, not OK."""
    rid = _make_session(client)
    r = client.post(
        "/api/replay/control",
        json={"action": "step_bar", "n": N + 500, "replay_id": rid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "END_OF_DATA"
    # An already-exhausted session keeps telling the truth.
    r2 = client.post("/api/replay/control", json={"action": "step_bar", "n": 5, "replay_id": rid})
    assert r2.json()["result"]["status"] == "END_OF_DATA"
    st = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st["unknown_events"] == 0


def test_c9_seek_to_end_reports_end_of_data(client: TestClient) -> None:
    rid = _make_session(client)
    r = client.post(
        "/api/replay/control",
        json={
            "action": "seek",
            "replay_id": rid,
            "seek_time": (T0 + timedelta(minutes=N - 1)).isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "END_OF_DATA"


def test_c9_mid_run_reports_ok_not_end_of_data(client: TestClient) -> None:
    """The honest status is not sticky: mid-run steps stay OK."""
    rid = _make_session(client)
    r = client.post("/api/replay/control", json={"action": "step_bar", "n": 20, "replay_id": rid})
    assert r.json()["result"]["status"] == "OK"
    r2 = client.post("/api/replay/control", json={"action": "step_bar", "n": 20, "replay_id": rid})
    assert r2.json()["result"]["status"] == "OK"
    st = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st["known_events"] == 40 and st["unknown_events"] == N - 40


def test_c9_reset_after_end_is_not_end_of_data(client: TestClient) -> None:
    """reset rewinds the cursor: it must not inherit END_OF_DATA."""
    rid = _make_session(client)
    client.post("/api/replay/control", json={"action": "play", "replay_id": rid})
    r = client.post("/api/replay/control", json={"action": "reset", "replay_id": rid})
    assert r.status_code == 200
    assert r.json()["result"]["status"] == "OK"
    st = client.get("/api/replay/state", params={"replay_id": rid}).json()
    assert st["known_events"] == 0 and st["unknown_events"] == N


# ---------------------------------------------------------------------------
# UI-M1: fabricated dataset identity is rejected (loader-level fail closed)
# ---------------------------------------------------------------------------


def _local_dataset_ready() -> bool:
    return M1.exists() and MODEL.exists()


def _real_window(hours: int = 6) -> dict[str, Any]:
    """A window that the LOCAL dataset can actually serve, plus its ids."""
    import polars as pl

    from nexus_scalp.research.mt5_tick_dataset import dataset_id

    df = pl.read_parquet(M1)
    t0 = df.row(0, named=True)["time_utc"]
    t0 = t0.replace(tzinfo=UTC) if t0.tzinfo is None else t0
    t_end = t0 + timedelta(minutes=60 * hours - 1)
    # UI-M1: the fingerprint is the digest of the bytes the loader actually
    # serves, so the canonical window is accepted by the identity gate. Tests
    # that need a bad fingerprint overwrite this field explicitly.
    import hashlib

    fp = hashlib.sha256(M1.read_bytes()).hexdigest()[:32]
    return {
        "dataset_id": dataset_id("XAUUSD", "bars_m1", t0, t_end),
        "dataset_fingerprint": fp,
        "symbol": "XAUUSD",
        "replay_mode": "BAR_REPLAY",
        "start_time": t0.isoformat(),
        "end_time": t_end.isoformat(),
        "git_commit": "impld-uim1",
        "model_artifact_path": str(MODEL),
    }


def test_uim1_fabricated_dataset_id_rejected(server_client: TestClient) -> None:
    """UI-M1: the loader is the authority on dataset identity.

    Web/replay_panel.js fabricated `UI-M1-YYYYMMDD` + `uipick-<start>-<end>`
    and `_replay_records_loader` accepted them silently, so a session could be
    created under a contract that names no dataset at all. A fabricated id now
    fails CLOSED with a 4xx that NAMES the bad id.
    """
    pytest.importorskip("polars")
    if not _local_dataset_ready():
        pytest.skip("local M1 dataset / model bundle not present")
    base = _real_window()
    for fake_id in ("UI-M1-20260924", "uipick-2026-07-01", "DS-NEVER-ACQUIRED"):
        payload = dict(base, dataset_id=fake_id, dataset_fingerprint="b" * 32)
        r = server_client.post("/api/replay/session", json=payload)
        assert r.status_code in (400, 422), (fake_id, r.status_code, r.text)
        detail = str(r.json().get("detail") or r.text)
        assert fake_id in detail, f"the 4xx must NAME the bad id: {detail}"


def test_uim1_bad_fingerprint_shape_rejected(server_client: TestClient) -> None:
    """The fingerprint must be the canonical 32-hex digest shape."""
    pytest.importorskip("polars")
    if not _local_dataset_ready():
        pytest.skip("local M1 dataset / model bundle not present")
    payload = dict(_real_window(), dataset_fingerprint="uipick-2026-07-01T00:00:00")
    r = server_client.post("/api/replay/session", json=payload)
    assert r.status_code in (400, 422), r.text
    assert "fingerprint" in str(r.json().get("detail", "")).lower()


def test_uim1_canonical_identity_accepted(server_client: TestClient) -> None:
    """The gate is not a blanket rejection: the canonical id round-trips.

    The id the loader itself would mint for that window is accepted, so the
    fail-closed path only ever refuses identities that name no dataset.
    """
    pytest.importorskip("polars")
    if not _local_dataset_ready():
        pytest.skip("local M1 dataset / model bundle not present")
    r = server_client.post("/api/replay/session", json=_real_window())
    assert r.status_code == 200, r.text
    rid = r.json()["replay_id"]
    st = server_client.get("/api/replay/state", params={"replay_id": rid})
    assert st.status_code == 200, st.text


def test_uim1_unknown_window_still_rejected(server_client: TestClient) -> None:
    """A canonical id whose window has no records fails closed too."""
    pytest.importorskip("polars")
    if not _local_dataset_ready():
        pytest.skip("local M1 dataset / model bundle not present")
    from nexus_scalp.research.mt5_tick_dataset import dataset_id

    far = datetime(2019, 1, 1, 0, 0, tzinfo=UTC)
    payload = dict(
        _real_window(),
        dataset_id=dataset_id("XAUUSD", "bars_m1", far, far + timedelta(minutes=359)),
        start_time=far.isoformat(),
        end_time=(far + timedelta(minutes=359)).isoformat(),
    )
    r = server_client.post("/api/replay/session", json=payload)
    assert r.status_code in (400, 422), r.text


# ---------------------------------------------------------------------------
# Source guard: no broker surface, no exception-text leak in the module
# ---------------------------------------------------------------------------


def test_source_guard_no_broker_surface_and_no_str_e() -> None:
    """Module-level guards: no broker surface, no exception text as detail.

    The H-15 rule is checked on the AST (comments/docstrings are prose; only
    what the code actually PASSES to a ``detail=`` kwarg can reach a client).
    """
    import ast

    src = (REPO / "src" / "nexus_scalp" / "web" / "replay_routes.py").read_text(encoding="utf-8")
    # Source guard: replay has no broker/order surface.
    assert "order_send(" not in src

    tree = ast.parse(src)
    leaks: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "detail" or not isinstance(kw.value, ast.Call):
                continue
            fn = kw.value.func
            if isinstance(fn, ast.Name) and fn.id in {"str", "repr"}:
                leaks.append(kw.value.lineno)
    assert not leaks, f"exception text passed as HTTP detail at line(s) {leaks}"
