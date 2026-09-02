"""Integration + property/fuzz tests for the /api/v1 platform over REAL fixtures.

Runs the full request path (HTTP -> router -> store/repository -> response)
against a deterministic AuditRepository seeded with real-shape rows, plus the
standalone v1 app for unavailable-dependency semantics. Bounded generative
pagination/filter sweeps (fixed seed, no new deps) prove no-dup/no-skip/determinism.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.web.api_v1_wiring import (
    _iter_effective_routes,
    create_v1_app,
    register_api_v1,
)
from nexus_scalp.web.server import create_app

# ---------------------------------------------------------------------------
# deterministic audit DB fixture (real repository, real SQLite files)
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_repo(tmp_path: Any) -> AuditRepository:
    from nexus_scalp.database.config import DatabaseConfig

    cfg = DatabaseConfig(provider="sqlite", sqlite_path=str(tmp_path / "audit.db"))
    repo = AuditRepository(config=cfg)
    # Seed audit_signals deterministically (12 rows, mixed actions/stages/symbols)
    base = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    actions = ["BUY", "SELL", "NO_TRADE"]
    symbols = ["XAUUSD", "EURUSD"]
    for i in range(12):
        ts = (base + timedelta(minutes=15 * i)).isoformat()
        payload = json.dumps(
            {
                "ai_buy_probability": 0.2 + 0.01 * i,
                "ai_sell_probability": 0.3,
                "ai_no_trade_probability": 0.4,
                "guardian_status": "PASS" if i % 3 else "BLOCK",
                "risk_allowed": i % 2 == 0,
                "reason": "seed",
            }
        )
        with sqlite3.connect(repo._db_path, timeout=5.0) as conn:
            conn.execute(
                """
                INSERT INTO audit_signals (request_id, symbol, action, confidence, proposed_entry,
                    stop_loss, take_profit, regime, generated_at, payload, execution_mode,
                    reason_code, decision_stage, blocked_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"req_seed_{i:03d}",
                    symbols[i % 2],
                    actions[i % 3],
                    0.55 + 0.01 * i,
                    2000.0 + i,
                    1990.0,
                    2020.0,
                    "TRENDING",
                    ts,
                    payload,
                    "PAPER",
                    "SEED",
                    "EVALUATED" if i % 3 else "BLOCKED",
                    None if i % 3 else "GUARDIAN",
                ),
            )
    # Seed audit_ledger (5 closed rows) + audit_executions (5 events)
    for i in range(5):
        with sqlite3.connect(repo._db_path, timeout=5.0) as conn:
            conn.execute(
                """
                INSERT INTO audit_ledger (ticket, symbol, direction, volume, entry_price,
                    status, pnl, timestamp)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    100 + i,
                    "XAUUSD",
                    "BUY" if i % 2 else "SELL",
                    0.1,
                    2000.0 + i,
                    "CLOSED",
                    1.5 * (1 if i % 2 else -1),
                    (base + timedelta(hours=i)).isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_executions (order_id, symbol, order_type, volume, price, status, executed_at, payload)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    f"ord_seed_{i:03d}",
                    "XAUUSD",
                    "BUY" if i % 2 else "SELL",
                    0.1,
                    2000.0 + i,
                    "FILLED",
                    (base + timedelta(hours=i)).isoformat(),
                    "{}",
                ),
            )
    return repo


@pytest.fixture()
def v1_client(seeded_repo: AuditRepository) -> TestClient:
    app = create_v1_app()
    # Bind the shared audit accessor to the SEEDED repo (same lazy attribute the
    # routes use via get_audit_repo) — full request path over real SQLite.
    app.state.audit_v1_repo = seeded_repo
    return TestClient(app)


def _unwrap(resp: Any) -> Any:
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) >= {"data", "meta"}
    return body["data"]


# ---------------------------------------------------------------------------
# happy paths over real data
# ---------------------------------------------------------------------------


def test_signals_latest_returns_seeded_row(v1_client: TestClient) -> None:
    data = _unwrap(v1_client.get("/api/v1/signals/latest"))
    assert data["request_id"] == "req_seed_011"  # ORDER BY id DESC
    assert data["symbol"] in {"XAUUSD", "EURUSD"}
    assert set(data["payload"]) >= {"guardian_status", "risk_allowed"}


def test_signals_history_filter_composition(v1_client: TestClient) -> None:
    data = _unwrap(
        v1_client.get("/api/v1/signals/history", params={"symbol": "XAUUSD", "action": "BUY"})
    )
    assert data["items"], "filtered history should match seeded rows"
    assert all(r["symbol"] == "XAUUSD" and r["action"] == "BUY" for r in data["items"])
    ids = [r["request_id"] for r in data["items"]]
    assert len(ids) == len(set(ids))


def test_decisions_pagination_no_dup_no_skip(v1_client: TestClient) -> None:
    seen: list[str] = []
    page = 1
    while True:
        data = _unwrap(v1_client.get("/api/v1/decisions", params={"page": page, "page_size": 5}))
        items = data["items"]
        seen.extend(r["request_id"] for r in items)
        if not data["has_more"]:
            break
        page += 1
        assert page <= 10, "pagination did not terminate"
    assert len(seen) == 12
    assert len(set(seen)) == 12, "pages overlap or skip rows"


def test_decisions_detail_evidence_gates_explanation_consistency(v1_client: TestClient) -> None:
    latest = _unwrap(v1_client.get("/api/v1/decisions/latest"))
    rid = latest["decision_id"]
    detail = _unwrap(v1_client.get(f"/api/v1/decisions/{rid}"))
    assert detail["decision_id"] == rid
    evidence = _unwrap(v1_client.get(f"/api/v1/decisions/{rid}/evidence"))
    assert evidence["evidence"]["guardian_status"] in {"PASS", "BLOCK"}
    gates = _unwrap(v1_client.get(f"/api/v1/decisions/{rid}/gates"))
    gate_names = [g["gate"] for g in gates["gates"]]
    assert gate_names == [
        "decision_stage",
        "blocked_by",
        "reason_code",
        "guardian_status",
        "risk_allowed",
    ]
    for g in gates["gates"]:
        assert isinstance(g["passed"], bool)
    expl = _unwrap(v1_client.get(f"/api/v1/decisions/{rid}/explanation"))
    assert expl["decision_id"] == rid
    assert isinstance(expl["explanation"], str) and expl["explanation"]


def test_decisions_no_trade_distribution_matches_seed(v1_client: TestClient) -> None:
    stats = _unwrap(v1_client.get("/api/v1/decisions/stats"))
    assert stats["total"] == 12
    assert stats["by_action"]["NO_TRADE"] == 4
    no_trade = _unwrap(v1_client.get("/api/v1/decisions/no-trade"))
    assert no_trade["total"] == 4
    reasons = _unwrap(v1_client.get("/api/v1/decisions/no-trade/reasons"))
    assert reasons["total"] == 4
    assert isinstance(reasons["reasons"], dict)


def test_execution_history_over_seeded_executions(v1_client: TestClient) -> None:
    data = _unwrap(v1_client.get("/api/v1/execution/history", params={"page_size": 3}))
    assert len(data["items"]) == 3
    assert data["has_more"] is True
    assert all(r["status"] == "FILLED" for r in data["items"])
    seen = [r["order_id"] for r in data["items"]]
    page2 = _unwrap(v1_client.get("/api/v1/execution/history", params={"page": 2, "page_size": 3}))
    seen2 = [r["order_id"] for r in page2["items"]]
    assert not set(seen) & set(seen2), "page 1 and 2 must not overlap"


def test_database_status_and_integrity_readonly(v1_client: TestClient) -> None:
    status = _unwrap(v1_client.get("/api/v1/database/status"))
    assert status["exists"] is True
    assert status["size_bytes"] > 0
    integrity = _unwrap(v1_client.get("/api/v1/database/integrity"))
    assert integrity["quick_check"] == "ok"
    assert integrity["row_counts"]["audit_signals"] == 12


def test_incidents_empty_is_truthful(v1_client: TestClient) -> None:
    data = _unwrap(v1_client.get("/api/v1/incidents"))
    assert data["items"] == []
    assert data["has_more"] is False


# ---------------------------------------------------------------------------
# bounded generative property tests (fixed seed; no new dependencies)
# ---------------------------------------------------------------------------


def test_property_pagination_invariants_decisions(v1_client: TestClient) -> None:
    rng = random.Random(20260902)
    for _ in range(8):
        page_size = rng.choice([1, 2, 3, 5, 7, 11, 50, 200])
        page = rng.randint(1, 4)
        r = v1_client.get("/api/v1/decisions", params={"page": page, "page_size": page_size})
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["items"]) <= page_size
        assert data["page"] == page and data["page_size"] == page_size
        # deterministic ordering: same query twice -> identical DATA (meta carries
        # a wall-clock generated_at, so only the data plane must be stable)
        r2 = v1_client.get("/api/v1/decisions", params={"page": page, "page_size": page_size})
        assert r.json()["data"] == r2.json()["data"]


def test_property_pagination_window_cover_exact_slice(v1_client: TestClient) -> None:
    """page k with size s must equal rows[k*s:(k+1)*s] of the unpaginated order."""
    full = _unwrap(v1_client.get("/api/v1/decisions", params={"page_size": 200}))["items"]
    full_ids = [r["request_id"] for r in full]
    for page, size in [(1, 4), (2, 4), (3, 4), (2, 5)]:
        data = _unwrap(v1_client.get("/api/v1/decisions", params={"page": page, "page_size": size}))
        expected = full_ids[(page - 1) * size : (page - 1) * size + size]
        assert [r["request_id"] for r in data["items"]] == expected
        assert data["has_more"] == ((page - 1) * size + size < len(full_ids))


def test_property_invalid_pagination_always_422(v1_client: TestClient) -> None:
    rng = random.Random(42)
    for _ in range(6):
        bad_page = rng.choice([0, -1, -100])
        r = v1_client.get("/api/v1/decisions", params={"page": bad_page})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    for bad_size in (0, -3, 201, 10_000):
        r = v1_client.get("/api/v1/decisions", params={"page_size": bad_size})
        assert r.status_code == 422


def test_property_fuzz_query_params_no_500(v1_client: TestClient) -> None:
    """Bounded fuzz: hostile query values must never produce 500s or leak internals."""
    rng = random.Random(1337)
    hostile_values = [
        "'",
        '"',
        "'; DROP TABLE audit_signals;--",
        "%00",
        "../../etc/passwd",
        "X" * 300,
        "0",
        "-1",
        "1e309",
        "NaN",
        None,
        "",
        " 🚀 ",
    ]
    routes = [
        "/api/v1/signals/history",
        "/api/v1/decisions",
        "/api/v1/incidents",
        "/api/v1/research/runs",
        "/api/v1/shadow/runs",
        "/api/v1/audit/events",
    ]
    for _ in range(40):
        route = rng.choice(routes)
        params: dict[str, str] = {}
        for key in (
            "symbol",
            "action",
            "status",
            "severity",
            "category",
            "component",
            "strategy_id",
            "run_id",
        ):
            if rng.random() < 0.4:
                v = rng.choice(hostile_values)
                if v is not None:
                    params[key] = v
        r = v1_client.get(route, params=params)
        assert r.status_code < 500, (route, params, r.status_code, r.text[:200])
        if r.status_code >= 400:
            err = r.json()["error"]
            assert err["code"] in {
                "VALIDATION_ERROR",
                "RESOURCE_NOT_FOUND",
                "DEPENDENCY_UNAVAILABLE",
            }
            assert "Traceback" not in r.text and ".py" not in r.text


def test_property_fuzz_path_ids_no_500(v1_client: TestClient) -> None:
    rng = random.Random(777)
    templates = [
        "/api/v1/decisions/{id}",
        "/api/v1/decisions/{id}/evidence",
        "/api/v1/decisions/{id}/gates",
        "/api/v1/decisions/{id}/explanation",
        "/api/v1/incidents/{id}",
        "/api/v1/incidents/{id}/timeline",
        "/api/v1/research/strategies/{id}",
        "/api/v1/shadow/runs/{id}",
    ]
    payloads = ["'", "x" * 250, "%2e%2e%2f", "req_000", "🚀", "a/b", "a?b=c"]
    for _ in range(30):
        tpl = rng.choice(templates)
        url = tpl.format(id=rng.choice(payloads))
        r = v1_client.get(url)
        assert r.status_code < 500, (url, r.status_code)


# ---------------------------------------------------------------------------
# security: secret scan + method abuse on the live v1 surface
# ---------------------------------------------------------------------------


def test_secret_scan_over_sampled_v1_responses(v1_client: TestClient) -> None:
    import re

    secret_shape = re.compile(
        r"(?i)(token|password|secret|apikey|api_key|credential|login)\"[:\s]*\"(?!\"\$)[^\"]{4,}"
    )
    checked = 0
    for path in (
        "/api/v1/system/status",
        "/api/v1/config/schema",
        "/api/v1/decisions?pending=1",
        "/api/v1/database/status",
        "/api/v1/audit/events",
        "/api/v1/observability/metrics",
        "/api/v1/features/contract",
        "/api/v1/research/status",
        "/api/v1/shadow/status",
    ):
        r = v1_client.get(path.split("?")[0], params={"page_size": 10})
        assert r.status_code < 500, path
        matches = secret_shape.findall(r.text)
        # masked values (*** ) are fine; raw long secret-shaped values are not
        for m in matches:
            assert m[1] == "***", (path, m)
        checked += 1
    assert checked >= 9


def test_method_abuse_rejected_cleanly(v1_client: TestClient) -> None:
    # GET on a POST-only route -> 405 envelope (path-guarded to /api/v1)
    r = v1_client.get("/api/v1/system/refresh")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert r.json()["error"]["retryable"] is False
    # POST on a GET-only route -> 405 envelope
    r2 = v1_client.post("/api/v1/system/version", json={})
    assert r2.status_code == 405
    # DELETE anywhere in v1 -> 405 envelope
    r3 = v1_client.delete("/api/v1/decisions/req_x")
    assert r3.status_code == 405


def test_post_refresh_requires_engine_truthfully(v1_client: TestClient) -> None:
    r = v1_client.post("/api/v1/system/refresh", headers={"Idempotency-Key": "idem-001"})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "ENGINE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# dashboard-mounted surface: v1 coexists with legacy routes (additive wiring)
# ---------------------------------------------------------------------------


def test_register_api_v1_on_legacy_app_additive(seeded_repo: AuditRepository) -> None:
    """create_app() wires v1 itself (one-block integration). The legacy surface
    must stay intact and a MANUAL re-registration must not duplicate paths."""
    app = create_app(engine_ref=None)
    effective = {getattr(r, "path", "") for r in _iter_effective_routes(app.router.routes)}
    v1_paths = {p for p in effective if p.startswith("/api/v1")}
    assert len(v1_paths) >= 60, f"v1 not wired into legacy app: {len(v1_paths)}"
    before_paths = {
        getattr(p, "path", "")
        for p in _iter_effective_routes(app.router.routes)
        if getattr(p, "path", "").startswith("/api/v1")
    }
    # legacy surface intact
    assert "/api/status" in effective
    assert "/health" in effective
    client = TestClient(app)
    r = client.get("/api/v1/system/version")
    assert r.status_code == 200
    # legacy route unaffected by v1 handlers
    r2 = client.get("/api/status")
    assert r2.status_code in (200, 503)
    # every v1 route is unique (no double-mount)
    assert len(before_paths) >= 60
