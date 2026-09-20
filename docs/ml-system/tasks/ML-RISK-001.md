# ML-RISK-001 — Position Replay & Economic Dataset Generation Engine

STREAM: STREAM I — TRADING INTEGRATION, RISK & POSITION MANAGEMENT
PRIORITY: P0 / Research-Critical
STATUS: DONE (PR #268 merged 2026-09-05; re-verified at main 8ce87fe1 2026-09-20: 15/15 tests in tests/unit/test_position_replay_pipeline.py pass in 3.71s, Python 3.11.16; manifest-registered; all 18 acceptance criteria below checked)
DEPENDENCIES: ML-DATA-001, ML-DATA-002, ML-FEAT-001, ML-UI-003
BLOCKS: Future Layer-2 Position Manager Training (ML-RISK-002)
AGENT_ROLE: AGENT-RISK
OWNERSHIP_SCOPE: src/nexus_scalp/model_generation/position_replay.py, src/nexus_scalp/model_generation/position_dataset_generator.py, src/nexus_scalp/web/model_studio_routes.py, src/nexus_scalp/cli/model_studio_commands.py, tests/unit/test_position_replay_pipeline.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE
TYPE: Dataset & Replay Pipeline Engine
RISK: High (Research-Critical Data Foundation)
ESTIMATED_SCOPE: 6 files, ~1,800 LOC

---

## 1. OBJECTIVE & EXECUTIVE SUMMARY

Build the authoritative, deterministic, leakage-safe historical replay pipeline required to generate the training dataset for the future Nexus Layer-2 Position/Risk Manager.

The pipeline executes the **REAL PRIMARY NEXUS MODEL** (`ScalpNet` 50D/70D) over **HISTORICAL MARKET DATA**, reconstructs its exact decisions, simulates resulting positions using canonical execution-cost geometry, records position-state observations at every decision bar, calculates future multi-horizon economic trajectories (MFE, MAE, Continuation Value), and outputs a cryptographically versioned Parquet dataset and companion manifest (`DatasetManifest`).

> **CRITICAL SCIENTIFIC DISCLAIMER (NO EDGE CLAIM):**
> This task generates evidence and training datasets for Layer-2 research. It makes **NO CLAIM OF TRADING EDGE**, **NO CLAIM OF PROFITABILITY**, and **DOES NOT PROMOTE MODELS TO PRODUCTION**.

---

## 2. SOURCE-OF-TRUTH MAP

Following forensic inspection of the codebase, the canonical implementation sites are:

| Subsystem Component | Canonical Implementation Site | Reused Contract / Guarantee |
| :--- | :--- | :--- |
| **Feature Generation** | `src/nexus_scalp/features/scalp_features.py` (`ScalpFeatureEngine`) | Causal 50D feature tensor computed strictly from completed bars $\le T$. |
| **70D Vector Assembly** | `src/nexus_scalp/features/liquidity_runtime.py`, `features70.py` | 50D base + 10D news + 10D liquidity, validated against schema hash. |
| **Schema Contract & Hash** | `src/nexus_scalp/features/schema_contract.py` | `feature_schema_hash()`, `ACTIVE_SCHEMA_ID = "scalp_v1"`. |
| **Model Architecture** | `src/nexus_scalp/models/scalp_net.py` (`ScalpNet`) | 3-Class contract (`NO_TRADE`=0, `BUY`=1, `SELL`=2), PyTorch inference mode. |
| **Model Registry & Loading** | `src/nexus_scalp/model_generation/position_replay.py` (`resolve_primary_model_bundle`) & `model_registry.py` | Strict weights deserialization (`weights_only=True`), model hash recording. |
| **Scaler Normalization** | `<model>.scaler.npz` | Sidecar z-score normalization $x_{\text{norm}} = \text{clip}((x - \mu)/\sigma, -5, 5)$. |
| **Execution Costs & Friction** | `src/nexus_scalp/configuration/execution_costs.py` & `configs/execution_assumptions.json` | Calibrated real spread ($0.147/oz) and adverse slippage ($0.05/oz). |
| **Historical Replay Engine** | `src/nexus_scalp/model_generation/position_replay.py` (`PositionReplayPipeline`) | Causal event loop, logical clock only, deterministic bar step. |
| **Dataset Manifest & Hashing** | `src/nexus_scalp/model_generation/dataset_manifest.py` (`DatasetManifest`, `compute_dataset_hash`) | Immutable SHA-256 parquet hash, array checksum, full model lineage. |

---

## 3. MASTER CAUSALITY CONTRACT

Every Position Manager input feature at time $T$ depends strictly on information causally available at or prior to $T$. Future bars $[T+1 \dots T+H]$ are strictly quarantined for label construction.

| Feature ID | Source Engine | Available At | Lookback | Causal Flag | Mathematical Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `timestamp` | Market Feed | $T$ | 0 bars | `True` | ISO-8601 bar close timestamp |
| `bar_index` | Replay Clock | $T$ | 0 bars | `True` | Monotonically increasing bar integer index |
| `position_id` | Trade Simulator | $T$ | 0 bars | `True` | Unique persistent position identifier |
| `trade_id` | Trade Simulator | $T$ | 0 bars | `True` | Unique integer trade sequence identifier |
| `direction` | Primary Model | $T_{\text{entry}}$ | 0 bars | `True` | Position direction (`BUY` / `SELL`) |
| `entry_timestamp`| Trade Simulator | $T_{\text{entry}}$ | 0 bars | `True` | Fill timestamp of the entry order |
| `entry_price` | Trade Simulator | $T_{\text{entry}}$ | 0 bars | `True` | Fill price including entry spread and slippage |
| `current_price` | Market Feed | $T$ | 0 bars | `True` | Current bar close price |
| `position_age` | Trade Simulator | $T$ | 0 bars | `True` | Holding duration in elapsed bars ($T - T_{\text{entry}}$) |
| `unrealized_pnl_gross` | Accounting | $T$ | 0 bars | `True` | Gross price excursion: $(\text{close} - \text{entry}) \times \text{dir}$ |
| `unrealized_pnl_net` | Accounting | $T$ | 0 bars | `True` | Net excursion minus roundtrip friction |
| `current_r_gross` | Accounting | $T$ | 0 bars | `True` | Gross PnL divided by initial risk unit $R_{\text{dist}}$ |
| `current_r_net` | Accounting | $T$ | 0 bars | `True` | Friction-adjusted PnL divided by $R_{\text{dist}}$ |
| `current_return` | Accounting | $T$ | 0 bars | `True` | Price return: $(\text{close} - \text{entry}) / \text{entry} \times \text{dir}$ |
| `distance_to_stop` | Risk Engine | $T$ | 0 bars | `True` | Absolute distance to stop loss in price points |
| `distance_to_target`| Risk Engine | $T$ | 0 bars | `True` | Absolute distance to take profit in price points |
| `distance_to_stop_r`| Risk Engine | $T$ | 0 bars | `True` | Distance to stop normalized by $R_{\text{dist}}$ |
| `distance_to_target_r`| Risk Engine | $T$ | 0 bars | `True` | Distance to target normalized by $R_{\text{dist}}$ |
| `atr` | Feature Engine | $T$ | 14 bars | `True` | Causal ATR(14) computed on completed bars $\le T$ |
| `spread` | Execution Assumptions | $T$ | 0 bars | `True` | Measured spread from canonical cost configuration |
| `estimated_slippage`| Execution Assumptions | $T$ | 0 bars | `True` | Calibrated adverse exit slippage expectation |
| `model_signal` | Primary Model | $T_{\text{entry}}$ | 0 bars | `True` | Discrete signal class at entry (`BUY`/`SELL`) |
| `model_probability`| Primary Model | $T_{\text{entry}}$ | 0 bars | `True` | Softmax probability of winning class at entry |
| `model_confidence` | Primary Model | $T_{\text{entry}}$ | 0 bars | `True` | Model confidence score at entry |
| `signal_age` | Replay Clock | $T$ | 0 bars | `True` | Bars elapsed since entry signal was emitted |
| `market_regime` | Regime Engine | $T$ | 50 bars | `True` | Causal regime classification (`TRENDING`, `RANGING`, etc.) |
| `primary_model_id`| Provenance | $T$ | 0 bars | `True` | Unique model identity string |
| `primary_model_hash`| Provenance | $T$ | 0 bars | `True` | SHA-256 digest of primary model weights |
| `schema_id` | Provenance | $T$ | 0 bars | `True` | Schema contract ID (`scalp_v1` / `scalp_v3`) |
| `schema_hash` | Provenance | $T$ | 0 bars | `True` | SHA-256 feature contract hash |
| `feature_order_hash`| Provenance | $T$ | 0 bars | `True` | SHA-256 column sequence digest |
| `scaler_hash` | Provenance | $T$ | 0 bars | `True` | SHA-256 scaler sidecar digest |

---

## 4. FUTURE TRAJECTORY & ECONOMIC TARGET FORMULAS

For each observation at bar $T$, future prices are inspected over the horizon $\tau \in [T+1 \dots \min(T+H, N)]$:

1. **Future Price Delta**:
   $$\Delta P(\tau) = (\text{Close}(\tau) - \text{EntryPrice}) \times \text{Direction}$$

2. **Future Net R**:
   $$\text{FutureR}_{\text{net}}(\tau) = \frac{\Delta P(\tau) - \text{Friction}}{R_{\text{dist}}}$$
   where $\text{Friction} = \text{Spread} + 2 \times \text{Slippage}$ and $R_{\text{dist}} = |\text{EntryPrice} - \text{StopLoss}|$.

3. **Maximum Favorable Excursion (MFE)**:
   $$\text{best\_future\_r} = \max_{\tau \in [T+1, T+H]} \text{FutureR}_{\text{net}}(\tau)$$
   $$\text{mfe\_usd} = \max_{\tau \in [T+1, T+H]} \Delta P(\tau) \times \text{ContractSize}$$

4. **Maximum Adverse Excursion (MAE)**:
   $$\text{worst\_future\_r} = \min_{\tau \in [T+1, T+H]} \text{FutureR}_{\text{net}}(\tau)$$
   $$\text{mae\_usd} = \min_{\tau \in [T+1, T+H]} \Delta P(\tau) \times \text{ContractSize}$$

5. **Continuation Value**:
   $$\text{continuation\_value} = \text{best\_future\_r} - \text{current\_r\_net}$$
   Represents the incremental net R gained by continuing to hold versus closing immediately at time $T$.

6. **Horizon Continuation Value**:
   $$\text{horizon\_continuation\_value} = \text{future\_r\_net}(T+H) - \text{current\_r\_net}$$
   Represents the net return delta realized at the terminal bar of the horizon.

7. **Actionable Decision Labels**:
   - **`KEEP`**: $\text{continuation\_value} > 0.20\text{R}$ and $\text{worst\_future\_r} > -0.50\text{R}$ (positive continuation expectancy without severe downside exposure).
   - **`REDUCE`**: $\text{current\_r\_net} \ge 0.80\text{R}$ and $\text{continuation\_value} \le 0.20\text{R}$ (lock in existing profit when additional upside is exhausted).
   - **`CLOSE`**: $\text{continuation\_value} \le 0.0\text{R}$ or $\text{worst\_future\_r} \le -0.50\text{R}$ (holding destroys value or exposes capital to adverse moves).

---

## 5. TEMPORAL SPLITTING & TRADE GROUP ISOLATION

To prevent any data leakage:
- **Chronological Partitions**: 70% Train, 15% Validation, 15% Out-Of-Sample (OOS).
- **Purge Boundaries**: Samples within $H$ bars of split boundaries are tagged as `purge` because their forward label window crosses partition lines.
- **Embargo Boundaries**: Post-split buffer bars prevent autoregressive leakage.
- **Trade Group Isolation**: All position-state samples belonging to a single `trade_id` are strictly isolated to that trade's initial partition. Zero trades cross between Train, Val, or OOS partitions.

---

## 6. ARTIFACT FORMATS & MANIFEST

1. **Authoritative Dataset**: Stored as Apache Parquet (`.parquet`) via Polars for maximum I/O speed and memory efficiency.
2. **Optional Export**: Flat CSV (`.csv`).
3. **Cryptographic Manifest**: Stored as `.manifest.json` adhering to `DatasetManifest` (`ML-DATA-002`), recording:
   - `dataset_id`, `dataset_version`, `dataset_hash` (SHA-256 of file)
   - `sha256_checksum` (SHA-256 of array values)
   - `feature_schema_id`, `feature_schema_hash`, `feature_order_hash`
   - `label_schema_id`, `label_config_hash`, `split_config_hash`
   - Complete `PrimaryModelIdentity` lineage.

---

## 7. AUTOMATED VALIDATION REPORT

The `PositionDatasetValidator` automatically verifies:
- Zero NaN or Infinite values.
- Zero duplicate `(position_id, bar_index)` records.
- Strictly monotonic timestamps per trade.
- Zero trade partition cross-leakage.
- File and manifest SHA-256 cryptographic match.

---

## 8. ACCEPTANCE CRITERIA VERIFICATION MATRIX

- [x] Real Nexus model replayed on historical market data.
- [x] Replay is 100% deterministic (verified bit-identical outputs across runs).
- [x] Exact model, schema, and scaler identities recorded in lineage.
- [x] Actual model predictions used (no synthetic or mock signals).
- [x] Position states generated from actual model behavior.
- [x] Position economics verified against canonical cost assumptions.
- [x] PnL and R calculations verified (gross and net).
- [x] Future trajectories reproducible and mathematically defined.
- [x] Continuous economic targets preserved before discrete reduction.
- [x] Inputs are strictly causal (proven by future price mutation tests).
- [x] Temporal Train/Val/OOS separation with purge boundaries.
- [x] Trade Group Isolation enforced (0 trades cross splits).
- [x] Dataset manifest and SHA-256 file hashes attached.
- [x] Machine-readable validation report generated.
- [x] 15 automated unit and economic invariant tests passing.
- [x] Memory-safe bounded execution on large datasets.
- [x] Documentation and task tracking updated.
