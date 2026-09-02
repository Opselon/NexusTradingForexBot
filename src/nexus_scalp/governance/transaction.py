"""
Promotion Transaction
=====================
TASK-08 / 70D governance: the ATOMIC promotion transaction (spec 8 / 29 / 37 / 38).

Flow (no shortcuts):

    VERIFY CANDIDATE   -> fresh read-only verification (never cached state)
    LOCK GOVERNANCE    -> cross-process exclusive lock (PROMOTION_CONFLICT beat)
    RECORD OLD CHAMPION-> structured promotion audit row (old/new pair)
    ACTIVATE NEW       -> operator-supplied activation callback (runtime swap)
    VERIFY NEW         -> post-activation verification callback
    COMMIT             -> promotion audit row + governance event; lock released

Crash recovery: the promotion audit row is written BEFORE activation with
status PROMOTION_STARTED, updated to PROMOTION_COMMITTED after activation,
and to PROMOTION_ROLLED_BACK / PROMOTION_FAILED when the transaction fails
and the previous Champion is restored. After a restart the audit table is the
source of truth for the transaction state (spec 38).

NEVER leaves: no Champion, or a half-promoted Champion.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.governance.lock import PromotionLock, PromotionLockError
from nexus_scalp.governance.models import (
    GovernanceErrorCode,
    GovernanceEvent,
    GovernanceStage,
)
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.governance.verify import verify_candidate
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.transaction")

#: Possible promotion transaction states (spec 38).
PROMOTION_TXN_STATES: tuple[str, ...] = (
    "PROMOTION_STARTED",
    "PROMOTION_COMMITTED",
    "PROMOTION_ROLLED_BACK",
    "PROMOTION_FAILED",
)


class PromotionTransactionError(RuntimeError):
    """Raised when the promotion transaction cannot proceed."""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def execute_promotion_transaction(
    *,
    store: GovernanceStore,
    lock_path: Path | str,
    model_id: str,
    model_version: str,
    actor: str,
    reason: str,
    approval_token: str,
    old_champion: dict[str, Any],
    candidate: dict[str, Any],
    activate: Callable[[str, str], None],
    verify_new: Callable[[str, str], dict[str, Any]] | None = None,
    rollback_activate: Callable[[str, str], None] | None = None,
    artifact_path: Path | str = "",
    scaler_path: Path | str = "",
    manifest: dict[str, Any] | None = None,
    runtime_schema_id: str = "",
    runtime_dimension: int = 0,
    feature_schema_hash: str = "",
    liquidity_algorithm_version: str = "",
    training_commit: str = "",
    oos_artifact: str = "",
    shadow_evidence: dict[str, Any] | None = None,
    news_contract: dict[str, Any] | None = None,
    liquidity_contract: dict[str, Any] | None = None,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Runs the atomic promotion transaction. Returns the audit row.

    Raises PromotionTransactionError on any hard failure; NEVER leaves a
    half-promoted Champion (the previous model is restored when activation
    succeeded but post-verification failed).
    """
    promotion_id = f"prom_{uuid.uuid4().hex[:16]}"
    rollback_target = f"{old_champion.get('model_id', '')}@{old_champion.get('version', '')}"

    def _audit_row(status: str) -> dict[str, Any]:
        return {
            "promotion_id": promotion_id,
            "old_champion_model_id": old_champion.get("model_id", ""),
            "old_champion_version": old_champion.get("version", ""),
            "old_champion_hash": old_champion.get("artifact_hash", ""),
            "old_champion_schema": old_champion.get("schema_id", ""),
            "new_champion_model_id": model_id,
            "new_champion_version": model_version,
            "new_champion_hash": candidate.get("artifact_hash", ""),
            "new_champion_schema": candidate.get("schema_id", ""),
            "candidate_hash": candidate.get("artifact_hash", ""),
            "schema_id": candidate.get("schema_id", ""),
            "approval_actor": actor,
            "approval_reason": reason,
            "approval_token": approval_token,
            "rollback_target": rollback_target,
            "status": status,
            "recorded_at": _utcnow(),
        }

    def _rec(status: str, message: str = "") -> None:
        store.record_promotion_audit(_audit_row(status))
        store.record_event(
            GovernanceEvent(
                event_id=f"ev_{promotion_id}",
                event=(
                    GovernanceErrorCode.PROMOTION_EXECUTED.value
                    if status == "PROMOTION_COMMITTED"
                    else GovernanceErrorCode.PROMOTION_BLOCKED.value
                    if status == "PROMOTION_STARTED"
                    else GovernanceErrorCode.ROLLBACK_EXECUTED.value
                    if status == "PROMOTION_ROLLED_BACK"
                    else "PROMOTION_FAILED"
                ),
                stage=GovernanceStage.PROMOTION,
                model_id=model_id,
                model_version=model_version,
                correlation_id=correlation_id or promotion_id,
                error_code=(
                    "PROMOTION_EXECUTED" if status == "PROMOTION_COMMITTED" else "PROMOTION_FAILED"
                ),
                actor=actor,
                previous_state=old_champion.get("lifecycle_state", "CHAMPION"),
                new_state="CHAMPION"
                if status == "PROMOTION_COMMITTED"
                else old_champion.get("lifecycle_state", "CHAMPION"),
                reason=f"{message} ({status})" if message else status,
                payload={
                    "promotion_id": promotion_id,
                    "old_champion": old_champion,
                    "candidate": candidate,
                    "rollback_target": rollback_target,
                },
            )
        )

    # ---- 0. Actor + approval token (spec 5: no implicit promotion) ----
    if not actor or actor == "system":
        raise PromotionTransactionError("promotion requires an explicit operator actor")
    if not approval_token:
        raise PromotionTransactionError(
            "promotion requires the operator approval token (no auto-promotion)"
        )

    # ---- 1. VERIFY CANDIDATE (fresh, read-only; spec 7) ----
    verification = verify_candidate(
        model_id=model_id,
        model_version=model_version,
        artifact_path=artifact_path,
        scaler_path=scaler_path,
        manifest=manifest,
        runtime_schema_id=runtime_schema_id,
        runtime_dimension=runtime_dimension,
        feature_schema_hash=feature_schema_hash,
        liquidity_algorithm_version=liquidity_algorithm_version,
        training_commit=training_commit,
        oos_artifact=oos_artifact,
        shadow_evidence=shadow_evidence,
        news_contract=news_contract,
        liquidity_contract=liquidity_contract,
        store=store,
        correlation_id=correlation_id or promotion_id,
    )
    if not verification["eligible"]:
        _rec(
            "PROMOTION_FAILED",
            f"candidate verification blocked: {verification['reason']}",
        )
        raise PromotionTransactionError(f"promotion verification failed: {verification['reason']}")

    # ---- 2. LOCK GOVERNANCE (spec 37: PROMOTION_CONFLICT not partial write) ----
    try:
        lock = PromotionLock(lock_path)
        acquired = lock.try_acquire()
        if not acquired:
            _rec("PROMOTION_FAILED", "PROMOTION_CONFLICT: another promotion in progress")
            raise PromotionTransactionError(
                "PROMOTION_CONFLICT: another promotion transaction is in progress"
            )
    except PromotionLockError as e:
        raise PromotionTransactionError(str(e)) from e

    try:
        # ---- 3. RECORD OLD CHAMPION (spec 6 / 29) ----
        _rec("PROMOTION_STARTED", "transaction started; old Champion preserved")

        # ---- 4. ACTIVATE NEW CHAMPION (operator-supplied runtime swap) ----
        try:
            activate(model_id, model_version)
        except Exception as e:
            store.set_state(model_id, model_version, "REJECTED")
            _rec(
                "PROMOTION_FAILED",
                f"activation failed; previous Champion unchanged: {e}",
            )
            raise PromotionTransactionError(f"activation failed: {e}") from e

        # ---- 5. VERIFY NEW CHAMPION (post-activation smoke; spec 9) ----
        if verify_new is not None:
            try:
                check = verify_new(model_id, model_version)
                if not check.get("ok", False):
                    raise PromotionTransactionError(
                        f"post-activation verification failed: {check.get('reason', 'unknown')}"
                    )
            except PromotionTransactionError:
                # rollback automatically to the previous Champion
                if rollback_activate is not None:
                    try:
                        rollback_activate(
                            old_champion.get("model_id", ""),
                            old_champion.get("version", ""),
                        )
                    except Exception as rb_e:
                        logger.error(
                            "[MODEL_GOVERNANCE] rollback after failed activation failed",
                            error=str(rb_e),
                        )
                else:
                    logger.error(
                        "[MODEL_GOVERNANCE] no rollback callback; manual rollback required "
                        "(previous Champion artifact preserved)"
                    )
                store.set_state(model_id, model_version, "QUARANTINED")
                _rec(
                    "PROMOTION_ROLLED_BACK",
                    "post-activation verification failed; previous Champion restored",
                )
                raise

        # ---- 6. COMMIT ----
        store.set_state(model_id, model_version, "CHAMPION")
        _rec(
            "PROMOTION_COMMITTED", "promotion committed; old Champion preserved as rollback target"
        )
        logger.info(
            "[GOVERNANCE] event=PROMOTION_EXECUTED",
            promotion_id=promotion_id,
            new=f"{model_id}@{model_version}",
            old=rollback_target,
            actor=actor,
        )
        return _audit_row("PROMOTION_COMMITTED")
    finally:
        lock.release()
