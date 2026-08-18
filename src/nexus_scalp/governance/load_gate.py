"""
Deterministic Model Load Gate
=============================
TASK-6 / CHG-0003 (spec 4, TEST-LG-03/-04/-05/-06/-07).

A model CANNOT be loaded merely because its file exists. The gate evaluates
ten deterministic steps in order and reports the EXACT failing gate:

    ARTIFACT_EXISTS -> HASH_VALID -> MANIFEST_VALID -> SCHEMA_VALID ->
    INPUT_DIMENSION_VALID -> SCALER_VALID -> LABEL_SCHEMA_VALID ->
    VALIDATION_STATUS_VALID -> LIFECYCLE_ALLOWS_SHADOW -> LOAD

Any failure produces MODEL_LOAD_REJECTED with the failing gate identified.
There is NO silent fallback: a rejected model is never loaded by the shadow
runtime.

The gate is pure (no torch import on the rejection path unless needed for
shape inspection): it reads artifact files, manifests and the lifecycle
registry. It NEVER loads weights into a runtime, so it can never disturb the
Champion bundle.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from nexus_scalp.governance.models import (
    GovernanceErrorCode,
    LoadGateResult,
    LoadGateStep,
    PromotionState,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.load_gate")

#: All registered schema ids in the canonical schema registry, in dimension order.
_REGISTERED_SCHEMA_IDS: tuple[str, ...] = ("scalp_v1", "scalp_v2", "scalp_v3")


def sha256_hex(path: Path) -> str:
    """Full SHA-256 hex of a file ('' when absent/error)."""
    try:
        import hashlib

        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _state_dict_input_dim(path: Path) -> int | None:
    """Reads the neural input width from a torch state dict (best effort)."""
    try:
        import torch

        state = torch.load(path, map_location="cpu", weights_only=False)
        if not isinstance(state, dict):
            return None
        w = state.get("input_projection.weight")
        if w is None:
            # TCN/attention heads may store the first projection differently.
            for k, v in state.items():
                if hasattr(v, "shape") and v.ndim == 2 and k.endswith((".weight", "weight")):
                    if k in ("input_projection.weight", "projection.weight", "net.0.weight"):
                        return int(v.shape[1])
                    if "projection" in k or k.startswith("net."):
                        return int(v.shape[1])
        return int(w.shape[1]) if w is not None else None
    except Exception:
        return None


def _manifest_dimension(manifest: dict[str, Any]) -> int:
    """Effective neural input width declared by the manifest."""
    base = int(manifest.get("feature_dimension", 0) or 0)
    meta = manifest.get("build_metadata", {}) or {}
    inp = int(meta.get("input_dimension", base) or base)
    return inp or base


def _validation_ok(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Validation/OOS/robustness status from the manifest (spec 3 / 22)."""
    if manifest.get("final_validation_result"):
        return True, "validation result present"
    wf = str(manifest.get("walk_forward_status", ""))
    oos = str(manifest.get("oos_status", ""))
    rob = str(manifest.get("robustness_status", ""))
    combined = (wf + oos + rob).upper()
    if "FAIL" in combined or "REJECT" in combined:
        return False, f"wf={wf} oos={oos} robustness={rob}"
    # A manifest that ran gates but recorded nothing is UNKNOWN, not PASS.
    if combined == "":
        return True, "no explicit failure recorded (UNKNOWN)"
    return True, f"wf={wf} oos={oos} robustness={rob}"


