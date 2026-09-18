# ML-UI-002 Handoff — End-to-End Dataset Ingestion, 50D/70D Normalization & Layer-2 Position Management Generator

## Task Summary
- **Task ID**: `ML-UI-002`
- **Agent**: `AGENT-UI` / `AGENT-DATA`
- **Date**: `2026-09-18`
- **Branch**: `agent/feature/ML-UI-DATASET-PIPELINE`
- **Status**: `DONE` (Ready for PR & Merge)
- **Goal**: Implement complete API-first pipeline in Neural Model Studio for historical market data downloading (1m, 3m, 5m, 15m), 50D/70D feature extraction and normalization inspection, real PyTorch model training loop dispatch, and Layer-2 Position Management Dataset generation based on simulated trade trajectories and mathematical continuation value labeling with anti-leakage purge/embargo splitting.

---

## 1. Scope of Changes

### 1. Backend REST Endpoints (`src/nexus_scalp/web/model_studio_routes.py`)
- `POST /api/model-studio/datasets/download`:
  - Downloads / ingests market candles for `symbol` and `timeframe` (`M1`, `M3`, `M5`, `M15`) with requested bar count.
  - Source adapters supported: `synthetic` (realistic GBM + microstructure), `mt5` (live terminal feed), `csv` (file import).
  - Validates schema, monotonicity, UTC timestamps, and saves parquet to `data/raw/`.
- `POST /api/model-studio/datasets/inspect-features`:
  - Computes 50D (Base technical) and 70D (Base 0..49 + News 50..59 + Liquidity 60..69) feature matrices.
  - Calculates per-feature statistics: `raw_min`, `raw_max`, `raw_mean`, `raw_std`, normalized sample value, zero-variance status, and health flags (`HEALTHY`, `CLAMPED`, `WARNING`).
  - Implements zero-variance clamping ($\sigma = \max(\sigma, 10^{-3})$) and bounds normalization to $[-5.0, 5.0]$.
- `POST /api/model-studio/position-dataset/generate`:
  - Simulates trade entries (BUY/SELL) across historical market bars using ScalpNet or heuristics.
  - Samples position state at each bar $t$ (direction, entry price, current price, unrealized R, position age, ATR, spread, model probability, market regime).
  - Calculates future path outcomes strictly looking ahead $[t+1 \dots t+H]$:
    - $R_{\text{best}}$: Maximum Favorable Excursion (MFE)
    - $R_{\text{worst}}$: Maximum Adverse Excursion (MAE)
    - Continuation value: $R_{\text{best}} - R_t$
    - Optimal mathematical action: `KEEP`, `CLOSE`, or `REDUCE`.
  - Implements anti-leakage chronological split (Train 70%, Validation 15%, OOS 15%) with purge/embargo gap.
  - Saves Apache Parquet dataset to `data/positions/` and returns SHA-256 integrity hash.
- `POST /api/model-studio/train`:
  - Upgraded from static mock to **real PyTorch training loop** with DataLoader, AdamW optimizer, CrossEntropyLoss, and per-epoch validation.
  - Saves trained model weights to `artifacts/model_generation/checkpoints/` and updates training progress state.

### 2. Core Machine Learning Generator (`src/nexus_scalp/model_generation/position_dataset_generator.py`)
- Independent, zero-dependency position simulation and mathematical labeling engine.
- Implements `PositionSample`, `PositionDatasetResult`, and `generate_position_dataset(...)`.
- Enforces strict anti-leakage boundaries (future lookahead restricted to labels only, input features restricted to $[0 \dots t]$).

### 3. CLI Management Surface (`src/nexus_scalp/cli/model_studio_commands.py`)
- `nexus dataset-download`: Ingest historical candles with `--symbol`, `--timeframe`, `--bars`, `--source`, `--json`.
- `nexus dataset-inspect`: Inspect and normalize 50D/70D features with `--dataset`, `--dim`, `--max-rows`, `--json`.
- `nexus position-dataset-generate`: Generate Layer-2 position dataset with `--dataset`, `--dim`, `--bars`, `--max-holding`, `--target-atr`, `--friction`, `--json`.

### 4. Legacy Web UI (`Web/index.html`, `Web/model_studio_ui.js`)
- Added **Dataset Download & Ingestion Card** with inputs for Symbol, Timeframe (1m, 3m, 5m, 15m), Candle Count, Source, and interactive Download button.
- Added **Feature Normalization & Inspection Panel** showing feature count, healthy count, zero-variance clamped count, scaler status, and a detailed 30-feature inspection table.
- Added **Layer-2 ML: Position Management Dataset Generator Card** with parameter controls, action label distribution (KEEP / CLOSE / REDUCE), mean continuation value, and file path display.

### 5. React Web UI (`frontend/src/features/model-studio/`)
- Created `frontend/src/features/model-studio/api.ts` with fully typed endpoints (`downloadDataset`, `inspectFeatures`, `generatePositionDataset`).
- Created `frontend/src/features/model-studio/model.ts` with complete TypeScript DTO interfaces.
- Updated `ModelStudioPage.tsx` with modern Tailwind cards, responsive tables, real-time feedback, and error handling.

### 6. Automated Testing (`tests/unit/test_model_studio_pipeline.py`)
- 7 comprehensive unit/integration tests verifying dataset download, feature inspection (50D & 70D), position dataset generation, real PyTorch training loop, CLI commands, and FastAPI endpoints via TestClient.

---

## 2. Quality Gate Verification

| Gate | Command | Result |
|:-----|:--------|:-------|
| Gate 1 — Ruff | `ruff check src/nexus_scalp/web/model_studio_routes.py src/nexus_scalp/model_generation/position_dataset_generator.py src/nexus_scalp/cli/model_studio_commands.py tests/unit/test_model_studio_pipeline.py` | `All checks passed!` |
| Gate 2 — Mypy | `mypy src/nexus_scalp/web/model_studio_routes.py src/nexus_scalp/model_generation/position_dataset_generator.py src/nexus_scalp/cli/model_studio_commands.py` | `Success: no issues found in 3 source files` |
| Gate 3 — Pytest | `pytest tests/unit/test_model_studio_pipeline.py tests/unit/test_model_studio.py -v` | `26 passed in 7.82s` |
| Gate 4 — Manifest | `python scripts/ci/verify_critical_suite_manifest.py` | `CRITICAL_SUITE_MANIFEST_OK: 201 paths all exist` |

---

## 3. Mathematical & Empirical Invariants
1. **Zero Data Leakage**: In position dataset generation, features at bar $t$ strictly use historical information $k \le t$. Future prices $k \in [t+1 \dots t+H]$ are strictly used for outcome labeling.
2. **Purge & Embargo**: Adjacent chronological folds (Train 70%, Validation 15%, OOS 15%) are separated by an embargo margin preventing trade overlap leakage.
3. **Safe Normalization**: Standard deviation is clamped to $\max(\sigma, 10^{-3})$ preventing division by zero on static features (e.g. session flags), and normalized values are bounded to $[-5.0, 5.0]$.
4. **Economic Decision Modeling**: Labels are formulated as economic decision values: $\text{Continuation Value} = R_{\text{best}} - R_t$, transforming exit management into a quantifiable trade utility problem.
