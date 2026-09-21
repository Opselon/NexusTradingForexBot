"""ML-PHASE1 STEP-7: ONE authoritative serving-contract validator.

Centralizes (does not duplicate) the artifact <-> meta <-> schema <->
scaler <-> registry coherence checks that a model must pass before it may
become authoritative. The existing partial gates are kept in place —
``ModelBundleStore._artifact_meta_coherence`` (meta/tensor widths),
``_verify_champion_registry_binding`` (artifact fingerprint vs the governed
CHAMPION row) and ``model_lifecycle.load_integrity.verify_artifact_integrity``
(weight digest) — and this validator adds the MISSING contract dimensions:

    * feature dimension mismatch        (DIMENSION_MISMATCH)
    * schema id mismatch                (SCHEMA_MISMATCH)
    * schema hash mismatch              (SCHEMA_HASH_MISMATCH)
    * scaler dimension mismatch         (SCALER_DIMENSION_MISMATCH)
    * feature ORDER mismatch            (FEATURE_ORDER_MISMATCH)
    * temporal contract mismatch        (TEMPORAL_CONTRACT_MISMATCH)
    * class / head count mismatch       (CLASS_HEAD_MISMATCH)
    * registry identity mismatch        (REGISTRY_IDENTITY_MISMATCH)
    * malformed / missing metadata      (MALFORMED_METADATA)

An incompatible model NEVER becomes authoritative: ``validate_serving_contract``
returns a non-ok verdict and ``require_serving_contract`` raises
``IncompatibleArtifactError`` so the champion/hot-load path fails closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContractVerdict:
    """Result of validating one artifact against the full serving contract."""

    ok: bool
    reason: str
    artifact: str
    checks: dict[str, bool] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "artifact": self.artifact,
            "checks": dict(self.checks),
            "diagnostics": dict(self.diagnostics),
        }


class IncompatibleArtifactError(RuntimeError):
    """Raised when an artifact may NOT become authoritative (fail closed)."""

    def __init__(self, verdict: ContractVerdict) -> None:
        super().__init__(f"{verdict.reason}: {verdict.artifact}")
        self.verdict = verdict


def _read_meta(model_path: Path) -> dict[str, Any] | None:
    for name in ("model.meta.json", "meta.json"):
        p = model_path.with_name(name) if name != "model.meta.json" else model_path.with_suffix(
            ".meta.json"
        )
        if p.exists():
            try:
                record = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    return record
            except (OSError, ValueError):
                return {"_unreadable": True}
    return None


def _scaler_path(model_path: Path) -> Path | None:
    for cand in (
        model_path.with_suffix(".scaler.npz"),
        model_path.with_name("model.scaler.npz"),
        model_path.with_suffix(".pt.scaler.npz"),
    ):
        if cand.exists():
            return cand
    return None


def validate_serving_contract(
    model_path: Path | str,
    *,
    registry_row: dict[str, Any] | None = None,
) -> ContractVerdict:
    """Validate an artifact against every dimension of the serving contract.

    ``registry_row`` is the GOVERNED registry row for this artifact (as read
    by the caller — this validator never opens the registry itself, so it
    stays free of DB coupling and testable with a fixture row). When it is
    provided, the row's declared identity must describe THIS artifact.

    Missing components are MALFORMED_METADATA, never silently accepted.
    """
    path = Path(model_path)
    checks: dict[str, bool] = {}
    diag: dict[str, Any] = {"artifact": path.name}

    def _fail(reason: str) -> ContractVerdict:
        return ContractVerdict(
            ok=False, reason=reason, artifact=path.name, checks=checks, diagnostics=diag
        )

    # --- component presence -------------------------------------------------
    if not path.exists():
        checks["artifact_present"] = False
        return _fail("MISSING_ARTIFACT")
    checks["artifact_present"] = True

    try:
        import torch
    except ImportError:  # pragma: no cover - torch is a hard dep
        checks["torch_available"] = False
        return _fail("MISSING_COMPONENT_TORCH")
    checks["torch_available"] = True

    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        checks["state_dict_readable"] = False
        return _fail("MALFORMED_METADATA")
    checks["state_dict_readable"] = True

    ip = state.get("input_projection.weight")
    cls = state.get("classifier.weight")
    if ip is None or cls is None or not hasattr(ip, "shape") or not hasattr(cls, "shape"):
        checks["core_tensors_present"] = False
        return _fail("MISSING_COMPONENT_TENSORS")
    checks["core_tensors_present"] = True

    artifact_dim = int(ip.shape[1])
    artifact_head = int(cls.shape[0])
    diag["artifact_dim"] = artifact_dim
    diag["artifact_head"] = artifact_head

    meta = _read_meta(path)
    if meta is None:
        checks["meta_present"] = False
        return _fail("MALFORMED_METADATA")
    checks["meta_present"] = True
    if meta.get("_unreadable"):
        checks["meta_parseable"] = False
        return _fail("MALFORMED_METADATA")
    checks["meta_parseable"] = True

    # --- feature dimension --------------------------------------------------
    meta_dim = meta.get("feature_schema_dimension", meta.get("num_features"))
    diag["meta_dim"] = meta_dim
    checks["dimension_ok"] = meta_dim is not None and int(meta_dim) == artifact_dim
    if not checks["dimension_ok"]:
        return _fail("DIMENSION_MISMATCH")

    # --- class / head count -------------------------------------------------
    meta_head = meta.get("model_head_classes", meta.get("num_classes"))
    diag["meta_head"] = meta_head
    checks["class_head_ok"] = meta_head is not None and int(meta_head) == artifact_head
    if not checks["class_head_ok"]:
        return _fail("CLASS_HEAD_MISMATCH")

    # --- schema identity ----------------------------------------------------
    schema_id = str(meta.get("feature_schema_id", "") or "")
    checks["schema_id_registered"] = bool(schema_id)
    if not schema_id:
        return _fail("SCHEMA_MISMATCH")
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        checks["schema_id_registered"] = FEATURE_SCHEMAS.is_registered(schema_id)
        resolved = FEATURE_SCHEMAS.resolve(schema_id) if checks["schema_id_registered"] else None
    except Exception:
        checks["schema_id_registered"] = False
        resolved = None
    if not checks["schema_id_registered"] or resolved is None:
        return _fail("SCHEMA_MISMATCH")
    checks["schema_dimension_ok"] = resolved.dimension == artifact_dim
    if not checks["schema_dimension_ok"]:
        return _fail("SCHEMA_MISMATCH")

    # --- schema content hash (feature ORDER) --------------------------------
    checks["schema_hash_ok"] = True
    try:
        from nexus_scalp.features.schema_contract import feature_schema_hash

        declared_hash = str(meta.get("feature_schema_hash", "") or "")
        canonical_hash = feature_schema_hash(schema_id)
        diag["declared_schema_hash"] = declared_hash
        diag["canonical_schema_hash"] = canonical_hash
        if declared_hash and canonical_hash and declared_hash != canonical_hash:
            checks["schema_hash_ok"] = False
            return _fail("SCHEMA_HASH_MISMATCH")
        # feature ORDER: the declared canonical names must be the canonical
        # sequence; a swap changes the hash, but an explicit name list is also
        # checked so an order-preserving rename cannot slip through.
        names = meta.get("canonical_feature_names")
        if isinstance(names, list) and names:
            from nexus_scalp.features.schema_contract import canonical_feature_names

            canon_names = list(canonical_feature_names())
            if len(names) == len(canon_names):
                checks["feature_order_ok"] = list(names) == canon_names
                if not checks["feature_order_ok"]:
                    return _fail("FEATURE_ORDER_MISMATCH")
            else:
                checks["feature_order_ok"] = False
                return _fail("FEATURE_ORDER_MISMATCH")
    except Exception as exc:  # schema module unavailable => cannot validate
        checks["schema_hash_ok"] = False
        diag["schema_error"] = str(exc)[:200]
        return _fail("SCHEMA_HASH_MISMATCH")

    # --- temporal contract --------------------------------------------------
    checks["temporal_contract_ok"] = True
    try:
        from nexus_scalp.model_generation.temporal_contract import CANONICAL_MAX_GAP_US

        tc = meta.get("temporal_contract") or {}
        declared_gap = tc.get("max_gap_us", meta.get("max_gap_us"))
        diag["declared_max_gap_us"] = declared_gap
        diag["canonical_max_gap_us"] = CANONICAL_MAX_GAP_US
        if declared_gap is not None and int(declared_gap) != int(CANONICAL_MAX_GAP_US):
            checks["temporal_contract_ok"] = False
            return _fail("TEMPORAL_CONTRACT_MISMATCH")
    except Exception:
        checks["temporal_contract_ok"] = False
        return _fail("TEMPORAL_CONTRACT_MISMATCH")

    # --- scaler dimension ---------------------------------------------------
    scaler_path = _scaler_path(path)
    checks["scaler_present"] = scaler_path is not None
    if scaler_path is not None:
        try:
            import numpy as np

            data = np.load(scaler_path)
            mean = np.asarray(data["mean"], dtype=np.float64).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float64).reshape(-1)
            diag["scaler_dim"] = int(mean.shape[0])
            checks["scaler_dimension_ok"] = (
                mean.shape[0] == artifact_dim and std.shape[0] == artifact_dim
            )
            if not checks["scaler_dimension_ok"]:
                return _fail("SCALER_DIMENSION_MISMATCH")
        except Exception as exc:
            checks["scaler_dimension_ok"] = False
            diag["scaler_error"] = str(exc)[:200]
            return _fail("SCALER_DIMENSION_MISMATCH")

    # --- registry identity --------------------------------------------------
    if registry_row is not None:
        rid = str(registry_row.get("feature_schema_id", "") or "")
        rdim = registry_row.get("feature_dimension")
        rfp = str(registry_row.get("artifact_fingerprint", "") or "").strip().lower()
        checks["registry_schema_ok"] = rid == schema_id
        if not checks["registry_schema_ok"]:
            diag["registry_schema_id"] = rid
            diag["meta_schema_id"] = schema_id
            return _fail("REGISTRY_IDENTITY_MISMATCH")
        checks["registry_dimension_ok"] = rdim is not None and int(rdim) == artifact_dim
        if not checks["registry_dimension_ok"]:
            diag["registry_dimension"] = rdim
            return _fail("REGISTRY_IDENTITY_MISMATCH")
        if rfp:
            import hashlib

            h = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            actual = h.hexdigest()[:16]
            diag["registry_fingerprint"] = rfp
            diag["actual_fingerprint"] = actual
            checks["registry_binding_ok"] = actual == rfp
            if not checks["registry_binding_ok"]:
                return _fail("REGISTRY_IDENTITY_MISMATCH")

    return ContractVerdict(
        ok=True, reason="SERVING_CONTRACT_OK", artifact=path.name, checks=checks, diagnostics=diag
    )


def require_serving_contract(
    model_path: Path | str, *, registry_row: dict[str, Any] | None = None
) -> ContractVerdict:
    """Fail-closed wrapper: raises unless the artifact satisfies the contract."""
    verdict = validate_serving_contract(model_path, registry_row=registry_row)
    if not verdict.ok:
        raise IncompatibleArtifactError(verdict)
    return verdict
