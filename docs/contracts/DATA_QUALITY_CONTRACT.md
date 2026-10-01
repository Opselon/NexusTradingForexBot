# RAW DATA QUALITY CONTRACT

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_generation/data_quality.py` (`DataQualityCertifier`)  
**Scope:** Ingestion of raw market data into certified, immutable clean datasets.

---

## 1. Principle

Raw market data from MT5, CSV feeds, or historical archives must never be ingested directly into feature engineering or training without passing the canonical Raw Data Quality Gate.

Validation checks must not be scattered ad-hoc across trainers. A single authoritative certification layer enforces dataset hygiene and outputs an immutable, fingerprinted clean dataset accompanied by a machine-readable `DataQualityReport`.

---

## 2. Gate Verification Requirements

### 2.1 Structural Integrity
- **Timestamp Column:** Mandatory `'time'` column, parsed into UTC `datetime` objects.
- **Chronological Ordering:** Monotonically non-decreasing timestamps. Unsorted frames are chronologically sorted.
- **Duplicate Records & Timestamps:** Exact duplicates and duplicate timestamps are detected and purged.
- **Price Columns:** Mandatory `'open'`, `'high'`, `'low'`, `'close'` (case-insensitive normalized to lowercase).
- **Non-Finite Detection:** Rejection of `NaN`, `Inf`, and `-Inf` in any price or volume column.

### 2.2 OHLC Logical Consistency
Bars with impossible geometric relationships are rejected and recorded with explicit failure categories:
- `HIGH_BELOW_LOW`: $high < low$
- `OPEN_ABOVE_HIGH`: $open > high$
- `OPEN_BELOW_LOW`: $open < low$
- `CLOSE_ABOVE_HIGH`: $close > high$
- `CLOSE_BELOW_LOW`: $close < low$
- `NON_POSITIVE_PRICE`: $price \le 0$

### 2.3 Market Continuity & Gap Classification
Inter-bar gaps exceeding expected bar duration are detected and classified:
- `SESSION_BREAK`: Normal weekend or scheduled session closures (e.g. gap $> 48$ hours).
- `BROKER_FEED_INTERRUPTION`: Unscheduled mid-session gap.

Gaps are not blindly classified as corruption; valid market closures are classified and retained in telemetry.

### 2.4 Volume & Spread Anomalies
- Zero volume bars are cataloged.
- Negative volumes are rejected.
- Spread spikes exceeding statistical distribution limits are cataloged as `spread_anomalies`.

### 2.5 Outlier Policy: Noise vs. Extreme Events
Extreme price returns are evaluated under an explainable policy:
- Microstructure spikes that immediately revert within 1 bar with abnormal spread are cataloged as candidate feed artifacts.
- Macroeconomic shocks (e.g. non-reverting directional moves on high volume) represent real market volatility and are preserved to train volatility robustness.
- Raw data is never blindly pruned using symmetric 3-sigma cuts.

---

## 3. Machine-Readable Quality Report Schema

Every certification pass produces a `DataQualityReport` serialized as JSON (`<dataset_id>.quality.json`):

```json
{
  "dataset_id": "XAUUSD_M1_20260101_20260601",
  "raw_rows": 100000,
  "valid_rows": 99746,
  "duplicate_rows": 37,
  "duplicate_timestamps": 37,
  "invalid_ohlc": 4,
  "nan_rows": 0,
  "inf_rows": 0,
  "gap_events": 19,
  "zero_volume": 12,
  "spread_anomalies": 8,
  "outlier_candidates": 183,
  "rejected_rows": 254,
  "quality_status": "PASS",
  "trainable": true,
  "fingerprint": "c4b8e...",
  "git_commit": "614f4ef...",
  "cleaning_version": "dq_v1",
  "rejection_breakdown": {
    "HIGH_BELOW_LOW": 2,
    "OPEN_ABOVE_HIGH": 2
  }
}
```

---

## 4. Certification Verdicts

| Status | Condition | Pipeline Behavior |
|--------|-----------|-------------------|
| `PASS` | Rejection rate $< 1.0\%$, no fatal structural failures | Dataset certified for training |
| `WARN` | Rejection rate between $1.0\%$ and $5.0\%$ | Dataset certified with audit warnings |
| `FAIL` | Rejection rate $> 5.0\%$ or fatal structural collapse | Training strictly blocked |
