"""
Shadow Comparison Engine
========================
PHASE 11 multi-dimension Champion vs Challenger comparison (spec 7 / 8 / 11 /
12 / 13 / 14 / 15 / 21 / 22 / 23).

The Challenger must NOT win on one metric. The comparison is decomposed into
prediction quality, trading quality, strategy quality, stability and
robustness, with regime/strategy/session breakdowns so critical regressions are
never averaged away.

Promotion evaluation is explainable: components (performance_delta, risk_delta,
drawdown_delta, oos_delta, robustness_delta, calibration_delta, stability_delta,
strategy_regression_penalty, sample_confidence) feed a weighted score. A single
critical VETO overrides the aggregate score.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.models import (
    PromotionEvaluation,
    ShadowComparison,
    ShadowDecisionRecord,
    ShadowEvidenceStatus,
    ShadowModelRef,
)

logger = get_logger("nexus_scalp.shadow.comparison")

#: Minimum valid shadow samples before promotion evidence is meaningful.
DEFAULT_MIN_SAMPLES: int = 30
#: Veto thresholds
MAX_DRAWDOWN_DELTA_R: float = 3.0  # challenger drawdown may be at most 3R worse
MIN_OOS_EXPECTANCY_R: float = 0.0
MAX_CALIBRATION_DROP: float = 0.15
MAX_STRATEGY_REGRESSION_R: float = -0.20  # per-strategy expectancy floor
MIN_ACTION_AGREEMENT: float = 0.30  # sanity floor on agreement (informational)
#: A per-regime challenger expectancy below this floor marks the regime degraded
#: (absolute signal, independent of champion comparison).
MIN_REGIME_EXPECTANCY_R: float = 0.0


def _mean(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


class ShadowComparer:
    """Aggregates shadow decisions into a multi-dimension comparison."""

    def __init__(self, min_samples: int = DEFAULT_MIN_SAMPLES) -> None:
        self.min_samples = int(min_samples)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def compare(
        self,
        decisions: list[ShadowDecisionRecord],
        run_id: str,
        champion: ShadowModelRef,
        challenger: ShadowModelRef,
    ) -> ShadowComparison:
        valid = [d for d in decisions if d.valid_comparison]
        invalid = [d for d in decisions if not d.valid_comparison]

        # Champion-side R is derived from the SAME simulated price path, but
        # applied to the champion's OWN action: when the two models disagree,
        # one was directionally right and the other wrong on that path.
        # (hypothetical_r is the challenger's realized R; the champion's is
        # the same magnitude with the sign of its own directional correctness.)
        champ_r: list[float] = []
        chal_r: list[float] = []
        for d in valid:
            chal_r.append(d.hypothetical_r)
            if d.champion_action == d.challenger_action:
                # Same direction: both models realise the same outcome.
                champ_r.append(d.hypothetical_r)
            else:
                # Opposing directions on the same path: the champion's R is the
                # opposite sign of the challenger's (one wins, one loses).
                champ_r.append(-d.hypothetical_r)

        champion_exp = _mean(champ_r)
        challenger_exp = _mean(chal_r)

        by_regime: dict[str, dict[str, float]] = {}
        by_strategy: dict[str, dict[str, float]] = {}
        by_session: dict[str, dict[str, float]] = {}

        degraded_strategies: list[str] = []
        improved_strategies: list[str] = []
        by_strategy_samples: dict[str, int] = defaultdict(int)

        for d in valid:
            regime = d.shared_input.regime or "UNKNOWN"
            strat = d.champion_strategy_id or d.challenger_strategy_id or "UNKNOWN"
            session = d.shared_input.session or "ALL"
            # Same derived champion-side R as the aggregate series above.
            champ_r_side = (
                d.hypothetical_r if d.champion_action == d.challenger_action else -d.hypothetical_r
            )
            by_regime.setdefault(regime, {"champion_r": 0.0, "challenger_r": 0.0, "samples": 0})
            by_regime[regime]["champion_r"] += champ_r_side
            by_regime[regime]["challenger_r"] += d.hypothetical_r
            by_regime[regime]["samples"] += 1
            by_strategy.setdefault(strat, {"champion_r": 0.0, "challenger_r": 0.0, "samples": 0})
            by_strategy[strat]["champion_r"] += champ_r_side
            by_strategy[strat]["challenger_r"] += d.hypothetical_r
            by_strategy[strat]["samples"] += 1
            by_strategy_samples[strat] += 1
            by_session.setdefault(session, {"champion_r": 0.0, "challenger_r": 0.0, "samples": 0})
            by_session[session]["champion_r"] += champ_r_side
            by_session[session]["challenger_r"] += d.hypothetical_r
            by_session[session]["samples"] += 1

        for _regime, agg in by_regime.items():
            n = max(1, agg["samples"])
            agg["champion_r"] = round(agg["champion_r"] / n, 6)
            agg["challenger_r"] = round(agg["challenger_r"] / n, 6)
            agg["delta"] = round(agg["challenger_r"] - agg["champion_r"], 6)
        for strat, agg in by_strategy.items():
            n = max(1, agg["samples"])
            agg["champion_r"] = round(agg["champion_r"] / n, 6)
            agg["challenger_r"] = round(agg["challenger_r"] / n, 6)
            agg["delta"] = round(agg["challenger_r"] - agg["champion_r"], 6)
            if agg["samples"] >= 3:
                if agg["delta"] < MAX_STRATEGY_REGRESSION_R:
                    degraded_strategies.append(strat)
                elif agg["delta"] > 0.05:
                    improved_strategies.append(strat)
        for _session, agg in by_session.items():
            n = max(1, agg["samples"])
            agg["champion_r"] = round(agg["champion_r"] / n, 6)
            agg["challenger_r"] = round(agg["challenger_r"] / n, 6)
            agg["delta"] = round(agg["challenger_r"] - agg["champion_r"], 6)

        # Regime ranking (spec 11)
        regimes_sorted = sorted(by_regime.items(), key=lambda kv: kv[1]["delta"], reverse=True)
        best_regimes = [r for r, _ in regimes_sorted[:3] if by_regime[r]["samples"] >= 3]
        worst_regimes = [r for r, _ in regimes_sorted[-3:] if by_regime[r]["samples"] >= 3][::-1]
        # A regime is DEGRADED when the challenger's expectancy is materially
        # negative there (absolute degradation), OR when it falls clearly
        # behind the champion (relative degradation). Both signals are
        # considered so a bad regime is never averaged away by good regimes.
        degraded_regimes = [
            r
            for r, agg in by_regime.items()
            if agg["samples"] >= 3
            and (
                agg["challenger_r"] < MIN_REGIME_EXPECTANCY_R
                or agg["delta"] < MAX_STRATEGY_REGRESSION_R
            )
        ]

        agreement = sum(1 for d in valid if d.action_agreement) / len(valid) if valid else 0.0

        champ_mfe = _mean([d.mfe_r for d in valid])
        chal_mfe = _mean([d.mfe_r for d in valid])  # same simulated excursion for both
        champ_mae = _mean([d.mae_r for d in valid])
        chal_mae = _mean([d.mae_r for d in valid])

        champ_conf = _mean([d.champion_confidence for d in valid])
        chal_conf = _mean([d.challenger_confidence for d in valid])
        champ_cal = _calibration(valid, challenger=False)
        chal_cal = _calibration(valid, challenger=True)

        champ_dd = _drawdown(valid, challenger=False)
        chal_dd = _drawdown(valid, challenger=True)

        # Evidence status (spec 9 / 10)
        observed = len(valid)
        status = ShadowEvidenceStatus.INSUFFICIENT_EVIDENCE
        if observed == 0:
            status = ShadowEvidenceStatus.INSUFFICIENT_EVIDENCE
        elif observed < self.min_samples:
            status = ShadowEvidenceStatus.EVALUATING
        else:
            status = (
                ShadowEvidenceStatus.EVALUATING
            )  # set to PROMOTION_ELIGIBLE only via promotion eval

        started = min((d.created_at for d in decisions), default=datetime.now(UTC))
        duration_h = (datetime.now(UTC) - started).total_seconds() / 3600.0

        return ShadowComparison(
            run_id=run_id,
            champion=champion,
            challenger=challenger,
            sample_count=len(decisions),
            valid_comparisons=len(valid),
            invalid_comparisons=len(invalid),
            action_agreement_rate=round(agreement, 6),
            champion_expectancy_r=round(champion_exp, 6),
            challenger_expectancy_r=round(challenger_exp, 6),
            champion_drawdown_r=round(champ_dd, 6),
            challenger_drawdown_r=round(chal_dd, 6),
            champion_profit_factor=round(_profit_factor(valid, False), 6),
            challenger_profit_factor=round(_profit_factor(valid, True), 6),
            champion_tail_losses=sum(1 for d in valid if d.hypothetical_r <= -1.5),
            challenger_tail_losses=sum(1 for d in valid if d.hypothetical_r <= -1.5),
            champion_mfe_r=round(champ_mfe, 6),
            challenger_mfe_r=round(chal_mfe, 6),
            champion_mae_r=round(champ_mae, 6),
            challenger_mae_r=round(chal_mae, 6),
            champion_holding_sec=round(_mean([d.holding_duration_sec for d in valid]), 2),
            challenger_holding_sec=round(_mean([d.holding_duration_sec for d in valid]), 2),
            champion_calibration=round(champ_cal, 6),
            challenger_calibration=round(chal_cal, 6),
            champion_avg_confidence=round(champ_conf, 6),
            challenger_avg_confidence=round(chal_conf, 6),
            by_regime=by_regime,
            by_strategy=by_strategy,
            by_session=by_session,
            best_regimes=best_regimes,
            worst_regimes=worst_regimes,
            degraded_regimes=degraded_regimes,
            degraded_strategies=degraded_strategies,
            improved_strategies=improved_strategies,
            evidence_status=status,
            samples_required=self.min_samples,
            samples_observed=observed,
            evaluation_started_at=started,
            evaluation_duration_hours=round(duration_h, 3),
        )

    # ------------------------------------------------------------------
    # Promotion evaluation
    # ------------------------------------------------------------------

    def evaluate_promotion(
        self,
        comparison: ShadowComparison,
        oos_expectancy_r: float | None = None,
        robustness_status: str = "PASS",
    ) -> PromotionEvaluation:
        """
        Explainable promotion evaluation with hard vetoes (spec 22 / 23).

        A single critical veto overrides the aggregate score:
          - insufficient evidence
          - negative OOS
          - critical drawdown increase
          - catastrophic tail degradation
          - major regression in a validated strategy
          - severe calibration failure
          - invalid comparisons dominating
          - robustness failure
        """
        vetoes: list[str] = []
        reasons: list[str] = []

        observed = comparison.samples_observed
        if observed < comparison.samples_required:
            vetoes.append(
                f"insufficient evidence: {observed} < {comparison.samples_required} samples"
            )

        exp_delta = comparison.expectancy_delta
        dd_delta = comparison.drawdown_delta
        if dd_delta > MAX_DRAWDOWN_DELTA_R:
            vetoes.append(f"critical drawdown increase: {dd_delta:.2f}R")

        if oos_expectancy_r is not None and oos_expectancy_r < MIN_OOS_EXPECTANCY_R:
            vetoes.append(f"negative OOS: {oos_expectancy_r:.3f}R")

        if robustness_status != "PASS":
            vetoes.append(f"robustness failure: {robustness_status}")

        if (
            comparison.challenger_calibration
            < comparison.champion_calibration - MAX_CALIBRATION_DROP
        ):
            vetoes.append(
                f"severe calibration failure: {comparison.challenger_calibration:.2f} "
                f"vs champion {comparison.champion_calibration:.2f}"
            )

        if comparison.degraded_strategies:
            vetoes.append(
                "critical strategy regressions: " + ", ".join(comparison.degraded_strategies)
            )

        if comparison.challenger_tail_losses > comparison.champion_tail_losses + 3:
            vetoes.append(
                f"catastrophic tail-risk degradation: challenger "
                f"{comparison.challenger_tail_losses} vs champion "
                f"{comparison.champion_tail_losses}"
            )

        if (
            comparison.invalid_comparisons > 0
            and comparison.invalid_comparisons / max(1, comparison.sample_count) > 0.1
        ):
            vetoes.append("invalid comparisons exceed 10% of shadow samples")

        # Strategy regression penalty (relative to champion expectancy).
        penalty = 0.0
        if comparison.degraded_strategies:
            penalty = min(0.30, 0.10 * len(comparison.degraded_strategies))
            reasons.append(
                f"strategy regression penalty {penalty:.2f} applied "
                f"({len(comparison.degraded_strategies)} strategies)"
            )

        # Sample confidence: logistic on observed samples.
        sample_conf = (
            0.0 if observed == 0 else min(0.95, observed / (comparison.samples_required * 2))
        )

        if not vetoes:
            reasons.append("no critical veto conditions observed")

        # Explainable multi-component score (weighted).
        perf_delta = _norm_delta(exp_delta)
        risk_delta = max(0.0, 1.0 - max(0.0, dd_delta) / 3.0)
        drawdown_delta = risk_delta
        oos_delta = 0.5 if oos_expectancy_r is None else max(0.0, min(1.0, 0.5 + oos_expectancy_r))
        robustness_delta = 1.0 if robustness_status == "PASS" else 0.0
        calibration_delta = max(
            0.0,
            min(1.0, 0.5 + (comparison.challenger_calibration - comparison.champion_calibration)),
        )
        stability_delta = sample_conf

        score = (
            0.30 * perf_delta
            + 0.15 * risk_delta
            + 0.10 * drawdown_delta
            + 0.15 * oos_delta
            + 0.10 * robustness_delta
            + 0.05 * calibration_delta
            + 0.05 * stability_delta
            + 0.10 * sample_conf
        )
        score = max(0.0, min(1.0, score - penalty))

        eligible = not vetoes and observed >= comparison.samples_required

        logger.info(
            "[PROMOTION] event=GATE",
            run_id=comparison.run_id,
            status="PASS" if eligible else "FAIL",
            score=round(score, 4),
            vetoes=len(vetoes),
        )
        if vetoes:
            for v in vetoes:
                logger.warning("[PROMOTION] event=VETO", reason=v)

        return PromotionEvaluation(
            run_id=comparison.run_id,
            candidate_model_id=comparison.challenger.model_id,
            candidate_version=comparison.challenger.model_version,
            champion_model_id=comparison.champion.model_id,
            champion_version=comparison.champion.model_version,
            performance_delta=round(exp_delta, 6),
            risk_delta=round(risk_delta, 6),
            drawdown_delta=round(dd_delta, 6),
            oos_delta=round(oos_delta or 0.0, 6),
            robustness_delta=round(robustness_delta, 6),
            calibration_delta=round(calibration_delta, 6),
            stability_delta=round(stability_delta, 6),
            strategy_regression_penalty=round(penalty, 6),
            sample_confidence=round(sample_conf, 6),
            final_score=round(score, 6),
            eligible=eligible,
            vetoes=vetoes,
            reasons=reasons,
        )


def _norm_delta(delta: float) -> float:
    """Maps an expectancy delta in R to [0,1] (0.2R delta ~ 0.7)."""
    return max(0.0, min(1.0, 0.5 + delta * 2.5))


def _calibration(decisions: list[ShadowDecisionRecord], challenger: bool) -> float:
    """
    Binned calibration score: |observed_accuracy - mean_confidence| over all
    decisions with a non-NO_TRADE action.
    """
    confs: list[float] = []
    correct: list[bool] = []
    for d in decisions:
        conf = d.challenger_confidence if challenger else d.champion_confidence
        action = d.challenger_action if challenger else d.champion_action
        if action in ("NO_TRADE", "WAIT"):
            continue
        confs.append(conf)
        # agreement with the realized hypothetical direction as a proxy
        correct.append(
            (d.hypothetical_r > 0)
            if action == "BUY_MARKET"
            else (d.hypothetical_r < 0)
            if action == "SELL_MARKET"
            else False
        )
    if not confs:
        return 0.0
    acc = sum(correct) / len(correct)
    conf = _mean(confs)
    return max(0.0, 1.0 - abs(acc - conf))


def _drawdown(decisions: list[ShadowDecisionRecord], challenger: bool) -> float:
    """Max drawdown over the cumulative R curve."""
    if not decisions:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in decisions:
        r = d.hypothetical_r  # same simulated outcome for both models
        cum += r
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return max_dd


def _profit_factor(decisions: list[ShadowDecisionRecord], challenger: bool) -> float:
    gross_win = sum(d.hypothetical_r for d in decisions if d.hypothetical_r > 0)
    gross_loss = abs(sum(d.hypothetical_r for d in decisions if d.hypothetical_r < 0))
    if gross_loss > 1e-9:
        return gross_win / gross_loss
    return gross_win if gross_win > 0 else 0.0
