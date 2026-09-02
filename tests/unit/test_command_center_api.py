"""
Tests for the Strategy Command Center API (Phase 3 foundation).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

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
from nexus_scalp.web.command_center_routes import CommandCenterAPI


class _FakeRepo:
    _is_sqlite = False
    _db_path = ":memory:"


def _make_entry(sid: str, lc: CandidateLifecycle, **kw) -> StrategyRegistryEntry:
    base = dict(
        strategy_id=sid,
        strategy_version="1.0.0",
        lifecycle=lc,
        created_at=datetime.now(UTC) - timedelta(days=2),
        updated_at=datetime.now(UTC) - timedelta(hours=1),
        validation_lineage=[
            f"{datetime.now(UTC).isoformat()}:{lc.value}:operator_promotion:actor=tester"
        ],
    )
    base.update(kw)
    return StrategyRegistryEntry(**base)


@pytest.fixture()
def api(monkeypatch):
    reg = StrategyRegistry(_FakeRepo())
    proj = LifecycleEventProjection(_FakeRepo())

    # In-memory registry override (no SQLite needed for routes logic).
    entries: dict[str, StrategyRegistryEntry] = {}
    _FakeRepo._registry_entries = entries

    def fake_get(sid, ver=None):
        return entries.get(sid)

    def fake_list(lifecycle=None, limit=200):
        out = list(entries.values())
        if lifecycle:
            out = [e for e in out if e.lifecycle.value == lifecycle]
        return out

    monkeypatch.setattr(reg, "get", fake_get)
    monkeypatch.setattr(reg, "list", fake_list)

    api = CommandCenterAPI(_FakeRepo(), registry=reg, projection=proj)

    # Prepopulate a few entries.
    entries["s-active"] = _make_entry(
        "s-active",
        CandidateLifecycle.ACTIVE,
        score=StrategyScore(final_score=0.9, verdict="VALIDATED"),
    )
    entries["s-validated"] = _make_entry(
        "s-validated",
        CandidateLifecycle.VALIDATED,
        score=StrategyScore(final_score=0.8, verdict="VALIDATED"),
        backtest=BacktestResult(
            strategy_id="s-validated", strategy_version="1", dataset_id="d", total_trades=30
        ),
    )
    entries["s-rejected"] = _make_entry("s-rejected", CandidateLifecycle.REJECTED)
    entries["s-discovered"] = _make_entry("s-discovered", CandidateLifecycle.DISCOVERED)
    return api


class TestCommandCenterOverview:
    def test_counts(self, api):
        ov = api.overview()
        assert ov["available"] is True
        assert ov["total_strategies"] == 4
        assert ov["by_lifecycle"]["ACTIVE"] == 1
        assert ov["by_lifecycle"]["VALIDATED"] == 1
        assert ov["execution_eligible_count"] == 1  # only ACTIVE
        assert ov["blocked_count"] == 3
        assert "evaluation_pipeline" in ov
        assert ov["evaluation_pipeline"]["BACKTEST_RUN"] == 1


class TestCommandCenterFleet:
    def test_filter_by_execution(self, api):
        blocked = api.fleet(execution_filter="BLOCKED")
        assert blocked["count"] == 3
        for r in blocked["rows"]:
            assert r["eligibility_state"] == "BLOCKED"

    def test_filter_by_lifecycle(self, api):
        validated = api.fleet(lifecycle="VALIDATED")
        assert validated["count"] == 1
        assert validated["rows"][0]["strategy_id"] == "s-validated"


class TestCommandCenterInspector:
    def test_inspector_found(self, api):
        ins = api.inspector("s-validated")
        assert ins["available"] is True
        assert ins["current_state"] == "VALIDATED"
        assert "evidence_completeness" in ins
        assert "invariant_check" in ins

    def test_inspector_missing(self, api):
        ins = api.inspector("no-such")
        assert ins["available"] is False


class TestExecutionSafety:
    def test_active_can_trade(self, api):
        es = api.execution_safety("s-active")
        assert es["eligibility_state"] == "YES"
        assert es["can_trade"] is True

    def test_validated_blocked(self, api):
        es = api.execution_safety("s-validated")
        assert es["eligibility_state"] == "BLOCKED"
        assert es["can_trade"] is False
        assert "shadow" in es["required_gate"].lower() or "promotion" in es["required_gate"].lower()

    def test_rejected_cannot(self, api):
        es = api.execution_safety("s-rejected")
        assert es["eligibility_state"] == "BLOCKED"
        assert es["can_trade"] is False


class TestValidationPipeline:
    def test_gates_present(self, api):
        vp = api.validation_pipeline("s-validated")
        gates = {g["gate"]: g for g in vp["gates"]}
        assert "BACKTEST" in gates
        assert gates["BACKTEST"]["total_trades"] == 30
        # No OOS/Robustness provided → NOT_RUN, not fabricated PASS.
        assert gates["OOS"]["status"] == "NOT_RUN"
        assert gates["ROBUSTNESS"]["status"] == "NOT_RUN"


class TestRegistryScaleNoCap:
    """BLOCKER 2: the API must reflect the FULL registry, not a 500-row cap."""

    def test_overview_reflects_full_registry(self, api):
        # Simulate a fleet larger than the former 500-row ceiling (1165 in prod).
        # Note: the `api` fixture already prepopulates 4 entries, so total = 1169.
        from tests.unit.test_command_center_api import _make_entry  # local import guard

        cls = CandidateLifecycle
        states = [
            cls.DISCOVERED,
            cls.BACKTESTING,
            cls.VALIDATING,
            cls.OOS_TESTING,
            cls.ROBUSTNESS_TESTING,
            cls.VALIDATED,
            cls.SHADOW,
            cls.ACTIVE,
            cls.REJECTED,
            cls.DEGRADED,
            cls.RETIRED,
        ]
        for i in range(1165):
            sid = f"s-scale-{i:04d}"
            api.registry.list = lambda *a, **k: list(_FakeRepo._registry_entries.values())
            api.registry.get = lambda sid, ver=None: _FakeRepo._registry_entries.get(sid)
            _FakeRepo._registry_entries[sid] = _make_entry(sid, states[i % len(states)])
        ov = api.overview()
        assert ov["available"] is True
        # 4 prepopulated by fixture + 1165 seeded = 1169 (cap must NOT truncate)
        assert ov["total_strategies"] == 1169, "overview must show the true fleet size"

    def test_fleet_default_limit_covers_full_registry(self, api):
        cls = CandidateLifecycle
        states = [cls.DISCOVERED, cls.VALIDATED, cls.ACTIVE, cls.REJECTED]
        for i in range(1165):
            sid = f"s-fleet-{i:04d}"
            _FakeRepo._registry_entries[sid] = _make_entry(sid, states[i % len(states)])
        fl = api.fleet()  # default limit is now 2000 (was 500)
        # 4 fixture entries + 1165 seeded = 1169 (cap must NOT truncate)
        assert fl["count"] == 1169, "fleet default must return the full registry"
        # explicit large limit also works
        fl2 = api.fleet(limit=2000)
        assert fl2["count"] == 1169


class TestTimeMachineReplay:
    def test_frame_at_reconstructs_historical_state(self, api):
        from datetime import UTC, datetime, timedelta

        from nexus_scalp.research.time_machine import TimeMachine

        # Seed a strategy with a two-step lineage.
        past = datetime.now(UTC) - timedelta(days=10)
        mid = datetime.now(UTC) - timedelta(days=5)
        sid = "s-tm"
        entry = _make_entry(
            sid,
            CandidateLifecycle.VALIDATED,
            validation_lineage=[
                f"{past.isoformat()}:DISCOVERED:seed",
                f"{mid.isoformat()}:VALIDATED:promotion",
            ],
        )
        _FakeRepo._registry_entries[sid] = entry
        tm = TimeMachine(_FakeRepo())
        # At 'past' the strategy should be DISCOVERED, not yet VALIDATED.
        frame = tm.frame_at([entry], past + timedelta(hours=1))
        assert frame["available"] is True
        assert frame["node_count"] == 1
        assert frame["nodes"][0]["zone"] == "DISCOVERED"
        # After 'mid' it should be VALIDATED.
        frame2 = tm.frame_at([entry], mid + timedelta(hours=1))
        assert frame2["nodes"][0]["zone"] == "VALIDATED"
        # Frame returns console + selected for frontend integration.
        assert "console" in frame2
        assert "selected" in frame2
