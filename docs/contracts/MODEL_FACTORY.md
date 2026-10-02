# MODEL FACTORY ARCHITECTURE

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_lab/studio_trainer.py`, `src/nexus_scalp/model_generation/`  
**Scope:** Canonical end-to-end training and certification engine for 50D and 70D models.

---

## 1. Primary Objective

The Model Factory turns raw market data into production-grade, reproducible, leakage-safe, data-certified neural network models.

All execution surfaces—CLI commands, Model Studio web routes, and CI test pipelines—consume the **single canonical trainer backend** (`StudioTrainer`). There are no duplicate training implementations.

---

## 2. Canonical Pipeline Topology

```
MT5 / HISTORICAL SOURCE
        │
        ▼
RAW MARKET DATA
        │
        ▼
DATA IDENTITY + FINGERPRINT (SHA-256)
        │
        ▼
RAW DATA QUALITY GATE (`DataQualityCertifier`)
        │
        ├──[REJECT]──► Malformed / Incomplete / Impossible Bars
        │
        ▼
CLEAN / CERTIFIED IMMUTABLE DATASET
        │
        ▼
FEATURE ENGINE (50D `scalp_v1` or 70D `scalp_v3`)
        │
        ▼
FEATURE QUALITY GATE (`FeatureQualityCertifier`)
        │
        ├──[REJECT]──► Redundancy / Constant Columns / NaN Drift
        │
        ▼
CAUSAL TRIPLE BARRIER LABEL ENGINE
        │
        ▼
LABEL QUALITY / DISTRIBUTION AUDIT (`LabelQualityAudit`)
        │
        ├──[REJECT]──► Class Collapse / Regime Imbalance
        │
        ▼
TEMPORAL SPLIT (Train / Validation / OOS Holdout)
        │
        ▼
PURGE + EMBARGO (De Prado overlap decontamination)
        │
        ▼
TRAIN-ONLY SCALING (Fitted on Train; frozen for Val & OOS)
        │
        ▼
SEQUENCE BUILDING (L=32, F=50 or 70)
        │
        ▼
TRAINING (PyTorch ScalpNet / MiniTransformer)
        │
        ▼
VALIDATION & WALK-FORWARD EVALUATION
        │
        ▼
OUT-OF-SAMPLE (OOS) HOLDOUT TESTING
        │
        ▼
ROBUSTNESS / ABLATION / REGIME VALIDATION
        │
        ▼
MODEL CERTIFICATION GATE (`ModelCertificationGate`)
        │
        ├──[REJECT]──► Non-certified Candidate
        │
        ▼
MODEL ARTIFACT BUNDLE (`.pt`, `.scaler.npz`, `.meta.json`, `.certificate.json`)
        │
        ▼
MODEL STUDIO & RUNTIME HOT-LOADER
```

---

## 3. Leakage Prevention Contract

1. **Scaler Leakage:** Mean ($\mu$) and standard deviation ($\sigma$) vectors are computed exclusively over the training set (`X_train`). Val and OOS data are standardized using frozen training parameters.
2. **Temporal Split:** Strict chronological split. Random $k$-fold cross-validation is forbidden on financial time series.
3. **Purge & Embargo:** Observations within the Triple Barrier holding horizon and subsequent cooling embargo period are purged at train/val/test boundaries.
4. **Sequence Leakage:** Windows spanning cross-split boundaries, cross-symbol boundaries, or inter-bar gaps $> 10$ minutes are marked invalid.
5. **Ablation & Hyperparameter Tuning:** All threshold optimization, feature pruning, and hyperparameter sweeps are restricted to training and validation folds; the OOS set is touched once at final certification.
