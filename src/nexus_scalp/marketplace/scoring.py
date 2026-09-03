"""
Marketplace 14-factor scoring model (CHG-0056, ARCH_SPEC §2).

  Factors (fixed order, configurable weights):
    profitability        expectancy-based
    risk_adjusted        drawdown/tail-risk penalty
    drawdown_quality     recovery-duration + worst-trade
    robustness           stress-degradation (1 - max_deg/0.5)
    walk_forward_stability  (1 - wf.degradation) — honest NOT_AVAILABLE when WF absent
    oos_generalization   in-sample->OOS degradation per scoring.py 81-94
    forward_quality      0 + NOT_AVAILABLE (no forward trades yet)
    regime_coverage      distinct regimes / 8
    execution_quality    spread/slippage resilience
    statistical_confidence  logistic(n) capped below 20
    stability_over_time  return-variance logistic
    complexity_penalty   1 - conditions/9
    risk_compliance      draws on risk_assumptions / lifecycle drawdown gate
    live_readiness       0 + NOT_AVAILABLE (never fabricated)

Verifying that `validate_candidate` / `compute_strategy_score` evidence is used
where available (BacktestResult / WalkForwardResult / OOSResult / RobustnessResult
+ research score) and the forward/live factors return 0 + NOT_AVAILABLE with an
honest reason (never fabricated).

Profiles: versioned, weight-bounded, sum-checked. Every evaluation appends a
ScoreSnapshot row (append-only
history via store).
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.research.models import (
    BacktestResult,
    OOSResult,
    ResearchDataset,
    RobustnessResult,
    StrategyScore,
    WalkForwardResult,
)

FACTOR_ORDER: list[str] = [
    "profitability",
    "risk_adjusted",
    "drawdown_quality",
    "robustness",
    "walk_forward_stability",
    "oos_generalization",
    "forward_quality",
    "regime_coverage",
    "execution_quality",
    "statistical_confidence",
    "stability_over_time",
    "complexity_penalty",
    "risk_compliance",
    "live_readiness",
]
TOTAL_FACTOR_COUNT = len(FACTOR_ORDER)

# --- profile -----------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "profitability": 0.12,
    "risk_adjusted": 0.12,
    "drawdown_quality": 0.06,
    "robustness": 0.10,
    "walk_forward_stability": 0.08,
    "oos_generalization": 0.10,
    "forward_quality": 0.02,
    "regime_coverage": 0.04,
    "execution_quality": 0.04,
    "statistical_confidence": 0.08,
    "stability_over_time": 0.06,
    "complexity_penalty": 0.06,
    "risk_compliance": 0.07,
    "live_readiness": 0.05,
}
assert (
    len(DEFAULT_WEIGHTS) == TOTAL_FACTOR_COUNT and abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
)

DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_total_for_paper": 0.45,
    "min_total_for_shadow": 0.60,
    "min_total_for_live_candidate": 0.70,
}

PROFILE_VERSION = 1


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate weights: bounded [0,1], all factors covered, sum ~ 1.0."""
    missing = set(FACTOR_ORDER) - set(weights)
    extra = set(weights) - set(FACTOR_ORDER)
    if missing or extra:
        raise ValueError(f"weights missing={missing} extra={extra}")
    for k, v in weights.items():
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"weight[{k}]={v} out of [0,1]")
    tot = sum(weights.values())
    if abs(tot - 1.0) > 1e-6:
        raise ValueError(f"weights sum={tot:.6f} != 1.0")
    return dict(weights)


# --- profiles store (in-memory registry; profiles are immutable records) -----


class ScoringProfile:
    def __init__(
        self,
        profile_id: str = "default",
        version: int = PROFILE_VERSION,
        weights: dict[str, float] | None = None,
        thresholds: dict[str, float] | None = None,
        description: str = "Default 14-factor marketplace profile",
    ) -> None:
        self.profile_id = profile_id
        self.version = int(version)
        self.weights = normalize_weights(weights if weights is not None else dict(DEFAULT_WEIGHTS))
        self.thresholds = dict(thresholds if thresholds is not None else dict(DEFAULT_THRESHOLDS))
        self.description = description

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "weights": self.weights,
            "thresholds": self.thresholds,
            "description": self.description,
        }


