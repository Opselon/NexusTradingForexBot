# TASK-002 — Deterministic Market Data Ingest & Dataset Pipeline Harness

Priority: P0
Status: READY
Type: Infrastructure & Data Tooling
Dependencies: None
Blocks: TASK-005, TASK-013
Human Decision Required: NO
Risk: Medium (Broker API rate limits, large Parquet generation, memory footprint)
Estimated Scope: 1 ingest script (scripts/data/ingest_historical_candles.py), 1 test suite, ~250 LOC

## Objective
Build an automated, reproducible data ingestion script to fetch historical XAUUSD M1 candles from MT5 / CSV and generate canonical, labeled (N, 50) training datasets passing GATE1_DATASET.

## Problem / Why
The repository currently has zero market datasets committed to Git. A clean checkout cannot reproduce model training or execute full walk-forward folds without manually obtaining broker data.

## Current Evidence
- **Path:** `data/` (Lines: `N/A`)
  - **Symbol:** `N/A`
  - **Behavior:** Directory contains zero historical parquet/csv files (gitignored)
  - **Classification:** `OBSERVATION`
  - **Confidence:** 100%
  - **Contradiction:** Docs reference 180-day training datasets that do not exist in fresh clones
- **Path:** `src/nexus_scalp/model_generation/dataset_factory.py` (Lines: `45-120`)
  - **Symbol:** `DatasetFactory.build_training_dataset`
  - **Behavior:** Expects Polars/Pandas DataFrame with OHLCV bars, generates features, applies TripleBarrierLabeler
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** No automated ingestion CLI to feed bars into the factory
- **Path:** `src/nexus_scalp/model_lifecycle/gates.py` (Lines: `40-75`)
  - **Symbol:** `gate_dataset_integrity`
  - **Behavior:** GATE1_DATASET checks min_rows >= 1000, required OHLCV columns, non-empty features
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Implement scripts/data/ingest_historical_candles.py with CLI arguments (--symbol XAUUSD --days 180 --output data/raw/).
2. Support MT5 broker history download when running on Windows / remote gateway, with CSV/Parquet fallback for offline/Linux environments.
3. Pipe ingested bars through ScalpFeatureEngine and TripleBarrierLabeler to produce a standardized TrainingDataset artifact.
4. Write tests/unit/test_dataset_ingest_pipeline.py asserting output passes GATE1_DATASET.

## Non-Goals
Do not commit multi-gigabyte raw market datasets to Git history (keep under data/ and gitignored).

## Preconditions
Network connectivity to MT5 gateway OR local sample CSV bar file.

## Dependencies
None

## Blocks
TASK-005 (Purge/Embargo Verification), TASK-013 (CI Training Validation Gap)

## Source Areas
- `src/nexus_scalp/model_generation/dataset_factory.py:1-150`
- `src/nexus_scalp/features/scalp_features.py:481-550`
- `src/nexus_scalp/labeling/triple_barrier.py:1-120`
- `src/nexus_scalp/model_lifecycle/gates.py:40-75`

## Investigation
Examine DatasetFactory to confirm whether it expects polars.DataFrame or pandas.DataFrame, and verify that timestamp sorting is strictly ascending.

## Implementation Plan
1. Inspect DatasetFactory.build_training_dataset input expectations.
2. Write scripts/data/ingest_historical_candles.py supporting --source [mt5|csv|synthetic].
3. Add validation step checking that row count >= 1000 and columns match ['time', 'open', 'high', 'low', 'close', 'tick_volume'].
4. Compute 50D feature matrix via ScalpFeatureEngine.compute_from_bars.
5. Apply TripleBarrierLabeler with default horizons (pt=1.5, sl=1.0, max_bars=15).
6. Save output metadata and features to artifacts/datasets/<dataset_id>/.
7. Run gate_dataset_integrity to assert pass.

## Tests
- `pytest tests/unit/test_dataset_factory.py -v`
- `python scripts/data/ingest_historical_candles.py --source synthetic --rows 5000 --output /tmp/test_dataset`

## Validation / Benchmark
Generated dataset must contain >= 1,000 rows, exactly 50 feature columns, non-null labels in {0, 1, 2}, and pass gate_dataset_integrity with status PASSED.

## Evidence Required
- Script created: scripts/data/ingest_historical_candles.py
- Test created: tests/unit/test_dataset_ingest_pipeline.py
- Terminal output demonstrating successful synthetic dataset generation and GATE1_DATASET validation

## Acceptance Criteria
1. scripts/data/ingest_historical_candles.py executes cleanly without errors.
2. Output dataset artifact passes gate_dataset_integrity(dataset) == True.
3. Zero NaN or Inf values in generated (N, 50) feature matrix.

## Failure / Abort Conditions
If ScalpFeatureEngine fails to compute features on standard OHLCV input or requires unavailable external feeds, STOP and document the failure.

## Human Stop Conditions
None.

## Expected Output
Working ingestion CLI and passing pipeline tests allowing any developer to generate training datasets locally.
