# MODEL STUDIO CONTROL PLANE

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/web/model_studio_routes.py`, `frontend/src/features/model-studio/`  
**Scope:** Interactive operator interface and REST API surface for model lifecycle management.

---

## 1. Overview

Model Studio is the unified control plane exposing the Model Factory lifecycle.
It governs:
1. Ingestion of raw market data and inspection of raw data quality.
2. 50D (`scalp_v1`) and 70D (`scalp_v3`) feature inspection, normalization calibration, and drift analysis.
3. Model training dispatch with live epoch, loss, and validation metrics progress streaming.
4. Formal 10-gate certification of candidate model bundles.
5. Strict load contract verification before hot-loading weights into the live trading engine.

---

## 2. Model Lifecycle States

A model checkpoint occupies exactly one of eight canonical lifecycle states:

```
  ┌─────────┐       ┌──────────┐       ┌────────────┐       ┌─────────────┐
  │  DRAFT  │ ───►  │ TRAINING │ ───►  │ VALIDATING │ ───►  │ OOS_TESTING │
  └─────────┘       └──────────┘       └────────────┘       └─────────────┘
                                                                   │
                                                                   ▼
       ┌──────────┐                            ┌───────────┐   [CERTIFY]
       │ REJECTED │ ◄────────────────────────  │ CERTIFIED │ ◄─────┘
       └──────────┘                            └───────────┘
                                                     │
                                                     ▼   [HOT-LOAD]
                                               ┌───────────┐
                                               │  LOADED   │
                                               └───────────┘
                                                     │
                                                     ▼   [PROMOTE]
                                               ┌───────────┐
                                               │  ACTIVE   │
                                               └───────────┘
```

| State | Description |
|-------|-------------|
| `DRAFT` | Experiment configuration defined, dataset bound, ready to train |
| `TRAINING` | Active optimization loop on PyTorch backend |
| `VALIDATING` | Epoch-level validation loss and metric evaluation |
| `OOS_TESTING` | Evaluation against strictly isolated out-of-sample holdout |
| `CERTIFIED` | Passed all 10 canonical gates with signed certificate |
| `REJECTED` | Failed one or more certification gates; loading strictly blocked |
| `LOADED` | Weights, scaler sidecar, and contract verified and memory-mapped |
| `ACTIVE` | Designated as the live trading champion model |

---

## 3. API Contract Matrix

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/model-studio/overview` | `GET` | Summary KPI metrics across datasets and models |
| `/api/model-studio/datasets` | `GET` | Catalog of market datasets with quality status |
| `/api/model-studio/datasets/download` | `POST` | Ingest historical bars from MT5/CSV with quality certification |
| `/api/model-studio/datasets/inspect-features` | `POST` | Inspect feature matrices and variance stats |
| `/api/model-studio/train` | `POST` | Start model training on selected dataset and contract |
| `/api/model-studio/train/progress` | `GET` | Poll epoch, loss, and training metrics |
| `/api/model-studio/models` | `GET` | Enumerate registered model checkpoints |
| `/api/model-studio/models/certify` | `POST` | Run 10-gate certification on candidate checkpoint |
| `/api/model-studio/models/{id}/certificate` | `GET` | Retrieve signed `ModelCertificate` and gate report |
| `/api/model-studio/models/{id}/quality-reports` | `GET` | Retrieve dataset, feature, and label quality reports |
| `/api/model-studio/models/load-contract/verify` | `POST` | Verify load contract compatibility before load |
| `/api/model-studio/models/hot-load` | `POST` | Hot-load certified weights and scaler into engine |
| `/api/model-studio/models/rollback` | `POST` | Atomically rollback to previous champion model |

---

## 4. Safety & Security Controls

- **Strict Load Gate:** Uncertified or rejected models are refused by `/hot-load` with an explicit reason code.
- **No Path Traversal:** Checkpoint and dataset paths are resolved against safe application directories; relative escapes (`../`) are rejected.
- **Non-Interactive Execution:** No arbitrary shell commands or code execution can be triggered via Model Studio.
- **Dual-Database Parity:** Metadata persistence works transparently on SQLite and PostgreSQL.
