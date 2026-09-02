"""
Trade Outcome Decomposition & Behavioral Analysis
=================================================
Phase 08 deterministic quality attribution.

This module answers "WHY did this trade work or fail?" separately from
"did the account make money?". Every score and every flag is a pure function of
recorded evidence - there is no model inference, no randomness and no hidden
state, so a decomposition can always be recomputed from the immutable ledger.

Separation enforced here (Phase 08 rules 7, 8, 19, 21):

    SIGNAL / STRATEGY QUALITY   was the thesis right?
    REGIME FIT                  was the context suitable?
    ENTRY QUALITY               was the fill and timing sound?
    RISK QUALITY                was the stop and R/R defensible?
    POSITION MANAGEMENT         did we hold/trail correctly?
    EXIT QUALITY                did we bank what the move offered?
    EXECUTION QUALITY           did the broker path behave?

A profitable trade with poor strategy and entry evidence is explicitly marked
`profitable_for_wrong_reason`; a losing trade with sound decision and risk is
marked `acceptable_loss`. This prevents "won = good" from becoming the learning
rule.

All thresholds live in `DecompositionThresholds` so they are auditable and
testable rather than scattered magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexus_scalp.experience.models import (
    BehavioralFlag,
    ExecutionContext,
    ExperienceRecord,
    OutcomeDecomposition,
    PositionBehavior,
    QualityVerdict,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.quality")


@dataclass(frozen=True)
class DecompositionThresholds:
    """Auditable thresholds for deterministic quality attribution."""

    #: Slippage above this fraction of planned risk is an entry chase.
    entry_chase_slippage_r: float = 0.15
    #: Slippage above this fraction of planned risk is an execution anomaly.
    slippage_anomaly_r: float = 0.30
    #: MFE below this R with immediate adverse excursion => premature entry.
    premature_entry_mfe_r: float = 0.15
    #: MAE above this R while still holding => invalidation ignored.
    invalidation_mae_r: float = 0.90
    #: Stated confidence above this paired with a loss => confidence overshoot.
    confidence_overshoot_threshold: float = 0.75
    #: Loss worse than this R counts as contradicting high confidence.
    confidence_overshoot_loss_r: float = -0.50
    #: Hold longer than expected_duration * this factor => excessive hold.
    excessive_hold_factor: float = 3.0
    #: Executed risk deviating more than this fraction => risk deviation.
    risk_deviation_tolerance: float = 0.25
    #: MFE above this R while banking below capture floor => early exit.
    early_exit_mfe_r: float = 1.20
    #: Fraction of MFE that must be captured to avoid an early-exit flag.
    early_exit_capture_floor: float = 0.35
    #: Stop distance below this multiple of ATR is inside normal noise.
    poor_stop_atr_multiple: float = 0.50
    #: Reward/risk below this absolute floor is a weak setup when no policy
    #: floor was recorded with the decision.
    default_min_rr: float = 1.2
    #: Re-entries in the same strategy family within this window => overtrading.
    reentry_window_sec: float = 300.0
    #: Number of entries inside the window that constitutes overtrading.
    reentry_count_threshold: int = 3


DEFAULT_THRESHOLDS = DecompositionThresholds()


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    """Clamps a score into the declared bounds."""
    return float(max(low, min(high, value)))


def _verdict(score: float) -> QualityVerdict:
    """Maps a bounded score to a coarse verdict."""
    if score >= 0.35:
        return QualityVerdict.GOOD
    if score >= -0.15:
        return QualityVerdict.ACCEPTABLE
    return QualityVerdict.POOR


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Division that returns 0.0 instead of raising on a zero denominator."""
    if abs(denominator) < 1e-9:
        return 0.0
    return float(numerator) / float(denominator)


