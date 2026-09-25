"""BUG-312 regression: /api/command-center/spatial must never go O(N x inspector).

The old route built one FULL inspector() per registry entry
(registry.get + event projection + evidence completeness + invariant check +
a 2000-row research_runs scan ~= 1.4-2.0 s each). With 500 strategies that is
~17 minutes sequential, so the endpoint never returned and the Command Center
page (spatial = default tab) hung on its loading skeleton forever.

The causal pin: CommandCenterAPI.inspector is patched to RAISE. The route must
still answer 200 with the exact enrichment fields the renderer consumes
(evaluation gates + eligibility_state + health elevation + ring counts),
because everything it needs is a pure function of the registry entry already
listed (build_snapshot), with the running-runs map fetched ONCE.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.research.event_projection import LifecycleEventProjection
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
)
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.web.command_center_integration import register_command_center_routes
from nexus_scalp.web.command_center_routes import CommandCenterAPI
from tests.unit.test_command_center_api import _make_entry


class _FakeRepo:
    _is_sqlite = False
    _db_path = ":memory:"


def _err(code: str, extra: dict | None = None) -> dict:
    return {"available": False, "error": code, **(extra or {})}


def _install_app(monkeypatch, entries: dict[str, StrategyRegistryEntry]) -> FastAPI:
    """Register the real route closures over an in-memory registry."""

    def fake_list(self, lifecycle=None, limit=200):
        out = list(entries.values())
        if lifecycle:
            out = [e for e in out if e.lifecycle.value == lifecycle]
        return out[:limit] if limit else out

    def fake_get(self, sid, ver=None):
        return entries.get(sid)

    # Class-level so the fresh StrategyRegistry built per request is patched too.
    monkeypatch.setattr(StrategyRegistry, "list", fake_list)
    monkeypatch.setattr(StrategyRegistry, "get", fake_get)
    # The projection is never consulted by the fixed route; make it loud.
    monkeypatch.setattr(
        LifecycleEventProjection,
        "events_for_strategy",
        lambda self, *a, **k: (_ for _ in ()).throw(
            AssertionError("spatial must not run per-strategy event projection")
        ),
    )
    # The O(N x inspector) path must be structurally impossible.
    monkeypatch.setattr(
        CommandCenterAPI,
        "inspector",
        lambda self, sid: (_ for _ in ()).throw(
            AssertionError("spatial must not call per-strategy inspector()")
        ),
    )

    class _Engine:
        audit = _FakeRepo()

    app = FastAPI()
    register_command_center_routes(app, _Engine, lambda o: o, _err)
    return app


@pytest.fixture()
def entries() -> dict[str, StrategyRegistryEntry]:
    return {
        "s-active": _make_entry(
            "s-active",
            CandidateLifecycle.ACTIVE,
            score=StrategyScore(final_score=0.9, verdict="VALIDATED"),
        ),
        "s-validated": _make_entry(
            "s-validated",
            CandidateLifecycle.VALIDATED,
            score=StrategyScore(final_score=0.8, verdict="VALIDATED"),
            backtest=BacktestResult(
                strategy_id="s-validated", strategy_version="1", dataset_id="d", total_trades=30
            ),
            oos=OOSResult(
                strategy_id="s-validated", strategy_version="1", dataset_id="d", status="PASS"
            ),
            robustness=RobustnessResult(
                strategy_id="s-validated", strategy_version="1", status="PASS"
            ),
        ),
        "s-rejected": _make_entry("s-rejected", CandidateLifecycle.REJECTED),
        "s-discovered": _make_entry("s-discovered", CandidateLifecycle.DISCOVERED),
    }


class TestSpatialNeverCallsInspector:
    def test_spatial_answers_without_inspector(self, monkeypatch, entries):
        app = _install_app(monkeypatch, entries)
        client = TestClient(app)
        res = client.get("/api/command-center/spatial")
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is True

        by_id = {n["strategy_id"]: n for n in body["nodes"]}
        assert set(by_id) == set(entries)

        # Elevation comes from health_score.final of the IN-MEMORY snapshot.
        assert by_id["s-validated"]["elevation"] == pytest.approx(0.8)
        assert by_id["s-active"]["elevation"] == pytest.approx(0.9)
        # Rejected has no score -> honest null, never a fabricated zero.
        assert by_id["s-rejected"]["elevation"] is None

        # Ring count = PASS evidence statuses (backtest+oos+robustness PASS,
        # walkforward absent -> 3 of 4, never fabricated).
        assert by_id["s-validated"]["ring_count"] == 3
        assert by_id["s-rejected"]["ring_count"] == 0

        # Enrichment the renderer consumes: evaluation gates + eligibility.
        ev = by_id["s-validated"]["evaluation"]
        assert set(ev["gates"]) == {"BACKTEST", "WALK_FORWARD", "OOS", "ROBUSTNESS", "SCORE"}
        assert ev["gates"]["BACKTEST"] == "PASS"
        assert ev["current_stage"] is not None
        assert by_id["s-active"]["eligibility_state"] == "YES"
        assert by_id["s-validated"]["eligibility_state"] == "BLOCKED"
        assert by_id["s-discovered"]["eligibility_state"] == "BLOCKED"

        # Zone census still reflects the full registry.
        counts = {z["zone"]: z["count"] for z in body["zones"]}
        assert counts["ACTIVE"] == 1
        assert counts["VALIDATED"] == 1
        assert counts["REJECTED"] == 1
        assert counts["DISCOVERED"] == 1
        assert body["meta"]["total_nodes"] == 4

    def test_spatial_scales_to_full_fleet(self, monkeypatch, entries):
        """500+ entries must answer through the same light path (no per-node I/O)."""
        states = list(CandidateLifecycle)
        for i in range(500):
            sid = f"s-scale-{i:04d}"
            entries[sid] = _make_entry(sid, states[i % len(states)])
        app = _install_app(monkeypatch, entries)
        client = TestClient(app)
        res = client.get("/api/command-center/spatial")
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is True
        assert body["meta"]["total_nodes"] == 504
        # Every node carries the enrichment (previously the slow part).
        assert all("evaluation" in n and "eligibility_state" in n for n in body["nodes"])
