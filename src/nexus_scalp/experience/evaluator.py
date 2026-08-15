"""
Strategy Evaluator & Confidence Engine
=======================================
Phase 08 Experience Intelligence evaluation subsystem.

Computes multi-dimensional statistical performance metrics, recency-weighted
R-expectancies, downside tail risk, MAE/MFE excursions, evidence-based confidence
scores, and data-driven strategy lifecycle state transitions.
Includes self-healing state reconstruction from the immutable experience ledger.
"""

import json
import math

import numpy as np

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceRecord, StrategyLifecycle, StrategyScore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.evaluator")


class StrategyEvaluator:
    """
    Evaluates historical experience records for strategy/context patterns.
    Computes statistical evidence scores, manages data-driven lifecycle state transitions,
    and supports self-healing derived intelligence reconstruction.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        min_samples_evaluating: int = 5,
        min_samples_validated: int = 20,
        decay_half_life_trades: float = 30.0,
        retire_expectancy_threshold_r: float = -0.20,
        retire_drawdown_threshold_r: float = 5.0,
        degrade_expectancy_threshold_r: float = 0.0,
    ) -> None:
        self.audit_repo = audit_repo
        self.min_samples_evaluating = min_samples_evaluating
        self.min_samples_validated = min_samples_validated
        self.decay_half_life_trades = decay_half_life_trades
        self.retire_expectancy_threshold_r = retire_expectancy_threshold_r
        self.retire_drawdown_threshold_r = retire_drawdown_threshold_r
        self.degrade_expectancy_threshold_r = degrade_expectancy_threshold_r

    def evaluate_strategy(
        self,
        strategy_id: str,
        experiences: list[ExperienceRecord],
        current_state: StrategyLifecycle | None = None,
    ) -> StrategyScore:
        """
        Evaluates a list of historical ExperienceRecord objects for a strategy.

        Computes expectancy, profit factor, tail risk, recency decay, confidence bounds,
        and determines the data-driven lifecycle transition.
        """
        # Filter for completed executed trades to compute outcomes
        closed_exps = [e for e in experiences if e.is_executed and e.is_closed]
        sample_count = len(closed_exps)

        if sample_count == 0:
            initial_lifecycle = current_state or StrategyLifecycle.DISCOVERED
            return StrategyScore(
                strategy_id=strategy_id,
                sample_count=0,
                lifecycle_state=initial_lifecycle,
            )

        # Extract R-multiples and USD PnLs
        r_outcomes = np.array([e.realized_r_multiple for e in closed_exps], dtype=float)
        pnl_outcomes = np.array([e.realized_pnl_usd for e in closed_exps], dtype=float)
        mae_r_outcomes = np.array(
            [
                abs(e.mae_points) / max(1e-5, abs(e.proposed_entry - e.stop_loss))
                for e in closed_exps
            ],
            dtype=float,
        )
        mfe_r_outcomes = np.array(
            [
                abs(e.mfe_points) / max(1e-5, abs(e.proposed_entry - e.stop_loss))
                for e in closed_exps
            ],
            dtype=float,
        )

        wins = np.sum(r_outcomes > 0.05)
        losses = np.sum(r_outcomes < -0.05)
        breakevens = sample_count - (wins + losses)

        win_rate = float(wins / sample_count)
        expectancy_usd = float(np.mean(pnl_outcomes))
        expectancy_r = float(np.mean(r_outcomes))

        gross_profit = (
            float(np.sum(pnl_outcomes[pnl_outcomes > 0.0])) if np.any(pnl_outcomes > 0.0) else 0.0
        )
        gross_loss = (
            float(np.abs(np.sum(pnl_outcomes[pnl_outcomes < 0.0])))
            if np.any(pnl_outcomes < 0.0)
            else 0.0
        )
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 0.0
            else (gross_profit if gross_profit > 0.0 else 1.0)
        )

        median_r = float(np.median(r_outcomes))
        r_variance = float(np.var(r_outcomes))
        avg_mae_r = float(np.mean(mae_r_outcomes))
        avg_mfe_r = float(np.mean(mfe_r_outcomes))

        # Downside tail risk (5th percentile worst R outcome)
        downside_tail_risk_r = (
            float(np.percentile(r_outcomes, 5)) if sample_count >= 5 else float(np.min(r_outcomes))
        )

        # Cumulative peak-to-trough drawdown in R
        cum_r = np.cumsum(r_outcomes)
        peak_r = np.maximum.accumulate(cum_r)
        dd_r = peak_r - cum_r
        max_drawdown_r = float(np.max(dd_r)) if len(dd_r) > 0 else 0.0

        # Recency-weighted R-expectancy using exponential decay
        weights = np.exp(-np.arange(sample_count)[::-1] / self.decay_half_life_trades)
        weights /= np.sum(weights)
        recency_weighted_expectancy_r = float(np.sum(weights * r_outcomes))

        # Evidence-based confidence score calibration (bounded [0.0, 1.0])
        sample_factor = min(1.0, sample_count / (2.0 * self.min_samples_validated))
        stability_factor = 1.0 / (1.0 + math.sqrt(r_variance))
        consistency_factor = max(0.0, min(1.0, (recency_weighted_expectancy_r + 1.0) / 2.0))
        confidence_score = float(
            round(0.40 * sample_factor + 0.30 * stability_factor + 0.30 * consistency_factor, 4)
        )

        # Determine Data-Driven Strategy Lifecycle State Transition
        lifecycle_state = self._determine_lifecycle_state(
            current_state=current_state,
            sample_count=sample_count,
            expectancy_r=expectancy_r,
            recency_expectancy_r=recency_weighted_expectancy_r,
            max_drawdown_r=max_drawdown_r,
            confidence_score=confidence_score,
        )

        score = StrategyScore(
            strategy_id=strategy_id,
            sample_count=sample_count,
            win_count=int(wins),
            loss_count=int(losses),
            breakeven_count=int(breakevens),
            win_rate=float(round(win_rate, 4)),
            expectancy_usd=float(round(expectancy_usd, 4)),
            expectancy_r=float(round(expectancy_r, 4)),
            profit_factor=float(round(profit_factor, 4)),
            median_r=float(round(median_r, 4)),
            max_drawdown_r=float(round(max_drawdown_r, 4)),
            downside_tail_risk_r=float(round(downside_tail_risk_r, 4)),
            avg_mae_r=float(round(avg_mae_r, 4)),
            avg_mfe_r=float(round(avg_mfe_r, 4)),
            r_variance=float(round(r_variance, 4)),
            recency_weighted_expectancy_r=float(round(recency_weighted_expectancy_r, 4)),
            confidence_score=confidence_score,
            lifecycle_state=lifecycle_state,
        )

        # Persist updated score into database registry
        self._persist_strategy_score(score)
        return score

    def rebuild_derived_intelligence(self, ledger: ExperienceLedger) -> dict[str, StrategyScore]:
        """
        SELF-HEALING API: Rebuilds derived strategy intelligence registry by replaying
        all historical experiences from the immutable audit_experiences table.
        """
        if not self.audit_repo._is_sqlite:
            return {}

        import sqlite3

        rebuilt_scores: dict[str, StrategyScore] = {}
        try:
            with sqlite3.connect(self.audit_repo._db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT DISTINCT strategy_id FROM audit_experiences;")
                strategy_ids = [
                    row["strategy_id"] for row in cursor.fetchall() if row["strategy_id"]
                ]

            for strat_id in strategy_ids:
                exps = ledger.get_experiences_for_strategy(strategy_id=strat_id, limit=5000)
                if exps:
                    score = self.evaluate_strategy(strategy_id=strat_id, experiences=exps)
                    rebuilt_scores[strat_id] = score

            logger.info(
                "SELF-HEALING: Successfully rebuilt derived strategy intelligence from immutable ledger",
                rebuilt_strategies_count=len(rebuilt_scores),
            )
        except Exception as e:
            logger.error("SELF-HEALING FAILED during strategy intelligence rebuild", error=str(e))

        return rebuilt_scores

    def _determine_lifecycle_state(
        self,
        current_state: StrategyLifecycle | None,
        sample_count: int,
        expectancy_r: float,
        recency_expectancy_r: float,
        max_drawdown_r: float,
        confidence_score: float,
    ) -> StrategyLifecycle:
        """
        Determines the state transition in the strategy lifecycle based on statistical evidence.
        """
        # Hard Retirement Safeguards: Persistent negative expectancy or severe drawdown
        if sample_count >= self.min_samples_evaluating:
            if (
                recency_expectancy_r <= self.retire_expectancy_threshold_r
                or max_drawdown_r >= self.retire_drawdown_threshold_r
            ):
                logger.warning(
                    "Strategy RETIRED due to persistent negative expectancy or severe drawdown",
                    recency_expectancy=recency_expectancy_r,
                    max_drawdown_r=max_drawdown_r,
                )
                return StrategyLifecycle.RETIRED

        # If current state is RETIRED or QUARANTINED, require strong validated evidence to recover
        if current_state in (StrategyLifecycle.RETIRED, StrategyLifecycle.QUARANTINED):
            if (
                sample_count >= self.min_samples_validated
                and recency_expectancy_r > 0.20
                and confidence_score >= 0.60
            ):
                logger.info("Retired strategy recovered to EVALUATING via validated evidence")
                return StrategyLifecycle.EVALUATING
            return current_state or StrategyLifecycle.RETIRED

        # Degradation check: Recent decay or flat expectancy
        if (
            sample_count >= self.min_samples_evaluating
            and recency_expectancy_r < self.degrade_expectancy_threshold_r
        ):
            return StrategyLifecycle.DEGRADED

        # Discovery & Evaluation Progression
        if sample_count < self.min_samples_evaluating:
            return StrategyLifecycle.DISCOVERED
        elif sample_count < self.min_samples_validated:
            return StrategyLifecycle.EVALUATING
        # Validated / Active check based on positive expectancy and confidence
        elif recency_expectancy_r > 0.0 and confidence_score >= 0.50:
            return StrategyLifecycle.ACTIVE
        elif recency_expectancy_r > -0.05:
            return StrategyLifecycle.VALIDATED
        else:
            return StrategyLifecycle.DEGRADED

    def _persist_strategy_score(self, score: StrategyScore) -> None:
        """Persists or updates the strategy score in the strategy_intelligence_registry table."""
        if not self.audit_repo._is_sqlite:
            return

        query = """
            INSERT INTO strategy_intelligence_registry
            (strategy_id, lifecycle_state, sample_count, win_rate, expectancy_r, profit_factor,
             confidence_score, score_payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, DATETIME('now'))
            ON CONFLICT(strategy_id) DO UPDATE SET
                lifecycle_state=excluded.lifecycle_state,
                sample_count=excluded.sample_count,
                win_rate=excluded.win_rate,
                expectancy_r=excluded.expectancy_r,
                profit_factor=excluded.profit_factor,
                confidence_score=excluded.confidence_score,
                score_payload=excluded.score_payload,
                updated_at=DATETIME('now');
        """

        score_dict = json.loads(score.model_dump_json())
        args = (
            score.strategy_id,
            score.lifecycle_state.value,
            score.sample_count,
            score.win_rate,
            score.expectancy_r,
            score.profit_factor,
            score.confidence_score,
            json.dumps(score_dict),
        )

        try:
            self.audit_repo._queue.put_nowait((query, args))
        except Exception as e:
            logger.error(
                "Failed to queue strategy score persistence",
                strategy_id=score.strategy_id,
                error=str(e),
            )

    def get_registered_strategy_score(self, strategy_id: str) -> StrategyScore | None:
        """Retrieves registered strategy score from database registry if present."""
        if not self.audit_repo._is_sqlite:
            return None

        import sqlite3

        try:
            with sqlite3.connect(self.audit_repo._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT score_payload FROM strategy_intelligence_registry WHERE strategy_id = ?",
                    (strategy_id,),
                )
                row = cursor.fetchone()
                if row and row["score_payload"]:
                    data = json.loads(row["score_payload"])
                    return StrategyScore.model_validate(data)
        except Exception as e:
            logger.error(
                "Failed to retrieve registered strategy score",
                strategy_id=strategy_id,
                error=str(e),
            )

        return None
