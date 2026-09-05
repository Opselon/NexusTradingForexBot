"""Artifact Store (PHASE 13).

Artifact-first directory hierarchy + integrity hashing:

    artifacts/
        model_generation/
            datasets/<dataset_id>/          dataset.parquet + dataset_manifest.json
            experiments/<experiment_id>/    experiment.json
            models/<model_id>/
                model.pt                    weights (binary)
                model.json                  ModelManifest
                scaler.npz                  scaler/preprocessor
                validation.json             ValidationResults

The store NEVER requires the trading DB. Model inference reads ONLY the
filesystem artifact (spec 6 / 40).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.artifact_store")

# AGENT-14 QA wave 2 (CHG-0061): dataset-artifact immutability + integrity
# exception taxonomy (mirrors research.mt5_tick_dataset v3).


class ArtifactConflictError(RuntimeError):
    """An existing immutable artifact blocks the requested rebuild.

    Corrections must mint a NEW dataset id (new fingerprint), never
    overwrite the bytes under an existing identity.
    """


class DatasetCorruptionError(RuntimeError):
    """A stored dataset artifact failed integrity verification on read
    (manifest dataset_hash != actual parquet bytes)."""

#: Allowed characters for artifact identifiers (model_id / dataset_id /
#: experiment_id). Prevents path traversal and accidental writes outside
#: the artifact root (forensic audit T03/T58).
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_artifact_id(artifact_id: str) -> str:
    """Rejects artifact ids that could escape the store root (path
    traversal / separators / traversal sequences)."""
    if not artifact_id or not isinstance(artifact_id, str):
        raise ValueError(f"Invalid artifact id: {artifact_id!r}")
    if not _SAFE_ID_RE.match(artifact_id):
        raise ValueError(f"Unsafe artifact id {artifact_id!r}: only [A-Za-z0-9_.-] allowed")
    if ".." in artifact_id:
        raise ValueError(f"Unsafe artifact id {artifact_id!r}: '..' not allowed")
    return artifact_id


def sha256_file(path: Path) -> str:
    """SHA256 hex digest (full file) or '' when absent."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(payload: dict[str, Any]) -> str:
    """Deterministic hash of a dict (sorted keys)."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactStore:
    """Filesystem artifact store — no DB dependency (spec 6 / 40)."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_artifact_root()
        self.datasets_dir = self.root / "datasets"
        self.experiments_dir = self.root / "experiments"
        self.models_dir = self.root / "models"
        for d in (self.datasets_dir, self.experiments_dir, self.models_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic JSON read/write (atomic via tmp + replace)
    # ------------------------------------------------------------------

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(path)

    def read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("[ARTIFACT] json read failed", path=str(path), error=str(e))
            return None

    # ------------------------------------------------------------------
    # Dataset artifacts
    # ------------------------------------------------------------------

    def dataset_dir(self, dataset_id: str) -> Path:
        return self.datasets_dir / validate_artifact_id(dataset_id)

    def dataset_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "dataset.parquet"

    def dataset_manifest_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "dataset_manifest.json"

    def save_dataset(
        self,
        dataset_id: str,
        data: Any,
        manifest: dict[str, Any],
        *,
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """Writes a dataset artifact (parquet + manifest). Returns the
        {path, hash} handle.

        AGENT-14 QA wave 2 (CHG-0061): a persisted dataset is immutable.
        Re-saving under an EXISTING id raises ArtifactConflictError unless
        ``allow_overwrite=True``; a deliberate overwrite records the
        previous bytes (superseded_dataset_hash) in the new manifest so the
        provenance trail is preserved. Corrections must normally mint a NEW
        dataset id.
        """
        d = self.dataset_dir(dataset_id)
        parquet_path = self.dataset_path(dataset_id)
        previous_hash = ""
        if parquet_path.exists():
            if not allow_overwrite:
                raise ArtifactConflictError(
                    f"dataset {dataset_id!r}: artifact already exists "
                    f"({sha256_file(parquet_path)[:12]}). Refusing to overwrite "
                    "an immutable artifact - corrections must mint a NEW "
                    "dataset id, or pass allow_overwrite=True explicitly."
                )
            previous_hash = sha256_file(parquet_path)
        d.mkdir(parents=True, exist_ok=True)
        data.write_parquet(parquet_path)
        manifest["dataset_hash"] = sha256_file(parquet_path)
        if previous_hash:
            manifest["superseded_dataset_hash"] = previous_hash
        self.write_json(self.dataset_manifest_path(dataset_id), manifest)
        return {"path": str(parquet_path), "hash": manifest["dataset_hash"]}

    def read_dataset(self, dataset_id: str):
        import polars as pl

        p = self.dataset_path(dataset_id)
        if not p.exists():
            # Missing -> None (same convention as read_dataset_manifest).
            # Callers guard with `if frame is None: pytest.skip(...)` /
            # `if frame is None or frame.is_empty()`; raising here turned
            # absent (not-yet-built) artifacts into hard test failures.
            return None
        frame = pl.read_parquet(p)
        # AGENT-14 QA wave 2 (CHG-0061): DETECT/REJECT — the stored manifest
        # dataset_hash must describe the ACTUAL parquet bytes. A tampered,
        # swapped, or partially-written artifact is never served silently.
        manifest = self.read_dataset_manifest(dataset_id) or {}
        expected = str(manifest.get("dataset_hash") or "")
        if expected:
            actual = sha256_file(p)
            if actual != expected:
                raise DatasetCorruptionError(
                    f"dataset {dataset_id!r}: manifest dataset_hash "
                    f"({expected[:12]}) != actual parquet sha256 "
                    f"({actual[:12]}) — the artifact was modified, corrupted, "
                    "or swapped; REJECTING. Rebuild under a NEW dataset id."
                )
        return frame

    def read_dataset_manifest(self, dataset_id: str) -> dict[str, Any] | None:
        return self.read_json(self.dataset_manifest_path(dataset_id))

    # ------------------------------------------------------------------
    # Experiment artifacts
    # ------------------------------------------------------------------

    def experiment_path(self, experiment_id: str) -> Path:
        return self.experiments_dir / validate_artifact_id(experiment_id) / "experiment.json"

    def save_experiment(self, experiment_id: str, config: dict[str, Any]) -> Path:
        p = self.experiment_path(experiment_id)
        self.write_json(p, config)
        return p

    def read_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        return self.read_json(self.experiment_path(experiment_id))

    # ------------------------------------------------------------------
    # Model artifacts
    # ------------------------------------------------------------------

    def model_dir(self, model_id: str) -> Path:
        return self.models_dir / validate_artifact_id(model_id)

    def model_weights_path(self, model_id: str) -> Path:
        return self.model_dir(model_id) / "model.pt"

    def model_manifest_path(self, model_id: str) -> Path:
        return self.model_dir(model_id) / "model.json"

    def model_scaler_path(self, model_id: str) -> Path:
        return self.model_dir(model_id) / "scaler.npz"

    def model_validation_path(self, model_id: str) -> Path:
        return self.model_dir(model_id) / "validation.json"

    def save_model_artifact(
        self,
        model_id: str,
        weights: Any,
        manifest: dict[str, Any],
        scaler: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> dict[str, Any]:
        """Writes a complete model artifact; returns handles + hashes.

        ``weights`` is a torch state_dict (never JSON), scaler is (mean, std).
        """
        d = self.model_dir(model_id)
        d.mkdir(parents=True, exist_ok=True)

        weights_path = self.model_weights_path(model_id)
        tmp_w = weights_path.with_name(weights_path.name + ".tmp")
        try:
            import torch

            torch.save(weights, tmp_w)
            tmp_w.replace(weights_path)
        finally:
            if tmp_w.exists():
                tmp_w.unlink(missing_ok=True)

        artifact_hash = sha256_file(weights_path)

        if scaler is not None:
            mean, std = scaler
            scaler_path = self.model_scaler_path(model_id)
            # np.savez auto-appends ".npz"; write to a tmp WITHOUT the suffix
            # then atomically rename to the final scaler.npz.
            tmp_s = scaler_path.with_name("scaler.tmp")
            try:
                np.savez(tmp_s, mean=mean, std=std)
                Path(str(tmp_s) + ".npz").replace(scaler_path)
            finally:
                for leftover in (tmp_s, Path(str(tmp_s) + ".npz")):
                    if leftover.exists():
                        leftover.unlink(missing_ok=True)
            manifest["scaler_hash"] = sha256_file(scaler_path)
        else:
            manifest["scaler_hash"] = ""

        manifest["artifact_hash"] = artifact_hash
        manifest["created_at"] = datetime.now(UTC).isoformat()
        self.write_json(self.model_manifest_path(model_id), manifest)
        return {
            "model_id": model_id,
            "weights_path": str(weights_path),
            "artifact_hash": artifact_hash,
            "manifest_path": str(self.model_manifest_path(model_id)),
        }

    def read_model_manifest(self, model_id: str) -> dict[str, Any] | None:
        return self.read_json(self.model_manifest_path(model_id))

    def read_scaler(self, model_id: str) -> tuple[np.ndarray, np.ndarray] | None:
        p = self.model_scaler_path(model_id)
        if not p.exists():
            return None
        try:
            data = np.load(p)
            return data["mean"], data["std"]
        except Exception as e:
            logger.warning("[ARTIFACT] scaler read failed", path=str(p), error=str(e))
            return None

    def save_validation(self, model_id: str, results: dict[str, Any]) -> None:
        self.write_json(self.model_validation_path(model_id), results)

    def read_validation(self, model_id: str) -> dict[str, Any] | None:
        return self.read_json(self.model_validation_path(model_id))

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def verify_artifact(self, model_id: str) -> dict[str, Any]:
        """Verifies a model artifact's on-disk integrity against the
        manifest's stored hashes. Never raises; returns a verdict dict."""
        manifest = self.read_model_manifest(model_id)
        if not manifest:
            return {"model_id": model_id, "ok": False, "reason": "MANIFEST_MISSING"}
        w = self.model_weights_path(model_id)
        if not w.exists():
            return {"model_id": model_id, "ok": False, "reason": "WEIGHTS_MISSING"}
        current = sha256_file(w)
        stored = manifest.get("artifact_hash", "")
        return {"model_id": model_id, "ok": current == stored, "hash": current}


#: Canonical artifact root (mirrors the repo's artifacts/ layout).
def default_artifact_root() -> Path:
    return Path("artifacts") / "model_generation"
