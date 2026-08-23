"""
Tests for the Debug Intelligence Engine (Phase 6).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nexus_scalp.research.debug_intelligence import (
    compute_anomaly_score,
    compute_debug_priority,
    compute_validation_consistency,
    decompose_strategy_health,
    generate_debug_hints,
)
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    StrategyRegistryEntry,
)


def _entry(lc=CandidateLifecycle.DISCOVERED, **kw):
    base = dict(
        strategy_id="s1",
        strategy_version="1",
        lifecycle=lc,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    base.update(kw)
    return StrategyRegistryEntry(**base)


class TestAnomalyScore:
    def test_clean_strategy_low_anomaly(self):
        e = _entry(CandidateLifecycle.VALIDATED, validation_lineage=["2026-01-01T00:00:00+00:00:VALIDATED"])
        r = compute_anomaly_score(e)
        assert r["anomaly_score"] < 0.3

    def test_oscillating_strategy_high_anomaly(self):
        lineage = [
            "2026-01-01T00:00:00+00:00:DISCOVERED",
            "2026-01-02T00:00:00+00:00:REJECTED",
            "2026-01-03T00:00:00+00:00:DISCOVERED",
            "2026-01-04T00:00:00+00:00:REJECTED",
            "2026-01-05T00:00:00+00:00:DISCOVERED",
            "2026-01-06T00:00:00+00:00:REJECTED",
        ]
        e = _entry(CandidateLifecycle.REJECTED, validation_lineage=lineage)
        r = compute_anomaly_score(e)
        assert r["anomaly_score"] > 0.5

    def test_score_is_decomposable(self):
        e = _entry(CandidateLifecycle.REJECTED, validation_lineage=["x:REJECTED:y"])
        r = compute_anomaly_score(e)
        assert set(r["components"].keys()) == {"transition_frequency", "failure_density", "oscillation_count"}


class TestValidationConsistency:
    def test_all_pass_is_consistent(self):
        e = _entry(
            CandidateLifecycle.VALIDATED,
            backtest=BacktestResult(strategy_id="s", strategy_version="1", dataset_id="d", expectancy_r=1.5, total_trades=30),
            oos=OOSResult(strategy_id="s", strategy_version="1", dataset_id="d", status="PASS"),
        )
        r = compute_validation_consistency(e)
        assert r["status"] == "CONSISTENT"

    def test_backtest_good_but_oos_fail_is_inconsistent(self):
        e = _entry(
            CandidateLifecycle.REJECTED,
            backtest=BacktestResult(strategy_id="s", strategy_version="1", dataset_id="d", expectancy_r=2.0, total_trades=50),
            oos=OOSResult(strategy_id="s", strategy_version="1", dataset_id="d", status="FAIL"),
        )
        r = compute_validation_consistency(e)
        assert r["status"] == "HIGH_INCONSISTENCY"
        assert 0.0 <= r["consistency_score"] < 1.0

    def test_no_data_not_available(self):
        r = compute_validation_consistency(_entry())
        assert r["status"] == "NOT_AVAILABLE"


class TestHealthDecomposition:
    def test_components_present(self):
        h = decompose_strategy_health(_entry(CandidateLifecycle.ACTIVE))
        assert set(h.keys()) == {
            "data_quality", "validation", "robustness",
            "execution_safety", "lifecycle_stability", "evidence_completeness",
        }

    def test_rejected_low_lifecycle_stability(self):
        h = decompose_strategy_health(_entry(CandidateLifecycle.REJECTED))
        assert h["lifecycle_stability"] < 0.5


class TestDebugPriority:
    def test_live_strategy_has_higher_priority_on_failure(self):
        live = _entry(
            CandidateLifecycle.SHADOW,
            validation_lineage=["a:OOS_TESTING:b", "c:REJECTED:d"],
        )
        dead = _entry(
            CandidateLifecycle.REJECTED,
            validation_lineage=["a:OOS_TESTING:b", "c:REJECTED:d"],
        )
        p_live = compute_debug_priority(live)["debug_priority_score"]
        p_dead = compute_debug_priority(dead)["debug_priority_score"]
        assert p_live > p_dead

    def test_no_failures_low_priority(self):
        p = compute_debug_priority(_entry(CandidateLifecycle.DISCOVERED))
        assert p["debug_priority_score"] < 0.5


class TestDebugHints:
    def test_categories_strictly_separated(self):
        e = _entry(
            CandidateLifecycle.REJECTED,
            validation_lineage=["2026-01-01T00:00:00+00:00:REJECTED:oos_fail"],
        )
        hints = generate_debug_hints(e)
        cats = {h["category"] for h in hints}
        assert cats == {"FACT", "INFERENCE", "HYPOTHESIS", "RECOMMENDATION"}

    def test_fact_never_confused_with_inference(self):
        e = _entry(
            CandidateLifecycle.REJECTED,
            validation_lineage=["2026-01-01T00:00:00+00:00:REJECTED:oos_fail"],
        )
        hints = {h["category"]: h["message"] for h in generate_debug_hints(e)}
        # FACT must state a recorded count (verifiable); INFERENCE must not.
        assert "1 recorded" in hints["FACT"]
        assert "suggest" in hints["INFERENCE"].lower()

    def test_clean_strategy_no_alarmism(self):
        hints = generate_debug_hints(_entry(CandidateLifecycle.DISCOVERED))
        assert all("failure" not in h["message"].lower() or h["category"] == "FACT" for h in hints)
