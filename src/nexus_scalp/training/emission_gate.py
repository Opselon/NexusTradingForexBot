"""ARTIFACT EMISSION GATE — synchronous hard contract check before publish.

P0-2026-09-04 producer fix. The gate inspects the EXACT state_dict that will
be serialized (never the constructed nn.Module alone) plus the metadata
payload, and refuses publication when the bundle would be incoherent:

    ACTUAL MODEL HEAD == METADATA CLASS COUNT == CANONICAL_CLASS_COUNT == 3
    ACTUAL INPUT DIM  == METADATA FEATURE DIM  == CANONICAL FEATURE DIM == 70
    SEQ LEN            == METADATA SEQ LEN     == CANONICAL SEQ LEN     == 32
    DATASET ID/SHA + FEATURE SCHEMA HASH must be present and equal.

The serialized state_dict is the source of truth: model.classifier.out_features
alone is NOT sufficient (the historical P0 shipped a [4,32] head while every
metadata field claimed 3).

Callers: WalkForwardTrainer._publish_candidate_bundle (post-write
self-inspection re-runs this gate on the re-opened on-disk tensors).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.training.emission_gate")

CANONICAL_CLASS_COUNT = 3
CANONICAL_FEATURE_DIM = 70
CANONICAL_SEQ_LEN = 32
CANONICAL_ARCHITECTURE = "scalp_v3"
CANONICAL_LINEAGE = "CLEAN_HISTORICAL"


class EmissionGateError(RuntimeError):
    """EMISSION_GATE_ABORT — the artifact may not be published."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise EmissionGateError(f"EMISSION_GATE_ABORT: {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_state_dict(state: dict[str, Any]) -> dict[str, int]:
    """Actual geometry from the serialized tensors (not the nn.Module)."""
    w = state.get("classifier.weight")
    b = state.get("classifier.bias")
    ip = state.get("input_projection.weight")
    _require(isinstance(w, Any) and w is not None and hasattr(w, "shape"), "state_dict missing classifier.weight")
    _require(b is not None and hasattr(b, "shape"), "state_dict missing classifier.bias")
    _require(ip is not None and hasattr(ip, "shape"), "state_dict missing input_projection.weight")
    return {
        "head": int(w.shape[0]),
        "hidden_out": int(w.shape[1]),
        "bias": int(b.shape[0]),
        "input_dim": int(ip.shape[1]),
        "hidden_in": int(ip.shape[0]),
    }


def run_emission_gate(
    state_dict: dict[str, Any],
    metadata: dict[str, Any],
    *,
    dataset_id: str | None,
    dataset_sha256: str | None,
    feature_schema_hash: str | None,
    feature_schema_id: str | None = None,
    seq_len: int | None = None,
    scaler_mean_dim: int | None = None,
    scaler_std_dim: int | None = None,
    label_schema_class_count: int | None = None,
) -> dict[str, Any]:
    """Hard gate. Raises EmissionGateError on ANY mismatch. Returns the
    verified geometry for the manifest."""
    geo = inspect_state_dict(state_dict)

    _require(geo["head"] == CANONICAL_CLASS_COUNT, f"actual head {geo['head']} != canonical {CANONICAL_CLASS_COUNT}")
    _require(geo["bias"] == CANONICAL_CLASS_COUNT, f"actual bias {geo['bias']} != canonical {CANONICAL_CLASS_COUNT}")
    _require(geo["head"] == geo["bias"], f"head {geo['head']} != bias {geo['bias']}")
    _require(geo["input_dim"] == CANONICAL_FEATURE_DIM, f"actual input dim {geo['input_dim']} != canonical {CANONICAL_FEATURE_DIM}")
    _require(geo["hidden_out"] == geo["hidden_in"], f"hidden mismatch {geo}")

    # metadata vs actual tensor
    meta_classes = int(metadata.get("num_classes", -1))
    head_meta = int(metadata.get("model_head_classes", -1))
    _require(meta_classes == CANONICAL_CLASS_COUNT, f"metadata num_classes {meta_classes} != {CANONICAL_CLASS_COUNT}")
    _require(head_meta == CANONICAL_CLASS_COUNT, f"metadata model_head_classes {head_meta} != {CANONICAL_CLASS_COUNT}")
    _require(int(metadata.get("num_features", -1)) == geo["input_dim"], "metadata num_features != actual input dim")
    _require(int(metadata.get("feature_schema_dimension", -1)) == geo["input_dim"], "metadata feature_schema_dimension != actual input dim")

    # label contract
    lc = metadata.get("label_contract") or {}
    if lc:
        _require(int(lc.get("class_count", -1)) == CANONICAL_CLASS_COUNT, "label_contract.class_count != canonical")
    if label_schema_class_count is not None:
        _require(label_schema_class_count == CANONICAL_CLASS_COUNT, "label schema class count != canonical")

    # provenance — never silently null
    _require(bool(dataset_id), "dataset_id missing (provenance)")
    _require(bool(dataset_sha256), "dataset_sha256 missing (provenance)")
    _require(bool(feature_schema_hash), "feature_schema_hash missing (provenance)")
    _require(metadata.get("dataset_id") == dataset_id, "metadata.dataset_id != bound dataset_id")
    _require(metadata.get("dataset_sha256") == dataset_sha256, "metadata.dataset_sha256 != bound dataset_sha256")
    _require(metadata.get("feature_schema_hash") == feature_schema_hash, "metadata.feature_schema_hash != bound hash")
    if feature_schema_id:
        _require(str(metadata.get("feature_schema_id")) == str(feature_schema_id), "metadata.feature_schema_id mismatch")
        _require(str(metadata.get("feature_schema_id")) == CANONICAL_ARCHITECTURE, f"schema {metadata.get('feature_schema_id')} != canonical {CANONICAL_ARCHITECTURE}")

    # sequence length
    effective_seq = int(seq_len if seq_len is not None else metadata.get("seq_len", -1))
    _require(effective_seq == CANONICAL_SEQ_LEN, f"seq_len {effective_seq} != canonical {CANONICAL_SEQ_LEN}")
    tc = metadata.get("temporal_contract") or {}
    if tc:
        _require(int(tc.get("seq_len", -1)) == CANONICAL_SEQ_LEN, "temporal_contract.seq_len != canonical")

    # smoke quarantine can never look production eligible
    if metadata.get("smoke") is True:
        _require(metadata.get("production_eligible") is False, "smoke artifact flagged production_eligible")

    # scaler geometry
    if scaler_mean_dim is not None:
        _require(scaler_mean_dim == CANONICAL_FEATURE_DIM, f"scaler mean dim {scaler_mean_dim} != {CANONICAL_FEATURE_DIM}")
    if scaler_std_dim is not None:
        _require(scaler_std_dim == CANONICAL_FEATURE_DIM, f"scaler std dim {scaler_std_dim} != {CANONICAL_FEATURE_DIM}")

    logger.info(
        "EMISSION_GATE_PASS head=%d input=%d seq=%d dataset=%s",
        geo["head"], geo["input_dim"], effective_seq, dataset_id,
    )
    return {
        "head": geo["head"],
        "input_dim": geo["input_dim"],
        "seq_len": effective_seq,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "feature_schema_hash": feature_schema_hash,
    }


def build_bundle_manifest(
    *,
    bundle_dir: Path,
    dataset_id: str,
    dataset_sha256: str,
    feature_schema_id: str,
    feature_schema_hash: str,
    label_schema_id: str,
    architecture: str,
    architecture_version: str,
    git_commit: str,
    training_command: str,
    seed: int,
    fold_count: int,
    epoch_count: int,
    lineage: str,
    production_eligible: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical bundle manifest binding every file by hash."""
    model_sha = sha256_file(bundle_dir / "model.pt")
    meta_sha = sha256_file(bundle_dir / "model.meta.json")
    scaler_sha = sha256_file(bundle_dir / "model.scaler.npz")
    manifest: dict[str, Any] = {
        "manifest_version": "1.0.0",
        "bundle_id": f"bundle_{model_sha[:12]}",
        "model_sha256": model_sha,
        "metadata_sha256": meta_sha,
        "scaler_sha256": scaler_sha,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "feature_schema_id": feature_schema_id,
        "feature_schema_hash": feature_schema_hash,
        "label_schema_id": label_schema_id,
        "class_count": CANONICAL_CLASS_COUNT,
        "input_dim": CANONICAL_FEATURE_DIM,
        "seq_len": CANONICAL_SEQ_LEN,
        "architecture": architecture,
        "architecture_version": architecture_version,
        "git_commit": git_commit,
        "training_command": training_command,
        "seed": seed,
        "fold_count": fold_count,
        "epoch_count": epoch_count,
        "creation_timestamp": _utc_now_iso(),
        "lineage": lineage,
        "production_eligible": bool(production_eligible),
    }
    if extra:
        manifest.update(extra)
    return manifest


def verify_bundle_against_manifest(bundle_dir: Path) -> dict[str, Any]:
    """Re-open a staged/published bundle from disk and verify EVERY binding.

    Returns the verified manifest. Raises EmissionGateError on any mismatch:
    stale sidecar, swapped weights, hash drift, missing marker file.
    """
    bundle_dir = Path(bundle_dir)
    model_path = bundle_dir / "model.pt"
    meta_path = bundle_dir / "model.meta.json"
    scaler_path = bundle_dir / "model.scaler.npz"
    manifest_path = bundle_dir / "manifest.json"

    for p in (model_path, meta_path, scaler_path, manifest_path):
        _require(p.exists(), f"bundle incomplete: missing {p.name} in {bundle_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(manifest.get("model_sha256") == sha256_file(model_path), "manifest.model_sha256 != model.pt bytes (stale sidecar / swapped weights)")
    _require(manifest.get("metadata_sha256") == sha256_file(meta_path), "manifest.metadata_sha256 != model.meta.json bytes")
    _require(manifest.get("scaler_sha256") == sha256_file(scaler_path), "manifest.scaler_sha256 != model.scaler.npz bytes")

    import torch

    state = torch.load(model_path, map_location="cpu", weights_only=True)
    _require(isinstance(state, dict), "model.pt is not a pure state_dict (safe-load rejected)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    import numpy as np

    data = np.load(scaler_path)
    mean_dim = int(np.asarray(data["mean"]).reshape(-1).shape[0])
    std_dim = int(np.asarray(data["std"]).reshape(-1).shape[0])

    run_emission_gate(
        state,
        meta,
        dataset_id=manifest.get("dataset_id"),
        dataset_sha256=manifest.get("dataset_sha256"),
        feature_schema_hash=manifest.get("feature_schema_hash"),
        feature_schema_id=meta.get("feature_schema_id"),
        seq_len=int(manifest.get("seq_len", -1)),
        scaler_mean_dim=mean_dim,
        scaler_std_dim=std_dim,
        label_schema_class_count=(meta.get("label_contract") or {}).get("class_count"),
    )
    _require(
        manifest.get("class_count") == CANONICAL_CLASS_COUNT
        and manifest.get("input_dim") == CANONICAL_FEATURE_DIM
        and manifest.get("seq_len") == CANONICAL_SEQ_LEN,
        "manifest contract fields disagree with canonical constants",
    )
    return manifest


def publish_bundle_atomic(staging_dir: Path, final_dir: Path) -> None:
    """Publish a fully-validated staging bundle.

    Strategy: write everything into ``staging_dir`` (dot-prefixed), validate,
    then commit with a rename. Directory rename is used when the target does
    not exist; otherwise the validated files are moved in one pass AFTER the
    manifest (the last file consumers must see) — consumers only trust a
    bundle whose manifest.json exists AND verifies, so no partially-written
    state can ever be mistaken for a valid candidate.
    """
    staging_dir = Path(staging_dir)
    final_dir = Path(final_dir)
    _require((staging_dir / "manifest.json").exists(), "staging bundle has no manifest.json — refusing to publish")
    verify_bundle_against_manifest(staging_dir)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        # Existing bundle: move components atomically file-by-file; the
        # manifest is written LAST (commit marker semantics — a bundle is
        # only valid when manifest.json is present AND its hashes verify).
        for name in ("model.pt", "model.scaler.npz", "model.meta.json", "metrics.json", "training_log.json"):
            src = staging_dir / name
            if src.exists():
                os.replace(src, final_dir / name)
        os.replace(staging_dir / "manifest.json", final_dir / "manifest.json")
        # re-verify at the FINAL location
        verify_bundle_against_manifest(final_dir)
        _cleanup(staging_dir)
    else:
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_dir, final_dir)  # atomic dir rename
    logger.info("BUNDLE_PUBLISHED %s", final_dir)


def new_staging_dir(parent: Path, purpose: str) -> Path:
    d = Path(parent) / f".staging_{purpose}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def _cleanup(p: Path) -> None:
    try:
        for child in p.iterdir():
            child.unlink()
        p.rmdir()
    except Exception:  # pragma: no cover
        pass


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def git_commit_head() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"