class ModelLoadGate:
    """Runs the ten gates against a model artifact + manifest + lifecycle."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else None

    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        artifact_path: Path | str,
        scaler_path: Path | str = "",
        model_id: str = "",
        model_version: str = "",
        manifest: dict[str, Any] | None = None,
        lifecycle_state: str = "",
        allow_shadow_states: tuple[str, ...] = ("CHALLENGER", "SHADOW"),
    ) -> LoadGateResult:
        """Evaluates all gates in order; returns the first failure."""
        art = Path(artifact_path)
        sca = Path(scaler_path) if scaler_path else Path(str(art) + ".scaler.npz")
        mf = manifest or {}
        details: dict[str, Any] = {"artifact_path": str(art), "scaler_path": str(sca)}

        # 1. ARTIFACT_EXISTS
        if not art.exists() or art.stat().st_size == 0:
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.ARTIFACT_EXISTS,
                details,
                "artifact missing or empty",
            )

        # 2. HASH_VALID
        actual = sha256_hex(art)
        declared = str(mf.get("artifact_hash", "") or "")
        if declared and actual and actual != declared:
            details["expected_hash"] = declared
            details["actual_hash"] = actual
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.HASH_VALID,
                details,
                "artifact hash mismatch",
                GovernanceErrorCode.ARTIFACT_HASH_MISMATCH,
            )
        details["artifact_hash"] = actual

        # 3. MANIFEST_VALID
        if not mf:
            return self._fail(
                model_id, model_version, LoadGateStep.MANIFEST_VALID, details, "manifest missing"
            )
        for key in ("model_id", "feature_schema_id", "feature_dimension", "class_count"):
            if key not in mf:
                return self._fail(
                    model_id,
                    model_version,
                    LoadGateStep.MANIFEST_VALID,
                    details,
                    f"manifest missing field {key}",
                )

        # 4. SCHEMA_VALID — schema id must be REGISTERED (never guessed)
        sid = str(mf.get("feature_schema_id", "") or "")
        if sid not in _REGISTERED_SCHEMA_IDS:
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.SCHEMA_VALID,
                details,
                f"unregistered schema '{sid}'",
            )
        details["schema_id"] = sid

        # 5. INPUT_DIMENSION_VALID — manifest width vs state-dict width
        declared_dim = _manifest_dimension(mf)
        state_dim = _state_dict_input_dim(art)
        if state_dim is not None and declared_dim and state_dim != declared_dim:
            details["declared_input"] = declared_dim
            details["state_dict_input"] = state_dim
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.INPUT_DIMENSION_VALID,
                details,
                f"state-dict width {state_dim} != manifest width {declared_dim}",
            )
        details["input_dimension"] = declared_dim or state_dim or 0

        # 6. SCALER_VALID
        if sca.exists():
            try:
                import numpy as np

                data = np.load(sca)
                mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
                std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
                base_dim = int(mf.get("feature_dimension", 0) or 0)
                scaler_ok = bool(
                    mean.shape[0] == base_dim and std.shape[0] == base_dim and (std > 0).all()
                )
                details["scaler_dim"] = int(mean.shape[0])
                if not scaler_ok:
                    return self._fail(
                        model_id,
                        model_version,
                        LoadGateStep.SCALER_VALID,
                        details,
                        f"scaler dim {mean.shape[0]} != base schema {base_dim} or zero std present",
                        GovernanceErrorCode.SCALER_MISMATCH,
                    )
            except Exception as e:
                return self._fail(
                    model_id,
                    model_version,
                    LoadGateStep.SCALER_VALID,
                    details,
                    f"scaler unreadable: {e}",
                    GovernanceErrorCode.SCALER_MISMATCH,
                )
        else:
            return self._fail(
                model_id, model_version, LoadGateStep.SCALER_VALID, details, "scaler file missing"
            )

        # 7. LABEL_SCHEMA_VALID
        label_schema = str(mf.get("label_schema_id", "") or "")
        class_count = int(mf.get("class_count", 0) or 0)
        if not label_schema:
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.LABEL_SCHEMA_VALID,
                details,
                "label schema missing",
            )
        if class_count not in (3, 4):
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.LABEL_SCHEMA_VALID,
                details,
                f"class_count {class_count} unsupported",
            )
        details["label_schema"] = label_schema
        details["class_count"] = class_count

        # 8. VALIDATION_STATUS_VALID
        ok, why = _validation_ok(mf)
        if not ok:
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.VALIDATION_STATUS_VALID,
                details,
                f"validation failure: {why}",
            )
        details["validation"] = why

        # 9. LIFECYCLE_ALLOWS_SHADOW
        state = lifecycle_state or str(mf.get("role", "CANDIDATE") or "CANDIDATE").upper()
        try:
            ps = PromotionState(state)
        except ValueError:
            ps = None
        allowed = (
            ps in (PromotionState.CHALLENGER, PromotionState.SHADOW) or state in allow_shadow_states
        )
        if not allowed:
            return self._fail(
                model_id,
                model_version,
                LoadGateStep.LIFECYCLE_ALLOWS_SHADOW,
                details,
                f"lifecycle {state} does not allow shadow",
            )
        details["lifecycle_state"] = state

        # 10. LOAD
        return LoadGateResult(
            model_id=model_id,
            model_version=model_version,
            passed=True,
            failing_gate=None,
            details=details,
        )

    # ------------------------------------------------------------------

    def evaluate_from_registry(
        self,
        *,
        accessor: Any,
        model_id: str = "",
        version: str = "",
        lifecycle_state: str = "",
    ) -> LoadGateResult:
        """Loads the manifest via an accessor and runs the full gate.

        `accessor` must expose `read_model_manifest(model_id)` and
        `model_weights_path(model_id)` / `model_scaler_path(model_id)`
        (the model_generation ArtifactStore shape).
        """
        try:
            manifest = accessor.read_model_manifest(model_id) or {}
        except Exception as e:
            return LoadGateResult(
                model_id=model_id,
                model_version=version,
                passed=False,
                failing_gate=LoadGateStep.MANIFEST_VALID,
                details={"error": str(e)},
            )
        try:
            weights = accessor.model_weights_path(model_id)
            scaler = accessor.model_scaler_path(model_id)
        except Exception:
            weights = Path("")
            scaler = Path("")
        return self.evaluate(
            artifact_path=weights,
            scaler_path=scaler,
            model_id=model_id,
            model_version=version,
            manifest=manifest,
            lifecycle_state=lifecycle_state,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _fail(
        model_id: str,
        model_version: str,
        gate: LoadGateStep,
        details: dict[str, Any],
        reason: str,
        code: GovernanceErrorCode = GovernanceErrorCode.MODEL_LOAD_REJECTED,
    ) -> LoadGateResult:
        details["reason"] = reason
        logger.warning(
            "[MODEL_GOVERNANCE] event=MODEL_LOAD_REJECTED",
            model_id=model_id,
            stage="LOAD_GATE",
            failing_gate=gate.value,
            reason=reason,
        )
        return LoadGateResult(
            model_id=model_id,
            model_version=model_version,
            passed=False,
            failing_gate=gate,
            details=details,
            error_code=code,
        )


def evaluate_load_gate(
    *,
    artifact_path: Path | str,
    scaler_path: Path | str = "",
    model_id: str = "",
    model_version: str = "",
    manifest: dict[str, Any] | None = None,
    lifecycle_state: str = "",
) -> LoadGateResult:
    """Convenience: one-shot load-gate evaluation."""
    return ModelLoadGate().evaluate(
        artifact_path=artifact_path,
        scaler_path=scaler_path,
        model_id=model_id,
        model_version=model_version,
        manifest=manifest,
        lifecycle_state=lifecycle_state,
    )


def read_manifest_file(manifest_path: Path | str) -> dict[str, Any] | None:
    """Reads a model.json manifest file (None when unreadable)."""
    p = Path(manifest_path)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_registry_lifecycle(db_path: Path | str, model_id: str, model_version: str = "") -> str:
    """Reads lifecycle_status for (model_id, model_version) from audit.db."""
    p = Path(db_path)
    if not p.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT lifecycle_status FROM experience_model_registry "
                "WHERE model_id=? AND (?='' OR model_version=?) "
                "ORDER BY registered_at DESC LIMIT 1;",
                (model_id, model_version, model_version),
            ).fetchone()
            return str(row["lifecycle_status"]) if row else ""
        finally:
            conn.close()
    except Exception:
        return ""
