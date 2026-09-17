# TASK-002 — Market Data Ingest & Dataset Generation Pipeline

## Priority
P0

## Status
PENDING

## Objective
Implement an automated harness to fetch historical XAUUSD M1 candles from MT5 or external Parquet and build canonical (N, 50) training datasets.

## Why This Task Exists
The repository currently has zero committed market datasets, preventing clean-checkout model training reproducibility.

## Current Evidence
Repository contains no raw market parquet/csv files; DatasetFactory (model_generation/dataset_factory.py) exists but lacks ingest hook.

## Scope
Create a deterministic dataset generation script that ingests M1 bars, computes 50D features, runs TripleBarrierLabeler, and persists training artifacts.

## Explicit Non-Goals
Do not commit gigabyte-sized binary datasets to git tracking.

## Dependencies
None

## Source Areas
src/nexus_scalp/model_generation/dataset_factory.py, scripts/data/ingest_historical_candles.py

## Files Likely Involved
src/nexus_scalp/model_generation/dataset_factory.py, scripts/data/ingest_historical_candles.py

## Investigation Required
Verify MT5 history download rate limits and Parquet schema alignment with Polars.

## Implementation Outline
Build CLI command nexus data ingest --symbol XAUUSD --days 180 to generate local cached training dataset.

## Tests Required
tests/unit/test_dataset_factory_deterministic.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Clean run of ingest command produces valid TrainingDataset artifact passing GATE1_DATASET.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.