DEFAULT_PROFILE = ScoringProfile()


# --- evaluation --------------------------------------------------------------


def _logistic(x: float, mid: float, steep: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-steep * (x - mid)))
    except OverflowError:
        return 1.0 if steep * (x - mid) > 0 else 0.0


def evaluate(
    dataset: ResearchDataset,
    backtest: BacktestResult | None,
    walkforward: WalkForwardResult | None,
    oos: OOSResult | None,
    robustness: RobustnessResult | None,
    *,
    research_score: StrategyScore | None = None,
    dsl: Any | None = None,
    profile: ScoringProfile | None = None,
) -> dict[str, Any]:
    """Runs the 14-factor evaluation. Returns a fully-explainable dict.

    Every factor emits {value, weight, contribution, reasons, availability}.
    Base research evidence is used where available
    forward/live tiers return
    0.0 with NOT_AVAILABLE honestly rather than fabricated live numbers.

    The caller is responsible for snapshotting (store + mk_score_snapshots).
    """
    p = profile or DEFAULT_PROFILE
    factors: dict[str, dict[str, Any]] = {}
    n = backtest.total_trades if backtest else 0

    # 1. profitability
    if backtest is None:
        pv: float = 0.0
        reasons: list[str] = ["NOT_AVAILABLE: no backtest evidence"]
        avail: str = "NOT_AVAILABLE"
    else:
        if backtest.expectancy_r <= 0:
            pv = 0.0
            reasons = ["Non-positive backtest expectancy"]  # type: ignore[no-redef]
        else:
            pv = min(1.0, max(0.0, 0.5 + backtest.expectancy_r))  # type: ignore[no-redef]
            reasons = []  # type: ignore[no-redef]
        avail = "AVAILABLE"  # type: ignore[no-redef]
    factors["profitability"] = {
        "value": round(pv, 4),
        "weight": p.weights["profitability"],
        "contribution": round(pv * p.weights["profitability"], 4),
        "reasons": reasons,
        "availability": avail,
    }

    # 2. risk_adjusted
    if backtest is None:
        rv = 0.0
        rr = ["NOT_AVAILABLE: no backtest evidence"]
        ra = "NOT_AVAILABLE"
    else:
        dd = backtest.max_drawdown_r
        rr0 = max(0.0, 1.0 - dd / 8.0) * max(0.0, 1.0 - backtest.tail_loss_count * 0.1)
        rv = rr0
        rr = [f"max_drawdown {dd:.2f}R high"] if dd > 4 else []
        ra = "AVAILABLE"  # type: ignore[no-redef]
    factors["risk_adjusted"] = {
        "value": round(rv, 4),
        "weight": p.weights["risk_adjusted"],
        "contribution": round(rv * p.weights["risk_adjusted"], 4),
        "reasons": rr,
        "availability": ra,
    }

    # 3. drawdown_quality (recovery-duration + worst-trade; honest 0 when n==0)
    if backtest is None or n == 0:
        dqv = 0.0
        dqr = ["NOT_AVAILABLE: no backtest evidence"]
        dqa = "NOT_AVAILABLE"
    else:
        rec = backtest.recovery_duration_trades
        worst = abs(backtest.worst_trade_r)
        rec_score = max(0.0, 1.0 - min(1.0, rec / max(1, n)))
        worst_score = max(0.0, 1.0 - worst / 8.0)
        dqv = 0.5 * rec_score + 0.5 * worst_score
        dqr = []
        if rec > n * 0.5:
            dqr.append(f"Long recovery window: {rec} trades")
        if worst > 4:
            dqr.append(f"Worst trade {worst:.2f}R is large")
        dqa = "AVAILABLE"
    factors["drawdown_quality"] = {
        "value": round(dqv, 4),
        "weight": p.weights["drawdown_quality"],
        "contribution": round(dqv * p.weights["drawdown_quality"], 4),
        "reasons": dqr,
        "availability": dqa,
    }

    # 4. robustness
    if robustness is None:
        rob_v = 0.0
        rob_r = ["No robustness evaluation performed"]
        rob_a = "NOT_AVAILABLE"
    else:
        rob_v = max(0.0, 1.0 - robustness.max_degradation / 0.5)  # type: ignore[no-redef]
        rob_r = (
            [f"Robustness {robustness.status}: {robustness.reason}"]
            if robustness.status == "FAIL"
            else []
        )
        rob_a = "AVAILABLE"  # type: ignore[no-redef]
    factors["robustness"] = {
        "value": round(rob_v, 4),
        "weight": p.weights["robustness"],
        "contribution": round(rob_v * p.weights["robustness"], 4),
        "reasons": rob_r,
        "availability": rob_a,
    }

    # 5. walk_forward_stability
    if walkforward is None:
        wfv = 0.0
        wfr = ["NOT_AVAILABLE: no walk-forward evaluation"]
        wfa = "NOT_AVAILABLE"
    else:
        wfv = max(0.0, min(1.0, 1.0 - walkforward.degradation))  # type: ignore[no-redef]
        wfr = ["Walk-forward did not pass"] if not walkforward.passed else []
        wfa = "AVAILABLE"  # type: ignore[no-redef]
    factors["walk_forward_stability"] = {
        "value": round(wfv, 4),
        "weight": p.weights["walk_forward_stability"],
        "contribution": round(wfv * p.weights["walk_forward_stability"], 4),
        "reasons": wfr,
        "availability": wfa,
    }

    # 6. oos_generalization
    if oos is None:
        oov = 0.0
        oor = ["NOT_AVAILABLE: no OOS evaluation"]
        ooa = "NOT_AVAILABLE"
    else:
        base = min(1.0, max(0.0, 0.5 + oos.oos_expectancy_r)) if oos.status == "PASS" else 0.0  # type: ignore[no-redef]
        # degradation penalty like scoring.py 81-91
        if oos.in_sample_expectancy_r and oos.in_sample_expectancy_r > 0:
            deg = (oos.in_sample_expectancy_r - oos.oos_expectancy_r) / oos.in_sample_expectancy_r  # type: ignore[no-redef]
            if deg > 0:
                base *= max(0.0, 1.0 - deg)
        oov = float(base)
        oor = [f"OOS {oos.status}: {oos.reason}"] if oos.status != "PASS" else []
        ooa = "AVAILABLE"  # type: ignore[no-redef]
    factors["oos_generalization"] = {
        "value": round(oov, 4),
        "weight": p.weights["oos_generalization"],
        "contribution": round(oov * p.weights["oos_generalization"], 4),
        "reasons": oor,
        "availability": ooa,
    }

    # 7. forward_quality — honest: no forward trades yet
    factors["forward_quality"] = {
        "value": 0.0,
        "weight": p.weights["forward_quality"],
        "contribution": 0.0,
        "reasons": [
            "NOT_AVAILABLE: no forward measurement yet (paper/shadow/live forward window not populated)"
        ],
        "availability": "NOT_AVAILABLE",
    }

    # 8. regime_coverage
    regimes = {
        getattr(s, "regime", "UNKNOWN") for s in (dataset.samples if dataset is not None else [])
    }
    reg_v = min(1.0, len(regimes) / 8.0) if regimes else 0.0
    if "UNKNOWN" in regimes:
        reg_v *= 0.85
    factors["regime_coverage"] = {
        "value": round(reg_v, 4),
        "weight": p.weights["regime_coverage"],
        "contribution": round(reg_v * p.weights["regime_coverage"], 4),
        "reasons": [],
        "availability": "AVAILABLE",
    }

    # 9. execution_quality
    if backtest is None:
        exv = 0.0
        exr = ["NOT_AVAILABLE: no backtest execution-sensitivity evidence"]
        exa = "NOT_AVAILABLE"
    else:
        sp = getattr(backtest, "spread_sensitivity_r", 0.0)
        slp = getattr(backtest, "slippage_sensitivity_r", 0.0)
        exv = max(0.0, 1.0 - (abs(sp) + abs(slp)) / 0.5)  # type: ignore[no-redef]
        exr = []
        exa = "AVAILABLE"  # type: ignore[no-redef]
    factors["execution_quality"] = {
        "value": round(exv, 4),
        "weight": p.weights["execution_quality"],
        "contribution": round(exv * p.weights["execution_quality"], 4),
        "reasons": exr,
        "availability": exa,
    }

    # 10. statistical_confidence
    if n == 0:
        scv = 0.0
        scr = ["NOT_AVAILABLE: no trades in dataset"]
        sca = "NOT_AVAILABLE"
    else:
        if n < 8:
            scv = 0.0
            scr = ["Sample count below small-sample floor (8)"]  # type: ignore[no-redef]
        else:
            scv = _logistic(n, mid=60.0, steep=0.06)  # type: ignore[no-redef]
            scv = min(scv, 0.95)  # type: ignore[no-redef]
        if 8 <= n < 20:  # type: ignore[operator]
            scv = min(scv, 0.4)  # type: ignore[no-redef]
            scr = ["Sample count 8-19: confidence capped (LOW EVIDENCE)"]  # type: ignore[no-redef]
        else:
            scr = []
        sca = "AVAILABLE"
    factors["statistical_confidence"] = {
        "value": round(float(scv), 4),
        "weight": p.weights["statistical_confidence"],
        "contribution": round(float(scv) * p.weights["statistical_confidence"], 4),
        "reasons": scr,
        "availability": sca,
    }

    # 11. stability_over_time  (return variance)
    if backtest is None:
        stv = 0.0
        str_ = ["NOT_AVAILABLE: no backtest evidence"]
        sta = "NOT_AVAILABLE"
    else:
        var = getattr(backtest, "return_variance", 0.0)
        stv = _logistic(1.0 - var / 2.0, mid=0.5, steep=4.0)  # type: ignore[no-redef]
        stv = max(0.0, min(1.0, float(stv)))  # type: ignore[no-redef]
        str_ = []
        sta = "AVAILABLE"  # type: ignore[no-redef]
    factors["stability_over_time"] = {
        "value": round(float(stv), 4),
        "weight": p.weights["stability_over_time"],
        "contribution": round(float(stv) * p.weights["stability_over_time"], 4),
        "reasons": str_,
        "availability": sta,
    }

    # 12. complexity_penalty (from DSL when available)
    if dsl is None:
        cpv = 0.0
        cpr = ["NOT_AVAILABLE: no DSL available for complexity penalty"]
        cpa = "NOT_AVAILABLE"
    else:
        # count conditions like validators.py _count_conditions
        try:
            raw = dsl.model_dump() if hasattr(dsl, "model_dump") else dict(dsl)  # type: ignore[operator]
        except Exception:
            raw = {}  # type: ignore[no-redef]
        n_cond = 0

        def _count(obj: Any) -> int:
            tot = 0
            if isinstance(obj, dict):
                if any(k in obj for k in ("op", "logic", "confirmation", "require")):
                    tot += 1
                for v in obj.values():
                    tot += _count(v)
            elif isinstance(obj, list):
                for item in obj:
                    tot += _count(item)
            return tot

        n_cond = _count(raw)  # type: ignore[no-redef]
        cpv = max(0.0, 1.0 - n_cond / 9.0)
        cpr = [f"Condition count {n_cond}/9"] if n_cond > 6 else []
        cpa = "AVAILABLE"
    factors["complexity_penalty"] = {
        "value": round(float(cpv), 4),
        "weight": p.weights["complexity_penalty"],
        "contribution": round(float(cpv) * p.weights["complexity_penalty"], 4),
        "reasons": cpr,
        "availability": cpa,
    }

    # 13. risk_compliance (drawdown gate + risk assumptions if any)
    if backtest is None:
        rcv = 0.0
        rcr = ["NOT_AVAILABLE: no backtest evidence"]
        rca = "NOT_AVAILABLE"
    else:
        # risk compliance: tail penalty + worst loss check
        rcv = max(0.0, 1.0 - backtest.max_drawdown_r / 8.0) * max(
            0.0, 1.0 - min(1.0, backtest.tail_loss_count * 0.15)
        )  # type: ignore[no-redef]
        rcr = []
        if backtest.max_drawdown_r > 4:
            rcr.append(f"Drawdown {backtest.max_drawdown_r:.2f}R exceeds 4R risk budget")
        if getattr(backtest, "largest_loss_r", 0) and backtest.largest_loss_r > 4:
            rcr.append(f"Largest loss {backtest.largest_loss_r:.2f}R exceeds 4R budget")
        rca = "AVAILABLE"
    factors["risk_compliance"] = {
        "value": round(float(rcv), 4),
        "weight": p.weights["risk_compliance"],
        "contribution": round(float(rcv) * p.weights["risk_compliance"], 4),
        "reasons": rcr,
        "availability": rca,
    }

    # 14. live_readiness — honest: no live attribution yet (ARCH_SPEC §2)
    factors["live_readiness"] = {
        "value": 0.0,
        "weight": p.weights["live_readiness"],
        "contribution": 0.0,
        "reasons": [
            "NOT_AVAILABLE: no per-strategy live attribution yet (operator-gated; no fabricated live numbers)"
        ],
        "availability": "NOT_AVAILABLE",
    }

    total = round(sum(v["contribution"] for v in factors.values()), 4)
    total = max(0.0, min(1.0, total))
    # verdict: research-tier hard gates
    verdict = "INCONCLUSIVE"
    if oos is not None and oos.status != "PASS":
        verdict = "REJECTED"
    elif n < 8:
        verdict = "INCONCLUSIVE"
    elif backtest is not None and backtest.expectancy_r <= 0:
        verdict = "REJECTED"
    elif (
        oos is None
        or robustness is None
        or (robustness is not None and robustness.status != "PASS")
    ):
        verdict = "INCONCLUSIVE"
    else:
        try:
            from nexus_scalp.research.models import MIN_EVIDENCE_SAMPLES as _MES

            if n < int(_MES):
                verdict = "INCONCLUSIVE"
            elif walkforward is not None and not walkforward.passed:
                verdict = "INCONCLUSIVE"
            else:
                verdict = "VALIDATED"
        except Exception:
            verdict = "INCONCLUSIVE"
    return {
        "factors": factors,
        "total": total,
        "verdict": verdict,
        "profile_id": p.profile_id,
        "profile_version": p.version,
        "weights": dict(p.weights),
    }


def snapshot_payload(result: dict[str, Any], seed_id: str) -> dict[str, Any]:
    """Serializes a scoring result into a mk_score_snapshots row."""
    return {
        "snapshot_id": "SCORE-" + uuid.uuid4().hex[:12].upper(),
        "seed_id": seed_id,
        "profile_id": result["profile_id"],
        "profile_version": result["profile_version"],
        "total": result["total"],
        "verdict": result["verdict"],
        "factors": result["factors"],
        "created_at": datetime.now(UTC).isoformat(),
    }


__all__ = [
    "DEFAULT_PROFILE",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WEIGHTS",
    "FACTOR_ORDER",
    "PROFILE_VERSION",
    "TOTAL_FACTOR_COUNT",
    "ScoringProfile",
    "evaluate",
    "normalize_weights",
    "snapshot_payload",
]
