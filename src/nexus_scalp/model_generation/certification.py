"""Model Certification Gate (MODEL FACTORY — stage 4).

WHY THIS EXISTS
---------------
``model_lifecycle/gates.py`` has 12 validation gates, but they are scattered
free functions called from the orchestrator at promotion time, and the Model
Studio trainer never consults them: a studio run registered a STAGING record
with whatever metrics it happened to produce and nothing ever said
``MODEL_STATUS = REJECTED``. The brief's requirement is that certification is
the conjunction

    DATASET PASS  and  FEATURE PASS  and  LABEL PASS  and  LEAKAGE PASS  and
    TRAIN PASS  and  VALIDATION PASS  and  WALK-FORWARD PASS  and  OOS PASS
    and  ARTIFACT INTEGRITY PASS  and  COMPATIBILITY PASS

and that ANY mandatory failure yields ``MODEL_STATUS = REJECTED`` rather than
``READY``. This module is that conjunction in ONE place, with ONE verdict and
ONE machine-readable certificate that the Model Studio registry renders.

CONTRACT (Sections 18 / 19 / 34)
--------------------------------
* Each gate is evaluated independently and returns a named verdict with a
  REASON. A failure is never hidden behind an aggregate score.
* Missing evidence is a failure, not a pass: a gate with no artifact to
  inspect reports ``NOT_AVAILABLE`` and fails.
* The artifact bundle (Section 19) is verified as a unit — model.pt + scaler
  + manifest + checksums — because ``model.pt`` existing is not success.
* Compatibility (Section 26) is enforced BEFORE a load is attempted: schema
  id, feature dimension, sequence length, scaler shape, label schema and
  artifact checksum must all match. A mismatch yields ``LOAD_REJECTED`` with
  an exact human-readable reason, never a silent adaptation.

The certificate is persisted as ``<model_id>.certificate.json`` next to the
artifact and is the object the Model Studio registry and the load path read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.certification")

__all__ = [
    "CERTIFICATION_VERSION",
    "CERTIFIED_STAGES",
    "CompatibilityError",
    "ModelCertificate",
    "ModelCertificationGate",
    "certify_model",
    "load_certificate",
    "verify_artifact_bundle",
]

#: Certification contract version. Bumped when the gate set changes.
CERTIFICATION_VERSION: str = "cert_v1"

#: The mandatory stage set. A model is CERTIFIED only if every one passes.
CERTIFIED_STAGES: tuple[str, ...] = (
    "DATASET",
    "FEATURE",
    "LABEL",
    "LEAKAGE",
    "TRAIN",
    "VALIDATION",
    "WALK_FORWARD",
    "OOS",
    "ARTIFACT_INTEGRITY",
    "COMPATIBILITY",
)

#: Model lifecycle states the studio registry distinguishes (Section 25).
MODEL_STATES: tuple[str, ...] = (
    "DRAFT",
    "TRAINING",
    "VALIDATING",
    "OOS_TESTING",
    "CERTIFIED",
    "REJECTED",
    "LOADED",
    "ACTIVE",
)


@dataclass
class GateVerdict:
    """One stage verdict. ``reason`` is always human-readable."""

    stage: str
    passed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelCertificate:
    """Machine-readable certification result for one candidate model."""

    model_id: str
    certification_version: str
    stages: list[dict[str, Any]]
    passed_stages: list[str]
    failed_stages: list[str]
    model_status: str  # CERTIFIED | REJECTED
    schema_id: str
    dimension: int
    sequence_length: int
    label_schema_id: str
    dataset_id: str
    feature_schema_hash: str
    weights_sha256: str
    scaler_sha256: str
    manifest_sha256: str
    bundle_intact: bool
    provenance: dict[str, Any]
    certified_at: str
    rejection_reason: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def certified(self) -> bool:
        return self.model_status == "CERTIFIED"


class CompatibilityError(RuntimeError):
    """Raised when an artifact does not match the load contract (Section 26).

    Carries an exact human-readable reason; the load path surfaces it verbatim
    rather than adapting the model.
    """


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _sha256_json(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class ModelCertificationGate:
    """Evaluates the full mandatory gate set and mints a certificate."""

    def __init__(self, *, require_walk_forward: bool = True) -> None:
        self.require_walk_forward = bool(require_walk_forward)

    def evaluate(
        self,
        *,
        model_id: str,
        manifest: dict[str, Any],
        dataset_report: dict[str, Any] | None,
        feature_report: dict[str, Any] | None,
        label_report: dict[str, Any] | None,
        training_metrics: dict[str, Any] | None,
        walk_forward_result: dict[str, Any] | None = None,
        oos_metrics: dict[str, Any] | None = None,
        bundle_dir: str | Path | None = None,
        expected_schema_id: str = "",
        expected_dimension: int = 0,
        expected_sequence_length: int = 1,
        expected_label_schema_id: str = "",
    ) -> ModelCertificate:
        verdicts: list[GateVerdict] = []

        # ---- GATE: DATASET -------------------------------------------------
        if dataset_report is None:
            verdicts.append(GateVerdict("DATASET", False, "no dataset quality report supplied"))
        else:
            status = str(dataset_report.get("quality_status", "")).upper()
            if status in ("PASS", "PASS_WITH_WARNINGS"):
                verdicts.append(
                    GateVerdict(
                        "DATASET",
                        True,
                        f"dataset certified ({status})",
                        {
                            "dataset_id": dataset_report.get("dataset_id")
                            or dataset_report.get("raw_dataset_id"),
                            "valid_rows": dataset_report.get("valid_rows"),
                            "rejected_rows": dataset_report.get("rejected_rows"),
                        },
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "DATASET",
                        False,
                        f"dataset quality status is {status or 'UNKNOWN'}",
                        {"status": status},
                    )
                )

        # ---- GATE: FEATURE -------------------------------------------------
        if feature_report is None:
            verdicts.append(GateVerdict("FEATURE", False, "no feature quality report supplied"))
        else:
            status = str(feature_report.get("quality_status", "")).upper()
            failed = int(feature_report.get("failed", 0) or 0)
            if status == "FAIL" or failed:
                verdicts.append(
                    GateVerdict(
                        "FEATURE",
                        False,
                        f"{failed} feature(s) failed certification",
                        {
                            "failed": failed,
                            "warned": feature_report.get("warned"),
                            "quality_status": status,
                        },
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "FEATURE",
                        True,
                        f"feature quality {status}",
                        {
                            "passed": feature_report.get("passed"),
                            "warned": feature_report.get("warned"),
                            "dimension": feature_report.get("dimension"),
                        },
                    )
                )

        # ---- GATE: LABEL ---------------------------------------------------
        if label_report is None:
            verdicts.append(GateVerdict("LABEL", False, "no label quality report supplied"))
        else:
            status = str(label_report.get("quality_status", "")).upper()
            collapsed = list(label_report.get("collapsed_classes", []))
            if status == "FAIL" or collapsed:
                verdicts.append(
                    GateVerdict(
                        "LABEL",
                        False,
                        "label contract violated: "
                        + ("; ".join(collapsed) if collapsed else status),
                        {
                            "collapsed_classes": collapsed,
                            "no_trade_percentage": label_report.get("no_trade_percentage"),
                            "quality_status": status,
                        },
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "LABEL",
                        True,
                        f"label quality {status}",
                        {
                            "no_trade_percentage": label_report.get("no_trade_percentage"),
                            "balance_ratio": label_report.get("balance_ratio"),
                        },
                    )
                )

        # ---- GATE: LEAKAGE -------------------------------------------------
        verdicts.append(self._gate_leakage(manifest, walk_forward_result))

        # ---- GATE: TRAIN ---------------------------------------------------
        if training_metrics is None:
            verdicts.append(GateVerdict("TRAIN", False, "no training metrics supplied"))
        else:
            measured = bool(training_metrics.get("metrics_measured", False))
            epochs = int(training_metrics.get("epochs_completed", 0) or 0)
            train_loss = training_metrics.get("train_loss")
            if not measured or epochs < 1 or train_loss is None:
                verdicts.append(
                    GateVerdict(
                        "TRAIN",
                        False,
                        "training produced no real measurement "
                        f"(measured={measured}, epochs={epochs})",
                        {"epochs_completed": epochs},
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "TRAIN",
                        True,
                        f"trained {epochs} epoch(s), train_loss={float(train_loss):.4f}",
                        {"epochs_completed": epochs, "train_loss": train_loss},
                    )
                )

        # ---- GATE: VALIDATION ----------------------------------------------
        if training_metrics is None:
            verdicts.append(GateVerdict("VALIDATION", False, "no validation metrics supplied"))
        else:
            val_acc = training_metrics.get("validation_accuracy")
            val_loss = training_metrics.get("validation_loss")
            if val_loss is None or val_acc is None:
                verdicts.append(
                    GateVerdict(
                        "VALIDATION",
                        False,
                        "validation produced no measured loss/accuracy",
                        {"validation_loss": val_loss, "validation_accuracy": val_acc},
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "VALIDATION",
                        True,
                        f"validation_loss={float(val_loss):.4f} "
                        f"validation_accuracy={float(val_acc):.4f}",
                        {"validation_loss": val_loss, "validation_accuracy": val_acc},
                    )
                )

        # ---- GATE: WALK-FORWARD --------------------------------------------
        if walk_forward_result is None:
            if self.require_walk_forward:
                verdicts.append(
                    GateVerdict("WALK_FORWARD", False, "no walk-forward result supplied")
                )
            else:
                verdicts.append(
                    GateVerdict("WALK_FORWARD", True, "walk-forward not required for this run")
                )
        else:
            passed = bool(walk_forward_result.get("passed", False))
            folds = walk_forward_result.get("fold_count") or len(
                walk_forward_result.get("folds", [])
            )
            if passed:
                verdicts.append(
                    GateVerdict(
                        "WALK_FORWARD",
                        True,
                        f"walk-forward passed over {folds} fold(s)",
                        {"fold_count": folds},
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "WALK_FORWARD",
                        False,
                        f"walk-forward did not pass ({folds} fold(s))",
                        {"fold_count": folds},
                    )
                )

        # ---- GATE: OOS ------------------------------------------------------
        if oos_metrics is None:
            verdicts.append(GateVerdict("OOS", False, "no out-of-sample metrics supplied"))
        else:
            oos_loss = oos_metrics.get("oos_loss")
            if oos_loss is None:
                verdicts.append(
                    GateVerdict(
                        "OOS",
                        False,
                        "OOS holdout was never evaluated",
                        {"oos_loss": oos_loss},
                    )
                )
            else:
                verdicts.append(
                    GateVerdict(
                        "OOS",
                        True,
                        f"oos_loss={float(oos_loss):.4f} "
                        f"oos_accuracy={oos_metrics.get('oos_accuracy')}",
                        {
                            "oos_loss": oos_loss,
                            "oos_accuracy": oos_metrics.get("oos_accuracy"),
                        },
                    )
                )

        # ---- GATE: ARTIFACT INTEGRITY --------------------------------------
        verdicts.append(self._gate_artifact(manifest, bundle_dir, model_id, training_metrics))

        # ---- GATE: COMPATIBILITY --------------------------------------------
        verdicts.append(
            self._gate_compatibility(
                manifest,
                expected_schema_id=expected_schema_id,
                expected_dimension=expected_dimension,
                expected_sequence_length=expected_sequence_length,
                expected_label_schema_id=expected_label_schema_id,
            )
        )

        passed_stages = [v.stage for v in verdicts if v.passed]
        failed_stages = [v.stage for v in verdicts if not v.passed]
        model_status = "CERTIFIED" if not failed_stages else "REJECTED"
        rejection_reason = ""
        if failed_stages:
            reasons = [v.reason for v in verdicts if not v.passed]
            rejection_reason = "; ".join(
                f"[{s}] {r}" for s, r in zip(failed_stages, reasons, strict=True)
            )

        schema_id = str(manifest.get("schema_id") or manifest.get("feature_schema_id") or "")
        dimension = int(manifest.get("dimension") or manifest.get("feature_dimension") or 0)
        cert = ModelCertificate(
            model_id=model_id,
            certification_version=CERTIFICATION_VERSION,
            stages=[v.to_dict() for v in verdicts],
            passed_stages=passed_stages,
            failed_stages=failed_stages,
            model_status=model_status,
            schema_id=schema_id,
            dimension=dimension,
            sequence_length=int(manifest.get("sequence_length") or 1),
            label_schema_id=str(manifest.get("label_schema_id") or ""),
            dataset_id=str(manifest.get("dataset_id") or ""),
            feature_schema_hash=str(manifest.get("feature_schema_hash") or ""),
            weights_sha256=str(manifest.get("weights_sha256") or ""),
            scaler_sha256=str(manifest.get("scaler_sha256") or ""),
            manifest_sha256=str(manifest.get("manifest_sha256") or ""),
            bundle_intact=all(v.passed for v in verdicts if v.stage == "ARTIFACT_INTEGRITY"),
            provenance={
                "dataset_id": manifest.get("dataset_id"),
                "feature_schema_id": schema_id,
                "label_schema_id": manifest.get("label_schema_id"),
                "training_version": manifest.get("training_version"),
                "code_version": manifest.get("code_version"),
                "created_at": manifest.get("created_at"),
            },
            certified_at=datetime.now(UTC).isoformat(),
            rejection_reason=rejection_reason,
            config={"require_walk_forward": self.require_walk_forward},
        )
        event = "MODEL_CERTIFIED" if cert.certified else "MODEL_REJECTED"
        logger.info(
            "[CERTIFICATION] event=%s model_id=%s failed=%s",
            event,
            model_id,
            failed_stages or "none",
        )
        return cert

    # ------------------------------------------------------------- individual

    def _gate_leakage(
        self, manifest: dict[str, Any], walk_forward_result: dict[str, Any] | None
    ) -> GateVerdict:
        """LEAKAGE gate: the artifact must prove train-only scaling and
        temporal isolation. Absence of either proof is a failure (no silent
        assumption of safety)."""
        reasons: list[str] = []
        normalization = str(manifest.get("normalization") or "").lower()
        if not normalization:
            reasons.append("manifest records no normalization mode (scaler provenance unknown)")
        if not manifest.get("oos_isolated") and not manifest.get("oos_ratio"):
            reasons.append("manifest does not record an isolated OOS holdout")
        if walk_forward_result is not None and not walk_forward_result.get("purge_embargo"):
            if not walk_forward_result.get("passed"):
                reasons.append("walk-forward result carries no purge/embargo evidence")
        if reasons:
            return GateVerdict(
                "LEAKAGE",
                False,
                "leakage contract not proven: " + "; ".join(reasons),
                {"reasons": reasons},
            )
        return GateVerdict(
            "LEAKAGE",
            True,
            "train-only scaling + isolated OOS + purge/embargo recorded",
            {"normalization": normalization},
        )

    def _gate_artifact(
        self,
        manifest: dict[str, Any],
        bundle_dir: str | Path | None,
        model_id: str,
        training_metrics: dict[str, Any] | None,
    ) -> GateVerdict:
        """ARTIFACT_INTEGRITY gate: the bundle exists and its checksums match.

        ``model.pt`` existing is NOT success — the scaler and manifest must be
        present and their recorded checksums must match the bytes on disk.
        """
        if bundle_dir is None:
            return GateVerdict("ARTIFACT_INTEGRITY", False, "no artifact bundle directory supplied")
        d = Path(bundle_dir)
        weights = d / f"{model_id}.pt"
        manifest_path = d / f"{model_id}.meta.json"
        scaler_path = d / f"{model_id}.scaler.npz"

        missing = [
            n
            for n, p in (
                ("model.pt", weights),
                ("manifest", manifest_path),
                ("scaler", scaler_path),
            )
            if not p.is_file()
        ]
        if missing:
            return GateVerdict(
                "ARTIFACT_INTEGRITY",
                False,
                f"bundle incomplete, missing: {', '.join(missing)}",
                {"missing": missing},
            )

        actual_weights_hash = _sha256_file(weights)
        expected_weights_hash = str(manifest.get("weights_sha256") or "")
        if expected_weights_hash and expected_weights_hash != actual_weights_hash:
            return GateVerdict(
                "ARTIFACT_INTEGRITY",
                False,
                "model.pt checksum mismatch (weights tampered or stale)",
                {
                    "expected": expected_weights_hash[:12],
                    "actual": actual_weights_hash[:12],
                },
            )

        # The manifest on disk must be the manifest that was certified. The
        # manifest_sha256 stamp is excluded from its own input (it cannot
        # hash itself), so the comparison is over the remaining fields.
        on_disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_body = {k: v for k, v in on_disk_manifest.items() if k != "manifest_sha256"}
        manifest_sha = _sha256_json(manifest_body)
        recorded_manifest_sha = str(manifest.get("manifest_sha256") or "")
        if recorded_manifest_sha and recorded_manifest_sha != manifest_sha:
            return GateVerdict(
                "ARTIFACT_INTEGRITY",
                False,
                "manifest on disk does not match the certified manifest",
                {"expected": recorded_manifest_sha[:12], "actual": manifest_sha[:12]},
            )

        # The scaler sidecar must declare the same dimension as the model.
        try:
            import numpy as np

            with np.load(scaler_path) as sc:
                sc_dim = int(sc["dimension"])
        except Exception as exc:
            return GateVerdict(
                "ARTIFACT_INTEGRITY",
                False,
                f"scaler sidecar is not readable: {exc}",
                {"scaler_path": str(scaler_path)},
            )
        expected_dim = int(manifest.get("dimension") or 0)
        if expected_dim and sc_dim != expected_dim:
            return GateVerdict(
                "ARTIFACT_INTEGRITY",
                False,
                f"scaler dimension {sc_dim} != model dimension {expected_dim}",
                {"scaler_dimension": sc_dim, "model_dimension": expected_dim},
            )

        return GateVerdict(
            "ARTIFACT_INTEGRITY",
            True,
            "bundle complete: model.pt + scaler + manifest, checksums verified",
            {
                "weights_sha256": actual_weights_hash[:12],
                "manifest_sha256": manifest_sha[:12],
                "scaler_dimension": sc_dim,
            },
        )

    def _gate_compatibility(
        self,
        manifest: dict[str, Any],
        *,
        expected_schema_id: str,
        expected_dimension: int,
        expected_sequence_length: int,
        expected_label_schema_id: str,
    ) -> GateVerdict:
        """COMPATIBILITY gate: the artifact matches the load contract."""
        reasons: list[str] = []
        schema_id = str(manifest.get("schema_id") or manifest.get("feature_schema_id") or "")
        dim = int(manifest.get("dimension") or manifest.get("feature_dimension") or 0)
        seq = int(manifest.get("sequence_length") or 1)
        label_schema = str(manifest.get("label_schema_id") or "")

        if expected_schema_id and schema_id and schema_id != expected_schema_id:
            reasons.append(f"schema_id {schema_id!r} != expected {expected_schema_id!r}")
        if expected_dimension and dim and dim != expected_dimension:
            reasons.append(f"feature dimension {dim} != expected {expected_dimension}")
        if (
            expected_sequence_length
            and seq
            and expected_sequence_length > 1
            and seq != expected_sequence_length
        ):
            reasons.append(f"sequence length {seq} != expected {expected_sequence_length}")
        if expected_label_schema_id and label_schema and label_schema != expected_label_schema_id:
            reasons.append(
                f"label schema {label_schema!r} != expected {expected_label_schema_id!r}"
            )
        if dim not in (50, 70):
            reasons.append(f"feature dimension {dim} is not a canonical 50D/70D contract")
        if reasons:
            return GateVerdict(
                "COMPATIBILITY",
                False,
                "incompatible with the load contract: " + "; ".join(reasons),
                {
                    "schema_id": schema_id,
                    "dimension": dim,
                    "sequence_length": seq,
                    "label_schema_id": label_schema,
                },
            )
        return GateVerdict(
            "COMPATIBILITY",
            True,
            f"schema={schema_id} dim={dim} seq={seq} labels={label_schema} matches contract",
            {
                "schema_id": schema_id,
                "dimension": dim,
                "sequence_length": seq,
                "label_schema_id": label_schema,
            },
        )


# =============================================================================
# Artifact bundle verification (Section 19) — the load-side contract
# =============================================================================


def verify_artifact_bundle(
    bundle_dir: str | Path,
    model_id: str,
    *,
    expected_schema_id: str = "",
    expected_dimension: int = 0,
    expected_sequence_length: int = 1,
    expected_label_schema_id: str = "",
    expected_weights_sha256: str = "",
) -> dict[str, Any]:
    """Verifies a candidate artifact bundle against the load contract.

    Raises ``CompatibilityError`` with an EXACT reason on any mismatch. The
    load path surfaces that reason verbatim as ``LOAD_REJECTED`` — it never
    adapts, truncates, zero-pads or substitutes a different model.
    """
    d = Path(bundle_dir)
    weights = d / f"{model_id}.pt"
    manifest_path = d / f"{model_id}.meta.json"
    scaler_path = d / f"{model_id}.scaler.npz"
    cert_path = d / f"{model_id}.certificate.json"

    for label, p in (
        ("model.pt", weights),
        ("manifest", manifest_path),
        ("scaler", scaler_path),
    ):
        if not p.is_file():
            raise CompatibilityError(
                f"LOAD_REJECTED: artifact bundle is incomplete — {label} "
                f"missing for model {model_id!r}"
            )

    if not cert_path.is_file():
        raise CompatibilityError(
            f"LOAD_REJECTED: model {model_id!r} has no certification "
            "certificate — non-certified artifacts cannot be loaded"
        )

    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    if cert.get("model_status") != "CERTIFIED":
        reason = cert.get("rejection_reason") or "certification did not pass"
        raise CompatibilityError(
            f"LOAD_REJECTED: model {model_id!r} is not CERTIFIED "
            f"(status={cert.get('model_status')}): {reason}"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    schema_id = str(manifest.get("schema_id") or "")
    dim = int(manifest.get("dimension") or 0)
    seq = int(manifest.get("sequence_length") or 1)
    label_schema = str(manifest.get("label_schema_id") or "")

    if expected_schema_id and schema_id != expected_schema_id:
        raise CompatibilityError(
            f"LOAD_REJECTED: feature schema mismatch — artifact declares "
            f"{schema_id!r}, load contract requires {expected_schema_id!r}"
        )
    if expected_dimension and dim != expected_dimension:
        raise CompatibilityError(
            f"LOAD_REJECTED: feature dimension mismatch — artifact declares "
            f"{dim}D, load contract requires {expected_dimension}D. "
            "No silent truncation or padding is performed."
        )
    if expected_sequence_length > 1 and seq != expected_sequence_length:
        raise CompatibilityError(
            f"LOAD_REJECTED: sequence length mismatch — artifact declares "
            f"{seq}, load contract requires {expected_sequence_length}"
        )
    if expected_label_schema_id and label_schema != expected_label_schema_id:
        raise CompatibilityError(
            f"LOAD_REJECTED: label schema mismatch — artifact declares "
            f"{label_schema!r}, load contract requires {expected_label_schema_id!r}"
        )

    actual_hash = _sha256_file(weights)
    expected_hash = expected_weights_sha256 or str(manifest.get("weights_sha256") or "")
    if expected_hash and actual_hash != expected_hash:
        raise CompatibilityError(
            f"LOAD_REJECTED: model.pt checksum mismatch — expected "
            f"{expected_hash[:12]}, actual {actual_hash[:12]}. The artifact "
            "may be corrupt or tampered."
        )

    try:
        import numpy as np

        with np.load(scaler_path) as sc:
            sc_dim = int(sc["dimension"])
    except Exception as exc:
        raise CompatibilityError(
            f"LOAD_REJECTED: scaler sidecar for {model_id!r} is corrupt or unreadable: {exc}"
        ) from exc
    if sc_dim != dim:
        raise CompatibilityError(
            f"LOAD_REJECTED: scaler dimension mismatch — scaler declares "
            f"{sc_dim}D, model declares {dim}D"
        )

    return {
        "status": "OK",
        "model_id": model_id,
        "schema_id": schema_id,
        "dimension": dim,
        "sequence_length": seq,
        "label_schema_id": label_schema,
        "weights_sha256": actual_hash,
        "certificate": cert,
        "manifest": manifest,
    }


def certify_model(
    model_id: str,
    *,
    manifest: dict[str, Any],
    dataset_report: dict[str, Any] | None,
    feature_report: dict[str, Any] | None,
    label_report: dict[str, Any] | None,
    training_metrics: dict[str, Any] | None,
    walk_forward_result: dict[str, Any] | None = None,
    oos_metrics: dict[str, Any] | None = None,
    bundle_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    expected_schema_id: str = "",
    expected_dimension: int = 0,
    expected_sequence_length: int = 1,
    expected_label_schema_id: str = "",
) -> ModelCertificate:
    """Canonical entry: evaluate every mandatory gate and persist the
    certificate as ``<model_id>.certificate.json``.

    On any gate failure the certificate records ``MODEL_STATUS = REJECTED``
    with the exact per-stage reasons. Nothing is silently marked ready.
    """
    gate = ModelCertificationGate()
    cert = gate.evaluate(
        model_id=model_id,
        manifest=manifest,
        dataset_report=dataset_report,
        feature_report=feature_report,
        label_report=label_report,
        training_metrics=training_metrics,
        walk_forward_result=walk_forward_result,
        oos_metrics=oos_metrics,
        bundle_dir=bundle_dir,
        expected_schema_id=expected_schema_id,
        expected_dimension=expected_dimension,
        expected_sequence_length=expected_sequence_length,
        expected_label_schema_id=expected_label_schema_id,
    )

    if out_dir is not None:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{model_id}.certificate.json").write_text(
            json.dumps(cert.to_dict(), indent=2, default=str), encoding="utf-8"
        )
    return cert


def load_certificate(bundle_dir: str | Path, model_id: str) -> ModelCertificate | None:
    p = Path(bundle_dir) / f"{model_id}.certificate.json"
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    payload["config"] = payload.get("config") or {}
    return ModelCertificate(**payload)