class OutcomeAnalyzer:
    """
    Deterministic decomposition of a closed position.

    Usage:
        analyzer = OutcomeAnalyzer()
        decomposition, flags = analyzer.analyze(record, behavior, execution,
                                                realized_r, exit_reason)

    `record` supplies the plan (entry, stop, target, confidence, policy floor);
    `behavior` and `execution` supply what actually happened. Missing evidence
    yields neutral 0.0 scores and `UNKNOWN` verdicts rather than invented ones.
    """

    def __init__(self, thresholds: DecompositionThresholds | None = None) -> None:
        self.t = thresholds or DEFAULT_THRESHOLDS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        record: ExperienceRecord,
        behavior: PositionBehavior,
        execution: ExecutionContext,
        realized_r: float,
        exit_reason: str = "",
        recent_context_entries: int = 0,
    ) -> tuple[OutcomeDecomposition, list[BehavioralFlag]]:
        """
        Computes the full decomposition plus measurable behavioral flags.

        Args:
            record: Immutable decision experience carrying the original plan.
            behavior: Observed position behaviour (MAE/MFE/duration/stop moves).
            execution: Observed execution context (fill, slippage, latency).
            realized_r: Realised R multiple of the closed trade.
            exit_reason: Canonical exit mechanism string.
            recent_context_entries: Entries already taken in this strategy
                family inside the re-entry window (0 when unknown).

        Returns:
            (decomposition, flags) - both derived purely from the inputs.
        """
        planned_risk = record.planned_risk_distance
        mae_r = (
            behavior.mae_r
            if behavior.mae_r > 0.0
            else abs(_safe_ratio(behavior.mae_points, planned_risk))
        )
        mfe_r = (
            behavior.mfe_r
            if behavior.mfe_r > 0.0
            else abs(_safe_ratio(behavior.mfe_points, planned_risk))
        )
        slippage_r = abs(_safe_ratio(execution.slippage_points, planned_risk))

        entry_quality = self._entry_quality(mfe_r, mae_r, slippage_r)
        risk_quality = self._risk_quality(record, behavior)
        execution_quality = self._execution_quality(execution, slippage_r)
        management_quality = self._management_quality(behavior, mfe_r, mae_r, realized_r)
        exit_quality = self._exit_quality(mfe_r, realized_r, exit_reason)
        strategy_quality = self._strategy_quality(mfe_r, mae_r, realized_r)
        signal_quality = self._signal_quality(record, mfe_r, mae_r)
        regime_fit = self._regime_fit(mfe_r, mae_r, behavior)

        flags = self._behavioral_flags(
            record=record,
            behavior=behavior,
            execution=execution,
            realized_r=realized_r,
            mae_r=mae_r,
            mfe_r=mfe_r,
            slippage_r=slippage_r,
            exit_reason=exit_reason,
            recent_context_entries=recent_context_entries,
        )

        profitable_for_wrong_reason = bool(
            realized_r > 0.0 and (strategy_quality < 0.0 or entry_quality < -0.25)
        )
        # A full stop-out has mae_r ~= 1.0 by definition, which necessarily
        # depresses strategy_quality. The acceptable-loss threshold is therefore
        # calibrated so that "the market gave us a real look, then took us out at
        # our planned risk" still counts as a sound decision, while "never went
        # our way at all" does not.
        acceptable_loss = bool(
            realized_r <= 0.0
            and strategy_quality >= -0.35
            and risk_quality >= 0.0
            and BehavioralFlag.RISK_DEVIATION not in flags
            and BehavioralFlag.THESIS_INVALIDATION_IGNORED not in flags
        )

        decomposition = OutcomeDecomposition(
            signal_quality=signal_quality,
            strategy_quality=strategy_quality,
            regime_fit=regime_fit,
            entry_quality=entry_quality,
            risk_quality=risk_quality,
            position_management_quality=management_quality,
            exit_quality=exit_quality,
            execution_quality=execution_quality,
            final_outcome_r=float(realized_r),
            strategy_verdict=_verdict(strategy_quality),
            execution_verdict=_verdict(execution_quality),
            management_verdict=_verdict(management_quality),
            profitable_for_wrong_reason=profitable_for_wrong_reason,
            acceptable_loss=acceptable_loss,
        )
        return decomposition, flags

    # ------------------------------------------------------------------
    # Dimension scores
    # ------------------------------------------------------------------

    def _entry_quality(self, mfe_r: float, mae_r: float, slippage_r: float) -> float:
        """
        Good entries move favourably quickly, with little immediate adverse
        excursion and little fill drift.
        """
        if mfe_r <= 0.0 and mae_r <= 0.0:
            return 0.0
        favourable = min(1.0, mfe_r)
        adverse = min(1.0, mae_r)
        score = favourable - adverse - (slippage_r * 2.0)
        return _clamp(score)

    def _risk_quality(self, record: ExperienceRecord, behavior: PositionBehavior) -> float:
        """
        Risk is good when the stop sits outside normal volatility noise and the
        accepted reward/risk clears the policy floor active at decision time.
        """
        planned_risk = record.planned_risk_distance
        if planned_risk <= 0.0:
            return 0.0

        score = 0.0
        atr = behavior.atr_at_entry
        if atr > 0.0:
            atr_multiple = planned_risk / atr
            if atr_multiple < self.t.poor_stop_atr_multiple:
                score -= 0.6
            elif atr_multiple <= 3.0:
                score += 0.4
            else:
                # Excessively wide stop: capital exposure without thesis support.
                score -= 0.2

        floor = record.min_rr_policy if record.min_rr_policy > 0.0 else self.t.default_min_rr
        if record.risk_reward_ratio >= floor:
            score += 0.4
        else:
            score -= 0.5

        return _clamp(score)

    def _execution_quality(self, execution: ExecutionContext, slippage_r: float) -> float:
        """Execution is penalised for adverse slippage, latency and rejections."""
        if execution.rejection_reason:
            return -1.0
        score = 1.0 - (slippage_r * 3.0)
        if execution.latency_ms > 1000.0:
            score -= 0.3
        elif execution.latency_ms > 500.0:
            score -= 0.15
        return _clamp(score)

    def _management_quality(
        self,
        behavior: PositionBehavior,
        mfe_r: float,
        mae_r: float,
        realized_r: float,
    ) -> float:
        """
        Management is judged on how much of the favourable excursion survived
        to the exit, and on whether protection was engaged when available.
        """
        if mfe_r <= 0.0:
            # Never in profit: management had little to manage. Deep adverse
            # excursion without any protective move is still penalised.
            return _clamp(
                -0.4 if (mae_r > self.t.invalidation_mae_r and not behavior.sl_moved) else 0.0
            )

        capture = _safe_ratio(realized_r, mfe_r)
        score = _clamp((capture - 0.5) * 2.0)
        if behavior.sl_moved and realized_r >= 0.0:
            score += 0.2
        if behavior.partial_closed and realized_r > 0.0:
            score += 0.1
        return _clamp(score)

    def _exit_quality(self, mfe_r: float, realized_r: float, exit_reason: str) -> float:
        """Exit quality reflects capture efficiency and exit mechanism."""
        reason = (exit_reason or "").upper()
        if mfe_r <= 0.0:
            # Losing trade: a controlled stop is an acceptable exit.
            if "SL" in reason or "STOP" in reason:
                return 0.2
            return _clamp(realized_r)

        capture = _safe_ratio(realized_r, mfe_r)
        score = _clamp((capture - 0.4) * 1.8)
        if "TAKE_PROFIT" in reason:
            score += 0.2
        if "RISK_FREE" in reason or "GIVEBACK" in reason:
            score += 0.1
        return _clamp(score)

    def _strategy_quality(self, mfe_r: float, mae_r: float, realized_r: float) -> float:
        """
        Strategy quality measures whether the market did what the thesis
        predicted, independently of what management/execution banked.

        A trade that immediately ran to deep MAE and only recovered by chance
        scores poorly even when the final PnL is positive.
        """
        if mfe_r <= 0.0 and mae_r <= 0.0:
            return 0.0
        thesis_strength = min(1.5, mfe_r) / 1.5
        thesis_damage = min(1.5, mae_r) / 1.5
        score = thesis_strength - thesis_damage
        if realized_r > 0.0 and mae_r > self.t.invalidation_mae_r and mfe_r < 0.5:
            # Won, but the thesis was invalidated first: luck, not edge.
            score = min(score, -0.2)
        return _clamp(score)

    def _signal_quality(self, record: ExperienceRecord, mfe_r: float, mae_r: float) -> float:
        """
        Signal quality relates the stated confidence to the observed favourable
        excursion. Confident signals that never moved favourably score poorly.
        """
        confidence = record.signal_confidence or record.model_probability
        if confidence <= 0.0 and mfe_r <= 0.0 and mae_r <= 0.0:
            return 0.0
        realised_edge = min(1.0, mfe_r) - min(1.0, mae_r)
        return _clamp(realised_edge - (confidence - 0.5))

    def _regime_fit(self, mfe_r: float, mae_r: float, behavior: PositionBehavior) -> float:
        """
        Regime fit asks whether the context permitted the move at all: a trade
        whose adverse excursion dwarfs its favourable excursion in the observed
        volatility was taken in an unsuitable regime.
        """
        if mfe_r <= 0.0 and mae_r <= 0.0:
            return 0.0
        if behavior.atr_at_entry <= 0.0:
            return _clamp(min(1.0, mfe_r) - min(1.0, mae_r))
        return _clamp((min(1.5, mfe_r) - min(1.5, mae_r)) / 1.5)

    # ------------------------------------------------------------------
    # Behavioral flags
    # ------------------------------------------------------------------

    def _behavioral_flags(
        self,
        record: ExperienceRecord,
        behavior: PositionBehavior,
        execution: ExecutionContext,
        realized_r: float,
        mae_r: float,
        mfe_r: float,
        slippage_r: float,
        exit_reason: str,
        recent_context_entries: int,
    ) -> list[BehavioralFlag]:
        """Derives every measurable behavioral failure for this trade."""
        flags: list[BehavioralFlag] = []
        planned_risk = record.planned_risk_distance

        if slippage_r >= self.t.entry_chase_slippage_r:
            flags.append(BehavioralFlag.ENTRY_CHASE)
        if slippage_r >= self.t.slippage_anomaly_r:
            flags.append(BehavioralFlag.EXECUTION_SLIPPAGE_ANOMALY)

        if mfe_r <= self.t.premature_entry_mfe_r and mae_r > self.t.premature_entry_mfe_r:
            flags.append(BehavioralFlag.PREMATURE_ENTRY)

        confidence = record.signal_confidence or record.model_probability
        if (
            confidence >= self.t.confidence_overshoot_threshold
            and realized_r <= self.t.confidence_overshoot_loss_r
        ):
            flags.append(BehavioralFlag.CONFIDENCE_OVERSHOOT)

        if mae_r >= self.t.invalidation_mae_r and realized_r <= 0.0 and not behavior.sl_moved:
            # A stop-out is the system RESPECTING the invalidation boundary, not
            # ignoring it. The flag is reserved for cases where price breached the
            # invalidation band and the position was still carried to some other
            # exit (manual close, hold-score decay, giveback, ...).
            reason_upper = (exit_reason or "").upper()
            stop_executed = "SL" in reason_upper or "STOP" in reason_upper
            if not stop_executed:
                flags.append(BehavioralFlag.THESIS_INVALIDATION_IGNORED)

        if (
            behavior.expected_duration_sec > 0.0
            and behavior.duration_sec
            > behavior.expected_duration_sec * self.t.excessive_hold_factor
            and realized_r <= 0.0
        ):
            flags.append(BehavioralFlag.EXCESSIVE_HOLD_DURATION)

        if planned_risk > 0.0 and behavior.initial_sl_distance > 0.0:
            deviation = abs(behavior.initial_sl_distance - planned_risk) / planned_risk
            if deviation > self.t.risk_deviation_tolerance:
                flags.append(BehavioralFlag.RISK_DEVIATION)

        if recent_context_entries >= self.t.reentry_count_threshold:
            flags.append(BehavioralFlag.REENTRY_OVERTRADING)

        if mfe_r >= self.t.early_exit_mfe_r and realized_r > 0.0:
            if _safe_ratio(realized_r, mfe_r) < self.t.early_exit_capture_floor:
                flags.append(BehavioralFlag.EARLY_EXIT)

        if (
            behavior.atr_at_entry > 0.0
            and planned_risk > 0.0
            and (planned_risk / behavior.atr_at_entry) < self.t.poor_stop_atr_multiple
        ):
            flags.append(BehavioralFlag.POOR_STOP_PLACEMENT)

        floor = record.min_rr_policy if record.min_rr_policy > 0.0 else self.t.default_min_rr
        if record.risk_reward_ratio > 0.0 and record.risk_reward_ratio < floor:
            flags.append(BehavioralFlag.WEAK_SETUP_ACCEPTED)

        # Preserve declaration order while removing duplicates.
        return list(dict.fromkeys(flags))


