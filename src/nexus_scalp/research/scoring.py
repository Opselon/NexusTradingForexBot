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
    MIN_EVIDENCE_SAMPLES,
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


def _oos_evidence_is_decisive(oos: OOSResult | None) -> bool:
    """True when the OOS window is decisive evidence for a positive edge.

    Decisive = the OOS gate attached a bootstrap significance whose 95% CI
    lower bound is > 0 with at least MIN_OOS_SIGNIFICANCE_SAMPLES trades.
    Legacy OOSResult instances without the field are accepted unchanged so
    persisted-history replays and direct constructors keep their contract.
    """
    if oos is None:
        return False
    sig = oos.oos_significance
    if not sig:
        return True  # legacy/no-significance producers: unchanged behavior
    return bool(sig.get("decisive"))


#: Minimum DSR confidence (Bailey-de Prado) for a mined candidate to count as
#: real-after-search; the conventional bar is 0.95.
DSR_CONFIDENCE_FLOOR: float = 0.95


def _selection_bias_control_passed(oos: OOSResult | None) -> bool:
    """True when the multiplicity controls (DSR / SPA) do not reject.

    DSR: when the gate attached a deflated Sharpe (i.e., n_trials > 1 was
    declared), the DSR confidence must reach DSR_CONFIDENCE_FLOOR.
    SPA: when a family Reality Check p-value is attached, it must not flag
    the best family as luck (survivor=False).
    Absent fields -> legacy behavior (True): only mined-multiplicity runs
    are deflated, direct single-strategy runs are untouched.
    """
    if oos is None:
        return True
    dsr = oos.deflated_sharpe
    if dsr:
        # n_trials <= 1 means no multiplicity declared -> no deflation.
        if int(dsr.get("n_trials", 1) or 1) > 1:
            if float(dsr.get("dsr", 0.0) or 0.0) < DSR_CONFIDENCE_FLOOR:
                return False
    spa = oos.spa
    if spa and spa.get("n_families", 0):
        if spa.get("survivor") is False:
            return False
    return True


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
        degr = max(0.0, min(1.0, 1.0 - walkforward.degradation))
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
    elif n < MIN_EVIDENCE_SAMPLES:
        # TASK-4 hard small-sample gate: never claim VALIDATED below the
        # evidence floor regardless of other gates.
        verdict = "INCONCLUSIVE"
        reasons.append(f"Sample count {n} below evidence floor {MIN_EVIDENCE_SAMPLES}")
    elif walkforward is not None and not walkforward.passed:
        verdict = "INCONCLUSIVE"
        reasons.append("Walk-forward did not pass")
    elif not _oos_evidence_is_decisive(oos):
        # EDGE HARDENING (2026-09-09): a VALIDATED verdict may no longer rest
        # on a point estimate. When the OOS gate produced a bootstrap
        # significance (it always does on the real gate path), the 95% CI for
        # mean OOS R must sit ENTIRELY above breakeven. Breakeven-or-noise OOS
        # (CI straddling 0, or too few OOS trades) is evidence-building, not
        # tradable edge. Legacy producers without the field keep old behavior.
        verdict = "INCONCLUSIVE"
        sig = (oos.oos_significance or {}) if oos is not None else {}
        if sig.get("decisive") is False and int(sig.get("n", 0) or 0) > 0:
            reasons.append(
                "OOS evidence not decisive: bootstrap 95% CI "
                f"[{sig.get('ci_low', 0.0):.3f}, {sig.get('ci_high', 0.0):.3f}] "
                f"on n={sig.get('n', 0)} straddles 0 or sample floor unmet"
            )
        else:
            reasons.append("OOS significance not available (evidence-building)")
    elif not _selection_bias_control_passed(oos):
        # EDGE ROUND-2 (2026-09-09): multiplicity control — the best-of-N
        # mined candidate must survive deflation (DSR) / the family Reality
        # Check (SPA) before the verdict may claim a real edge.
        verdict = "INCONCLUSIVE"
        dsr = (oos.deflated_sharpe or {}) if oos is not None else {}
        spa = (oos.spa or {}) if oos is not None else {}
        if dsr and int(dsr.get("n_trials", 1) or 1) > 1:
            reasons.append(
                "selection-bias control failed: deflated Sharpe "
                f"{float(dsr.get('dsr', 0.0)):.3f} < {DSR_CONFIDENCE_FLOOR:.2f} "
                f"across n_trials={dsr.get('n_trials')}"
            )
        if spa and spa.get("n_families", 0):
            reasons.append(
                "selection-bias control failed: family Reality Check "
                f"p={float(spa.get('p_value', 1.0)):.3f} — best-of-"
                f"{spa.get('n_families')} families not distinguished from luck"
            )
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
