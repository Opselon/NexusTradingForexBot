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
        """Starts a shadow run (idempotent by run_id).

        CHG-0046 D11: freezes the run identity (git revision, configuration
        version, artifact hashes) at START. Nothing may silently change
        under a live run — the finalize step re-verifies the challenger
        artifact hash and fails the run honestly on drift.
        """
        run_id = run_id or f"shadow_{uuid.uuid4().hex[:12]}"
        if self.active_run_id and self.active_run_id != run_id:
            # complete the previous run first
            self.finish_run()
        self.active_run_id = run_id
        self._decisions = []
        self._started = datetime.now(UTC)
        self._frozen_git_revision = _git_revision()
        self._frozen_configuration_version = str(
            getattr(self, "_configuration_version", "") or ""
        )
        self._frozen_challenger_hash = challenger_ref.artifact_hash or ""
        self._frozen_champion_hash = champion.artifact_hash or ""
        run = ShadowRun(
            run_id=run_id,
            champion=champion,
            challenger=challenger_ref,
            status="RUNNING",
            started_at=self._started,
            git_revision=self._frozen_git_revision,
            configuration_version=self._frozen_configuration_version,
            challenger_artifact_hash=self._frozen_challenger_hash,
            champion_artifact_hash=self._frozen_champion_hash,
        )
        self.store.save_run(run)
        logger.info(
            "[SHADOW] event=START",
            run_id=run_id,
            champion=f"{champion.model_id}@{champion.model_version}",
            challenger=f"{challenger_ref.model_id}@{challenger_ref.model_version}",
            git_revision=run.git_revision,
            challenger_hash=run.challenger_artifact_hash,
        )
        return run_id

    def attach_challenger(self, runtime: ChallengerRuntime | None) -> None:
        """Attaches the shadow runtime; None disables shadow recording."""
        self.active_challenger = runtime

    def finish_run(self, status: str = "COMPLETED", error: str = "") -> None:
        """Completes the active run and persists the aggregated comparison.

        CHG-0046 D11: the challenger's LIVE artifact hash is re-derived and
        compared against the frozen run identity. A replaced artifact fails
        the run (FAILED/ARTIFACT_REPLACED) — historical evidence is never
        silently re-attributed to a different model. Legacy runs (frozen
        hash empty) cannot drift-check and are marked honestly.
        """
        if not self.active_run_id:
            return
        chal_ref = (
            self.active_challenger.ref
            if self.active_challenger and self.active_challenger.ref
            else ShadowModelRef(model_id="", model_version="")
        )
        frozen_hash = str(getattr(self, "_frozen_challenger_hash", "") or "")
        live_hash = chal_ref.artifact_hash or ""
        if status == "COMPLETED":
            if frozen_hash and live_hash and live_hash != frozen_hash:
                status = "FAILED"
                error = (
                    f"ARTIFACT_REPLACED: frozen={frozen_hash} live={live_hash} "
                    "— run evidence invalid for promotion"
                )
                logger.error("[SHADOW] event=RUN_INVALIDATED", run_id=self.active_run_id)
            elif frozen_hash and not live_hash:
                status = "COMPLETED"
                error = "ARTIFACT_UNVERIFIED: challenger ref lost before finalize"
            elif not frozen_hash:
                error = error or "IDENTITY_NOT_RECORDED (legacy run: no frozen artifact hash)"
        run = ShadowRun(
            run_id=self.active_run_id,
            champion=self._champion_ref() or ShadowModelRef(model_id="", model_version=""),
            challenger=chal_ref,
            status=status,
            started_at=self._run_started_at(),
            finished_at=datetime.now(UTC),
            decision_count=len(self._decisions),
            error=error,
            git_revision=str(getattr(self, "_frozen_git_revision", "") or ""),
            configuration_version=str(getattr(self, "_frozen_configuration_version", "") or ""),
            challenger_artifact_hash=frozen_hash,
            champion_artifact_hash=str(getattr(self, "_frozen_champion_hash", "") or ""),
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
        champion_entry: float = 0.0,
        champion_sl: float = 0.0,
        champion_tp: float = 0.0,
        shadow_entry: float = 0.0,
        shadow_sl: float = 0.0,
        shadow_tp: float = 0.0,
        spread_usd: float = 0.0,
    ) -> ShadowDecisionRecord | None:
        """
        Records one parallel Champion/Champion decision using the SAME live
        feature vector. Returns the record, or None when shadow is disabled.

        This function NEVER executes anything: the Challenger output is a
        hypothetical proposal only.

        CHG-0046: actions are normalized onto the canonical vocabulary
        (BUY/SELL/NO_TRADE/WAIT) BEFORE any comparison — the policy emits
        BUY/SELL while model argmax emits BUY_MARKET/SELL_MARKET, and raw
        string equality fabricated disagreements (D2). Risk geometry for
        BOTH sides is captured at record time (D3): the outcome resolver
        fills hypothetical_r / mfe_r / mae_r afterwards from certified
        ticks; nothing here invents outcomes.
        """
        if self.active_challenger is None or not self.active_run_id:
            return None

        from nexus_scalp.shadow.compat import normalize_action

        runtime = self.active_challenger
        champ_action_canonical = normalize_action(champion_action)
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
            champion_action=champ_action_canonical,
            champion_confidence=champion_confidence,
            champion_probabilities=champion_probabilities,
            champion_strategy_id=champion_strategy_id,
            challenger_action=normalize_action(challenger_action),
            challenger_confidence=challenger_conf,
            challenger_probabilities=challenger_probs,
            action_agreement=(
                valid and champ_action_canonical == normalize_action(challenger_action)
            ),
            valid_comparison=valid,
            invalid_reason=invalid_reason,
            champion_entry=champion_entry,
            champion_sl=champion_sl,
            champion_tp=champion_tp,
            shadow_entry=shadow_entry,
            shadow_sl=shadow_sl,
            shadow_tp=shadow_tp,
            spread_usd=spread_usd,
            outcome_status="PENDING",  # resolved ONLY by the certified tick resolver
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


def _git_revision() -> str:
    """Best-effort git revision for run-freeze identity ('' = NOT_RECORDED).

    Never raises: identity capture must never disturb the run lifecycle.
    Prefers `git rev-parse` (3s timeout); falls back to reading the HEAD
    ref file directly (frozen/EXE layouts without git installed).
    """
    import subprocess
    from pathlib import Path

    try:
        head = Path(__file__).resolve()
        for parent in head.parents:
            git_head = parent / ".git" / "HEAD"
            if git_head.exists():
                try:
                    out = subprocess.run(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=str(parent),
                        capture_output=True,
                        text=True,
                        timeout=3,
                        check=False,
                    )
                    if out.returncode == 0 and out.stdout.strip():
                        return out.stdout.strip()[:12]
                except Exception:
                    pass
                ref = git_head.read_text(encoding="utf-8", errors="replace").strip()
                if ref.startswith("ref:"):
                    ref_path = parent / ".git" / ref[4:].strip()
                    if ref_path.exists():
                        return ref_path.read_text(encoding="utf-8", errors="replace").strip()[:12]
                return ref[:12]
    except Exception:
        pass
    return ""


def current_evidence(engine: ShadowEngine) -> dict[str, Any]:
    return {
        "run_id": engine.active_run_id,
        "decisions": len(engine._decisions),
        "challenger_loaded": engine.active_challenger is not None,
        "last_comparison_run": engine.last_comparison.run_id if engine.last_comparison else "",
    }
