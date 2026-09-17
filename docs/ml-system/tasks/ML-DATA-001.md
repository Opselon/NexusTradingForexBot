# ML-DATA-001 — Historical M1 Market Data Ingest & Parquet Storage Harness

STREAM: STREAM A — DATA
PRIORITY: P0
STATUS: READY
DEPENDENCIES: None
AGENT_ROLE: AGENT-DATA
OWNERSHIP_SCOPE: data/, scripts/data/, src/nexus_scalp/market_data/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Build a reproducible, automated ingestion CLI to download historical XAUUSD M1 candles from MT5 (or import external Parquet/CSV) and store standardized, validated market bars passing GATE1_DATASET.

## WHY_IT_EXISTS
The repository currently has zero committed market datasets. A fresh clone cannot reproduce training, run walk-forward folds, or benchmark models without manually sourcing broker data.

## CURRENT_EVIDENCE
- **Path:** `data/` (Lines: `N/A`)
  - **Symbol:** `data directory`
  - **Behavior:** Contains zero market bar files (gitignored by design)
  - **Classification:** `OBSERVATION`
  - **Confidence:** 100%
  - **Contradiction:** Documentation references 180-day datasets that do not exist locally
- **Path:** `src/nexus_scalp/model_generation/dataset_factory.py` (Lines: `45-120`)
  - **Symbol:** `DatasetFactory.build_training_dataset`
  - **Behavior:** Constructs features from Polars DataFrame; lacks raw data ingestion entry point
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lifecycle/gates.py` (Lines: `40-75`)
  - **Symbol:** `gate_dataset_integrity`
  - **Behavior:** Asserts min_rows >= 1000, required OHLCV columns, monotonic timestamps
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- No raw market data exists in git.
- DatasetFactory requires a DataFrame with ['time', 'open', 'high', 'low', 'close', 'tick_volume'].
- GATE1_DATASET enforces integrity.

## UNKNOWNs
- Broker rate limits and throttling parameters for historical tick/M1 downloads over MT5 API.

## SCOPE
Implement scripts/data/ingest_historical_candles.py with options for MT5 download, external CSV/Parquet import, and synthetic generation; validate against GATE1_DATASET.

## NON_GOALS
Do not commit multi-gigabyte raw market datasets into git tracking.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/dataset_factory.py`
- `src/nexus_scalp/model_lifecycle/gates.py:40-75`
- `scripts/data/`

## FILES_LIKELY_TO_CHANGE
- `scripts/data/ingest_historical_candles.py`
- `tests/unit/test_data_ingest.py`

## INVESTIGATION_PLAN
Check whether Polars or Pandas is the canonical in-memory representation for DatasetFactory; confirm datetime timezone handling (UTC required).

## IMPLEMENTATION_PLAN
1. Inspect DatasetFactory.build_training_dataset input signature.
2. Create scripts/data/ingest_historical_candles.py supporting --source [mt5|csv|synthetic] --symbol XAUUSD --days 180 --output data/raw/.
3. Implement row count, column format, and monotonic timestamp validation.
4. Add unit test tests/unit/test_data_ingest.py using synthetic sample bars.
5. Verify output file passes gate_dataset_integrity.

## TEST_PLAN
- `pytest tests/unit/test_data_ingest.py -v`

## BENCHMARK_PLAN
Ingest 50,000 M1 bars; verify ingestion throughput >= 10,000 bars/sec into Parquet.

## EVIDENCE_REQUIRED
- Script created: scripts/data/ingest_historical_candles.py
- Passing pytest execution output
- Terminal log of sample ingestion passing GATE1_DATASET

## ACCEPTANCE_CRITERIA
1. Ingestion CLI executes cleanly with exit code 0.
2. Ingested Parquet file passes gate_dataset_integrity() == True.
3. Output columns contain strictly UTC timestamps, positive prices, and zero NaNs.

## ABORT_CONDITIONS
If MT5 broker API returns corrupted bar arrays or unhandled timezone offsets, abort and report data provider defect.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/data/ingest_historical_candles.py`
- `tests/unit/test_data_ingest.py`

## SHARED_FILE_RISK
Low. AGENT-DATA owns scripts/data/ exclusively.
