# ML-DATA-002 Handoff — Dataset Manifest Schema, Versioning & Artifact Hashing

## Task Summary
- **Task ID**: `ML-DATA-002`
- **Agent**: `AGENT-DATA`
- **Stream**: `STREAM A — DATA`
- **Date**: `2026-09-18`
- **Branch**: `agent/data/ml-data-002`
- **Status**: `DONE`
- **Goal**: Design and enforce an immutable dataset manifest format recording dataset_id, row count, date range, SHA256 checksum, feature schema hash, and split indices to guarantee exact reproducibility and cryptographic tamper detection.

---

## Deliverables

1. **Dataset Manifest Schema & Hashing Engine**: `src/nexus_scalp/model_generation/dataset_manifest.py`
   - `DatasetManifest`: Immutable Pydantic model (spec 7 / 8 / 17) recording:
     - `dataset_id`: Deterministic dataset identifier.
     - `row_count` & `row_counts`: Total rows and split breakdown (`train`, `val`, `test`, `purged`).
     - `temporal_range` & `date_range`: ISO-8601 start and end timestamps (with automatic synchronization).
     - `sha256_checksum`: Cryptographic SHA-256 hash over raw features and labels content.
     - `dataset_hash`: SHA-256 hash over the serialized parquet artifact bytes.
     - `feature_schema_id` & `feature_schema_hash`: Canonical schema contract binding (50D `scalp_v1` / 70D `scalp_v3`).
     - `split_indices` & `split_ranges`: Explicit fold split indices and boundaries for exact reproducibility.
     - `created_at` (UTC) & `metadata`.
   - `compute_dataset_hash`: Fast, memory-safe, streaming SHA-256 computation over NumPy arrays, Polars DataFrames, PyTorch tensors, Python sequences, and raw files. Chunked streaming (`chunk_size=65536`) guarantees $< 1$ MB memory overhead even on multi-GB datasets.
   - `DatasetIntegrityError`: Specific exception (inheriting from `DatasetCorruptionError`) raised whenever dataset values or manifest checksums fail cryptographic verification.
   - `DatasetLoadResult`: Transparent container supporting both tuple unpacking (`frame, manifest = factory.load(...)`) and transparent attribute forwarding (`res.height`, `res.columns`, `res.manifest`).

2. **Integration into DatasetFactory**: `src/nexus_scalp/model_generation/dataset_factory.py`
   - `DatasetFactory.build()`: Automatically computes `sha256_checksum`, `row_count`, `split_indices`, and records them in `dataset_manifest.json`.
   - `DatasetFactory.save()`: Saves dataset parquet and manifest with cryptographic checksum computation and split indexing.
   - `DatasetFactory.load()`: Loads and cryptographically verifies:
     1. Parquet artifact existence and manifest JSON presence.
     2. Parquet file checksum matches manifest `dataset_hash`.
     3. Array content hash matches manifest `sha256_checksum`.
     4. Row count matches manifest `row_count`.
     - Any perturbation, bit-flip, or row addition/deletion raises `DatasetIntegrityError`.

3. **Domain Contracts Re-export**: `src/nexus_scalp/model_generation/models.py` and `__init__.py`
   - Re-exports `DatasetManifest`, `DatasetIntegrityError`, `DatasetLoadResult`, and `compute_dataset_hash` with `__all__` export alignment.

4. **Automated Unit Test Suite**: `tests/unit/test_dataset_manifest.py`
   - 16 passing unit tests validating:
     - Schema immutability, serialization, and roundtrip JSON loading.
     - Deterministic hashing across NumPy, Polars, and PyTorch tensors.
     - Bit-flip tamper sensitivity (1-bit perturbation changes hash).
     - Label tampering detection.
     - Benchmark overhead SLA: 100,000 rows x 50 columns hashed in $\sim 0.012$s ($< 1.0$s SLA).
     - `DatasetFactory.save()` and `DatasetFactory.load()` roundtrip.
     - Array modification, label modification, row addition/drop, and manifest tampering detection.
     - `DatasetFactory.build()` end-to-end manifest generation and reload verification.

5. **CI/CD Critical Suite Manifest**: `tests/critical_suite.txt`
   - Registered `tests/unit/test_dataset_manifest.py`.
   - Verified via `scripts/ci/verify_critical_suite_manifest.py` (205 paths valid).

---

## Verification Evidence
- `pytest tests/unit/test_dataset_manifest.py -v`: 16 passed in 3.36s.
- `ruff check`: All checks passed.
- `ruff format --check`: 5 files already formatted.
- `mypy src`: Success: no issues found in 5 source files.
- `scripts/ci/verify_critical_suite_manifest.py`: `CRITICAL_SUITE_MANIFEST_OK: 205 paths all exist`.
