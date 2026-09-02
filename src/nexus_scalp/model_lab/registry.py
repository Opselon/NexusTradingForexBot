"""MODEL LAB — experiment configuration + registry (CHG-0047).

Explicit configuration for every lab experiment (no hard-coded research
settings) and a JSON registry with the lab-only lifecycle vocabulary:

    CREATED -> TRAINING -> COMPLETED -> FAILED
                                     -> REJECTED
                                     -> VALIDATED
                                     -> PROMOTION_CANDIDATE

There is deliberately NO "PRODUCTION_ACTIVE" state: promotion is out of
scope for the lab and handled by a future promotion agent via the handoff
manifest (see lab/manifests.py).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LAB_ROOT = Path("artifacts/models/research")
REGISTRY_PATH = LAB_ROOT / "registry.json"


class LabStatus(StrEnum):
    """Lab lifecycle vocabulary — production states are unreachable here."""

    CREATED = "CREATED"
    TRAINING = "TRAINING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    VALIDATED = "VALIDATED"
    PROMOTION_CANDIDATE = "PROMOTION_CANDIDATE"


class ExperimentSpec(BaseModel):
    """One bounded, fully-explicit lab experiment (no hidden defaults)."""

    model_config = ConfigDict(frozen=True)

    experiment_id: str
    question: str = ""
    hypothesis: str = ""

    model_family: str = (
        "LEGACY_SCALPNET_V1"  # | MLP_V2 | TCN_ATTENTION_V1 | TEACHER_TCN_ATTN | STUDENT_MLP
    )
    input_dimension: int = 70
    num_classes: int = 3  # NO_TRADE/BUY/SELL — the verified trained contract
    class_order: list[str] = Field(default_factory=lambda: ["NO_TRADE", "BUY", "SELL"])
    sequence_length: int = 1  # 1 = single timestep (contextual); >1 = causal window

    learning_rate: float = 5e-4
    batch_size: int = 256
    epochs: int = 10
    weight_decay: float = 0.0
    seed: int = 42

    # imbalance recipe knobs (mirroring the production trainer defaults)
    focal_gamma: float = 2.0
    label_smoothing: float = 0.08
    active_class_boost: float = 3.0
    oversample_ratio: float = 0.85

    # distillation knobs (teacher experiments only)
    distill_temperature: float = 2.0
    distill_weight: float = 0.5

    # calibration / evaluation
    calibration_method: str = "none"  # none | temperature
    purge_gap_bars: int = 15
    embargo_bars: int = 3
    time_budget_sec: int = 1800

    dataset_id: str = ""
    notes: str = ""

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _registry_read() -> dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"updated_at": _now(), "experiments": {}, "models": {}}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _registry_write(reg: dict[str, Any]) -> None:
    reg["updated_at"] = _now()
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=1, default=str), encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def register_experiment(spec: ExperimentSpec, git_revision: str) -> str:
    """Registers one experiment. Idempotent on experiment_id."""
    reg = _registry_read()
    reg["experiments"][spec.experiment_id] = {
        "status": LabStatus.CREATED.value,
        "spec_fingerprint": spec.fingerprint(),
        "spec": spec.model_dump(mode="json"),
        "git_revision": git_revision,
        "created_at": reg["experiments"].get(spec.experiment_id, {}).get("created_at", _now()),
        "updated_at": _now(),
    }
    _registry_write(reg)
    return spec.experiment_id


def update_status(
    experiment_id: str,
    status: LabStatus,
    *,
    model_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
    artifacts: dict[str, Any] | None = None,
) -> None:
    reg = _registry_read()
    entry = reg["experiments"].get(experiment_id)
    if entry is None:
        raise KeyError(f"experiment {experiment_id} is not registered")
    entry["status"] = LabStatus(status).value
    entry["updated_at"] = _now()
    if model_id:
        entry["model_id"] = model_id
    if metrics is not None:
        entry["metrics"] = metrics
    if error is not None:
        entry["error"] = error
    if artifacts is not None:
        entry["artifacts"] = artifacts
    _registry_write(reg)


def get_experiment(experiment_id: str) -> dict[str, Any] | None:
    return _registry_read()["experiments"].get(experiment_id)


def list_experiments(status: str | None = None) -> list[dict[str, Any]]:
    rows = list(_registry_read()["experiments"].values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return rows