def compute_behavior_metrics(
    mae_points: float,
    mfe_points: float,
    mae_usd: float,
    mfe_usd: float,
    planned_risk_distance: float,
    duration_sec: float,
    time_to_mae_sec: float = 0.0,
    time_to_mfe_sec: float = 0.0,
    expected_duration_sec: float = 0.0,
    initial_sl_distance: float = 0.0,
    sl_moved: bool = False,
    tp_moved: bool = False,
    partial_closed: bool = False,
    atr_at_entry: float = 0.0,
) -> PositionBehavior:
    """
    Builds a `PositionBehavior` with risk-normalised excursions.

    Normalising MAE/MFE by the planned stop distance is what makes excursions
    comparable across symbols, volatility regimes and lot sizes.
    """
    mae_r = abs(_safe_ratio(mae_points, planned_risk_distance))
    mfe_r = abs(_safe_ratio(mfe_points, planned_risk_distance))
    return PositionBehavior(
        mae_points=float(mae_points),
        mfe_points=float(mfe_points),
        mae_usd=float(mae_usd),
        mfe_usd=float(mfe_usd),
        mae_r=mae_r,
        mfe_r=mfe_r,
        time_to_mae_sec=max(0.0, float(time_to_mae_sec)),
        time_to_mfe_sec=max(0.0, float(time_to_mfe_sec)),
        duration_sec=max(0.0, float(duration_sec)),
        expected_duration_sec=max(0.0, float(expected_duration_sec)),
        initial_sl_distance=max(0.0, float(initial_sl_distance)),
        sl_moved=bool(sl_moved),
        tp_moved=bool(tp_moved),
        partial_closed=bool(partial_closed),
        atr_at_entry=max(0.0, float(atr_at_entry)),
    )
