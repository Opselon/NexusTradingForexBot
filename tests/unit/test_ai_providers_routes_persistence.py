"""Web-route tests for the durable decision endpoints (Sections 23, 41, 63).

SIMULATED TEST DATA: the decisions seeded here are synthetic; none come from a
live position and none place orders.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An API client whose decisions ledger and settings DB are both isolated
    to the tmp dir — the real app reads from %LOCALAPPDATA% otherwise."""
    monkeypatch.setenv("NEXUS_DECISIONS_DB", str(tmp_path / "decisions.db"))
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))

    from nexus_scalp.web.ai_providers_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    # The router already carries its own /api/ai-providers prefix.
    app.include_router(router)
    return TestClient(app)


class TestDecisionsEndpoint:
    """/decisions reads the durable ledger and reports where it read from."""

    def test_empty_ledger_reports_source_honestly(self, client: TestClient):
        res = client.get("/api/ai-providers/decisions")
        assert res.status_code == 200
        body = res.json()
        # An empty ledger is reported as in-memory (no rows to serve), never
        # as a fake durable read.
        assert body["source"] == "in_memory"
        assert body["decisions"] == []

    def test_a_recorded_decision_is_served_as_durable(self, client: TestClient):
        from nexus_scalp.ai_providers.store import ProviderDecisionStore
        from nexus_scalp.settings.paths import decisions_db_path

        store = ProviderDecisionStore(db_path=decisions_db_path())
        store.record(
            {
                "decision_id": "dec-api-001",
                "final_action": "CLOSE",
                "policy": {"scores": {"CLOSE": 0.8}, "winner": "CLOSE"},
                "risk": {"allowed": False, "rejections": ["max_daily_loss"]},
                "evidence": {},
                "providers_used": ["openrouter"],
                "providers_failed": [],
                "fallback_used": False,
                "fallback_reason": "",
                "decision_mode": "SHADOW",
                "versions": {},
                "created_at": "2026-09-24T11:00:00Z",
                "latency_ms": 88.0,
                "stage_timings_ms": {},
                "is_test_data": True,
                "active_provider": "openrouter",
            }
        )
        store.close()

        res = client.get("/api/ai-providers/decisions")
        body = res.json()
        assert body["source"] == "durable"
        assert len(body["decisions"]) == 1
        row = body["decisions"][0]
        assert row["decision_id"] == "dec-api-001"
        assert row["final_action"] == "CLOSE"
        # The risk gate outcome must survive the round trip — that is the
        # audit trail the operator reads (Section 41).
        assert row["risk"]["allowed"] is False
        assert row["risk"]["rejections"] == ["max_daily_loss"]
        assert row["is_test_data"] is True

    def test_limit_is_honoured(self, client: TestClient):
        from nexus_scalp.ai_providers.store import ProviderDecisionStore
        from nexus_scalp.settings.paths import decisions_db_path

        store = ProviderDecisionStore(db_path=decisions_db_path())
        for i in range(10):
            p = {
                "decision_id": f"dec-lim-{i:03d}",
                "final_action": "HOLD",
                "policy": {"scores": {}, "winner": "HOLD"},
                "risk": {"allowed": True, "rejections": []},
                "evidence": {},
                "providers_used": [],
                "providers_failed": [],
                "fallback_used": False,
                "fallback_reason": "",
                "decision_mode": "HYBRID",
                "versions": {},
                "created_at": f"2026-09-24T12:00:0{i}Z",
                "latency_ms": 10.0,
                "stage_timings_ms": {},
                "is_test_data": True,
            }
            store.record(p)
        store.close()

        res = client.get("/api/ai-providers/decisions?limit=3")
        body = res.json()
        assert body["source"] == "durable"
        assert len(body["decisions"]) == 3


class TestDecisionDetailEndpoint:
    """/decision/{id} prefers the ledger and 404s on a miss."""

    def test_404_for_unknown_decision(self, client: TestClient):
        res = client.get("/api/ai-providers/decision/nope")
        assert res.status_code == 404

    def test_detail_returns_the_durable_row(self, client: TestClient):
        from nexus_scalp.ai_providers.store import ProviderDecisionStore
        from nexus_scalp.settings.paths import decisions_db_path

        store = ProviderDecisionStore(db_path=decisions_db_path())
        store.record(
            {
                "decision_id": "dec-detail-001",
                "final_action": "REDUCE",
                "policy": {"scores": {"REDUCE": 0.55}, "winner": "REDUCE"},
                "risk": {"allowed": True, "rejections": []},
                "evidence": {"openrouter": {"p_reduce": 0.55}},
                "providers_used": ["openrouter"],
                "providers_failed": ["system_one"],
                "fallback_used": True,
                "fallback_reason": "system_one timeout",
                "decision_mode": "HYBRID",
                "versions": {"contract": "1.1.0"},
                "created_at": "2026-09-24T13:00:00Z",
                "latency_ms": 250.0,
                "stage_timings_ms": {},
                "is_test_data": True,
                "active_provider": "openrouter",
            }
        )
        store.close()

        res = client.get("/api/ai-providers/decision/dec-detail-001")
        assert res.status_code == 200
        body = res.json()
        assert body["source"] == "durable"
        assert body["decision"]["decision_id"] == "dec-detail-001"
        assert body["decision"]["fallback_used"] is True
        assert body["decision"]["fallback_reason"] == "system_one timeout"
        assert body["decision"]["providers_failed"] == ["system_one"]
