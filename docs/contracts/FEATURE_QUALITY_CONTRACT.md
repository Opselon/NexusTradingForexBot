# FEATURE QUALITY CONTRACT

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_generation/feature_quality.py` (`FeatureQualityCertifier`)  
**Scope:** Verification of computed features before scaling, sequence construction, and training.

---

## 1. Principle

Feature engineering transforms raw price/volume bars into high-dimensional representations.
No feature matrix may be passed to training, validation, or inference without passing the canonical Feature Quality Gate.

Supported canonical feature configurations:
- **50D Contract (`scalp_v1`):** 50 engineered price-action, momentum, volatility, and volume features.
- **70D Contract (`scalp_v3`):** 70 features extending 50D with order-flow, book imbalance, liquidity microstructure, and macroeconomic news embeddings.

---

## 2. Gate Verification Requirements

### 2.1 Dimensional & Schema Integrity
- **Contract Dimension:** Must match 50 or 70. Any other dimension fails loudly.
- **Column Consistency:** Feature column count must match the declared schema definition exactly.
- **Canonical Schema Hash:** SHA-256 fingerprint computed across the sorted feature definitions.

### 2.2 Numerical Validity
- **Non-Finite Detection:** Rejection of `NaN`, `Inf`, and `-Inf` rates exceeding threshold ($0.1\%$).
- **Variance Screening:** Detection of zero-variance (strictly constant) or near-zero variance ($\sigma^2 < 10^{-8}$) features. Constant columns convey zero information and break Z-score standardization.

### 2.3 Redundancy & Multicollinearity
- **Exact Aliases:** Identification of feature pairs with identical values across all samples ($\Delta = 0$).
- **Correlation Ceiling:** Identification of feature pairs with Pearson $|r| > 0.999$, cataloging redundant transformations.

### 2.4 Temporal Stability & Distribution Drift
- Features are partitioned chronologically into temporal blocks (e.g. 4 contiguous deciles).
- Mean and standard deviation are evaluated per block.
- Features exhibiting catastrophic distribution drift ($> 10\times$ baseline variance across blocks) are flagged for audit.

---

## 3. Machine-Readable Feature Report Schema

Every certification pass produces a `FeatureQualityReport` serialized as JSON (`<model_id>.feature_quality.json`):

```json
{
  "schema_id": "scalp_v1",
  "schema_hash": "a1b2c3d...",
  "dimension": 50,
  "samples": 10000,
  "features": [
    {
      "index": 0,
      "name": "returns_1m",
      "status": "PASS",
      "mean": 0.0001,
      "std": 0.0012,
      "variance": 1.44e-6,
      "nan_rate": 0.0,
      "inf_rate": 0.0,
      "constant": false,
      "issues": []
    }
  ],
  "passed": 50,
  "warned": 0,
  "failed": 0,
  "constant_features": [],
  "nan_features": [],
  "alias_groups": [],
  "quality_status": "PASS",
  "warnings": []
}
```

---

## 4. Certification Verdicts

| Status | Condition | Pipeline Behavior |
|--------|-----------|-------------------|
| `PASS` | All features finite, non-constant, zero fatal issues | Model pipeline proceeds |
| `WARN` | Highly correlated features or mild drift detected | Proceed with recorded warnings |
| `FAIL` | Any constant feature, NaN feature, or dimension mismatch | Model training strictly aborted |
