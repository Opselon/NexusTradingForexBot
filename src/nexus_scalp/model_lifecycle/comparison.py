"""
Champion vs Challenger Comparison
=================================
PHASE 10 (spec 17 / 18 / 19 / 38).

A Challenger must NOT win on one metric. The comparison is multi-dimensional:

    PREDICTION QUALITY   class-specific behavior, calibration
    TRADING QUALITY      expectancy, R distribution, drawdown, profit factor,
                         downside tail
    STRATEGY QUALITY     performance across validated strategy contexts
    ROBUSTNESS           spread/slippage/execution friction, regime variation
    STABILITY            fold-to-fold and temporal stability

A candidate is eligible only when it demonstrates improvement WITHOUT critical
degradation in risk, stability, OOS or robustness:
    expectancy +       drawdown NOT worse        tail NOT worse
    OOS not negative   robustness not fragile    stability not worse
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.model_lifecycle.models import ChampionChallengerComparison
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.comparison")

#: Hard ceilings relative to the Champion.
MAX_DRAWDOWN_WORSE_R: float = 3.0  # challenger may be at most 3R worse
MAX_TAIL_WORSE_ABS: float = 1.0
#: Minimum improvement in expectancy R to count as "better".
MIN_EXPECTANCY_IMPROVEMENT_R: float = 0.05


class ChampionChallengerComparator:
    """Computes the structured comparison + promotion eligibility."""

    def compare(
        self,
        champion: dict[str, Any],
        challenger: dict[str, Any],
        run_id: str,
    ) -> ChampionChallengerComparison:
        """Returns the comparison with per-metric deltas and eligibility."""
        exp_c = float(champion.get("expectancy_r", 0.0))
        exp_t = float(challenger.get("expectancy_r", 0.0))
        dd_c = float(champion.get("max_drawdown_r", 0.0))
        dd_t = float(challenger.get("max_drawdown_r", 0.0))
        oos_c = float(champion.get("oos_expectancy_r", 0.0))
        oos_t = float(challenger.get("oos_expectancy_r", 0.0))
        tail_c = float(champion.get("tail_loss_count", 0.0))
        tail_t = float(challenger.get("tail_loss_count", 0.0))
        rob_c = str(champion.get("robustness_status", "PASS"))
        rob_t = str(challenger.get("robustness_status", "PASS"))
        stab_c = float(champion.get("stability", 1.0))
        stab_t = float(challenger.get("stability", 1.0))

        reasons: list[str] = []
        eligible = True

        # Expectancy: challenger should be no worse, ideally better.
        exp_delta = exp_t - exp_c
        if exp_t <= 0.0:
            eligible = False
            reasons.append(f"challenger expectancy {exp_t:.3f}R is non-positive")
        elif exp_delta < -MIN_EXPECTANCY_IMPROVEMENT_R:
            eligible = False
            reasons.append(f"challenger expectancy {exp_t:.3f}R worse than champion {exp_c:.3f}R")

        # Drawdown: challenger must not be critically worse.
        dd_delta = dd_t - dd_c
        if dd_delta > MAX_DRAWDOWN_WORSE_R:
            eligible = False
            reasons.append(
                f"challenger drawdown {dd_t:.2f}R is >{MAX_DRAWDOWN_WORSE_R}R worse than champion"
            )

        # OOS: must be non-negative and not critically degraded.
        if oos_t < 0.0:
            eligible = False
            reasons.append(f"challenger OOS {oos_t:.3f}R is negative")
        if oos_c > 0.0 and oos_t < oos_c - 0.5:
            eligible = False
            reasons.append(f"challenger OOS {oos_t:.3f}R degraded vs champion {oos_c:.3f}R")

        # Tail: challenger tail losses must not be materially worse in abs count.
        if tail_t > tail_c + MAX_TAIL_WORSE_ABS:
            eligible = False
            reasons.append(f"challenger tail losses {tail_t} exceed champion {tail_c}")

        # Robustness: challenger must not be fragile while champion is robust.
        if rob_t == "FAIL" and rob_c == "PASS":
            eligible = False
            reasons.append("challenger robustness FAIL while champion PASS")

        # Stability: challenger must not be materially less stable.
        if stab_t < stab_c - 0.3:
            eligible = False
            reasons.append(f"challenger stability {stab_t:.2f} degraded vs champion {stab_c:.2f}")

        # Multi-dimension improvement score (explainable, not a pass gate).
        score = _improvement_score(exp_delta, dd_delta, oos_t, stab_t)

        comparison = ChampionChallengerComparison(
            candidate_model_id=str(challenger.get("model_id", "")),
            candidate_version=str(challenger.get("model_version", "")),
            champion_model_id=str(champion.get("model_id", "")),
            champion_version=str(champion.get("model_version", "")),
            run_id=run_id,
            expectancy_r={"champion": exp_c, "challenger": exp_t, "delta": exp_delta},
            max_drawdown_r={"champion": dd_c, "challenger": dd_t, "delta": dd_delta},
            oos_expectancy_r={"champion": oos_c, "challenger": oos_t, "delta": oos_t - oos_c},
            calibration_score={
                "champion": float(champion.get("calibration_score", 0.0)),
                "challenger": float(challenger.get("calibration_score", 0.0)),
            },
            robustness_status={"champion": rob_c, "challenger": rob_t},
            stability={"champion": stab_c, "challenger": stab_t, "delta": stab_t - stab_c},
            improvement_score=score,
            eligible=eligible,
            reasons=reasons,
        )
        logger.info(
            "[MODEL] event=CHALLENGER_COMPARISON",
            candidate=comparison.candidate_model_id,
            eligible=eligible,
            expectancy_delta=round(exp_delta, 4),
            drawdown_delta=round(dd_delta, 4),
            score=round(score, 4),
        )
        return comparison


def _improvement_score(exp_delta: float, dd_delta: float, oos_t: float, stab_t: float) -> float:
    """
    Bounded [0,1] explainable improvement score.

    Weights: expectancy improvement 50%, drawdown discipline 20%, OOS level 20%,
    stability 10%. It is informational; eligibility is decided by the gates.
    """
    exp_component = max(0.0, min(1.0, 0.5 + exp_delta))
    dd_component = max(0.0, min(1.0, 1.0 - max(0.0, dd_delta) / 3.0))
    oos_component = max(0.0, min(1.0, 0.5 + oos_t))
    stab_component = max(0.0, min(1.0, stab_t))
    return round(
        0.5 * exp_component + 0.2 * dd_component + 0.2 * oos_component + 0.1 * stab_component,
        4,
    )
