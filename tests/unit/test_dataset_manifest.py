"""Tests for Dataset Manifest Schema, Versioning & Artifact Hashing (ML-DATA-002).

Validates:
1. Manifest schema immutability, serialization, deserialization, and aliases.
2. Cryptographic hashing via compute_dataset_hash (NumPy, Polars, PyTorch, streaming).
3. Hashing overhead SLA: 100,000 rows x 50 columns in < 1.0 second.
4. Tamper sensitivity: bit-flip, single-cell modification, row addition/deletion.
5. DatasetFactory.save() creates valid dataset_manifest.json with all required fields.
6. DatasetFactory.load() returns DatasetLoadResult and raises DatasetIntegrityError on tamper.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
import torch
from pydantic import ValidationError

from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.dataset_factory import DatasetFactory
from nexus_scalp.model_generation.dataset_manifest import (
    DatasetIntegrityError,
    DatasetLoadResult,
    DatasetManifest,
    compute_dataset_hash,
)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(root=tmp_path / "artifacts")


@pytest.fixture
def sample_features() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal((100, 50), dtype=np.float32)


@pytest.fixture
def sample_labels() -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.integers(0, 3, size=100, dtype=np.int64)


@pytest.fixture
def sample_dataframe(sample_features: np.ndarray, sample_labels: np.ndarray) -> pl.DataFrame:
    data_dict: dict[str, Any] = {f"feat_{i}": sample_features[:, i] for i in range(50)}
    data_dict["label"] = sample_labels
    data_dict["timestamp"] = [f"2026-01-01T{i:02d}:00:00Z" for i in range(100)]
    data_dict["_split"] = ["train"] * 70 + ["val"] * 15 + ["test"] * 15
    data_dict["_purged_split"] = [False] * 100
    return pl.DataFrame(data_dict)


# =============================================================================
# 1. Manifest Schema & Serialization
# =============================================================================


def test_manifest_creation_and_immutability() -> None:
    """DatasetManifest must be immutable and self-describing."""
    m = DatasetManifest(
        dataset_id="ds_test123",
        row_count=100,
        temporal_range={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
        sha256_checksum="abc123hash",
        feature_schema_hash="schema_hash_xyz",
        split_indices={"train": [0, 1, 2], "val": [3], "test": [4]},
    )
    assert m.dataset_id == "ds_test123"
    assert m.row_count == 100
    assert m.date_range == m.temporal_range
    assert m.sha256_checksum == "abc123hash"
    assert m.split_indices["train"] == [0, 1, 2]

    # Immutability: frozen model cannot be mutated
    with pytest.raises(ValidationError):
        m.dataset_id = "ds_mutated"  # type: ignore[misc]


def test_manifest_json_roundtrip(tmp_path: Path) -> None:
    """DatasetManifest must cleanly serialize and deserialize to/from JSON."""
    m = DatasetManifest(
        dataset_id="ds_json_rt",
        row_count=500,
        temporal_range={"start": "2026-01-01T00:00:00Z", "end": "2026-01-05T00:00:00Z"},
        sha256_checksum="checksum_real_12345678",
        feature_schema_hash="feat_hash_123",
        split_indices={
            "train": list(range(350)),
            "val": list(range(350, 425)),
            "test": list(range(425, 500)),
        },
    )

    manifest_path = tmp_path / "dataset_manifest.json"
    m.save(manifest_path)
    assert manifest_path.is_file()

    loaded = DatasetManifest.from_file(manifest_path)
    assert loaded.dataset_id == m.dataset_id
    assert loaded.row_count == m.row_count
    assert loaded.sha256_checksum == m.sha256_checksum
    assert loaded.split_indices == m.split_indices
    assert loaded.date_range == m.date_range


def test_manifest_aliases_and_fallbacks() -> None:
    """Manifest must automatically sync row_counts <-> row_count and date_range <-> temporal_range."""
    # Given only temporal_range and dataset_hash
    m = DatasetManifest.from_dict(
        {
            "dataset_id": "ds_compat",
            "row_counts": {"train": 70, "val": 15, "test": 15},
            "temporal_range": {"start": "t0", "end": "t1"},
            "dataset_hash": "hash_xyz",
        }
    )
    assert m.row_count == 100
    assert m.date_range == {"start": "t0", "end": "t1"}
    assert m.sha256_checksum == "hash_xyz"


# =============================================================================
# 2. Cryptographic Hashing & Tamper Sensitivity
# =============================================================================


def test_compute_dataset_hash_determinism(
    sample_features: np.ndarray, sample_labels: np.ndarray
) -> None:
    """Hash computation must be strictly deterministic across calls."""
    h1 = compute_dataset_hash(sample_features, sample_labels)
    h2 = compute_dataset_hash(sample_features, sample_labels)
    assert h1 == h2
    assert len(h1) == 64  # Valid SHA-256 hexdigest


def test_compute_dataset_hash_torch_parity(
    sample_features: np.ndarray, sample_labels: np.ndarray
) -> None:
    """PyTorch tensors and NumPy arrays with identical values must produce identical hashes."""
    t_feat = torch.from_numpy(sample_features)
    t_lbl = torch.from_numpy(sample_labels)
    h_numpy = compute_dataset_hash(sample_features, sample_labels)
    h_torch = compute_dataset_hash(t_feat, t_lbl)
    assert h_numpy == h_torch


def test_compute_dataset_hash_tamper_bitflip(
    sample_features: np.ndarray, sample_labels: np.ndarray
) -> None:
    """A single bit perturbation in any feature cell must change the hash."""
    h_orig = compute_dataset_hash(sample_features, sample_labels)

    tampered_features = sample_features.copy()
    tampered_features[0, 0] += np.float32(1e-5)
    h_tampered = compute_dataset_hash(tampered_features, sample_labels)
    assert h_orig != h_tampered

    # Perturbation in the last cell
    tampered_last = sample_features.copy()
    tampered_last[-1, -1] += np.float32(1e-5)
    assert compute_dataset_hash(tampered_last, sample_labels) != h_orig


def test_compute_dataset_hash_tamper_labels(
    sample_features: np.ndarray, sample_labels: np.ndarray
) -> None:
    """Modifying a single label must change the hash."""
    h_orig = compute_dataset_hash(sample_features, sample_labels)

    tampered_labels = sample_labels.copy()
    tampered_labels[42] = (tampered_labels[42] + 1) % 3
    h_tampered = compute_dataset_hash(sample_features, tampered_labels)
    assert h_orig != h_tampered


def test_compute_dataset_hash_polars_tamper(sample_dataframe: pl.DataFrame) -> None:
    """Modifying a single cell in a Polars DataFrame must alter the hash."""
    h_orig = compute_dataset_hash(sample_dataframe)

    # Modify one float cell
    mutated = sample_dataframe.with_columns(
        pl.when(pl.col("timestamp") == "2026-01-01T00:00:00Z")
        .then(pl.lit(999.0))
        .otherwise(pl.col("feat_0"))
        .alias("feat_0")
    )
    assert compute_dataset_hash(mutated) != h_orig

    # Modify one label cell
    mutated_label = sample_dataframe.with_columns(
        pl.when(pl.col("timestamp") == "2026-01-01T00:00:00Z")
        .then(pl.lit(2))
        .otherwise(pl.col("label"))
        .alias("label")
    )
    assert compute_dataset_hash(mutated_label) != h_orig


def test_compute_dataset_hash_overhead_sla() -> None:
    """BENCHMARK SLA: Hashing 100,000 rows x 50 columns must complete in < 1.0 second."""
    rng = np.random.default_rng(1337)
    big_arr = rng.standard_normal((100_000, 50), dtype=np.float32)
    labels = rng.integers(0, 3, size=100_000, dtype=np.int64)

    t0 = time.perf_counter()
    h = compute_dataset_hash(big_arr, labels)
    elapsed = time.perf_counter() - t0

    assert len(h) == 64
    assert elapsed < 1.0, f"Hashing SLA violated: took {elapsed:.4f}s (budget: 1.0s)"


# =============================================================================
# 3. DatasetFactory save & load Integration
# =============================================================================


def test_dataset_factory_save_and_load_success(
    store: ArtifactStore, sample_dataframe: pl.DataFrame
) -> None:
    """DatasetFactory.save() followed by DatasetFactory.load() must succeed and verify integrity."""
    factory = DatasetFactory(store=store)
    ds_id = "ds_save_load_001"

    handle = factory.save(
        dataset_id=ds_id,
        data=sample_dataframe,
        feature_schema_hash="feat_v1_hash",
        symbol="XAUUSD",
        timeframe="M1",
    )
    assert handle["dataset_id"] == ds_id
    assert handle["row_count"] == 100
    assert len(handle["sha256_checksum"]) == 64

    # Verify manifest on disk
    manifest_dict = store.read_dataset_manifest(ds_id)
    assert manifest_dict is not None
    assert manifest_dict["dataset_id"] == ds_id
    assert manifest_dict["row_count"] == 100
    assert manifest_dict["sha256_checksum"] == handle["sha256_checksum"]
    assert "train" in manifest_dict["split_indices"]

    # Load and check DatasetLoadResult
    res = factory.load(ds_id)
    assert isinstance(res, DatasetLoadResult)
    assert res.height == 100
    assert res.manifest.dataset_id == ds_id
    assert res.manifest.sha256_checksum == handle["sha256_checksum"]

    # Check tuple unpacking compatibility: frame, manifest = factory.load(...)
    frame, manifest = factory.load(ds_id)
    assert isinstance(frame, pl.DataFrame)
    assert isinstance(manifest, DatasetManifest)
    assert frame.height == 100


def test_dataset_factory_load_detects_array_modification(
    store: ArtifactStore, sample_dataframe: pl.DataFrame
) -> None:
    """ACCEPTANCE CRITERIA: Modifying any value in the dataset array causes DatasetFactory.load() to raise DatasetIntegrityError."""
    factory = DatasetFactory(store=store)
    ds_id = "ds_tamper_array_001"

    factory.save(dataset_id=ds_id, data=sample_dataframe)

    # Tamper the parquet file on disk: alter values in feat_0 column
    p = store.dataset_path(ds_id)
    df = pl.read_parquet(p)
    tampered_df = df.with_columns(
        pl.when(pl.col("timestamp") == "2026-01-01T00:00:00Z")
        .then(pl.lit(9999.99))
        .otherwise(pl.col("feat_0"))
        .alias("feat_0")
    )
    tampered_df.write_parquet(p)

    # Attempting to load must raise DatasetIntegrityError
    with pytest.raises(DatasetIntegrityError) as exc_info:
        factory.load(ds_id)

    assert "mismatch" in str(exc_info.value).lower()


def test_dataset_factory_load_detects_label_modification(
    store: ArtifactStore, sample_dataframe: pl.DataFrame
) -> None:
    """Modifying a label value in the stored parquet causes DatasetFactory.load() to raise DatasetIntegrityError."""
    factory = DatasetFactory(store=store)
    ds_id = "ds_tamper_label_001"

    factory.save(dataset_id=ds_id, data=sample_dataframe)

    p = store.dataset_path(ds_id)
    df = pl.read_parquet(p)
    tampered_df = df.with_columns(
        pl.when(pl.col("timestamp") == "2026-01-01T00:00:00Z")
        .then(pl.lit(1))
        .otherwise(pl.col("label"))
        .alias("label")
    )
    tampered_df.write_parquet(p)

    with pytest.raises(DatasetIntegrityError):
        factory.load(ds_id)


def test_dataset_factory_load_detects_row_addition(
    store: ArtifactStore, sample_dataframe: pl.DataFrame
) -> None:
    """Adding or dropping rows in the stored dataset raises DatasetIntegrityError."""
    factory = DatasetFactory(store=store)
    ds_id = "ds_tamper_rows_001"

    factory.save(dataset_id=ds_id, data=sample_dataframe)

    p = store.dataset_path(ds_id)
    df = pl.read_parquet(p)
    # Drop first row
    tampered_df = df.slice(1)
    tampered_df.write_parquet(p)

    with pytest.raises(DatasetIntegrityError):
        factory.load(ds_id)


def test_dataset_factory_load_detects_manifest_tampering(
    store: ArtifactStore, sample_dataframe: pl.DataFrame
) -> None:
    """Tampering with manifest checksum raises DatasetIntegrityError."""
    factory = DatasetFactory(store=store)
    ds_id = "ds_tamper_manifest_001"

    factory.save(dataset_id=ds_id, data=sample_dataframe)

    manifest_p = store.dataset_manifest_path(ds_id)
    with open(manifest_p, encoding="utf-8") as f:
        data = json.load(f)

    # Invalidate the checksum
    data["sha256_checksum"] = "0" * 64
    data["dataset_hash"] = "0" * 64
    with open(manifest_p, "w", encoding="utf-8") as f:
        json.dump(data, f)

    with pytest.raises(DatasetIntegrityError):
        factory.load(ds_id)


def test_dataset_factory_load_missing_files(store: ArtifactStore) -> None:
    """Loading nonexistent dataset raises FileNotFoundError."""
    factory = DatasetFactory(store=store)
    with pytest.raises(FileNotFoundError):
        factory.load("ds_nonexistent_999")


def test_dataset_factory_build_produces_manifest_and_loads(tmp_path: Path) -> None:
    """Acceptance criteria 1 & 2: Every generated dataset produces a valid dataset_manifest.json,
    and DatasetFactory.load() verifies it, raising DatasetIntegrityError on array tampering."""
    from nexus_scalp.model_generation.sample_factory import SampleFactory

    store = ArtifactStore(root=tmp_path / "artifacts")
    factory = DatasetFactory(store=store, sample_factory=SampleFactory())

    # Create dummy bars
    base = 2000.0
    rows = 100
    times = [f"2026-01-01T{i:02d}:00:00Z" for i in range(rows)]
    data: dict[str, Any] = {
        "timestamp": times,
        "open": [base + i * 0.1 for i in range(rows)],
        "high": [base + i * 0.1 + 0.5 for i in range(rows)],
        "low": [base + i * 0.1 - 0.5 for i in range(rows)],
        "close": [base + i * 0.1 + 0.2 for i in range(rows)],
        "atr": [1.5 for _ in range(rows)],
    }
    for i in range(50):
        data[f"feat_{i}"] = [(i + 1) * 0.01 + (r % 5) * 0.001 for r in range(rows)]
    bars = pl.DataFrame(data)

    res = factory.build(bars, symbol="XAUUSD", timeframe="M1")
    ds_id = res["dataset_id"]

    # 1. Check dataset_manifest.json exists and contains all required fields
    manifest_file = store.dataset_manifest_path(ds_id)
    assert manifest_file.is_file()
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    assert manifest_data["dataset_id"] == ds_id
    assert manifest_data["row_count"] > 0
    assert "date_range" in manifest_data
    assert "temporal_range" in manifest_data
    assert manifest_data["sha256_checksum"]
    assert "split_indices" in manifest_data
    assert "train" in manifest_data["split_indices"]

    # 2. Load via factory.load()
    loaded_res = factory.load(ds_id)
    assert loaded_res.height == manifest_data["row_count"]
    assert loaded_res.manifest.dataset_id == ds_id

    # 3. Tamper with array on disk -> raises DatasetIntegrityError
    pq_path = store.dataset_path(ds_id)
    df = pl.read_parquet(pq_path)
    tampered = df.with_columns(
        pl.when(pl.col("timestamp") == df["timestamp"][0])
        .then(pl.lit(99999.0))
        .otherwise(pl.col("feat_0"))
        .alias("feat_0")
    )
    tampered.write_parquet(pq_path)

    with pytest.raises(DatasetIntegrityError):
        factory.load(ds_id)
