# ML-UI-002 — Model Studio E2E Dataset Ingestion, 50D/70D Normalization & Layer-2 Position Management Generator

STREAM: STREAM L — OBSERVABILITY, UX & INTEGRATION
PRIORITY: P1
STATUS: DONE
DEPENDENCIES: ML-UI-001, ML-DATA-001
BLOCKS: None
AGENT_ROLE: AGENT-UI
OWNERSHIP_SCOPE: src/nexus_scalp/web/model_studio_routes.py, src/nexus_scalp/model_generation/position_dataset_generator.py, src/nexus_scalp/cli/model_studio_commands.py, Web/index.html, Web/model_studio_ui.js, frontend/src/features/model-studio/
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE
TYPE: Feature & UI Pipeline
RISK: Low
ESTIMATED_SCOPE: 6 files, ~900 LOC

## OBJECTIVE
Deliver a complete API-first pipeline in Neural Model Studio enabling operators to:
1. Download and ingest historical market candles (1m, 3m, 5m, 15m) with user-selected bar count and data adapter (Synthetic, MT5, CSV).
2. Inspect, calculate, and normalize 50D base and 70D multi-source feature representations with zero-variance clamping, NaN defense, and statistical summaries for top canonical features.
3. Dispatch real PyTorch model training runs with selected datasets, monitoring loss and validation metrics.
4. Generate Layer-2 Position Management datasets via historical simulation of trade entries and anti-leakage mathematical labeling (KEEP, CLOSE, REDUCE) based on continuation values ($R_{\text{future}} - R_{\text{current}}$) with purge/embargo boundaries.

## WHY_IT_EXISTS
Model Studio previously allowed prediction tests and synthetic stress runs, but lacked direct UI-accessible dataset ingestion, feature inspection, and training dispatch. Furthermore, scaling beyond basic directional entry models requires a second-layer ML model (Position/Risk Manager) trained on simulated trade trajectories to make optimal trade continuation and exit decisions.

## ARCHITECTURE & MATHEMATICAL MODEL
### 1. Ingestion Pipeline
- Endpoint: `POST /api/model-studio/datasets/download`
- Parameters: `symbol`, `timeframe` (M1, M3, M5, M15), `bars`, `source` (synthetic, mt5, csv).
- Enforces strict monotonic UTC timestamp ordering, positive OHLC values, zero NaN/Inf.
- Outputs Apache Parquet dataset to `data/raw/` with throughput and size telemetry.

### 2. Feature Inspection & 50D / 70D Normalization
- Endpoint: `POST /api/model-studio/datasets/inspect-features`
- Extracts 50D (Base technical & microstructure) or 70D (Base 0..49 + News 50..59 + Liquidity 60..69).
- Calculates per-feature statistics: `raw_min`, `raw_max`, `raw_mean`, `raw_std`, and normalized sample values.
- Applies zero-variance clamping ($\sigma = \max(\sigma, 10^{-3})$) and bounds normalization to $[-5.0, 5.0]$.
- Categorizes feature health: `HEALTHY`, `CLAMPED` (zero variance), `WARNING` (NaNs).

### 3. Layer-2 Position Management Dataset Generator
- Endpoint: `POST /api/model-studio/position-dataset/generate`
- Pipeline:
  $$\text{Historical Bars} \xrightarrow{\text{50D/70D Features}} \text{Base Model Decisions} \xrightarrow{\text{Trade Simulation}} \text{Position States} \xrightarrow{\text{Future Horizon}} \text{Mathematical Labels}$$
- Position State Features (at bar $t$):
  - Direction (LONG/SHORT), entry price, current price.
  - Unrealized PnL in R-multiples: $R_t = \frac{\text{price}_t - \text{entry}}{1.5 \times \text{ATR}}$
  - Position age in bars/minutes, current ATR, spread, model probability, market regime.
- Anti-Leakage Future Horizon $[t+1 \dots t+H]$:
  - $R_{\text{best}} = \max_{k \in [1, H]} R_{t+k}$ (Maximum Favorable Excursion)
  - $R_{\text{worst}} = \min_{k \in [1, H]} R_{t+k}$ (Maximum Adverse Excursion)
  - $\text{Continuation Value} = R_{\text{best}} - R_t$
- Optimal Action Labels:
  - $\mathbf{KEEP}$: $\text{Continuation Value} > \text{friction}$ and $R_{\text{worst}} > -1.0$ (holding yields additional R without hitting stop loss).
  - $\mathbf{REDUCE}$: $R_t \ge +1.5$ and adverse excursion approaches.
  - $\mathbf{CLOSE}$: Continuation value $\le 0$ or drawdown risk dominates.
- Anti-Leakage Chronological Split:
  - Train: 70%, Validation: 15%, Out-of-Sample (OOS): 15% with purge/embargo gap between splits to eliminate boundary leakage.

## ACCEPTANCE CRITERIA
- [x] 1. `POST /api/model-studio/datasets/download` ingests market candles for M1, M3, M5, M15.
- [x] 2. `POST /api/model-studio/datasets/inspect-features` inspects and normalizes 50D and 70D feature matrices.
- [x] 3. `POST /api/model-studio/position-dataset/generate` simulates trades and produces position-state datasets with KEEP/CLOSE/REDUCE labels.
- [x] 4. `POST /api/model-studio/train` executes real PyTorch training loop on selected datasets.
- [x] 5. CLI commands `dataset-download`, `dataset-inspect`, and `position-dataset-generate` operational with `--json` output.
- [x] 6. Web UI (`Web/index.html`, `Web/model_studio_ui.js`) provides interactive cards for download, feature inspection, training, and position dataset generation.
- [x] 7. React UI (`frontend/src/features/model-studio/`) provides fully typed API client and components.
- [x] 8. Comprehensive automated test suite in `tests/unit/test_model_studio_pipeline.py` passes 100%.
