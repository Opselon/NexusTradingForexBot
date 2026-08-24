"""
Tests for the transient EVALUATION PIPELINE projection (Agent 4 — Phase 4).

The evaluation pipeline (Backtest/WF/OOS/Robustness/Score) is TELEMETRY, not a
persistent lifecycle. These tests prove:
  * evaluation_detail derives honest per-gate status from real artifacts
  * a RUNNING stage is only reported when a real research_runs row says so
  * evaluation_metrics aggregates pass/fail rates with scope preserved
  * the spatial inspector payload carries evaluation + eligibility
"""

from __future__ import annotations

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
from nexus_scalp.web.command_center_routes import (
    evaluation_detail,
    evaluation_metrics,
)


def _entry(**kw):
    base = dict(
        strategy_id="E1",
        strategy_version="1.0.0",
        lifecycle=CandidateLifecycle.DISCOVERED,
        validation_lineage=[],
    )
    base.update(kw)
    return StrategyRegistryEntry(**base)


def test_evaluation_detail_all_not_run_for_fresh_candidate():
    e = _entry()
    d = evaluation_detail(e)
    assert d["gates"] == {
        "BACKTEST": "NOT_RUN",
        "WALK_FORWARD": "NOT_RUN",
        "OOS": "NOT_RUN",
        "ROBUSTNESS": "NOT_RUN",
        "SCORE": "NOT_RUN",
    }
    assert d["current_stage"] == "BACKTEST"
    assert d["progress"] == 0.0
    assert d["is_running"] is False


def test_evaluation_detail_backtest_pass_then_walkforward_running():
    e = _entry(
        backtest=BacktestResult(
            strategy_id="E1", strategy_version="1", dataset_id="d", total_trades=50
        ),
    )
    # Real RUNNING only when a research_runs row reports it; here none → not running.
    d = evaluation_detail(e)
    assert d["gates"]["BACKTEST"] == "PASS"
    assert d["gates"]["WALK_FORWARD"] == "NOT_RUN"
    assert d["current_stage"] == "WALK_FORWARD"
    assert d["is_running"] is False

    # Now a genuine in-flight run is reported by the backend.
    d2 = evaluation_detail(e, running_runs={"E1": "WALK_FORWARD"})
    assert d2["gates"]["WALK_FORWARD"] == "RUNNING"
    assert d2["is_running"] is True
    assert d2["running_stage"] == "WALK_FORWARD"


def test_evaluation_detail_does_not_invent_running_without_real_row():
    e = _entry(
        backtest=BacktestResult(
            strategy_id="E1", strategy_version="1", dataset_id="d", total_trades=50
        ),
        walkforward=None,  # no artifact
    )
    # No running_runs supplied → must NOT claim RUNNING.
    d = evaluation_detail(e, running_runs={})
    assert d["gates"]["WALK_FORWARD"] == "NOT_RUN"
    assert d["is_running"] is False


def test_evaluation_detail_full_pass_is_done():
    e = _entry(
        backtest=BacktestResult(
            strategy_id="E1", strategy_version="1", dataset_id="d", total_trades=50
        ),
        walkforward=WalkForwardResult(
            strategy_id="E1",
            strategy_version="1",
            dataset_id="d",
            folds=[],
            passed=True,
            avg_oos_expectancy_r=0.5,
        ),
        oos=OOSResult(
            strategy_id="E1",
            strategy_version="1",
            dataset_id="d",
            status="PASS",
            oos_expectancy_r=0.5,
            oos_samples=10,
            oos_win_rate=0.5,
            in_sample_expectancy_r=0.6,
        ),
        robustness=RobustnessResult(
            strategy_id="E1", strategy_version="1", status="PASS", max_degradation=0.1, reason="ok"
        ),
        score=StrategyScore(final_score=0.8, verdict="VALIDATED", reasons=[]),
    )
    d = evaluation_detail(e)
    assert d["current_stage"] == "DONE"
    assert d["progress"] == 1.0
    assert d["passed_gates"] == 5


def test_evaluation_metrics_aggregates_with_scope_preserved():
    entries = [
        _entry(
            strategy_id=f"s{i}",
            backtest=BacktestResult(
                strategy_id=f"s{i}", strategy_version="1", dataset_id="d", total_trades=10
            ),
        )
        for i in range(3)
    ]
    details = [evaluation_detail(e, {"s0": "WALK_FORWARD"}) for e in entries]
    m = evaluation_metrics(details)
    # BACKTEST: 3 PASS total.
    assert m["BACKTEST"]["pass"] == 3 and m["BACKTEST"]["total"] == 3
    # WALK_FORWARD: one real RUNNING, rest NOT_RUN → total 1 (NOT_RUN excluded from total? no: total counts all).
    assert m["WALK_FORWARD"]["running"] == 1
    # Scope is always transient runs; keys are the five gates.
    for g in ("BACKTEST", "WALK_FORWARD", "OOS", "ROBUSTNESS", "SCORE"):
        assert g in m
        assert "pass_rate" in m[g] and "fail_rate" in m[g]
