# ML-DATA-002 — Dataset Manifest Schema, Versioning & Artifact Hashing

STREAM: STREAM A — DATA
PRIORITY: P1
STATUS: DONE
DEPENDENCIES: ML-DATA-001
AGENT_ROLE: AGENT-DATA
OWNERSHIP_SCOPE: src/nexus_scalp/model_generation/dataset_manifest.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Design and enforce an immutable dataset manifest format recording dataset_id, row count, date range, SHA256 checksum, feature schema hash, and split indices to guarantee exact reproducibility.

## WHY_IT_EXISTS
Currently, training runs reference arbitrary local files without cryptographic manifests. If a dataset is modified or overwritten, previous model training runs cannot be reproduced or audited.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/dataset_factory.py` (Lines: `120-180`)
  - **Symbol:** `TrainingDataset`
  - **Behavior:** Contains data arrays but lacks immutable SHA256 content hash and manifest serialization
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lifecycle/models.py` (Lines: `80-110`)
  - **Symbol:** `TrainingDatasetRecord`
  - **Behavior:** Stores dataset metadata in SQLite but does not enforce content hash verification on load
  - **Classification:** `LIFECYCLE`
  - **Confidence:** 100%
  - **Contradiction:** No cryptographic content verification

## FACTS
- TrainingDataset holds features and labels.
- No content hash is computed or verified on dataset reload.

## UNKNOWNs
- Storage footprint of serializing fold split indices within dataset manifest vs recomputing dynamically.

## SCOPE
Create DatasetManifest dataclass; compute SHA256 of raw features and labels; serialize manifest.json alongside dataset; verify hash on reload.

## NON_GOALS
Do not store duplicate copies of the raw array tensors inside the manifest.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/dataset_factory.py`
- `src/nexus_scalp/model_lifecycle/models.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/model_generation/dataset_manifest.py`
- `tests/unit/test_dataset_manifest.py`

## INVESTIGATION_PLAN
Verify whether SHA256 can be computed in streaming chunks to avoid loading multi-gigabyte arrays twice into memory.

## IMPLEMENTATION_PLAN
1. Define DatasetManifest schema in dataset_manifest.py.
2. Implement compute_dataset_hash(features, labels) using hashlib.sha256.
3. Add manifest serialization to DatasetFactory.save().
4. Add hash verification check in DatasetFactory.load().
5. Add unit tests asserting tamper detection (bit-flip changes hash).

## TEST_PLAN
- `pytest tests/unit/test_dataset_manifest.py -v`

## BENCHMARK_PLAN
Measure hashing overhead on 100,000 row x 50 column dataset; must complete in < 1.0 second.

## EVIDENCE_REQUIRED
- Code diff in dataset_manifest.py
- Pytest output demonstrating tamper detection and clean round-trip serialization

## ACCEPTANCE_CRITERIA
- [x] 1. Every generated dataset produces a valid dataset_manifest.json.
- [x] 2. Modifying any value in the dataset array causes DatasetFactory.load() to raise DatasetIntegrityError.

## VERIFICATION_EVIDENCE
- Automated unit test suite: `tests/unit/test_dataset_manifest.py` (16 passing tests).
- Benchmark overhead: 100,000 rows x 50 columns hashed in ~0.012s (< 1.0s SLA).
- Tamper detection verified: bit-flip in features, label perturbation, row drop/add, and parquet byte corruption all raise `DatasetIntegrityError`.
- Critical suite manifest verification passed (`CRITICAL_SUITE_MANIFEST_OK: 205 paths all exist`).
- Gates passed: `ruff check`, `ruff format --check`, `mypy src`, `pytest tests/unit/test_dataset_manifest.py -v`.

## ABORT_CONDITIONS
If hashing causes memory exhaustion (OOM) on large datasets, optimize buffer chunking.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/model_generation/dataset_manifest.py`
- `tests/unit/test_dataset_manifest.py`

## SHARED_FILE_RISK
Low. New module owned by AGENT-DATA.
