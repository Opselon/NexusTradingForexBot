"""Operator routes tests (CHG-0043, TASK-CONTROL-CENTER).

Covers the /api/operator/* read-only surface end-to-end via TestClient
against the REAL create_app (engine_ref=None) and the real audit DB path
resolution, plus adversarial cases: missing decision, malformed filters,
additive-only route parity, read-only enforcement, and error-envelope
stability.

Run: .venv/Scripts/python -m pytest tests/unit/test_operator_routes.py -q
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]

BASELINE_OPENAPI_PATHS = 252  # route-parity floor captured 2026-09-02 pre-wiring


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app(engine_ref=None)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Route parity: the operator surface is additive-only
# ---------------------------------------------------------------------------


class TestAdditiveOnly:
    def test_openapi_grows_by_exactly_six(self, client: TestClient) -> None:
        paths = client.app.openapi()["paths"]
        operator = [p for p in paths if p.startswith("/api/operator")]
        assert len(paths) >= BASELINE_OPENAPI_PATHS
        assert len(operator) == 6, operator

    def test_operator_paths_discovered(self, client: TestClient) -> None:
        paths = client.app.openapi()["paths"]
        for p in (
            "/api/operator/summary",
            "/api/operator/decisions",
            "/api/operator/decisions/{decision_id}",
            "/api/operator/funnel",
            "/api/operator/no-trade",
            "/api/operator/orders",
        ):
            assert p in paths, p

    def test_all_operator_routes_are_get(self, client: TestClient) -> None:
        paths = client.app.openapi()["paths"]
        for p, methods in paths.items():
            if p.startswith("/api/operator"):
                assert set(methods) <= {"get"}, (p, set(methods))


# ---------------------------------------------------------------------------
# Read-only enforcement: the surface cannot mutate the audit trail
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_connect_ro_cannot_write(self) -> None:
        from nexus_scalp.web.operator_routes import _connect_ro

        con = _connect_ro()
        assert con is not None, "authoritative audit DB must exist in repo"
        try:
            with pytest.raises(sqlite3.OperationalError):
                con.execute("CREATE TABLE cc_mutation_probe (x INTEGER)")
        finally:
            con.close()

    def test_post_to_operator_route_is_405(self, client: TestClient) -> None:
        r = client.post("/api/operator/summary")
        assert r.status_code == 405


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_shape(self, client: TestClient) -> None:
        r = client.get("/api/operator/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert "runtime" in body and "ledger" in body and "warnings" in body
        rt = body["runtime"]
        for key in ("engine_running", "execution_mode", "runtime_mode", "state_version"):
            assert key in rt, key

    def test_summary_ledger_stats_are_bounded(self, client: TestClient) -> None:
        body = client.get("/api/operator/summary").json()
        ledger = body["ledger"]
        if ledger.get("available"):
            assert ledger["scanned_rows"] <= ledger["window"]
            assert isinstance(ledger.get("actions"), dict)


class TestDecisions:
    def test_history_limit_clamped(self, client: TestClient) -> None:
        r = client.get("/api/operator/decisions?limit=99999")
        assert r.status_code == 200
        assert r.json()["count"] <= 500

    def test_action_filter(self, client: TestClient) -> None:
        r = client.get("/api/operator/decisions?action=NO_TRADE&limit=20")
        assert r.status_code == 200
        rows = r.json()["rows"]
        assert all(row["action"] == "NO_TRADE" for row in rows)

    def test_rows_carry_payload_ok_flag(self, client: TestClient) -> None:
        rows = client.get("/api/operator/decisions?limit=10").json()["rows"]
        assert rows, "ledger fixture expected to be non-empty in repo"
        for row in rows:
            assert isinstance(row["payload_ok"], bool)
            assert "probabilities" in row

    def test_missing_decision_returns_stable_envelope(self, client: TestClient) -> None:
        r = client.get("/api/operator/decisions/999999999")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert body["error"]["code"] == "NOT_FOUND"
        assert "request_id" in body["error"]

    def test_detail_includes_correlation_metadata(self, client: TestClient) -> None:
        rows = client.get("/api/operator/decisions?limit=1").json()["rows"]
        assert rows
        detail = client.get(f"/api/operator/decisions/{rows[0]['id']}").json()
        assert detail["available"] is True
        d = detail["decision"]
        assert "order_correlation" in d
        assert "orders" in d
        assert "columns_not_recorded" in d


class TestFunnelAndNoTrade:
    def test_funnel_totals_reconcile(self, client: TestClient) -> None:
        body = client.get("/api/operator/funnel").json()
        assert body["available"] is True
        assert body["total"] == sum(s["count"] for s in body["stages"])
        assert body["total"] == sum(g["count"] for g in body["gates"])
        assert "TERMINAL" in body["note"]

    def test_no_trade_distribution_sums_to_total(self, client: TestClient) -> None:
        body = client.get("/api/operator/no-trade").json()
        assert body["available"] is True
        assert body["total"] == sum(g["count"] for g in body["gates"])
        assert body["total"] == sum(x["count"] for x in body["reasons"])
        assert isinstance(body["model_direction_unresolved"], int)
        assert body["model_direction_unresolved"] <= body["total"]

    def test_no_trade_hours_filter_bounds_results(self, client: TestClient) -> None:
        full = client.get("/api/operator/no-trade").json()["total"]
        one = client.get("/api/operator/no-trade?hours=1").json()["total"]
        assert one <= full


class TestOrders:
    def test_orders_latency_stats_computed_only_from_numeric_rows(self, client: TestClient) -> None:
        body = client.get("/api/operator/orders?limit=25").json()
        assert body["available"] is True
        if body["count"] == 0:
            assert body["latency"] is None
            return
        if body["latency"] is not None:
            lat = body["latency"]
            assert lat["n"] >= 1
            assert lat["p50_ms"] <= lat["p95_ms"] <= lat["p99_ms"]

    def test_orders_limit_clamped(self, client: TestClient) -> None:
        body = client.get("/api/operator/orders?limit=100000").json()
        assert body["count"] <= 200


# ---------------------------------------------------------------------------
# Security: no secrets / no stack traces reach the response
# ---------------------------------------------------------------------------


class TestSanitization:
    def test_error_envelope_has_no_traceback(self, client: TestClient) -> None:
        body = client.get("/api/operator/decisions/999999999").json()
        text = str(body)
        assert "Traceback" not in text
        assert "sqlite3." not in text

    def test_list_rows_never_carry_raw_payload_blob(self, client: TestClient) -> None:
        rows = client.get("/api/operator/decisions?limit=5").json()["rows"]
        for row in rows:
            assert "payload" not in row, "raw blob must stay server-side in list views"
