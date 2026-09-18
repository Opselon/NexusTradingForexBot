# ML-DATA-001 — Historical M1 Market Data Ingest & Parquet Storage Harness

STREAM: STREAM A — DATA
PRIORITY: P0
STATUS: DONE
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
- [x] 1. Ingestion CLI executes cleanly with exit code 0 (`scripts/data/ingest_historical_candles.py`).
- [x] 2. Ingested Parquet file passes schema integrity validation and `normalize_bars_frame` contract.
- [x] 3. Output columns contain strictly UTC timestamps, positive prices, and zero NaNs.
- [x] 4. Monotonic increasing timestamp verification enforced.
- [x] 5. Deterministic synthetic test generation for reproducible offline testing in CI.
- [x] 6. Throughput benchmark CLI and harness verifies SLA >= 10,000 bars/sec.
- [x] 7. Automated test suite `tests/unit/test_data_ingest.py` (18/18 tests passed) registered in `tests/critical_suite.txt`.

## ABORT_CONDITIONS
If MT5 broker API returns corrupted bar arrays or unhandled timezone offsets, abort and report data provider defect.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/data/ingest_historical_candles.py`
- `tests/unit/test_data_ingest.py`

## EXECUTION_EVIDENCE_AND_COMPLETION_RECORD
- **Task ID**: `ML-DATA-001`
- **Execution Date**: `2026-09-18`
- **Agent**: `AGENT-DATA`
- **Branch**: `agent/feature/ML-DATA-001`
- **PR**: [#259](https://github.com/Opselon/NexusTradingForexBot/pull/259) (Merge Commit: `d52bcde6c7375a9d6ad06ab29521eb3e432b6d1f`)
- **Status**: `DONE` (Merged & Verified on Main)
- **Script**: `scripts/data/ingest_historical_candles.py`
  - Supports 3 ingestion sources: `synthetic` (deterministic geometric Brownian motion + volatility jumps), `csv` (broker export with lenient schema matching), and `mt5` (live broker gateway integration via `MT5Adapter`).
  - Strict contract enforcement: monotonic UTC timestamps, positive prices, valid OHLC geometry ($high \ge \max(open, close), low \le \min(open, close)$), 0 NaNs/Nulls, min row count threshold.
  - Formats output as Apache Parquet (ZSTD-compressed, snappy fallback) to `data/raw/{symbol}_{timeframe}.{source}.parquet` (or custom `--output`).
  - Complete CLI interface with `--json` isolation (redirects console logging to stderr to keep stdout strictly pure JSON) and `--benchmark` mode.
- **Test Suite**: `tests/unit/test_data_ingest.py`
  - 18 automated offline unit tests covering synthetic generation, CSV parsing, fail-loud validations (missing columns, insufficient rows, price anomalies), mock & dead MT5 adapters, throughput SLA, and CLI execution.
  - All 18 tests pass in < 5 seconds.
- **Manifest**: Registered in `tests/critical_suite.txt` (201/201 paths verified).

## SHARED_FILE_RISK
Low. AGENT-DATA owns scripts/data/ exclusively.
