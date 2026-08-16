"""
Shadow Engine
=============
PHASE 11 wires the runtime, comparer, and store into one bounded engine
(spec 4 / 5 / 6 / 21).

The ShadowEngine is the ONLY entry point the LiveEngine uses to record a
shadow decision. It guarantees:
  * same-input integrity (the champion's live feature hash is stamped),
  * schema-safety (a Challenger with an incompatible schema is never used),
  * zero order authority (this module imports no adapter/order manager/risk),
  * every recorded decision is flagged simulated=True.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.challenger import ChallengerRuntime
from nexus_scalp.shadow.comparison import ShadowComparer
from nexus_scalp.shadow.models import (
    PromotionEvaluation,
    ShadowComparison,
    ShadowDecisionRecord,
    ShadowModelRef,
    ShadowRun,
    SharedInputRef,
)
from nexus_scalp.shadow.store import ShadowStore

logger = get_logger("nexus_scalp.shadow.engine")


class ShadowEngine:
    """
    Bounded shadow evaluation engine (no execution capability).
    """

    def __init__(
        self,
        store: ShadowStore,
        comparer: ShadowComparer | None = None,
    ) -> None:
        self.store = store
        self.comparer = comparer or ShadowComparer()
        self.active_challenger: ChallengerRuntime | None = None
        self.active_run_id: str = ""
        self._decisions: list[ShadowDecisionRecord] = []
        self.last_comparison: ShadowComparison | None = None
        self.last_promotion: PromotionEvaluation | None = None

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        run_id: str | None,
        champion: ShadowModelRef,
        challenger_ref: ShadowModelRef,
    ) -> str:
        """Starts a shadow run (idempotent by run_id)."""
        run_id = run_id or f"shadow_{uuid.uuid4().hex[:12]}"
        if self.active_run_id and self.active_run_id != run_id:
            # complete the previous run first
            self.finish_run()
        self.active_run_id = run_id
        self._decisions = []
        run = ShadowRun(
            run_id=run_id,
            champion=champion,
            challenger=challenger_ref,
            status="RUNNING",
            started_at=datetime.now(UTC),
        )
        self.store.save_run(run)
        logger.info(
            "[SHADOW] event=START",
            run_id=run_id,
            champion=f"{champion.model_id}@{champion.model_version}",
            challenger=f"{challenger_ref.model_id}@{challenger_ref.model_version}",
        )
        return run_id

    def attach_challenger(self, runtime: ChallengerRuntime | None) -> None:
        """Attaches the shadow runtime; None disables shadow recording."""
        self.active_challenger = runtime

    def finish_run(self, status: str = "COMPLETED", error: str = "") -> None:
        """Completes the active run and persists the aggregated comparison."""
        if not self.active_run_id:
            return
        run = ShadowRun(
            run_id=self.active_run_id,
            champion=self._champion_ref() or ShadowModelRef(model_id="", model_version=""),
            challenger=self.active_challenger.ref
            if self.active_challenger and self.active_challenger.ref
            else ShadowModelRef(model_id="", model_version=""),
            status=status,
            started_at=self._run_started_at(),
            finished_at=datetime.now(UTC),
            decision_count=len(self._decisions),
            error=error,
        )
        self.store.save_run(run)
        if self._decisions and self.active_challenger and self.active_challenger.ref:
            comparison = self.comparer.compare(
                self._decisions,
                run_id=self.active_run_id,
                champion=self._champion_ref() or ShadowModelRef(model_id="", model_version=""),
                challenger=self.active_challenger.ref,
            )
            self.last_comparison = comparison
            self.store.save_comparison(comparison)
            logger.info(
                "[SHADOW] event=RESULT",
                run_id=self.active_run_id,
                expectancy=comparison.challenger_expectancy_r,
                drawdown=comparison.challenger_drawdown_r,
                samples=comparison.samples_observed,
            )
        self.active_run_id = ""

    # ------------------------------------------------------------------
    # Decision recording (the only live-path entry point)
    # ------------------------------------------------------------------

    def record_shadow_decision(
        self,
        *,
        timestamp: datetime,
        symbol: str,
        timeframe: str,
        feature_hash: str,
        feature_schema_id: str,
        feature_dimension: int,
        regime: str,
        session: str,
        configuration_version: str,
        champion_ref: ShadowModelRef,
        champion_action: str,
        champion_confidence: float,
        champion_probabilities: list[float],
        champion_strategy_id: str,
        decision_id: str = "",
        feature_vector: list[float] | None = None,
    ) -> ShadowDecisionRecord | None:
        """
        Records one parallel Champion/Challenger decision using the SAME live
        feature vector. Returns the record, or None when shadow is disabled.

        This function NEVER executes anything: the Challenger output is a
        hypothetical proposal only.
        """
        if self.active_challenger is None or not self.active_run_id:
            return None

        runtime = self.active_challenger
        shared_input = SharedInputRef(
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            feature_hash=feature_hash,
            feature_schema_id=feature_schema_id,
            feature_dimension=feature_dimension,
            regime=regime,
            session=session,
            configuration_version=configuration_version,
        )

        # Schema-safety: challenger schema must match the live schema.
        valid = True
        invalid_reason = ""
        if (
            runtime.ref is None
            or runtime.ref.feature_schema_id != feature_schema_id
            or runtime.ref.feature_dimension != feature_dimension
        ):
            valid = False
            invalid_reason = (
                f"challenger schema {runtime.ref.feature_schema_id if runtime.ref else '?'}/"
                f"{runtime.ref.feature_dimension if runtime.ref else '?'}D != live "
                f"{feature_schema_id}/{feature_dimension}D"
            )

        # Run challenger inference on the SAME feature vector - but we need the
        # actual feature values; the caller passes them via context. For the
        # live hook we accept the vector separately.
        challenger_action = "N/A"
        challenger_conf = 0.0
        challenger_probs: list[float] = []
        if valid and feature_vector is not None:
            try:
                result = runtime.infer(feature_vector)
                challenger_action = result["action"]
                challenger_conf = result["confidence"]
                challenger_probs = result["probabilities"]
            except Exception as e:
                valid = False
                invalid_reason = f"challenger inference failed: {e}"
                logger.error("[CHALLENGER] event=INFERENCE_FAILED", error=str(e))
        elif valid:
            invalid_reason = "feature vector not supplied for shadow comparison"
            valid = False

        decision = ShadowDecisionRecord(
            shadow_decision_id=f"sd_{uuid.uuid4().hex[:16]}",
            run_id=self.active_run_id,
            decision_id=decision_id,
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            champion=champion_ref,
            challenger=runtime.ref or ShadowModelRef(model_id="", model_version=""),
            shared_input=shared_input,
            champion_action=champion_action,
            champion_confidence=champion_confidence,
            champion_probabilities=champion_probabilities,
            champion_strategy_id=champion_strategy_id,
            challenger_action=challenger_action,
            challenger_confidence=challenger_conf,
            challenger_probabilities=challenger_probs,
            action_agreement=(valid and champion_action == challenger_action),
            valid_comparison=valid,
            invalid_reason=invalid_reason,
            hypothetical_r=0.0,  # resolved on exit simulation
        )
        self._decisions.append(decision)
        self.store.save_decision(decision)
        logger.info(
            "[SHADOW] event=DECISION",
            timestamp=timestamp.isoformat(),
            champion_action=champion_action,
            challenger_action=challenger_action,
            agreement=decision.action_agreement,
        )
        return decision

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _champion_ref(self) -> ShadowModelRef | None:
        return self._champion if hasattr(self, "_champion") else None

    _champion: ShadowModelRef | None = None

    def set_champion_ref(self, ref: ShadowModelRef) -> None:
        self._champion = ref

    def _run_started_at(self) -> datetime:
        return self._started if hasattr(self, "_started") else datetime.now(UTC)

    _started: datetime = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Aggregation endpoints for the worker / API
    # ------------------------------------------------------------------

    def current_evidence(self) -> dict[str, Any]:
        return {
            "run_id": self.active_run_id,
            "decisions": len(self._decisions),
            "challenger_loaded": self.active_challenger is not None,
            "last_comparison_run": self.last_comparison.run_id if self.last_comparison else "",
        }
