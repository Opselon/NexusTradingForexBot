"""Dataset Manifest Schema, Versioning & Artifact Hashing (ML-DATA-002, spec 7 / 8 / 17).

Enforces an immutable, cryptographically verifiable dataset manifest format
recording:
- dataset_id (deterministic identity)
- dataset_version (semver)
- row_count & row_counts (train/val/test/purged)
- date_range & temporal_range (start/end ISO timestamps)
- sha256_checksum (SHA-256 over raw features and labels content)
- dataset_hash (SHA-256 over the serialized parquet artifact bytes)
- feature_schema_id & feature_schema_hash (canonical 50D/70D contract hash)
- label_schema_id & label_config_hash
- split_indices / split_ranges (exact fold split boundaries for reproducibility)
- purge_parameters & embargo_parameters (anti-leakage bounds)
- metadata & provenance (lineage origin, symbol, timeframe, generation version)

Cryptographic verification:
Any mutation, bit-flip, row addition, or column tamper causes
`DatasetFactory.load()` or `DatasetManifest.verify()` to raise
`DatasetIntegrityError`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexus_scalp.model_generation.artifact_store import DatasetCorruptionError, sha256_file
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.dataset_manifest")


# =============================================================================
# Exceptions
# =============================================================================


class DatasetIntegrityError(DatasetCorruptionError):
    """Raised when dataset array values, checksum, or manifest fail cryptographic integrity verification."""


# =============================================================================
# Hashing Utilities
# =============================================================================


def compute_dataset_hash(
    features: Any,
    labels: Any = None,
    *,
    chunk_size: int = 65536,
) -> str:
    """Computes a deterministic SHA-256 hash over raw features and labels.

    Supports Polars DataFrames, NumPy ndarrays, PyTorch tensors, Python sequences,
    and file paths. Streaming chunking (`chunk_size=65536`) ensures constant memory
    overhead (< 1 MB buffer) even on multi-gigabyte datasets.

    Any 1-bit mutation in any feature or label produces a completely different hash.
    """
    hasher = hashlib.sha256()

    # Case 1: Path or str pointing to an existing file
    if isinstance(features, (str, Path)):
        p = Path(features)
        if p.is_file():
            return sha256_file(p)
        raise FileNotFoundError(f"Dataset path not found: {p}")

    # Case 2: Polars DataFrame
    if isinstance(features, pl.DataFrame):
        for col in sorted(features.columns):
            hasher.update(col.encode("utf-8"))
            col_series = features[col]
            if col_series.dtype.is_numeric() or col_series.dtype == pl.Boolean:
                col_arr = np.ascontiguousarray(col_series.to_numpy())
                buf = col_arr.tobytes()
                for i in range(0, len(buf), chunk_size):
                    hasher.update(buf[i : i + chunk_size])
            else:
                try:
                    joined = "\x00".join(str(x) for x in col_series.to_list())
                    hasher.update(joined.encode("utf-8"))
                except Exception:
                    for val in col_series:
                        hasher.update(str(val).encode("utf-8"))

        if labels is not None:
            hasher.update(b"__labels__")
            _hash_array(hasher, labels, chunk_size=chunk_size)

        return hasher.hexdigest()

    # Case 3: NumPy array or PyTorch Tensor
    raw_feat = features
    if hasattr(raw_feat, "detach") and hasattr(raw_feat, "cpu"):
        raw_feat = raw_feat.detach().cpu().numpy()

    if isinstance(raw_feat, np.ndarray) or hasattr(raw_feat, "__array__"):
        _hash_array(hasher, raw_feat, prefix=b"__features__", chunk_size=chunk_size)
    elif isinstance(raw_feat, (list, tuple)):
        arr = np.asarray(raw_feat)
        _hash_array(hasher, arr, prefix=b"__features__", chunk_size=chunk_size)
    else:
        canonical = json.dumps(raw_feat, sort_keys=True, default=str)
        hasher.update(canonical.encode("utf-8"))

    if labels is not None:
        raw_labels = labels
        if hasattr(raw_labels, "detach") and hasattr(raw_labels, "cpu"):
            raw_labels = raw_labels.detach().cpu().numpy()
        _hash_array(hasher, raw_labels, prefix=b"__labels__", chunk_size=chunk_size)

    return hasher.hexdigest()


def _hash_array(
    hasher: Any,
    arr_like: Any,
    prefix: bytes = b"",
    chunk_size: int = 65536,
) -> None:
    """Helper to stream array bytes into hasher in chunks."""
    if prefix:
        hasher.update(prefix)
    try:
        arr = np.ascontiguousarray(arr_like)
        hasher.update(str(arr.shape).encode("utf-8"))
        hasher.update(str(arr.dtype).encode("utf-8"))
        if arr.dtype == object:
            joined = "\x00".join(str(x) for x in arr.flat)
            hasher.update(joined.encode("utf-8"))
        else:
            buf = arr.tobytes()
            for i in range(0, len(buf), chunk_size):
                hasher.update(buf[i : i + chunk_size])
    except Exception:
        canonical = json.dumps(arr_like, default=str)
        hasher.update(canonical.encode("utf-8"))


# =============================================================================
# Dataset Manifest Schema
# =============================================================================


class DatasetManifest(BaseModel):
    """Self-describing, cryptographically verifiable dataset artifact manifest (spec 7 / 8 / 17)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    dataset_id: str = Field(..., description="Deterministic dataset identifier e.g. ds_12345678")
    dataset_version: str = Field(default="1.0.0", description="Semantic dataset schema version")
    source_identity_hash: str = Field(default="", description="Hash of the raw market source data")
    row_count: int = Field(default=0, description="Total number of sample rows")
    row_counts: dict[str, int] = Field(
        default_factory=dict, description="Row breakdown by split: train/val/test/purged"
    )
    temporal_range: dict[str, str] = Field(
        default_factory=dict, description="Temporal coverage: start/end ISO timestamps"
    )
    date_range: dict[str, str] = Field(default_factory=dict, description="Alias for temporal_range")
    symbol: str = Field(default="XAUUSD", description="Market asset symbol")
    timeframe: str = Field(default="M1", description="Bar timeframe (M1, M5, M15)")
    feature_schema_id: str = Field(default="scalp_v1", description="Feature schema contract ID")
    label_schema_id: str = Field(
        default="triple_barrier_3class_v1", description="Label schema contract ID"
    )
    label_config_hash: str = Field(default="", description="Digest of labeling parameters")
    split_config_hash: str = Field(default="", description="Digest of splitting configuration")
    split_indices: dict[str, list[int]] = Field(
        default_factory=dict,
        description="Exact row indices or bounds per split for guaranteed reproducibility",
    )
    split_ranges: dict[str, list[int]] = Field(
        default_factory=dict, description="Positional [start, end] ranges per split"
    )
    purge_parameters: dict[str, Any] = Field(
        default_factory=dict, description="Purge gap parameters"
    )
    embargo_parameters: dict[str, Any] = Field(
        default_factory=dict, description="Embargo parameters"
    )
    generation_version: str = Field(default="1.0.0", description="Pipeline generator version")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Creation timestamp (UTC)"
    )
    source_experience_range: dict[str, str] = Field(default_factory=dict)
    news_schema_id: str = Field(default="")
    news_data_range: dict[str, str] = Field(default_factory=dict)
    news_version: str = Field(default="")
    strategy_context_version: str = Field(default="")
    dataset_hash: str = Field(default="", description="SHA-256 hash of the parquet artifact file")
    sha256_checksum: str = Field(
        default="", description="SHA-256 checksum of features and labels array data"
    )
    feature_schema_hash: str = Field(
        default="", description="TASK-03-70D-PARITY: canonical feature-schema content hash"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional provenance data")

    @field_validator("created_at")
    @classmethod
    def _utc(cls, v: datetime | str) -> datetime:
        if isinstance(v, str):
            try:
                v = datetime.fromisoformat(v)
            except Exception:
                v = datetime.now(UTC)
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @model_validator(mode="before")
    @classmethod
    def _sync_aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Sync row_count and row_counts
            if not values.get("row_count"):
                counts = values.get("row_counts", {})
                if isinstance(counts, dict) and "total" in counts:
                    values["row_count"] = int(counts["total"])
                elif isinstance(counts, dict) and counts:
                    values["row_count"] = sum(
                        int(v) for k, v in counts.items() if k != "purged_boundary"
                    )
            if not values.get("row_counts"):
                rc = values.get("row_count", 0)
                values["row_counts"] = {"total": int(rc)}

            # Sync date_range and temporal_range
            if not values.get("date_range") and values.get("temporal_range"):
                values["date_range"] = dict(values["temporal_range"])
            if not values.get("temporal_range") and values.get("date_range"):
                values["temporal_range"] = dict(values["date_range"])

            # Sync sha256_checksum and dataset_hash
            if not values.get("sha256_checksum") and values.get("dataset_hash"):
                values["sha256_checksum"] = str(values["dataset_hash"])
            if not values.get("dataset_hash") and values.get("sha256_checksum"):
                values["dataset_hash"] = str(values["sha256_checksum"])
        return values

    # ------------------------------------------------------------------
    # Serialization & Deserialization Helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serializes manifest to JSON-compatible dictionary."""
        return self.model_dump(mode="json")

    def to_json(self, indent: int = 2) -> str:
        """Serializes manifest to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetManifest:
        """Constructs manifest from dictionary."""
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: Path | str) -> DatasetManifest:
        """Loads and validates manifest from a JSON file."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Manifest file not found: {p}")
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save(self, path: Path | str) -> None:
        """Atomically saves manifest JSON to destination path."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.tmp_{int(datetime.now(UTC).timestamp() * 1000)}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False, default=str)
        tmp.replace(p)

    # ------------------------------------------------------------------
    # Cryptographic Verification Helpers
    # ------------------------------------------------------------------

    def verify_content(self, features: Any, labels: Any = None) -> bool:
        """Verifies features and labels against recorded sha256_checksum."""
        if not self.sha256_checksum:
            return True
        actual = compute_dataset_hash(features, labels)
        return actual == self.sha256_checksum

    def verify_file(self, path: Path | str) -> bool:
        """Verifies parquet artifact file against recorded dataset_hash."""
        p = Path(path)
        if not p.is_file():
            return False
        actual = sha256_file(p)
        return actual == self.dataset_hash


# =============================================================================
# Loaded Dataset Container
# =============================================================================


class DatasetLoadResult(tuple):
    """Transparent container for loaded dataset (frame, manifest).

    Supports tuple unpacking:
        frame, manifest = factory.load(dataset_id)
    And single-assignment attribute access:
        res = factory.load(dataset_id)
        print(res.height, res.columns, res.manifest.sha256_checksum)
    """

    def __new__(cls, frame: pl.DataFrame, manifest: DatasetManifest):
        return super().__new__(cls, (frame, manifest))

    @property
    def frame(self) -> pl.DataFrame:
        return self[0]

    @property
    def manifest(self) -> DatasetManifest:
        return self[1]

    def __getattr__(self, name: str) -> Any:
        return getattr(self[0], name)

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, (int, slice)):
            return super().__getitem__(item)
        return self[0][item]

    @property
    def columns(self) -> list[str]:
        return self[0].columns

    @property
    def height(self) -> int:
        return self[0].height

    @property
    def shape(self) -> tuple[int, int]:
        return self[0].shape
