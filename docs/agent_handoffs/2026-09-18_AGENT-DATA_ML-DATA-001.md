# ML-DATA-001 Handoff — Historical M1 Market Data Ingest & Parquet Storage Harness

## Task Summary
- **Task ID**: `ML-DATA-001`
- **Agent**: `AGENT-DATA`
- **Date**: `2026-09-18`
- **Branch**: `agent/feature/ML-DATA-001`
- **PR**: [#259](https://github.com/Opselon/NexusTradingForexBot/pull/259)
- **Merge Commit**: `d52bcde6c7375a9d6ad06ab29521eb3e432b6d1f`
- **Status**: `DONE` (Merged & Verified on Main)
- **Goal**: Build reproducible, automated ingestion CLI to download historical M1 candles from MT5 (or import CSV / synthetic) and store standardized, validated market bars passing canonical dataset contracts into Parquet storage.

---

## Deliverables
1. **Canonical Ingestion CLI**: `scripts/data/ingest_historical_candles.py`
   - Three ingestion sources:
     - `synthetic`: Deterministic geometric Brownian motion with volatility jumps (ideal for offline CI).
     - `csv`: Broker-exported CSV parsing with schema column mapping.
     - `mt5`: MetaTrader 5 broker gateway integration via `MT5Adapter`.
   - Strict contract validation:
     - Canonical columns: `time`, `open`, `high`, `low`, `close`, `tick_volume`
     - UTC timestamps, strictly monotonic increasing
     - Positive prices ($> 0$), valid OHLC geometry ($high \ge \max(open, close), low \le \min(open, close)$)
     - Zero NaNs / Nulls / Infs
     - Integration with `normalize_bars_frame`
   - Storage format: Apache Parquet (ZSTD-compressed, snappy fallback)
   - Layout: `data/raw/{symbol}_{timeframe}.{source}.parquet` (or custom `--output`)
   - CLI flags: `--json` (with complete stderr log isolation for pure JSON stdout) and `--benchmark`
2. **Deterministic Offline Unit Tests**: `tests/unit/test_data_ingest.py`
   - 18 comprehensive tests covering synthetic generation, CSV parsing, fail-loud validations, mock/dead MT5 adapters, throughput SLA, and CLI execution.
   - 100% passing without external dependencies.
3. **Manifest & Gates**:
   - Added to `tests/critical_suite.txt` (201/201 valid paths).
   - Validated by `verify_critical_suite_manifest.py`.
