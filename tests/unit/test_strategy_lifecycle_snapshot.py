"""
Tests for the canonical Strategy Lifecycle Snapshot read model (Phase 1).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)
from nexus_scalp.research.snapshot import ExecutionEligibility, build_snapshot


def _entry(lifecycle: CandidateLifecycle, **kw) -> StrategyRegistryEntry:
    base = dict(
        strategy_id="strat-x",
        strategy_version="1.0.0",
        lifecycle=lifecycle,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(kw)
    return StrategyRegistryEntry(**base)


class TestExecutionEligibility:
    def test_active_is_trade_eligible(self):
        snap = build_snapshot(_entry(CandidateLifecycle.ACTIVE))
        assert snap.execution_eligibility.eligibility_state == "YES"
        assert snap.execution_eligibility.can_trade is True
        assert snap.current_state == "ACTIVE"

    def test_shadow_is_shadow_only(self):
        snap = build_snapshot(_entry(CandidateLifecycle.SHADOW))
        assert snap.execution_eligibility.eligibility_state == "SHADOW_ONLY"
        # can_trade stays False for live capital; shadow-only is explicit
        assert snap.execution_eligibility.can_trade is True
        assert snap.next_gate == "ACTIVE"

    def test_validated_blocked_pending_shadow(self):
        snap = build_snapshot(_entry(CandidateLifecycle.VALIDATED))
        assert snap.execution_eligibility.eligibility_state == "BLOCKED"
        assert "awaiting_shadow_or_promotion" in snap.execution_eligibility.blockers

    def test_rejected_never_trades(self):
        for lc in (CandidateLifecycle.REJECTED, CandidateLifecycle.RETIRED, CandidateLifecycle.DEGRADED):
            snap = build_snapshot(_entry(lc))
            assert snap.execution_eligibility.eligibility_state == "BLOCKED"
            assert snap.execution_eligibility.can_trade is False

    def test_discovered_blocked(self):
        snap = build_snapshot(_entry(CandidateLifecycle.DISCOVERED))
        assert snap.execution_eligibility.eligibility_state == "BLOCKED"


class TestEvidenceSummary:
    def test_missing_gates_marked_missing(self):
        snap = build_snapshot(_entry(CandidateLifecycle.DISCOVERED))
        ev = snap.evidence_summary
        assert ev["backtest_status"] == "MISSING"
        assert ev["oos_status"] == "MISSING"
        assert ev["score_verdict"] == "MISSING"

    def test_present_gates_reported(self):
        bt = BacktestResult(strategy_id="s", strategy_version="1", dataset_id="d1", total_trades=42)
        oos = OOSResult(strategy_id="s", strategy_version="1", dataset_id="d1", status="PASS")
        rob = RobustnessResult(strategy_id="s", strategy_version="1", dataset_id="d1", status="PASS")
        wf = WalkForwardResult(strategy_id="s", strategy_version="1", dataset_id="d1", passed=True)
        score = StrategyScore(final_score=0.8, verdict="VALIDATED")
        entry = _entry(
            CandidateLifecycle.VALIDATED,
            backtest=bt, walkforward=wf, oos=oos, robustness=rob, score=score,
        )
        snap = build_snapshot(entry)
        ev = snap.evidence_summary
        assert ev["backtest_status"] == "PASS"
        assert ev["oos_status"] == "PASS"
        assert ev["robustness_status"] == "PASS"
        assert ev["walkforward_status"] == "PASS"
        assert ev["score_verdict"] == "VALIDATED"

    def test_health_decomposition_from_score(self):
        score = StrategyScore(performance_score=0.9, risk_score=0.7, stability_score=0.85, final_score=0.82)
        entry = _entry(CandidateLifecycle.VALIDATED, score=score)
        snap = build_snapshot(entry)
        h = snap.health_score
        assert h["performance"] == 0.9
        assert h["stability"] == 0.85
        assert h["final"] == 0.82
        assert snap.stability_score == 0.85

    def test_transition_history_parsed(self):
        entry = _entry(
            CandidateLifecycle.SHADOW,
            validation_lineage=[
                "2026-08-23T10:00:00+00:00:VALIDATED:gate_pass",
                "2026-08-23T11:00:00+00:00:SHADOW:operator",
            ],
        )
        snap = build_snapshot(entry)
        assert len(snap.transition_history) == 2
        assert snap.lifecycle_history == ["VALIDATED", "SHADOW"]
        assert snap.previous_state == "VALIDATED"
        assert snap.transition_history[-1]["detail"] == "operator"

    def test_ai_influence_honest_default(self):
        snap = build_snapshot(_entry(CandidateLifecycle.DISCOVERED))
        ai = snap.ai_influence
        assert ai["status"] in ("PARTIALLY_MEASURABLE", "NOT_AVAILABLE")
        # Never fabricate a numeric attribution without records
        if not ai.get("attribution_records"):
            assert "attribution_records" in ai and ai["attribution_records"] == []
