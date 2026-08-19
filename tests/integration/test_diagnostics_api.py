"""TASK-12 diagnostics API integration tests (TEST-INCIDENT-API-01..05).

Verifies the read-only forensic incident endpoints:
  GET /api/diagnostics/incidents
  GET /api/diagnostics/incidents/{id}
  GET /api/diagnostics/health
  GET /api/diagnostics/lineage
  GET /api/diagnostics/search
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from nexus_scalp.incidents.models import Incident, IncidentCategory, IncidentSeverity
from nexus_scalp.incidents.store import IncidentStore
from nexus_scalp.web.server import create_app


def _seed_incident(db: Path) -> str:
    store = IncidentStore(db_path=str(db))
    store.ensure_schema()
    inc = Incident(
        severity=IncidentSeverity.HIGH,
        category=IncidentCategory.MT5,
        component="mt5",
        operation="MT5_CALL_FAILED",
        correlation_id="corr-12",
    )
    store.save(inc)
    return inc.incident_id


def test_diagnostics_incidents_roundtrip(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "audit.db"
    inc_id = _seed_incident(db)

    def fake_db_path() -> str:
        return str(db)

    import nexus_scalp.web.server as server_mod

    monkeypatch.setattr(server_mod, "db_path_for_audit", fake_db_path)
    app = create_app(None)
    client = TestClient(app)

    # list
    r = client.get("/api/diagnostics/incidents")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["counts"]["total"] == 1
    assert data["incidents"][0]["incident_id"] == inc_id

    # health
    r = client.get("/api/diagnostics/health")
    assert r.status_code == 200
    assert r.json()["counts"]["high"] == 1

    # detail
    r = client.get(f"/api/diagnostics/incidents/{inc_id}")
    assert r.status_code == 200
    assert r.json()["incident"]["component"] == "mt5"

    # search
    r = client.get("/api/diagnostics/search", params={"query": "MT5"})
    assert r.status_code == 200
    assert len(r.json()["incidents"]) >= 1

    # lineage
    r = client.get("/api/diagnostics/lineage", params={"field": "pnl"})
    assert r.status_code == 200
    assert r.json()["available"] is True
    assert "MT5" in r.json()["source"]


def test_diagnostics_routes_are_read_only(tmp_path: Path, monkeypatch) -> None:
    """Every /api/diagnostics route must be GET (no mutation surface)."""
    db = tmp_path / "audit.db"
    _seed_incident(db)

    def fake_db_path() -> str:
        return str(db)

    import nexus_scalp.web.server as server_mod

    monkeypatch.setattr(server_mod, "db_path_for_audit", fake_db_path)
    app = create_app(None)
    client = TestClient(app)
    for method, path in [
        ("post", "/api/diagnostics/incidents"),
        ("put", "/api/diagnostics/incidents/x"),
        ("delete", "/api/diagnostics/incidents/x"),
        ("post", "/api/diagnostics/health"),
    ]:
        r = client.request(method, path)
        assert r.status_code in (405, 404), f"{method.upper()} {path} should not be allowed"
