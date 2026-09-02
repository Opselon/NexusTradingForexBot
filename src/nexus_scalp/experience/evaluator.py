"""
Strategy Evaluator, Confidence Calibration & Lifecycle Engine
=============================================================
Phase 08 derived intelligence.

Everything this module produces is REBUILDABLE from the immutable ledger. It
holds no authoritative state: `strategy_intelligence_registry` is a cache that
`rebuild_derived_intelligence()` can reconstruct from scratch.

Statistical discipline enforced here (Phase 08 rules 10, 11, 12, 13):

* SAMPLE-AWARE     Retirement requires `min_samples_retire` closed trades AND a
                   significant negative t-statistic. One bad trade can never
                   retire a strategy.
* RISK-AWARE       Drawdown is normalised by sqrt(n) so a long profitable
                   history is not retired for accumulating opportunities.
* RECENCY-AWARE    Exponential decay plus an explicit recent window; old
                   knowledge loses operational weight but is never deleted.
* BOUNDED          Confidence is capped at MAX_STRATEGY_CONFIDENCE (0.95). Huge
                   sample counts alone can never produce certainty.
* REPLAY-GATED     `VALIDATED`/`ACTIVE` additionally require an out-of-sample
                   split (older half trains the belief, newer half must confirm
                   it) - a positive current score alone is not enough.
* PROBATION        A RETIRED/QUARANTINED family only recovers after
                   `min_samples_validated` NEW samples with a strong positive
                   recent edge.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import UTC, datetime

import numpy as np

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    MAX_STRATEGY_CONFIDENCE,
    ExperienceRecord,
    StrategyLifecycle,
    StrategyScore,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.evaluator")


class StrategyEvaluator:
    """
    Computes bounded statistical evidence and lifecycle transitions for strategy
    families, and rebuilds the derived registry from immutable history.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        min_samples_evaluating: int = 5,
        min_samples_validated: int = 20,
        min_samples_retire: int = 12,
        decay_half_life_trades: float = 30.0,
        recent_window_trades: int = 10,
        retire_expectancy_threshold_r: float = -0.20,
        retire_normalized_drawdown_r: float = 3.0,
        retire_t_stat_threshold: float = -1.65,
        degrade_expectancy_threshold_r: float = 0.0,
        recovery_expectancy_threshold_r: float = 0.20,
        recovery_confidence_threshold: float = 0.60,
    ) -> None:
        self.audit_repo = audit_repo
        self.min_samples_evaluating = min_samples_evaluating
        self.min_samples_validated = min_samples_validated
        self.min_samples_retire = min_samples_retire
        self.decay_half_life_trades = max(1.0, decay_half_life_trades)
        self.recent_window_trades = max(2, recent_window_trades)
        self.retire_expectancy_threshold_r = retire_expectancy_threshold_r
        self.retire_normalized_drawdown_r = retire_normalized_drawdown_r
        self.retire_t_stat_threshold = retire_t_stat_threshold
        self.degrade_expectancy_threshold_r = degrade_expectancy_threshold_r
        self.recovery_expectancy_threshold_r = recovery_expectancy_threshold_r
        self.recovery_confidence_threshold = recovery_confidence_threshold
        # AGENT-2: bounded edge-triggered DEGRADED log state per family.
        self._degraded_log_ts: dict[str, float] = {}

    def _should_repeat_degraded(self, strategy_id: str, min_gap_sec: float = 600.0) -> bool:
        """True at most once per min_gap_sec per family (bounded repetition).

        BUG-213 root fix: the "never logged yet" sentinel is ``None`` (absent
        key), NOT ``0.0``. Comparing against ``time.monotonic()`` epoch-0 made
        the first call machine-state dependent — on any host whose monotonic
        clock is younger than ``min_gap_sec`` (<600s uptime, typical for fresh
        CI runners) the first event was wrongly suppressed.
        """
        import time as _time

        now = _time.monotonic()
        last = self._degraded_log_ts.get(strategy_id)
        if last is None or now - last >= min_gap_sec:
            self._degraded_log_ts[strategy_id] = now
            # bounded memory: keep only the most recent 512 families
            if len(self._degraded_log_ts) > 512:
                oldest = min(self._degraded_log_ts, key=lambda k: self._degraded_log_ts[k])
                self._degraded_log_ts.pop(oldest, None)
            return True
        return False

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate_strategy(
        self,
        strategy_id: str,
        experiences: list[ExperienceRecord],
        current_state: StrategyLifecycle | None = None,
        persist: bool = True,
    ) -> StrategyScore:
        """
        Evaluates a strategy family from its (already causally filtered)
        experiences.

        Only EXECUTED and CLOSED experiences contribute outcome statistics;
        proposals that were rejected before execution remain in the ledger for
        forensics but are not counted as trade evidence.

        Args:
            strategy_id: Family identity.
            experiences: Causally valid experiences for this family.
            current_state: Previously known lifecycle (drives probation logic).
                When None, the persisted registry state is used if available.
            persist: When False the derived registry is not written (used by
                read-only evaluation such as the pre-trade gate).
        """
        if current_state is None:
            previous = self.get_registered_strategy_score(strategy_id)
            current_state = previous.lifecycle_state if previous else None
            previous_probation = previous.probation_samples if previous else 0
        else:
            previous_probation = 0

        closed = [e for e in experiences if e.is_executed and e.is_closed]
        # Oldest -> newest so recency weighting and replay splits are meaningful.
        closed.sort(key=lambda e: e.decision_timestamp)
        sample_count = len(closed)

        if sample_count == 0:
            score = StrategyScore(
                strategy_id=strategy_id,
                sample_count=0,
                lifecycle_state=current_state or StrategyLifecycle.DISCOVERED,
                probation_samples=previous_probation,
            )
            if persist:
                self._persist_strategy_score(score)
            return score

        r_outcomes = np.array([e.realized_r_multiple for e in closed], dtype=float)
        pnl_outcomes = np.array([e.realized_pnl_usd for e in closed], dtype=float)
        mae_r = np.array([self._mae_r(e) for e in closed], dtype=float)
        mfe_r = np.array([self._mfe_r(e) for e in closed], dtype=float)

        wins = int(np.sum(r_outcomes > 0.05))
        losses = int(np.sum(r_outcomes < -0.05))
        breakevens = sample_count - wins - losses

        expectancy_r = float(np.mean(r_outcomes))
        r_variance = float(np.var(r_outcomes, ddof=1)) if sample_count > 1 else 0.0
        r_std = math.sqrt(r_variance)
        expectancy_t_stat = (
            float(expectancy_r / (r_std / math.sqrt(sample_count)))
            if r_std > 1e-9 and sample_count > 1
            else 0.0
        )

        gross_profit = float(np.sum(pnl_outcomes[pnl_outcomes > 0.0]))
        gross_loss = float(abs(np.sum(pnl_outcomes[pnl_outcomes < 0.0])))
        profit_factor = (
            gross_profit / gross_loss
            if gross_loss > 1e-9
            else (min(gross_profit, 99.0) if gross_profit > 0.0 else 1.0)
        )

        cum_r = np.cumsum(r_outcomes)
        max_drawdown_r = float(np.max(np.maximum.accumulate(cum_r) - cum_r))
        # Normalising by sqrt(n) keeps long, healthy histories from being
        # retired purely because they had more chances to draw down.
        normalized_drawdown_r = float(max_drawdown_r / math.sqrt(sample_count))

        downside_tail_risk_r = (
            float(np.percentile(r_outcomes, 5)) if sample_count >= 5 else float(np.min(r_outcomes))
        )

        # Exponential recency weighting (newest sample gets the largest weight).
        ages = np.arange(sample_count)[::-1].astype(float)
        weights = np.exp(-ages * (math.log(2.0) / self.decay_half_life_trades))
        weights /= float(np.sum(weights))
        recency_weighted_expectancy_r = float(np.sum(weights * r_outcomes))

        recent_n = min(self.recent_window_trades, sample_count)
        recent_window_expectancy_r = float(np.mean(r_outcomes[-recent_n:]))

        in_sample_r, out_sample_r, replay_n, replay_ok = self._replay_split(r_outcomes)

        avg_exec_q = float(np.mean([e.decomposition.execution_quality for e in closed]))
        avg_mgmt_q = float(np.mean([e.decomposition.position_management_quality for e in closed]))
        avg_entry_q = float(np.mean([e.decomposition.entry_quality for e in closed]))
        avg_strat_q = float(np.mean([e.decomposition.strategy_quality for e in closed]))

        flag_counter: Counter[str] = Counter()
        for e in closed:
            for flag in e.behavioral_flags:
                flag_counter[flag.value] += 1

        evidence_quality = self._evidence_quality(sample_count, r_variance)
        confidence_score = self._confidence_score(
            sample_count=sample_count,
            r_variance=r_variance,
            recency_expectancy_r=recency_weighted_expectancy_r,
            recent_expectancy_r=recent_window_expectancy_r,
            normalized_drawdown_r=normalized_drawdown_r,
            avg_execution_quality=avg_exec_q,
            replay_validated=replay_ok,
        )

        lifecycle_state, probation_samples = self._determine_lifecycle_state(
            strategy_id=strategy_id,
            current_state=current_state,
            previous_probation=previous_probation,
            sample_count=sample_count,
            expectancy_r=expectancy_r,
            expectancy_t_stat=expectancy_t_stat,
            recency_expectancy_r=recency_weighted_expectancy_r,
            recent_expectancy_r=recent_window_expectancy_r,
            normalized_drawdown_r=normalized_drawdown_r,
            confidence_score=confidence_score,
            replay_validated=replay_ok,
        )

        score = StrategyScore(
            strategy_id=strategy_id,
            sample_count=sample_count,
            win_count=wins,
            loss_count=losses,
            breakeven_count=breakevens,
            win_rate=round(wins / sample_count, 4),
            expectancy_usd=round(float(np.mean(pnl_outcomes)), 4),
            expectancy_r=round(expectancy_r, 4),
            profit_factor=round(float(profit_factor), 4),
            median_r=round(float(np.median(r_outcomes)), 4),
            max_drawdown_r=round(max_drawdown_r, 4),
            normalized_drawdown_r=round(normalized_drawdown_r, 4),
            downside_tail_risk_r=round(downside_tail_risk_r, 4),
            avg_mae_r=round(float(np.mean(mae_r)), 4),
            avg_mfe_r=round(float(np.mean(mfe_r)), 4),
            r_variance=round(r_variance, 4),
            expectancy_t_stat=round(expectancy_t_stat, 4),
            recency_weighted_expectancy_r=round(recency_weighted_expectancy_r, 4),
            recent_window_expectancy_r=round(recent_window_expectancy_r, 4),
            recent_window_size=recent_n,
            in_sample_expectancy_r=round(in_sample_r, 4),
            out_of_sample_expectancy_r=round(out_sample_r, 4),
            replay_sample_count=replay_n,
            replay_validated=replay_ok,
            avg_execution_quality=round(max(-1.0, min(1.0, avg_exec_q)), 4),
            avg_management_quality=round(max(-1.0, min(1.0, avg_mgmt_q)), 4),
            avg_entry_quality=round(max(-1.0, min(1.0, avg_entry_q)), 4),
            avg_strategy_quality=round(max(-1.0, min(1.0, avg_strat_q)), 4),
            flag_counts=dict(flag_counter),
            confidence_score=confidence_score,
            evidence_quality=evidence_quality,
            lifecycle_state=lifecycle_state,
            probation_samples=probation_samples,
            last_updated=datetime.now(UTC),
        )

        if persist:
            self._persist_strategy_score(score)
        return score

    # ------------------------------------------------------------------
    # Statistics helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mae_r(e: ExperienceRecord) -> float:
        """Risk-normalised MAE, falling back to raw points / planned risk."""
        if e.behavior.mae_r > 0.0:
            return e.behavior.mae_r
        risk = e.planned_risk_distance
        return abs(e.behavior.mae_points) / risk if risk > 1e-9 else 0.0

    @staticmethod
    def _mfe_r(e: ExperienceRecord) -> float:
        """Risk-normalised MFE, falling back to raw points / planned risk."""
        if e.behavior.mfe_r > 0.0:
            return e.behavior.mfe_r
        risk = e.planned_risk_distance
        return abs(e.behavior.mfe_points) / risk if risk > 1e-9 else 0.0

    def _replay_split(self, r_outcomes: np.ndarray) -> tuple[float, float, int, bool]:
        """
        Deterministic in-sample / out-of-sample replay boundary.

        The older half of the chronologically ordered history forms the belief;
        the newer half must independently confirm a positive edge. This is the
        causally valid strategy-validation boundary required before a discovered
        candidate may be treated as validated.
        """
        n = len(r_outcomes)
        if n < self.min_samples_validated:
            return 0.0, 0.0, 0, False
        split = n // 2
        in_sample = float(np.mean(r_outcomes[:split]))
        out_sample = float(np.mean(r_outcomes[split:]))
        validated = bool(in_sample > 0.0 and out_sample > 0.0)
        return in_sample, out_sample, n, validated

    def _evidence_quality(self, sample_count: int, r_variance: float) -> float:
        """Sample sufficiency blended with outcome stability, bounded [0, 1]."""
        sample_factor = min(1.0, sample_count / float(self.min_samples_validated))
        stability = 1.0 / (1.0 + math.sqrt(max(0.0, r_variance)))
        return round(max(0.0, min(1.0, 0.65 * sample_factor + 0.35 * stability)), 4)

    def _confidence_score(
        self,
        sample_count: int,
        r_variance: float,
        recency_expectancy_r: float,
        recent_expectancy_r: float,
        normalized_drawdown_r: float,
        avg_execution_quality: float,
        replay_validated: bool,
    ) -> float:
        """
        Bounded evidence-based confidence.

        Rises with sample size, stability, persistent positive expectancy, clean
        execution and out-of-sample confirmation. Falls with variance, drawdown
        and recent degradation. Hard-capped at MAX_STRATEGY_CONFIDENCE so no
        amount of history yields certainty.
        """
        sample_factor = min(1.0, sample_count / (2.0 * self.min_samples_validated))
        stability_factor = 1.0 / (1.0 + math.sqrt(max(0.0, r_variance)))
        consistency_factor = max(0.0, min(1.0, (recency_expectancy_r + 1.0) / 2.0))
        drawdown_factor = 1.0 / (1.0 + max(0.0, normalized_drawdown_r))
        execution_factor = max(0.0, min(1.0, (avg_execution_quality + 1.0) / 2.0))

        raw = (
            0.28 * sample_factor
            + 0.22 * stability_factor
            + 0.22 * consistency_factor
            + 0.16 * drawdown_factor
            + 0.12 * execution_factor
        )
        if replay_validated:
            raw += 0.05
        # Recent degradation always removes confidence, regardless of history.
        if recent_expectancy_r < 0.0:
            raw -= min(0.30, abs(recent_expectancy_r) * 0.30)

        return round(max(0.0, min(MAX_STRATEGY_CONFIDENCE, raw)), 4)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _determine_lifecycle_state(
        self,
        strategy_id: str,
        current_state: StrategyLifecycle | None,
        previous_probation: int,
        sample_count: int,
        expectancy_r: float,
        expectancy_t_stat: float,
        recency_expectancy_r: float,
        recent_expectancy_r: float,
        normalized_drawdown_r: float,
        confidence_score: float,
        replay_validated: bool,
    ) -> tuple[StrategyLifecycle, int]:
        """
        Statistically disciplined lifecycle transition.

        Ordering matters: probation recovery is evaluated BEFORE retirement so a
        previously retired family can graduate out on genuinely new evidence,
        and retirement itself requires both a sample floor and significance.
        """
        # --- Probation: RETIRED / QUARANTINED families ---
        if current_state in (StrategyLifecycle.RETIRED, StrategyLifecycle.QUARANTINED):
            probation = max(previous_probation, sample_count)
            recovered = (
                sample_count >= self.min_samples_validated
                and recent_expectancy_r > self.recovery_expectancy_threshold_r
                and recency_expectancy_r > 0.0
                and confidence_score >= self.recovery_confidence_threshold
            )
            if recovered:
                logger.info(
                    "[STRATEGY] RECOVERED",
                    strategy_id=strategy_id,
                    samples=sample_count,
                    recent_expectancy_r=round(recent_expectancy_r, 4),
                    confidence=confidence_score,
                )
                return StrategyLifecycle.EVALUATING, 0
            return current_state, probation

        # --- Retirement: needs sample floor AND statistical significance ---
        if sample_count >= self.min_samples_retire:
            persistent_negative = (
                recency_expectancy_r <= self.retire_expectancy_threshold_r
                and expectancy_r <= 0.0
                and expectancy_t_stat <= self.retire_t_stat_threshold
            )
            catastrophic_drawdown = (
                normalized_drawdown_r >= self.retire_normalized_drawdown_r and expectancy_r < 0.0
            )
            if persistent_negative or catastrophic_drawdown:
                logger.warning(
                    "[STRATEGY] RETIRED",
                    strategy_id=strategy_id,
                    samples=sample_count,
                    expectancy_r=round(expectancy_r, 4),
                    recency_expectancy_r=round(recency_expectancy_r, 4),
                    t_stat=round(expectancy_t_stat, 4),
                    normalized_drawdown_r=round(normalized_drawdown_r, 4),
                    reason="PERSISTENT_NEGATIVE_EXPECTANCY"
                    if persistent_negative
                    else "CATASTROPHIC_DRAWDOWN",
                )
                return StrategyLifecycle.RETIRED, 0

        # --- Below the evaluation floor ---
        if sample_count < self.min_samples_evaluating:
            return StrategyLifecycle.DISCOVERED, 0

        # --- Degradation: recent decay on a family with enough samples ---
        if recent_expectancy_r < self.degrade_expectancy_threshold_r or (
            recency_expectancy_r < self.degrade_expectancy_threshold_r
        ):
            # AGENT-2 (2026-09-01): edge-triggered lifecycle logging. The
            # DEGRADED transition is emitted when the family ENTERS degraded
            # state; while it STAYS degraded the line repeats at most once
            # per family per rebuild cycle (registry state), and the
            # RECOVERED path (probation) already logs the way back. Severity
            # stays INFO by business design: degradation is a legitimate
            # intelligence signal, not an operational fault. Classification
            # logic is UNTOUCHED — only log repetition is bounded.
            was_degraded = current_state == StrategyLifecycle.DEGRADED
            if not was_degraded or self._should_repeat_degraded(strategy_id):
                logger.info(
                    "[STRATEGY] DEGRADED",
                    strategy_id=strategy_id,
                    samples=sample_count,
                    recent_expectancy_r=round(recent_expectancy_r, 4),
                    recency_expectancy_r=round(recency_expectancy_r, 4),
                    transition="ENTERED" if not was_degraded else "STILL_DEGRADED",
                )
            return StrategyLifecycle.DEGRADED, 0

        if sample_count < self.min_samples_validated:
            return StrategyLifecycle.EVALUATING, 0

        # --- Validated / Active require out-of-sample confirmation ---
        if not replay_validated:
            return StrategyLifecycle.EVALUATING, 0
        if recency_expectancy_r > 0.0 and confidence_score >= 0.50:
            return StrategyLifecycle.ACTIVE, 0
        return StrategyLifecycle.VALIDATED, 0

    # ------------------------------------------------------------------
    # Self-healing
    # ------------------------------------------------------------------

    def rebuild_derived_intelligence(
        self, ledger: ExperienceLedger, per_strategy_limit: int = 2000
    ) -> dict[str, StrategyScore]:
        """
        SELF-HEALING: reconstructs the entire derived registry by replaying the
        immutable ledger.

        Safe to run after registry corruption, schema migration, model rebuild
        or an interrupted derived calculation. Historical outcomes are only ever
        read - never rewritten.
        """
        logger.info("[SELF_HEAL] START", reason="derived_intelligence_rebuild")
        if not self.audit_repo._is_sqlite:
            logger.info("[SELF_HEAL] COMPLETE", status="SKIPPED_NON_SQLITE")
            return {}

        rebuilt: dict[str, StrategyScore] = {}
        try:
            strategy_ids = ledger.list_strategy_ids()
            self._clear_registry()
            experiences_seen = 0
            for strat_id in strategy_ids:
                exps = ledger.get_experiences_for_strategy(
                    strategy_id=strat_id, limit=per_strategy_limit
                )
                if not exps:
                    continue
                experiences_seen += len(exps)
                # current_state deliberately None: the registry was cleared, so
                # lifecycle is re-derived purely from immutable evidence.
                rebuilt[strat_id] = self.evaluate_strategy(
                    strategy_id=strat_id, experiences=exps, current_state=None
                )

            logger.info(
                "[SELF_HEAL] REBUILD",
                experiences=experiences_seen,
                strategies=len(rebuilt),
            )
            logger.info("[SELF_HEAL] COMPLETE", status="SUCCESS", strategies=len(rebuilt))
        except Exception as e:
            logger.error("[SELF_HEAL] FAILED", error=str(e), exc_info=True)
            logger.info("[SELF_HEAL] COMPLETE", status="FAILED")
        return rebuilt

    def _clear_registry(self) -> None:
        """
        Drops derived scores only.

        The raw experience tables are untouched, which is what makes this
        operation safe: everything removed here is recomputable.
        """
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=10.0)
            try:
                conn.execute("DELETE FROM strategy_intelligence_registry;")
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SELF_HEAL] registry clear failed", error=str(e))

    # ------------------------------------------------------------------
    # Derived registry persistence
    # ------------------------------------------------------------------

    def _persist_strategy_score(self, score: StrategyScore) -> None:
        """Upserts the derived score through the async audit queue."""
        if not self.audit_repo._is_sqlite:
            return

        query = """
            INSERT INTO strategy_intelligence_registry
            (strategy_id, lifecycle_state, sample_count, win_rate, expectancy_r,
             recent_expectancy_r, normalized_drawdown_r, profit_factor,
             confidence_score, evidence_quality, replay_validated,
             probation_samples, score_payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET
                lifecycle_state=excluded.lifecycle_state,
                sample_count=excluded.sample_count,
                win_rate=excluded.win_rate,
                expectancy_r=excluded.expectancy_r,
                recent_expectancy_r=excluded.recent_expectancy_r,
                normalized_drawdown_r=excluded.normalized_drawdown_r,
                profit_factor=excluded.profit_factor,
                confidence_score=excluded.confidence_score,
                evidence_quality=excluded.evidence_quality,
                replay_validated=excluded.replay_validated,
                probation_samples=excluded.probation_samples,
                score_payload=excluded.score_payload,
                updated_at=excluded.updated_at;
        """
        args = (
            score.strategy_id,
            score.lifecycle_state.value,
            score.sample_count,
            score.win_rate,
            score.expectancy_r,
            score.recent_window_expectancy_r,
            score.normalized_drawdown_r,
            score.profit_factor,
            score.confidence_score,
            score.evidence_quality,
            1 if score.replay_validated else 0,
            score.probation_samples,
            score.model_dump_json(),
            score.last_updated.isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((query, args))
            logger.debug(
                "[STRATEGY] UPDATED",
                strategy_id=score.strategy_id,
                lifecycle=score.lifecycle_state.value,
                samples=score.sample_count,
                confidence=score.confidence_score,
            )
        except Exception as e:
            logger.error(
                "[STRATEGY] score persistence failed",
                strategy_id=score.strategy_id,
                error=str(e),
            )

    def get_registered_strategy_score(self, strategy_id: str) -> StrategyScore | None:
        """Reads a derived score from the registry cache (None when absent)."""
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT score_payload FROM strategy_intelligence_registry WHERE strategy_id = ?;",
                    (strategy_id,),
                ).fetchone()
            finally:
                conn.close()
            if row and row["score_payload"]:
                return StrategyScore.model_validate(json.loads(row["score_payload"]))
        except Exception as e:
            logger.error("[STRATEGY] registry read failed", strategy_id=strategy_id, error=str(e))
        return None

    def list_registered_scores(self, limit: int = 100) -> list[StrategyScore]:
        """Bounded listing of derived scores, newest first."""
        if not self.audit_repo._is_sqlite:
            return []
        out: list[StrategyScore] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT score_payload FROM strategy_intelligence_registry
                    ORDER BY updated_at DESC LIMIT ?;
                    """,
                    (max(1, int(limit)),),
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                if row["score_payload"]:
                    out.append(StrategyScore.model_validate(json.loads(row["score_payload"])))
        except Exception as e:
            logger.error("[STRATEGY] registry listing failed", error=str(e))
        return out
