"""Model Governance — package entry (no execution imports by contract)."""

from __future__ import annotations

from nexus_scalp.governance.engine import ModelGovernanceEngine
from nexus_scalp.governance.load_gate import (
    ModelLoadGate,
    evaluate_load_gate,
    read_manifest_file,
    read_registry_lifecycle,
)
from nexus_scalp.governance.models import (
    CalibrationBucket,
    DriftAlert,
    GovernanceErrorCode,
    GovernanceEvent,
    GovernanceStage,
    LoadGateResult,
    LoadGateStep,
    PromotionState,
    PromotionTransition,
    RegistryCategory,
    RegistryModel,
    ShadowParity,
)
from nexus_scalp.governance.reporting import build_governance_report, model_shadow_update_text
from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime
from nexus_scalp.governance.store import GovernanceStore

__all__ = [
    "CalibrationBucket",
    "DriftAlert",
    "GovernanceErrorCode",
    "GovernanceEvent",
    "GovernanceShadowRuntime",
    "GovernanceStage",
    "GovernanceStore",
    "LoadGateResult",
    "LoadGateStep",
    "ModelGovernanceEngine",
    "ModelLoadGate",
    "PromotionState",
    "PromotionTransition",
    "RegistryCategory",
    "RegistryModel",
    "ShadowParity",
    "build_governance_report",
    "evaluate_load_gate",
    "model_shadow_update_text",
    "read_manifest_file",
    "read_registry_lifecycle",
]
