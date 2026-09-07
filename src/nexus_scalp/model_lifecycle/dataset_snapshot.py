"""
TrainingDataset Snapshot Persistence (Learning-Loop Closure, Phase 4)
=====================================================================

A Training Run must always reference an IMMUTABLE dataset snapshot. The
deterministic builder (`model_lifecycle.dataset.TrainingDatasetBuilder`)
produces the identity; this module persists the rows + a full manifest as a
versioned artifact and verifies identity on read.

REUSE-FIRST: the snapshot lives inside the EXISTING ArtifactStore tree
(`artifacts/model_generation/datasets/<dataset_id>/dataset.parquet` +
`dataset_manifest.json`) — the same immutable, hash-verified store the
model_generation datasets use. No third dataset system is created; the
TrainingDataset provenance (feature/label contract, source ledger identity,
leakage controls, training config) is stamped into the store manifest.

Invariant: if the snapshot cannot be written or verified, the caller must
BLOCK training (TRAINING = BLOCKED) — never train against an untracked
in-memory dataset in the production lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nexus_scalp.model_generation.artifact_store import (
    ArtifactConflictError,
    ArtifactStore,
    sha256_file,
)
from nexus_scalp.model_lifecycle.models import TrainingDataset
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.dataset_snapshot")

SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class DatasetSnapshotError(RuntimeError):
    """Raised when a snapshot cannot be persisted or fails verification."""


class DatasetSnapshotMissingError(DatasetSnapshotError):
    """Raised when a referenced snapshot does not exist (fail-closed)."""


def dataset_rows_frame(dataset: TrainingDataset) -> pl.DataFrame:
    """Converts a TrainingDataset to the canonical parquet frame.

    Column order is fixed; feature vectors are stored as feat_0..feat_{n-1}
    so the frame is directly consumable by ChallengerTrainer._to_polars_frame.
    """
    rows = dataset.ordered_rows()
    n_feat = dataset.feature_dimension
    data: dict[str, list[Any]] = {
        "sample_id": [r.sample_id for r in rows],
        "experience_id": [r.experience_id for r in rows],
        "idempotency_key": [r.idempotency_key for r in rows],
        "decision_timestamp": [r.decision_timestamp.isoformat() for r in rows],
        "feature_schema_id": [r.feature_schema_id for r in rows],
        "feature_dimension": [r.feature_dimension for r in rows],
        "label": [r.label for r in rows],
        "label_str": [r.label_str for r in rows],
        "strategy_id": [r.strategy_id for r in rows],
        "strategy_version": [r.strategy_version for r in rows],
        "regime": [r.regime for r in rows],
        "symbol": [r.symbol for r in rows],
        "timeframe": [r.timeframe for r in rows],
        "session": [r.session for r in rows],
        "sample_weight": [r.sample_weight for r in rows],
        "outcome_r": [r.outcome_r for r in rows],
        "is_executed": [r.is_executed for r in rows],
        "is_closed": [r.is_closed for r in rows],
        "exit_reason": [r.exit_reason for r in rows],
    }
    vec = [r.feature_vector for r in rows]
    for i in range(n_feat):
        data[f"feat_{i}"] = [v[i] if i < len(v) else None for v in vec]
    return pl.DataFrame(data)


def build_snapshot_manifest(
    dataset: TrainingDataset,
    *,
    training_config: dict[str, Any] | None = None,
    build_identity: str = "",
) -> dict[str, Any]:
    """Builds the manifest payload for a TrainingDataset snapshot.

    Persists (spec Phase 4): dataset identity + hash, source ledger identity,
    feature contract, label contract, time boundaries, leakage controls
    (only_executed / as_of / open-position exclusion), training configuration.
    """
    cfg = training_config or {}
    feature_names = [f"feat_{i}" for i in range(dataset.feature_dimension)]
    return {
        "snapshot_kind": "TRAINING_DATASET",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "config_hash": dataset.config_hash,
        "row_count": dataset.sample_count,
        "source_experience_ids_count": len(dataset.source_experience_ids),
        "source_range": dict(dataset.source_range),
        "source_ledger": {
            "kind": "experience_ledger",
            "experience_count": len(dataset.source_experience_ids),
            "first_experience_id": (
                dataset.source_experience_ids[0] if dataset.source_experience_ids else ""
            ),
            "last_experience_id": (
                dataset.source_experience_ids[-1] if dataset.source_experience_ids else ""
            ),
        },
        "feature_contract": {
            "feature_schema_id": dataset.feature_schema_id,
            "feature_dimension": dataset.feature_dimension,
            "feature_columns": feature_names,
        },
        "label_contract": {
            "label_schema_id": "triple_barrier_3class_v1",
            "label_map": {"NO_TRADE": 0, "BUY_MARKET": 1, "SELL_MARKET": 2},
            "label_distribution": dataset.label_distribution(),
        },
        "leakage_controls": {
            "causal_as_of": bool(dataset.source_range) or None,
            "open_positions_excluded": True,
            "temporal_order": "decision_timestamp ascending",
        },
        "training_config": cfg,
        "build_identity": build_identity,
        "created_at": datetime.now(UTC).isoformat(),
    }


class TrainingDatasetSnapshotStore:
    """Persists + verifies immutable TrainingDataset snapshots."""

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def save_snapshot(
        self,
        dataset: TrainingDataset,
        *,
        training_config: dict[str, Any] | None = None,
        build_identity: str = "",
    ) -> dict[str, Any]:
        """Persists the dataset as an immutable artifact; returns the handle.

        Raises DatasetSnapshotError when the snapshot exists with different
        bytes (identity collision — a rebuilt dataset must mint a new id) or
        when verification after write fails.
        """
        dataset_id = dataset.dataset_id
        manifest = build_snapshot_manifest(
            dataset, training_config=training_config, build_identity=build_identity
        )
        frame = dataset_rows_frame(dataset)
        try:
            handle = self.store.save_dataset(dataset_id, frame, manifest, allow_overwrite=False)
        except ArtifactConflictError as e:
            existing = self.store.read_dataset_manifest(dataset_id) or {}
            existing_hash = str(existing.get("dataset_hash", "") or "")
            same_rows = existing.get("row_count") == dataset.sample_count and existing.get(
                "config_hash"
            ) == dataset.config_hash
            if same_rows and existing_hash:
                # Same content re-persisted (restart-during-cycle replay):
                # idempotent success, return the existing identity.
                logger.info(
                    "[DATASET_SNAPSHOT] event=SNAPSHOT_REUSED dataset_id=%s",
                    dataset_id,
                )
                return {
                    "path": str(self.store.dataset_path(dataset_id)),
                    "hash": existing_hash,
                    "manifest": existing,
                    "reused": True,
                }
            raise DatasetSnapshotError(
                f"dataset snapshot identity collision for {dataset_id}: {e}"
            ) from e

        verified = self.verify_snapshot(dataset_id, expected_hash=handle["hash"])
        if not verified:
            raise DatasetSnapshotError(
                f"snapshot verification failed immediately after write for {dataset_id}"
            )
        logger.info(
            "[DATASET_SNAPSHOT] event=SNAPSHOT_SAVED dataset_id=%s rows=%s hash=%s",
            dataset_id,
            dataset.sample_count,
            str(handle["hash"])[:12],
        )
        return {"path": handle["path"], "hash": handle["hash"], "manifest": manifest, "reused": False}

    # ------------------------------------------------------------------
    # Verify / load
    # ------------------------------------------------------------------

    def verify_snapshot(self, dataset_id: str, *, expected_hash: str = "") -> bool:
        """Verifies existence + manifest hash against the actual bytes."""
        path = self.store.dataset_path(dataset_id)
        if not path.exists():
            return False
        manifest = self.store.read_dataset_manifest(dataset_id) or {}
        stored_hash = str(manifest.get("dataset_hash", "") or "")
        if not stored_hash:
            return False
        if expected_hash and stored_hash != expected_hash:
            return False
        try:
            actual = sha256_file(path)
        except OSError:
            return False
        return actual == stored_hash

    def require_snapshot(self, dataset_id: str, *, expected_hash: str = "") -> Path:
        """Fail-closed load: BLOCKED unless the snapshot verifies.

        Raises DatasetSnapshotMissingError when the artifact is absent or fails
        integrity verification — callers must treat this as TRAINING=BLOCKED.
        """
        if not self.verify_snapshot(dataset_id, expected_hash=expected_hash):
            raise DatasetSnapshotMissingError(
                f"TRAINING=BLOCKED: dataset snapshot {dataset_id} missing or "
                "failed integrity verification (no untracked in-memory training)"
            )
        return self.store.dataset_path(dataset_id)

    def load_manifest(self, dataset_id: str) -> dict[str, Any]:
        return self.store.read_dataset_manifest(dataset_id) or {}
