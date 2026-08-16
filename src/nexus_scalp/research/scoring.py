"""
Strategy Validation Score
=========================
PHASE 09B (spec 17 / 18 / 38).

Decomposable multi-dimensional score. NOT a single win rate. Each dimension is
bounded [0,1], and the final score is a weighted combination. Small-sample
protection (spec 18) is mandatory: 8 trades at +1.2R stays LOW EVIDENCE, and
never HIGH CONFIDENCE.

The score is fully explainable: every dimension and every reason is exposed.
"""

from __future__ import annotations

import math

from nexus_scalp.research.models import (
    BacktestResult,
    OOSResult,
    ResearchDataset,
    RobustnessResult,
    StrategyScore,
    WalkForwardResult,
)

#: Sample sizes beyond which confidence saturates (logistic midpoints).
_SAMPLE_MID = 60.0
_SAMPLE_STEEPNESS = 0.06


def _logistic(x: float, mid: float, steep: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-steep * (x - mid)))
    except OverflowError:
        return 1.0 if steep * (x - mid) > 0 else 0.0


def compute_strategy_score(
    dataset: ResearchDataset,
    backtest: BacktestResult,
    walkforward: WalkForwardResult | None,
    oos: OOSResult | None,
    robustness: RobustnessResult | None,
) -> StrategyScore:
    """
    Computes the explainable strategy validation score and verdict.

    Verdict rules (hard gates):
      - OOS FAIL  -> REJECTED (regardless of in-sample/win rate).
      - Robustness FAIL -> keeps score low; candidate not validated.
      - Walk-forward fail -> not validated.
      - Small sample -> verdict NEVER validated unless sample floor met.
    """
    n = backtest.total_trades
    reasons: list[str] = []

    # --- PERFORMANCE ---------------------------------------------------------
    # Expectancy R normalized: 0.5R maps to ~0.7, capped at 1.0.
    perf = min(1.0, max(0.0, 0.5 + backtest.expectancy_r))
    if backtest.expectancy_r <= 0.0:
        perf = 0.0
        reasons.append("Non-positive backtest expectancy")

    # --- RISK -----------------------------------------------------------------
    # Drawdown <= 2R is ideal; >= 8R maps to ~0. Penalize tail losses.
    dd = backtest.max_drawdown_r
    risk = max(0.0, 1.0 - (dd / 8.0))
    tail = backtest.tail_loss_count
    risk *= max(0.0, 1.0 - tail * 0.1)
    if dd > 4.0:
        reasons.append(f"Max drawdown {dd:.2f}R is high")

    # --- STABILITY ------------------------------------------------------------
    var = backtest.return_variance
    stability = _logistic(1.0 - (var / 2.0), mid=0.5, steep=4.0)
    stability = max(0.0, min(1.0, stability))

    # --- OOS ------------------------------------------------------------------
    if oos is not None:
        oos_score = 0.0
        if oos.status == "PASS":
            oos_score = min(1.0, max(0.0, 0.5 + oos.oos_expectancy_r))
        else:
            reasons.append(f"OOS gate {oos.status}: {oos.reason}")
        # Degradation penalty: if OOS < in-sample, reduce.
        if oos.in_sample_expectancy_r and oos.in_sample_expectancy_r > 0:
            deg = (oos.in_sample_expectancy_r - oos.oos_expectancy_r) / oos.in_sample_expectancy_r
            if deg > 0:
                oos_score *= max(0.0, 1.0 - deg)
    else:
        oos_score = 0.0
        reasons.append("No OOS evaluation performed")

    # --- ROBUSTNESS -----------------------------------------------------------
    if robustness is not None:
        rob = max(0.0, 1.0 - robustness.max_degradation / 0.5)
        if robustness.status == "FAIL":
            reasons.append(f"Robustness {robustness.status}: {robustness.reason}")
    else:
        rob = 0.0
        reasons.append("No robustness evaluation performed")

    # --- SAMPLE CONFIDENCE (small-sample protection) ---------------------------
    # Logistic in sample count with a hard floor below SMALL_SAMPLE_FLOOR.
    sample_conf = 0.0
    if n < 8:
        sample_conf = 0.0
        reasons.append("Sample count below small-sample floor (8)")
    else:
        sample_conf = _logistic(n, mid=_SAMPLE_MID, steep=_SAMPLE_STEEPNESS)
        sample_conf = min(sample_conf, 0.95)
    # Cap confidence heavily for small-but-not-tiny samples.
    if n < 20:
        sample_conf = min(sample_conf, 0.4)
        reasons.append("Sample count 8-19: confidence capped (LOW EVIDENCE)")

    # --- REGIME COVERAGE -------------------------------------------------------
    regimes = {s.regime for s in dataset.samples}
    # Normalize coverage by 8 common regime buckets.
    regime_cov = min(1.0, len(regimes) / 8.0)
    if "UNKNOWN" in regimes:
        regime_cov *= 0.85

    # --- RECENCY ---------------------------------------------------------------
    # Reward recent performance (last 20% of trades).
    ordered = sorted(dataset.samples, key=lambda s: s.decision_timestamp)
    recent_n = max(1, n // 5)
    recent = ordered[-recent_n:] if recent_n else []
    if recent:
        recent_exp = sum(s.realized_r for s in recent) / len(recent)
        recency = min(1.0, max(0.0, 0.5 + recent_exp))
    else:
        recency = 0.0
        reasons.append("No recent samples for recency")

    # --- EXECUTION RESILIENCE ---------------------------------------------------
    # Degradation of expectancy under spread/slippage stress.
    sp = backtest.spread_sensitivity_r
    sl = backtest.slippage_sensitivity_r
    exec_res = max(0.0, 1.0 - (abs(sp) + abs(sl)) / 0.5)

    # --- DEGRADATION ------------------------------------------------------------
    if walkforward is not None:
        degr = max(0.0, 1.0 - walkforward.degradation)
    else:
        degr = 0.0
        reasons.append("No walk-forward evaluation performed")

    # --- FINAL SCORE ------------------------------------------------------------
    weights = {
        "performance": 0.20,
        "risk": 0.15,
        "stability": 0.10,
        "oos": 0.20,
        "robustness": 0.15,
        "sample": 0.08,
        "regime": 0.04,
        "recency": 0.04,
        "execution": 0.02,
        "degradation": 0.02,
    }
    final = (
        perf * weights["performance"]
        + risk * weights["risk"]
        + stability * weights["stability"]
        + oos_score * weights["oos"]
        + rob * weights["robustness"]
        + sample_conf * weights["sample"]
        + regime_cov * weights["regime"]
        + recency * weights["recency"]
        + exec_res * weights["execution"]
        + degr * weights["degradation"]
    )
    final = round(max(0.0, min(1.0, final)), 4)

    # --- VERDICT (hard gates) ----------------------------------------------------
    verdict = "INCONCLUSIVE"
    if oos is not None and oos.status != "PASS":
        verdict = "REJECTED"
        reasons.append("OOS_FAILURE")
    elif n < 8:
        verdict = "INCONCLUSIVE"
    elif (
        perf <= 0.0
        or oos is None
        or oos.status != "PASS"
        or robustness is None
        or robustness.status != "PASS"
    ):
        verdict = "REJECTED" if (oos is not None and oos.status != "PASS") else "INCONCLUSIVE"
    elif walkforward is not None and not walkforward.passed:
        verdict = "INCONCLUSIVE"
        reasons.append("Walk-forward did not pass")
    else:
        verdict = "VALIDATED"
        reasons.append("All evidence gates passed")

    return StrategyScore(
        performance_score=round(perf, 4),
        risk_score=round(risk, 4),
        stability_score=round(stability, 4),
        oos_score=round(oos_score, 4),
        robustness_score=round(rob, 4),
        sample_confidence=round(sample_conf, 4),
        regime_coverage=round(regime_cov, 4),
        recency_score=round(recency, 4),
        execution_resilience=round(exec_res, 4),
        degradation_score=round(degr, 4),
        final_score=final,
        verdict=verdict,
        reasons=reasons,
    )
