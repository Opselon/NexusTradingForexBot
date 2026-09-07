"""
LearningCycle-driven orchestrator glue (Learning-Loop Closure, Phases 5/7/8).
============================================================================

Bridges the LearningCycle state machine to the existing controlled pipeline:

    LearningCycleOrchestrator.run_cycle(trigger, ...)
      DATASET_BUILDING -> persist snapshot (dataset_snapshot) -> DATASET_READY
      TRAINING         -> ModelLifecycleOrchestrator.run_controlled_training
      TRAINED/VALIDATING/VALIDATED (gates ran inside controlled training)
      SHADOW_ATTACHING -> auto-handoff IF config.learning.shadow.enabled
      otherwise        -> COMPLETED (challenger waits for operator attach)

Handoff safeguards (Phase 7), all verified BEFORE SHADOW_ATTACHING:
  artifact exists + hash match + model contract (via inspect_artifact) +
  registry CHALLENGER status + no already-attached challenger + no
  conflicting active cycle + champion identity + promotion policy.
Any failure -> cycle BLOCKED with the exact reason persisted.

Everything here is DISABLED unless config.learning enables it (fail-closed);
the orchestrator takes an explicit `LearningConfig` and refuses to run when
`enabled` is False.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.model_generation.artifact_store import sha256_file
from nexus_scalp.model_lifecycle.dataset_snapshot import (
    DatasetSnapshotError,
    TrainingDatasetSnapshotStore,
)
from nexus_scalp.model_lifecycle.learning_config import LearningConfig
from nexus_scalp.model_lifecycle.learning_cycle import (
    LearningCycleStore,
)
from nexus_scalp.model_lifecycle.orchestrator import ModelLifecycleOrchestrator
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.learning_loop")


class HandoffBlockedError(RuntimeError):
    """Raised when a handoff safeguard fails (reason carried)."""


def _resolve_cycle_store_db_path(audit_repo: AuditRepository) -> str:
    """Resolve the LearningCycleStore SQLite path from the audit repo.

    The store is a state machine over the CANONICAL audit.db, so production
    passes the real ``audit_repo._db_path``. Test doubles (``audit_repo =
    MagicMock()``) make ``_db_path`` a MagicMock whose ``str()`` is not a
    valid filesystem path, and ``sqlite3.connect`` then dies with
    ``OperationalError: unable to open database file`` (observed 2026-09-07:
    13 red in tests/unit/test_htf_warmup_gate.py). Fall back to the shared
    in-memory convention (same as AuditRepository's ``:memory:`` handling)
    whenever the attribute is not a plain string path — the cycle store is
    process-local state, so in-memory stays hermetic and never touches the
    production artifacts/audit.db (BUG-223 rule).
    """
    raw = getattr(audit_repo, "_db_path", None)
    if isinstance(raw, str) and raw.strip():
        return raw
    return ":memory:"


class LearningCycleOrchestrator:
    """Runs one governed learning cycle end-to-end (no promotion authority)."""

    def __init__(
        self,
        *,
        audit_repo: AuditRepository,
        ledger: ExperienceLedger,
        orchestrator: ModelLifecycleOrchestrator,
        config: LearningConfig | None = None,
        cycle_store: LearningCycleStore | None = None,
        snapshot_store: TrainingDatasetSnapshotStore | None = None,
    ) -> None:
        self.config = config or LearningConfig()  # all-disabled by default
        self.audit_repo = audit_repo
        self.ledger = ledger
        self.orchestrator = orchestrator
        self.cycles = cycle_store or LearningCycleStore(
            _resolve_cycle_store_db_path(audit_repo)
        )
        self.snapshots = snapshot_store or TrainingDatasetSnapshotStore()
        # Restart safety: mark in-flight cycles from a dead process as FAILED.
        self.cycles.recover_interrupted()

    # ------------------------------------------------------------------
    # Trigger gate (Phase 6)
    # ------------------------------------------------------------------

    def should_trigger(self, *, last_cycle_completed_at: float | None = None) -> tuple[bool, str]:
        """Config-driven retrain trigger evaluation (never self-activates).

        Conditions: learning.enabled AND retrain.enabled AND ledger has
        >= min_new_experiences verified experiences AND the interval has
        elapsed (interval is enforced by the worker clock; kept honest here).
        """
        cfg = self.config
        if not cfg.enabled:
            return False, "learning disabled"
        if not cfg.retrain.enabled:
            return False, "retrain disabled"
        total = self.ledger.count_experiences()
        if total < cfg.retrain.min_new_experiences:
            return False, f"insufficient experiences: {total} < {cfg.retrain.min_new_experiences}"
        return True, "eligible"

    # ------------------------------------------------------------------
    # One full cycle
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        *,
        trigger: str = "scheduled",
        trigger_identity: str | None = None,
        include_no_trade: bool = True,
        weight_no_trade: float = 0.25,
        num_epochs: int = 3,
        hyperparameters: dict[str, Any] | None = None,
        shadow_attach: Any = None,
    ) -> dict[str, Any]:
        """Executes DATASET -> TRAINING -> VALIDATION -> (optional) SHADOW.

        shadow_attach: callable(cycle_id, candidate_model_id, artifact_path)
        — the LiveEngine-provided attach implementation (Phase 7/8), invoked
        ONLY when config.learning.shadow.enabled AND every handoff safeguard
        passes. This orchestrator itself has no engine handles (no execution
        capability).
        """
        if not self.config.enabled:
            return {"cycle": None, "blocked": "learning disabled by configuration"}
        identity = trigger_identity or f"{trigger}:{self.ledger.count_experiences()}"
        cycle_id = self.cycles.start_cycle(trigger, identity)
        out: dict[str, Any] = {"cycle_id": cycle_id}

        try:
            # ---- 1. DATASET -------------------------------------------------
            self.cycles.transition(cycle_id, "DATASET_BUILDING", reason="build dataset")
            dataset = self.orchestrator.build_training_dataset(
                include_no_trade=include_no_trade, weight_no_trade=weight_no_trade
            )
            if dataset.sample_count < self.config.retrain.min_new_experiences:
                self.cycles.transition(
                    cycle_id,
                    "BLOCKED",
                    reason=f"INSUFFICIENT_SAMPLES: {dataset.sample_count}",
                    decision="BLOCKED",
                )
                out["blocked"] = "INSUFFICIENT_SAMPLES"
                return out
            handle = self.snapshots.save_snapshot(
                dataset,
                training_config={"include_no_trade": include_no_trade,
                                  "weight_no_trade": weight_no_trade,
                                  "num_epochs": num_epochs,
                                  **(hyperparameters or {})},
                build_identity="learning_cycle",
            )
            self.cycles.transition(
                cycle_id,
                "DATASET_READY",
                dataset_id=dataset.dataset_id,
                dataset_hash=handle["hash"],
                reason=f"snapshot rows={dataset.sample_count}",
            )
            out["dataset_id"] = dataset.dataset_id
            out["dataset_hash"] = handle["hash"]

            # ---- 2. TRAINING (controlled; never touches champion) -----------
            self.cycles.transition(cycle_id, "TRAINING", reason="controlled training")
            result = self.orchestrator.run_controlled_training(
                dataset,
                hyperparameters=hyperparameters or {
                    "num_folds": 5, "epochs_per_fold": 3, "batch_size": 64
                },
                num_epochs=num_epochs,
                build_identity=f"learning_cycle:{cycle_id}",
            )
            run_id = str(result.get("run_id", ""))
            self.cycles.transition(
                cycle_id,
                "TRAINED",
                training_run_id=run_id,
                reason=f"gates_passed={result.get('all_gates_passed')}",
            )
            out["training_run_id"] = run_id
            out["all_gates_passed"] = bool(result.get("all_gates_passed"))

            # ---- 3. VALIDATION bookkeeping (gates ran inside training) ------
            self.cycles.transition(cycle_id, "VALIDATING")
            if not result.get("all_gates_passed"):
                self.cycles.transition(
                    cycle_id,
                    "REJECTED",
                    reason="validation gates failed",
                    decision="REJECT",
                )
                out["decision"] = "REJECTED"
                return out
            self.cycles.transition(
                cycle_id, "VALIDATED", validation_run_id=run_id, reason="all gates passed"
            )

            # ---- 4. SHADOW HANDOFF (Phase 7/8; config-gated) -----------------
            should_attach = shadow_attach is not None and self.config.shadow.enabled
            if not should_attach:
                self.cycles.transition(
                    cycle_id,
                    "COMPLETED",
                    reason="challenger ready; shadow attach disabled (operator path)",
                )
                out["decision"] = "COMPLETED_NO_SHADOW"
                return out

            self.cycles.transition(cycle_id, "SHADOW_ATTACHING", reason="auto handoff")
            candidate_model_id = self._candidate_model_id(result, run_id)
            artifact_path = self._candidate_artifact_path(result)
            self._verify_handoff(
                cycle_id=cycle_id,
                run_result=result,
                candidate_model_id=candidate_model_id,
                artifact_path=artifact_path,
                dataset_hash=handle["hash"],
            )
            try:
                attach = shadow_attach
                assert attach is not None
                attach(cycle_id, candidate_model_id, artifact_path)
            except Exception as attach_err:
                self.cycles.transition(
                    cycle_id,
                    "BLOCKED",
                    reason=f"SHADOW_ATTACH_FAILED: {attach_err}",
                    decision="BLOCKED",
                )
                out["blocked"] = "SHADOW_ATTACH_FAILED"
                return out
            self.cycles.transition(
                cycle_id,
                "SHADOW_RUNNING",
                candidate_model_id=candidate_model_id,
                candidate_artifact_hash=sha256_file(Path(artifact_path)) if artifact_path else "",
                shadow_run_id=cycle_id,
                reason="challenger attached to shadow",
            )
            out["decision"] = "SHADOW_ATTACHED"
            out["candidate_model_id"] = candidate_model_id
            return out

        except HandoffBlockedError as e:
            self.cycles.transition(
                cycle_id, "BLOCKED", reason=str(e), decision="BLOCKED"
            )
            out["blocked"] = str(e)
            return out
        except DatasetSnapshotError as e:
            self.cycles.transition(
                cycle_id, "BLOCKED", reason=f"DATASET_SNAPSHOT: {e}", decision="BLOCKED"
            )
            out["blocked"] = f"DATASET_SNAPSHOT: {e}"
            return out
        except Exception as e:  # failure-isolated: record, never propagate up
            logger.error("[LEARNING_LOOP] cycle failed", cycle_id=cycle_id, error=str(e))
            self.cycles.transition(
                cycle_id, "FAILED", reason=str(e)[:500], error_code="CYCLE_EXCEPTION"
            )
            out["failed"] = str(e)
            return out

    # ------------------------------------------------------------------
    # Handoff safeguards (Phase 7.2)
    # ------------------------------------------------------------------

    def _verify_handoff(
        self,
        *,
        cycle_id: str,
        run_result: dict[str, Any],
        candidate_model_id: str,
        artifact_path: str | Path,
        dataset_hash: str,
    ) -> None:
        """Every check below must pass; first failure raises HandoffBlockedError."""
        from nexus_scalp.model_lifecycle.models import ModelStatus

        p = Path(artifact_path) if artifact_path else None
        if p is None or not p.exists():
            raise HandoffBlockedError("CANDIDATE_ARTIFACT_MISSING")
        artifact_hash = sha256_file(p)
        if not artifact_hash:
            raise HandoffBlockedError("CANDIDATE_HASH_UNVERIFIABLE")
        registry = self.orchestrator.lifecycle_registry
        row: dict[str, Any] | None = registry.get_status(candidate_model_id, candidate_model_id)
        if row is None:
            # candidates register under (model_id, version)=(candidate_<run>, run)
            candidates = registry.list_models(limit=50)
            row = next(
                (r for r in candidates if r.get("training_run_id") == run_result.get("run_id")),
                None,
            )
        if row is None:
            raise HandoffBlockedError("CANDIDATE_NOT_IN_REGISTRY")
        status = str(row.get("lifecycle_status", ""))
        if status not in (ModelStatus.CHALLENGER.value, ModelStatus.CANDIDATE.value):
            raise HandoffBlockedError(f"CANDIDATE_STATUS_INVALID: {status}")
        declared_dim = int(row.get("feature_dimension", 0) or 0)
        if declared_dim <= 0:
            raise HandoffBlockedError("MODEL_CONTRACT_UNRESOLVED")
        champion = self.orchestrator.champion_manager.champion_or_none()
        if champion is None:
            raise HandoffBlockedError("CHAMPION_UNAVAILABLE")
        if self.cycles.active_cycle_for(f"shadow:{candidate_model_id}") is not None:
            raise HandoffBlockedError("CONFLICTING_ACTIVE_CYCLE")
        if not self.config.promotion.enabled and not self.config.shadow.enabled:
            raise HandoffBlockedError("PROMOTION_POLICY_DISABLED")

    def _candidate_model_id(self, result: dict[str, Any], run_id: str) -> str:
        run = result.get("run", {})
        artifacts = run.get("artifacts") or []
        if artifacts:
            return str(artifacts[0].get("model_id") or f"candidate_{run_id}")
        return f"candidate_{run_id}"

    def _candidate_artifact_path(self, result: dict[str, Any]) -> str:
        run = result.get("run", {})
        artifacts = run.get("artifacts") or []
        if artifacts:
            return str(artifacts[0].get("artifact_path") or "")
        return ""

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "cycles": self.cycles.summary(),
            "config": {
                "enabled": self.config.enabled,
                "retrain": self.config.retrain.enabled,
                "shadow": self.config.shadow.enabled,
                "promotion": self.config.promotion.enabled,
                "online_finetune": self.config.online_finetune.enabled,
            },
        }
