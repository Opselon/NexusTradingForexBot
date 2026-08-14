# 🧠 Nexus Scalp Engine (NSE) - Forensic Master Skill & Context Anchor

> **TARGET AUDIENCE:** AI Coding Agents (Cursor, Copilot, ChatGPT, Claude, Jules).
> **PURPOSE:** Authoritative, repository-grounded Master Skill & Architecture Map for the Nexus Scalp Engine repository.
> **SOURCE OF TRUTH:** Actual Repository Code (verified via forensic audit).
> **ABSOLUTE DIRECTIVE:** READ-ONLY for codebase files. Do NOT modify any file in this repository except `agents/skill.md`.

---

## 📑 Table of Contents

1. [AI Agent Knowledge Map: Read This First](#1-ai-agent-knowledge-map-read-this-first)
2. [Repository Architecture & File-by-File Inventory](#2-repository-architecture--file-by-file-inventory)
3. [Architecture Reconstruction & Dependency Graph](#3-architecture-reconstruction--dependency-graph)
4. [AI / ML Forensic Audit](#4-ai--ml-forensic-audit)
   - 4.1 [AI/ML Component Inventory](#41-aiml-component-inventory)
   - 4.2 [ScalpNet Neural Architecture](#42-scalpnet-neural-architecture)
   - 4.3 [Feature Engineering (50D Master Contract)](#43-feature-engineering-50d-master-contract)
   - 4.4 [Cost-Aware Triple-Barrier Labeling](#44-cost-aware-triple-barrier-labeling)
   - 4.5 [Purged Walk-Forward Training & Validation](#45-purged-walk-forward-training--validation)
   - 4.6 [Online Fine-Tuning, Quality Gate, & Hot-Swapping](#46-online-fine-tuning-quality-gate--hot-swapping)
5. [Signal Pipeline & Rule Matrix Forensics](#5-signal-pipeline--rule-matrix-forensics)
6. [Risk Engine & Position Sizing Forensics](#6-risk-engine--position-sizing-forensics)
7. [Execution & MT5 Integration Forensics](#7-execution--mt5-integration-forensics)
8. [Position Protection, Reversal Protocol, & State Machine](#8-position-protection-reversal-protocol--state-machine)
9. [Hot-Path Safety & Latency Forensics](#9-hot-path-safety--latency-forensics)
10. [Web / API, SSE, WebSocket, & Debug Hub Forensics](#10-web--api-sse-websocket--debug-hub-forensics)
11. [Observability & Telegram Notifier Forensics](#11-observability--telegram-notifier-forensics)
12. [Configuration Architecture & Flow](#12-configuration-architecture--flow)
13. [Testing & CI/CD Forensics](#13-testing--cicd-forensics)
14. [Verified Hard Invariants](#14-verified-hard-invariants)
15. [Documentation vs. Reality Audit](#15-documentation-vs-reality-audit)
16. [Future Engineering Recommendations](#16-future-engineering-recommendations)

---

## 1. AI Agent Knowledge Map: Read This First

If you are an AI coding agent tasked with inspecting or extending this codebase in the future, **read this section before reading or touching any code file**.

### 🗺️ Layer Mapping & Interface Invariants

Before modifying any file, verify its architectural layer and dependencies:

| Layer | Directories / Files | Role & Responsibilities | Invariants & Rules |
| :--- | :--- | :--- | :--- |
| **Domain** | `src/nexus_scalp/domain/` (`models.py`, `enums.py`) | Immutable data contracts (`TickData`, `TradeProposal`, `Position`, `TradeOrder`, `AccountInfo`). | **NEVER MUTATE.** All domain models use Pydantic `frozen=True`. Use `.model_copy(update={...})`. |
| **Ports** | `src/nexus_scalp/ports/` (`mt5_port.py`, `gateway_port.py`) | Protocol interfaces defining dependency inversion boundaries. | Any signature change in `IMT5Port` MUST be updated across `DirectMT5Adapter`, `RemoteGatewayAdapter`, and `PaperAdapter`. |
| **Adapters** | `src/nexus_scalp/adapters/` (`mt5/`, `paper/`, `database/`) | External IPC, MT5 bindings, paper simulation, SQLite WAL persistence (`AuditRepository`). | DB writes are queued asynchronously via a dedicated background worker thread (`_worker_thread`). |
| **Features** | `src/nexus_scalp/features/` (`scalp_features.py`, `regime_classifier.py`) | 50D Feature Vector calculation and Market Regime classification. | **Contract: exactly 50 float features.** Values must be finite and clipped to `[-3.0, +3.0]`. |
| **Models** | `src/nexus_scalp/models/` (`scalp_net.py`) | PyTorch deep neural network (`ScalpNet` v3). | Dual-path: 2D MLP for single-tick, 3D TCN + Self-Attention for sequences. Inputs: `(Batch, 50)`. Outputs: 4 logits (`0=NO_TRADE`, `1=BUY_MARKET`, `2=SELL_MARKET`, `3=WAIT`). |
| **Training** | `src/nexus_scalp/training/` (`walk_forward_trainer.py`), `src/nexus_scalp/labeling/` (`triple_barrier.py`) | Purged walk-forward trainer, triple-barrier labeler, online fine-tuning. | Uses Polars. When filtering Polars DataFrames, **ALWAYS use bitwise operators** `~pl.col(...)`, NEVER Python `not`. |
| **Signals** | `src/nexus_scalp/signals/` (`policy.py`, `rule_matrix.py`) | Multi-confluence policy routing, SMC God Mode, predictive limit generation, 30+ rule matrix. | Generates `TradeProposal`. Respects Regime Guardian Gate (unsafe regimes return `NO_TRADE`). |
| **Risk** | `src/nexus_scalp/risk/` (`risk_engine.py`) | Capital allocation, dynamic lot sizing, Almgren-Chriss slippage bounds, margin clamping. | All entry proposals pass through `calculate_dynamic_volume()`. Clamped to free margin (20%) and account tier caps. |
| **Execution** | `src/nexus_scalp/execution/` (`order_manager.py`) | 60-scenario router, 11 position lifecycles, breakeven lock, profit giveback, AI reversal. | Enforces `HARD_MAX_LOTS = 10.0` and `MAX_TOTAL_EXPOSURE = 1`. Restricts pending re-quotes with a 30s lock and 1.0x ATR drift. |
| **Application**| `src/nexus_scalp/application/` (`live_engine.py`) | Main async event loop (50ms hot path), bar aggregation, state sync, retrain orchestrator. | **NEVER BLOCK THE EVENT LOOP.** No synchronous I/O or model training inside `_process_tick_pipeline()`. |
| **Web / API** | `src/nexus_scalp/web/` (`server.py`), `Web/` | FastAPI REST endpoints, WebSocket (`/web`), SSE (`/api/ticks/stream`), Debug Hub. | Enum instances in JSON responses MUST be serialized via `serialize_enums()`. Background tasks stored in `app.state.background_tasks`. |

---

## 2. Repository Architecture & File-by-File Inventory

### 📁 Complete Root & Source Structure

```text
NexusTradingForexBot/
├── .github/workflows/                 # CI/CD Workflows
│   ├── ci.yml                         # Quality, Ruff, Mypy, Pytest with coverage
│   ├── docker.yml                     # Docker Build & Publish to GHCR
│   ├── release.yml                    # Automated GitHub Release on tags
│   └── security.yml                   # CodeQL Analysis & Trivy Security Scans
├── Web/                               # Modern Frontend Control Center UI
│   ├── app.js                         # Interactive Dashboard UI logic
│   ├── index.html                     # Visualizer & Control Panel Markup
│   └── styles.css                     # Premium Dark Glassmorphism Styling
├── configs/                           # Application Configurations
│   ├── base.yaml                      # Default base settings
│   └── live.yaml.example              # Example live runtime configuration
├── docker/                            # Containerization Scripts
│   ├── entrypoint.sh                  # Container entrypoint script
│   └── healthcheck.sh                 # Container health check script
├── src/
│   ├── cli/
│   │   └── train_model.py             # ⚠️ Legacy CLI Training Script (Stale 18D contract)
│   └── nexus_scalp/
│       ├── adapters/                  # External Infrastructure Integration
│       │   ├── database/
│       │   │   └── audit_repository.py# SQLite WAL Audit Repository & Ledger Autopsy
│       │   ├── mt5/
│       │   │   ├── mt5_adapter.py     # Direct Win32 MetaTrader 5 IPC Binding
│       │   │   └── remote_gateway.py  # ZMQ Remote Gateway Adapter
│       │   └── paper/
│       │       └── paper_adapter.py   # Paper Trading / Simulation Adapter
│       ├── application/
│       │   └── live_engine.py         # Main Async Live Orchestrator & Hot Path
│       ├── cli/
│       │   └── main.py                # Primary Typer CLI Application (`nse`)
│       ├── configuration/
│       │   └── config.py              # Pydantic Settings & YAML Config Parser
│       ├── domain/
│       │   ├── enums.py               # ActionType, ExecutionMode, OrderType
│       │   └── models.py              # Frozen Dataclasses & Pydantic Domain Models
│       ├── execution/
│       │   └── order_manager.py       # Master Order Lifecycle Manager (1792 lines)
│       ├── features/
│       │   ├── order_manager.py       # ❌ DEAD/STALE FILE (Legacy 234-line manager)
│       │   ├── regime_classifier.py   # Gaussian Mixture / Rule-Based Regime Classifier
│       │   └── scalp_features.py      # 50D Master Feature Vector Pipeline
│       ├── labeling/
│       │   └── triple_barrier.py      # Cost-Aware Purged Triple-Barrier Labeler
│       ├── market_data/
│       │   ├── bar_aggregator.py      # Tick-to-Bar M1 Aggregator
│       │   └── tick_storage.py        # Tick Storage & Parquet Exporter
│       ├── models/
│       │   └── scalp_net.py           # ScalpNet v3 Dual-Path Temporal Transformer
│       ├── observability/
│       │   ├── logging.py             # Structlog Structured JSON Logger
│       │   └── telegram_notifier.py   # Async ThreadPool Telegram Alert Notifier
│       ├── ports/
│       │   ├── gateway_port.py        # Remote Gateway Interface
│       │   └── mt5_port.py            # MetaTrader 5 Broker Interface (`IMT5Port`)
│       ├── risk/
│       │   └── risk_engine.py         # Dynamic Lot Sizing & Risk Engine
│       ├── signals/
│       │   ├── policy.py              # Signal Policy Engine & SMC God Mode
│       │   └── rule_matrix.py         # 30+ Advanced Scalping Rule Matrix Engine
│       ├── training/
│       │   └── walk_forward_trainer.py# Purged Walk-Forward & Online Fine-Tuning Engine
│       └── web/
│           └── server.py              # FastAPI Web Backend, SSE, WebSocket, & Debug Hub
├── tests/                             # Test Suite
│   ├── integration/                   # Integration Tests
│   └── unit/                          # Unit Tests (17 test modules)
├── Dockerfile                         # Production Dockerfile
├── docker-compose.yml                 # Docker Compose Orchestration
├── main.py                            # Top-level Entry Point
├── NexusTradingForexBot.py            # Legacy Top-level Entry Point
├── pyproject.toml                     # Project Toolchain & Package Definition
└── requirements.txt                   # Dependency Requirements
```

---

## 3. Architecture Reconstruction & Dependency Graph

```text
                        ┌────────────────────────┐
                        │ MetaTrader 5 Terminal  │
                        └───────────┬────────────┘
                                    │ Ticks / Deals / Orders
                                    ▼
                        ┌────────────────────────┐
                        │ DirectMT5Adapter / IPC │
                        └───────────┬────────────┘
                                    │ TickData
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                              LiveEngine                              │
 │  ┌─────────────────┐   ┌──────────────────────┐   ┌───────────────┐  │
 │  │ BarAggregator   │──>│ ScalpFeatureEngine   │──>│ ScalpNet      │  │
 │  └─────────────────┘   │ (50D Feature Vector) │   │ (PyTorch Net) │  │
 │                        └──────────┬───────────┘   └───────┬───────┘  │
 │                                   │                       │          │
 │                                   ▼                       ▼          │
 │                        ┌──────────────────────────────────────────┐  │
 │                        │               SignalPolicy               │  │
 │                        │ (SMC God Mode / Regime Gate / Predictive)│  │
 │                        └──────────────────┬───────────────────────┘  │
 └───────────────────────────────────────────┼──────────────────────────┘
                                             │ TradeProposal
                                             ▼
                                ┌────────────────────────┐
                                │       RiskEngine       │
                                │ (Lot Sizing & Clamps)  │
                                └────────────┬───────────┘
                                             │ TradeOrder
                                             ▼
                                ┌────────────────────────┐
                                │ OrderLifecycleManager  │
                                │(11 States/Breakeven/SL)│
                                └────────────┬───────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       │                                           │
                       ▼                                           ▼
          ┌────────────────────────┐                  ┌────────────────────────┐
          │    AuditRepository     │                  │    TelegramNotifier    │
          │  (SQLite WAL Ledger)   │                  │ (ThreadPool Async)     │
          └────────────────────────┘                  └────────────────────────┘
                       ▲                                           ▲
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             │ Real-time State
                                             ▼
                                ┌────────────────────────┐
                                │     FastAPI Server     │
                                │ (REST / SSE / WS / UI) │
                                └────────────────────────┘
```

---

## 4. AI / ML Forensic Audit

### 4.1 AI/ML Component Inventory

| Component | File | Class | Purpose | Input | Output | Training | Inference | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Neural Net** | `models/scalp_net.py` | `ScalpNet` | Institutional Causal Temporal Transformer. | `(Batch, 50)` or `(Batch, Seq, 50)` | 4 Class Logits / Probabilities | `walk_forward_trainer.py` | `live_engine.py` | ✅ VERIFIED |
| **Feature Engine** | `features/scalp_features.py` | `ScalpFeatureEngine` | Calculates 50D Price Action, SMC, ICT, Ichimoku & MTF features. | `completed_bars`, `TickData` | `FeatureVector` (50D) | On-the-fly / Pipeline | `live_engine.py` | ✅ VERIFIED |
| **Regime Classifier** | `features/regime_classifier.py` | `MarketRegimeClassifier` | Microstructure Regime Classifier (Vol, Chop, News). | `TickData`, `BarData` | `MarketRegimeState` | Rule-based / GMM | `live_engine.py` | ✅ VERIFIED |
| **Labeler** | `labeling/triple_barrier.py` | `TripleBarrierLabeler` | Cost-aware Purged Triple-Barrier Labeling. | Polars `DataFrame` (OHLCV, ATR, Spread) | Polars `DataFrame` + `label`, `is_eval_sample`, `is_purged` | Offline / Fine-tuning | N/A (Training only) | ✅ VERIFIED |
| **Walk-Forward Trainer** | `training/walk_forward_trainer.py` | `WalkForwardTrainer` | Purged Walk-Forward Training & Validation. | Polars `DataFrame` with 50D features & labels | Trained `model.pt`, `.scaler.npz`, `.meta.json` | `train_and_validate()` | N/A (Orchestrator) | ✅ VERIFIED |
| **Online Fine-Tuner** | `training/walk_forward_trainer.py` | `WalkForwardTrainer.fine_tune_online()` | Online fine-tuning with Quality Gate. | Cloned `ScalpNet`, recent 300+ bar Polars `DataFrame` | Quality-gated fine-tuned `ScalpNet` | `fine_tune_online()` | Live background task | ✅ VERIFIED |

---

### 4.2 ScalpNet Neural Architecture

Defined in `src/nexus_scalp/models/scalp_net.py`:
- **Input Dimension:** 50 continuous features (`num_features=50`).
- **Output Classes:** 4 classes (`0=NO_TRADE`, `1=BUY_MARKET`, `2=SELL_MARKET`, `3=WAIT`).
- **Dual-Path Routing:**
  1. **2D Single-Tick Snapshot Mode (`x.dim() == 2`):** Routes through an input projection layer (`Linear(50, 128)` -> `LayerNorm`) into a ResNet MLP with residual skip connections (`Linear(128, 128)` -> `GeLU` -> `Dropout` -> `Linear(128, 128)` -> `LayerNorm(h + h_mlp)`). Designed for sub-millisecond hot-path inference.
  2. **3D Sequence Mode (`x.dim() == 3`):** Routes through 3 sequential 1D Causal Convolutions (`CausalConv1d` with kernel size 3, dilations 1, 2, 4) enforcing left-side temporal padding `(padding, 0)`, followed by Sinusoidal Positional Encoding and Multi-Head Self-Attention (`MultiheadAttention(embed_dim=128, num_heads=4)`).
- **Classification Head:** Dense projection layers `Linear(128, 64)` -> `GeLU` -> `Dropout(0.25)` -> `Linear(64, 32)` -> `GeLU` -> `Linear(32, 4)`.
- **Training vs. Live Mode:** Returns unnormalized logits when `return_logits=True` or `self.training=True`; returns `Softmax` probabilities during live inference (`return_logits=False`).

---

### 4.3 Feature Engineering (50D Master Contract)

Defined in `src/nexus_scalp/features/scalp_features.py`:
- **Contract Identifier:** `FEATURE_NAMES` tuple containing exactly 50 named features.
- **Dimensionality Enforcement:** Hard assertion `if NUM_FEATURES != 50: raise RuntimeError(...)`.
- **Feature Categories:**
  1. *Gold Micro Metrics & Volatility:* `live_tick_displacement`, `log_return_m1`, `atr_m1`.
  2. *Candle Anatomy:* `upper_wick_ratio`, `lower_wick_ratio`, `body_to_range_ratio`, `is_doji`, `pinbar_sig`, `engulfing_sig`, `close_location_value`, `consecutive_momentum_count`.
  3. *Swing Structure & Patterns:* `dist_to_swing_high_20`, `dist_to_swing_low_20`, `price_compression_flag_ratio`, `extreme_sig`, `stop_hunt_depth`, `liquidity_sweep_signal`.
  4. *Market Sessions:* `session_tokyo`, `session_london`, `session_ny`, `session_overlap_london_ny`.
  5. *Time-Series Lags:* `lag_1_log_return`, `lag_2_log_return`, `lag_3_log_return`, `lag_1_atr_ratio`, `lag_1_volume_z`, `lag_1_clv`.
  6. *ICT Microstructure:* `fvg_sig` (depth), `order_block_type` (`ob_strength`), `choch_sig`, `breakout_sig`, `rapid_reversal_spike_val`.
  7. *Ichimoku Kinko Hyo:* `norm_tk_diff`, `tk_cross_signal`, `kumo_sig`, `norm_kumo_width`, `norm_dist_to_tenkan`, `norm_dist_to_kijun`.
  8. *Indicators & Stat-Arb:* `norm_rsi`, `dist_to_ema_21`, `dist_to_ema_50`, `cross_asset_z_score`.
  9. *Multi-Timeframe Context:* `htf_h4_trend`, `htf_h1_momentum`, `htf_m30_structure`, `htf_m15_confirmation`, `support_zone_dist`, `resistance_zone_dist`.
  10. *SMC Institutional Validation:* `feat_ob_valid_bos`, `feat_ob_equilibrium_ratio`, `feat_ob_liquidity_swept`, `feat_ob_fib_50_60_alignment`.
- **Sanitization & Clamping:** `to_tensor_input()` replaces `NaN`/`Inf` with `0.0` and clamps every value to `[-3.0, +3.0]`.

---

### 4.4 Cost-Aware Triple-Barrier Labeling

Defined in `src/nexus_scalp/labeling/triple_barrier.py`:
- **Algorithm:** Marcos Lopez de Prado's Purged Triple-Barrier method.
- **Barriers:**
  - *Upper Take Profit Barrier:* `buy_tp = entry + (atr * tp_mult)`
  - *Lower Stop Loss Barrier:* `buy_sl = entry - (atr * sl_mult)`
  - *Vertical Time Barrier:* `max_holding_bars = 15`
- **Friction Handling:** Calculates `effective_friction = max(friction_usd, entry_spread)` where `friction_usd = 0.35` ($0.35/oz). Feasibility check requires `tp_dist > effective_friction`.
- **Purging & Embargo:** `embargo_bars = 3`. Sets `is_purged = True` and skips overlapping forward bars to eliminate serial correlation.
- **MAE Safeguard:** On vertical time expiration, evaluates net PnL after friction; labels sample as trade if PnL > 0.50 * ATR and Maximum Adverse Excursion ratio <= `max_allowed_mae_ratio` (0.75).
- **Class Output:** Generates 3 classes: `NO_TRADE` (0), `BUY_MARKET` (1), `SELL_MARKET` (2).

---

### 4.5 Purged Walk-Forward Training & Validation

Defined in `src/nexus_scalp/training/walk_forward_trainer.py`:
- **Split Construction:** Blocked time-series walk-forward with purged gap (`purge_gap_bars = 15`). Default `num_folds = 34`, `train_ratio = 0.70`.
- **Data Scaling:** Fits `ScalerBundle` (mean and std) on training split only; transforms test split. Saved to `.scaler.npz`.
- **Loss Function & Optimization:** `AdamW(lr=5e-4, weight_decay=1e-4)` with `CosineAnnealingLR`. Class-balanced loss weights applied to minority trade classes (`active_class_boost = 3.0`).
- **Artifact Persistence:** Saves weights atomically to `model.pt`, scaler to `model.scaler.npz`, and metadata to `model.meta.json`.

---

### 4.6 Online Fine-Tuning, Quality Gate, & Hot-Swapping

Defined in `src/nexus_scalp/training/walk_forward_trainer.py` and `src/nexus_scalp/application/live_engine.py`:
- **Trigger & Cadence:** `LiveEngine` buffers up to 4000 feature snapshots in `_rolling_feature_records`. Every 50 new M1 bars (`_retrain_interval_bars = 50`), if buffer >= 300 rows, triggers `_trigger_async_online_fine_tune()` asynchronously via `asyncio.to_thread`.
- **Buffer Labeling:** Labels buffered M1 bars using `TripleBarrierLabeler`.
- **Model Cloning & Isolation:** Clones target `ScalpNet` model (`copy.deepcopy()`) to prevent state corruption during training.
- **Loss & Weights:** Uses `FocalLossWithSmoothing(gamma=1.0, label_smoothing=0.08)` and exponential time-decay sample weights (`half_life_bars = 120.0`). Random oversampling applied to minority active classes (`BUY`/`SELL`).
- **Multi-Metric Quality Gate:** Fine-tuned candidate model MUST pass all quality checks:
  1. *Validation Accuracy:* Must exceed baseline accuracy by at least `min_accuracy_improvement` (+3.0%) and reach at least `min_validation_accuracy` (35%).
  2. *SELL Dominance Cap:* Predicted SELL class ratio must NOT exceed `max_sell_dominance` (58%).
  3. *Class Recall:* Recall for present active classes (`BUY`/`SELL`) must NOT collapse to 0.0%.
  4. *Buffer Diversity Guard:* Rejects degenerate buffers containing < 2 distinct target classes.
  5. *Validation Loss:* Must strictly beat baseline validation loss (`final_val_loss <= baseline_val_loss + 1e-4`).
- **Atomic Hot-Swapping:** If Quality Gate PASSES, saves new checkpoint and atomically swaps `ModelBundle(model, scaler, artifact_path)` under `_bundle_lock`. If Quality Gate FAILS, rolls back to baseline weights with zero downtime.

---

## 5. Signal Pipeline & Rule Matrix Forensics

### 5.1 Decision Pipeline Flow

```text
Market Tick
     │
     ▼
Regime Guardian Gate  ──[Unsafe Regime?]──> NO_TRADE (BLOCKED_BY_GUARDIAN)
     │ Safe
     ▼
Tick Duplicate Filter ──[Duplicate Tick?]──> NO_TRADE (TICK_DUPLICATE_SUPPRESSED)
     │ New Tick
     ▼
AI Reversal Veto      ──[Opposing AI + Structure?]──> CLOSE_POSITION (AI_REVERSAL_SIGNAL)
     │ No Reversal
     ▼
Frequency Throttle    ──[< 60s since last signal?]──> NO_TRADE (ORDER_FREQUENCY_THROTTLED)
     │ Passed
     ▼
Exposure Gate         ──[Exposure >= 1?]──> NO_TRADE (MAX_EXPOSURE_REACHED / PENDING_ORDER_LOCKED)
     │ Exposure Free
     ▼
SMC God Mode Check    ──[BOS + OB + Sweep + FVG?]──> Bypass HTF filters (SMC_GOD_MODE)
     │ Standard Flow
     ▼
Rule Matrix Pre-Trade ──[Veto Rule Active?]──> NO_TRADE (Blocked by Rule)
     │ Passed
     ▼
Technical & AI Rules  ──[Confidence >= Threshold?]──> TradeProposal (BUY/SELL/LIMIT/STOP)
```

### 5.2 SMC God Mode Execution Path

When `feat_ob_valid_bos > 0` (or `choch`), valid order block (`order_block_type != 0`), confidence >= `ai_zone_confidence_threshold` (0.60), liquidity sweep presence, and FVG coincide:
- Sets `execution_mode = "SMC_GOD_MODE"`.
- Bypasses Higher-Timeframe trend conflict filters and Support/Resistance margin filters.
- Applies a 15% confidence penalty (`confidence *= 0.85`) as a risk offset.

### 5.3 Predictive Limit Order Generation

When a valid Order Block is confirmed (`order_block_type != 0`) and SMC God Mode is inactive:
- Places `BUY_LIMIT` or `SELL_LIMIT` at 50% Order Block Equilibrium level (`target_entry_price = swing_low + 0.50 * (swing_high - swing_low)`).
- Sets Stop Loss at deepest wick + ATR buffer (`deepest_wick - atr * atr_sl_buffer_multiplier`).
- Sets Take Profit at opposing liquidity level, adjusting if necessary to satisfy `min_risk_reward_ratio` (1.8).

### 5.4 30-Rule Matrix Engine

Defined in `src/nexus_scalp/signals/rule_matrix.py`:
- Database-backed rule matrix supporting pre-trade filter vetoes, force entries, and in-trade rule exits.
- Background hot-reloading from SQLite table `trading_rules_config` every 5 seconds.
- Rules include: `FVG_FILL_SCALP`, `LIQUIDITY_SWEEP_SNIPER`, `SPREAD_SQUEEZE_BREAKOUT`, `ZERO_DRAWDOWN_TRAILING`, `NEWS_FADE_REVERSAL`, `ACCOUNT_PRESERVATION_SAFEGUARD`, and 25+ others.

---

## 6. Risk Engine & Position Sizing Forensics

Defined in `src/nexus_scalp/risk/risk_engine.py`:

### 6.1 Volume Sizing Pipeline (`calculate_dynamic_volume`)

1. **Input Validation:** Verifies entry, SL, equity, free margin, leverage, contract size, and volume step for `None`, `NaN`, `Inf`, or non-positive values.
2. **Fixed Dollar Risk Calculation:** Computes `risk_amount_usd = account.equity * (risk_pct / 100.0)`.
3. **Raw Risk-Based Volume:** `raw_lots = risk_amount_usd / (sl_distance * trade_contract_size)`. Wide SL reduces lot size; tight SL increases lot size.
4. **Volume Step Floor:** Rounds down to nearest broker `volume_step` via `_floor_to_step()`.
5. **Account Tier Safety Ceilings:**
   - Equity < $100: Max 0.02 lots.
   - Equity < $1,000: Max 0.10 lots.
   - Equity < $10,000: Max 1.00 lot.
   - Equity >= $10,000: Max 10.0 lots (clamped by `volume_max`).
6. **Free Margin Clamp (20% Ceiling):** Calculates required margin; limits volume to require at most 20% of free margin (`max_margin_volume = (free_margin * 0.20 * leverage) / (contract_size * entry)`).
7. **Micro-Account Minimum Exception:** For accounts with equity < $50, if computed volume < `volume_min`, grants minimum lot exception (`volume = volume_min`).
8. **Almgren-Chriss Slippage Impact Guard:** `_estimate_market_impact()` computes expected USD slippage. If `slippage_usd / expected_reward_usd > max_impact_reward_ratio` (0.45), reduces volume stepwise down to `volume_min`.
9. **Execution Boundary Hard Clamp:** `OrderLifecycleManager._clamp_dispatch_volume()` unconditionally enforces `HARD_MAX_LOTS = 10.0` on every order dispatch.

### 6.2 Can any code path bypass the Risk Engine?

**No.** Every order dispatch path in `OrderLifecycleManager` (`dispatch_order`, `execute_ai_reversal`, `execute_lifecycle_action`) forces `_clamp_dispatch_volume()`, which queries the `RiskEngine` clamp when available and enforces `HARD_MAX_LOTS = 10.0` unconditionally.

---

## 7. Execution & MT5 Integration Forensics

### 7.1 MT5 Adapter Architecture

Defined in `src/nexus_scalp/adapters/mt5/mt5_adapter.py`:
- Direct C++ Win32 Python IPC binding via `MetaTrader5` package (Windows native).
- Implements `IMT5Port` interface defined in `src/nexus_scalp/ports/mt5_port.py`.
- Platform Guard: Checks `platform.system() == "Windows"`. Falls back gracefully on Linux/macOS with mock/stub return values for local testing.

### 7.2 Unified Dispatch Router

`OrderLifecycleManager` provides unified dispatch routing:
- `dispatch_order()`: Handles entry orders (`BUY`, `SELL`, `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`).
- `execute_lifecycle_action()`: Handles position lifecycle actions (`CLOSE_POSITION`, `PARTIAL_CLOSE`, `MODIFY_SL_TP`, `CANCEL_ORDER`).
- Rejection Safeguard: 3 consecutive broker order rejections transitions global state to `SAFE_MODE`, blocking further order submission.

---

## 8. Position Protection, Reversal Protocol, & State Machine

Defined in `src/nexus_scalp/execution/order_manager.py`:

### 8.1 The 11 Explicit In-Trade Lifecycles (`PositionState`)

```text
                               ┌──────────────────────────┐
                               │    Position Opened       │
                               └────────────┬─────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     │ PnL >= $0                                   │ PnL < $0
                     ▼                                             ▼
       ┌──────────────────────────┐                  ┌──────────────────────────┐
       │    PROFIT_UNPROTECTED    │                  │        LOSS_EARLY        │
       └────────────┬─────────────┘                  └────────────┬─────────────┘
                    │ PnL >= $15                                  │ Negative PnL
                    ▼                                             ▼
       ┌──────────────────────────┐                  ┌──────────────────────────┐
       │     PROFIT_PROTECTED     │                  │ LOSS_RECOVERY_CANDIDATE  │
       └────────────┬─────────────┘                  └────────────┬─────────────┘
                    │ Trailing Active                             │ Rec Prob >= 70%
                    ▼                                             ▼
       ┌──────────────────────────┐                  ┌──────────────────────────┐
       │     PROFIT_TRAILING      │                  │ LOSS_RECOVERY_CONFIRMED  │
       └────────────┬─────────────┘                  └────────────┬─────────────┘
                    │ Giveback < 70%                              │ Rec Prob < 30%
                    ▼                                             ▼
       ┌──────────────────────────┐                  ┌──────────────────────────┐
       │ PROFIT_GIVEBACK_WARNING  │                  │  LOSS_RECOVERY_FAILING   │
       └────────────┬─────────────┘                  └────────────┬─────────────┘
                    │ Peak >= $20 & Ret < 30%                     │ Exit Pressure
                    ▼                                             ▼
       ┌──────────────────────────┐                  ┌──────────────────────────┐
       │ PROFIT_GIVEBACK_CRITICAL │                  │    LOSS_EXIT_PRESSURE    │
       └──────────────────────────┘                  └────────────┬─────────────┘
                                                                  │ Budget/Horizon Exceeded
                                                                  ▼
                                                     ┌──────────────────────────┐
                                                     │      LOSS_HARD_EXIT      │
                                                     └──────────────────────────┘
```

### 8.2 Deterministic Position Protection Invariants

- **Breakeven Lock:** Triggered when PnL >= `$15.00` (`BREAKEVEN_PROFIT_USD`) or 1.5 * ATR in USD PnL. Locks in +0.20 pips profit (`BREAKEVEN_LOCK_PIPS`).
- **Profit Giveback Protection:** When peak unrealized profit >= `$20.00` (`PROFIT_GIVEBACK_PEAK_USD`), if profit retention ratio drops below 30% (`PROFIT_GIVEBACK_MIN_RETENTION`), triggers `PROFIT_GIVEBACK_PROTECTION` market close.
- **ATR Trailing Stop:** Tightens stop loss to `price - (1.15 * ATR)` for BUYs or `price + (1.15 * ATR)` for SELLs. Never loosens an existing stop loss.
- **Hold Score Floor:** Trades in floating profit receive a guaranteed `hold_score` floor of 85 (`PROFIT_SHIELD_SCORE_FLOOR_ACTIVE`). Trades that gave back profit and went negative are capped at `hold_score <= 10`.

### 8.3 AI Position Reversal Protocol

When holding a `BUY` and a strong `SELL` signal emerges (or vice versa):
1. `SignalPolicy._evaluate_ai_reversal()` detects opposing AI probabilities + structural signal (ChoCh / liquidity sweep / Kumo).
2. Emits `CLOSE_POSITION` proposal with reason `AI_REVERSAL_SIGNAL` and target `reversal_action`.
3. `OrderLifecycleManager.execute_ai_reversal()` intercepts proposal:
   - Closes conflicting active ticket on MT5 immediately.
   - Records `exit_mechanism = "AI_REVERSAL_EXIT"` in ledger autopsy.
   - Removes ticket from exposure cache immediately.
   - *Only after closure confirmation*, dispatches the new directional reversal order.
4. **No Stacking Invariant:** Opposing orders are NEVER stacked. If closure fails, no new order is dispatched.

---

## 9. Hot-Path Safety & Latency Forensics

### 9.1 Hot-Path Operations (Inside `_process_tick_pipeline`)

- `BarAggregator.process_tick()`: Memory operations only.
- `ScalpFeatureEngine.compute_from_bars()`: O(1) NumPy array slices and math.
- `MarketRegimeClassifier.classify_tick()`: O(1) volatility and spread checks.
- `ScalpNet` PyTorch Forward Pass: CPU/GPU matrix multiplication on 1x50 tensor.
- `SignalPolicy.evaluate_probabilities()`: Rule evaluations and proposal generation.
- `AuditRepository.log_signal()`: Appends to SQLite write queue (`_queue.put_nowait()`).
- `ServerState.update_live_visuals()`: Under thread lock, updates UI visual state.

### 9.2 Hot-Path Latency Assessment

- **Claim:** 50ms hot path guaranteed.
- **Reality:** Target / Architectural Goal.
- **Instrumentation Check:** There is NO performance timer (`perf_counter()`) or histogram surrounding `_process_tick_pipeline()`.
- **Latency Values in Audit Ledger:** Static float literals (`0.012`, `0.015`, `0.009`, `0.011`, `0.008`) are passed directly to `audit.log_order()` inside `order_manager.py`!
- **Classification:** ⚠️ PARTIALLY VERIFIED / TARGET ONLY.

---

## 10. Web / API, SSE, WebSocket, & Debug Hub Forensics

Defined in `src/nexus_scalp/web/server.py`:

| Method | Route | File | Function | Purpose | Risk |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | `server.py` | `serve_index()` | Serves frontend `index.html`. | Low |
| `GET` | `/styles.css` | `server.py` | `serve_styles()` | Serves dashboard CSS stylesheet. | Low |
| `GET` | `/app.js` | `server.py` | `serve_app()` | Serves frontend JS logic. | Low |
| `GET` | `/api/status` | `server.py` | `get_status()` | Returns full system telemetry & state. | Low |
| `GET` | `/api/rules` | `server.py` | `get_trading_rules()` | Returns status of all 30+ scalping rules. | Low |
| `POST` | `/api/rules/toggle` | `server.py` | `toggle_trading_rule()` | Enables/disables a specific scalping rule. | Medium |
| `GET` | `/api/account/summary` | `server.py` | `get_account_summary()` | Returns balance, equity, win rate & metrics. | Low |
| `GET` | `/api/account/trades` | `server.py` | `get_account_trades()` | Paginated closed trade autopsies from DB. | Low |
| `GET` | `/api/account/growth` | `server.py` | `get_account_growth()` | Equity growth chart points. | Low |
| `POST` | `/api/engine/toggle` | `server.py` | `toggle_engine()` | Starts or stops live engine run loop. | High |
| `GET` | `/api/config` | `server.py` | `get_config()` | Reads active YAML configuration. | Low |
| `POST` | `/api/config` | `server.py` | `save_config()` | Atomic write & live hot-reload of config. | High |
| `GET` | `/api/chart/history` | `server.py` | `get_chart_history()` | Bootstraps 150+ bar history & visual overlays. | Low |
| `GET` | `/api/algo/config` | `server.py` | `get_algo_config()` | Returns quantitative algo parameters. | Low |
| `PUT` | `/api/algo/config` | `server.py` | `save_algo_config()` | Hot-swaps algo parameters in live policy. | High |
| `POST` | `/api/positions/modify` | `server.py` | `modify_position()` | Modifies live position SL / TP on broker. | High |
| `POST` | `/api/positions/close` | `server.py` | `close_position()` | Closes live position ticket on broker. | High |
| `POST` | `/api/simulation/tick` | `server.py` | `inject_tick()` | Injects simulated tick into live pipeline. | Medium |
| `POST` | `/api/replay/toggle` | `server.py` | `toggle_replay()` | Toggles historical replay simulation mode. | Medium |
| `GET` | `/api/debug/features` | `server.py` | `get_debug_features()` | Real-time values & health status of all 50D features. | Low |
| `POST` | `/api/debug/model-test` | `server.py` | `post_debug_model_test()` | Runs instant PyTorch inference against 50D vector. | Low |
| `GET` | `/api/debug/health` | `server.py` | `get_debug_health()` | Diagnostics for Features, Model, Risk, MT5, DB. | Low |
| `GET` | `/api/debug/ipc-telemetry` | `server.py` | `get_debug_ipc_telemetry()` | Recent execution events & IPC latencies. | Low |
| `GET` | `/api/observability/stats` | `server.py` | `get_observability_stats()` | Telegram queue depth & status. | Low |
| `GET` | `/api/ticks/stream` | `server.py` | `sse_telemetry_stream()` | Async SSE stream emitting system state @ 5Hz. | Low |
| `WS` | `/web`, `/ws` | `server.py` | `websocket_endpoint()` | WebSocket stream for live visualizer updates. | Low |

---

## 11. Observability & Telegram Notifier Forensics

Defined in `src/nexus_scalp/observability/telegram_notifier.py` and `logging.py`:
- **Structured Logging:** Uses `structlog` with JSON formatting in production and colored console presentation in dev mode. Log outputs rotated under `artifacts/logs/`.
- **Telegram Alert Subsystem:**
  - Non-blocking execution via `concurrent.futures.ThreadPoolExecutor(max_workers=2)`.
  - Token Security: Redacts Telegram bot tokens from log files. Supports environment variable override `NEXUS_TELEGRAM_BOT_TOKEN`.
  - Conversation Threading: Thread replying via `reply_to_message_id`. Tracks message IDs per ticket in `_order_message_ids`.
  - Rate Limiting & Queue: Internal Queue `_queue` with rate-limiting sleep intervals to prevent Telegram API HTTP 429 errors.

---

## 12. Configuration Architecture & Flow

```text
configs/base.yaml  or  configs/live.yaml
               │
               ▼
Environment Variables (NEXUS_*)
               │
               ▼
AppConfig (Pydantic BaseSettings in configuration/config.py)
               │
               ▼
Live Runtime Objects (SignalPolicy, RiskEngine, OrderLifecycleManager)
```

- **Dynamic Hot-Swapping:** `POST /api/config` and `PUT /api/algo/config` write to `configs/live.yaml` atomically via temporary file replacement (`.yaml.tmp` -> `.yaml`) and update running instance attributes (`engine.config`, `signal_policy.algo_config`, `risk_engine.config`) without restarting the process.

---

## 13. Testing & CI/CD Forensics

### 13.1 Test Inventory

- **Total Test Files:** 19 test modules in `tests/` (16 unit, 3 integration).
- **Test Status:** 67 tests passing (`python -m pytest`).
- **Integration Tests:**
  - `test_database_execution_audit.py`: Validates WAL mode, queue persistence, and ledger autopsy writes.
  - `test_signal_pipeline_health.py`: End-to-end signal pipeline health checks.
  - `test_playwright_e2e.py`: Playwright frontend visualizer tests (skipped if Playwright unavailable).

### 13.2 CI/CD Workflows (`.github/workflows/`)

1. `ci.yml`: Triggers on push/PR to `main` and `develop`. Uses Python 3.11. Runs `ruff check .`, `ruff format --check .`, `mypy src`, and `pytest --cov=src`.
2. `docker.yml`: Builds single-architecture `linux/amd64` Docker image and pushes to GitHub Container Registry (`ghcr.io`).
3. `release.yml`: Creates automated GitHub Release when a `v*` tag is pushed.
4. `security.yml`: Runs GitHub CodeQL Python analysis and Trivy filesystem vulnerability scanner on PRs and weekly schedule (Mondays at 06:00 UTC).

---

## 14. Verified Hard Invariants

### 🔴 VERIFIED INVARIANTS (Confirmed by Code)

1. **Frozen Domain Models:** All dataclasses in `domain/models.py` use `ConfigDict(frozen=True)`. Instance mutation is impossible.
2. **50D Feature Vector Contract:** `FEATURE_NAMES` tuple contains 50 features. `ScalpNet`, `WalkForwardTrainer`, and `ScalpFeatureEngine` enforce 50D tensor inputs.
3. **Execution Volume Clamping:** `OrderLifecycleManager._clamp_dispatch_volume()` forces lot size clamping to `HARD_MAX_LOTS = 10.0` and queries `RiskEngine` on every order dispatch.
4. **Single-Exposure Limit:** `MAX_TOTAL_EXPOSURE = 1` enforced in `SignalPolicy` and `OrderLifecycleManager`. Max 1 active position OR 1 pending order engine-wide.
5. **Pending Order Lock:** Restricts cancel/recreate churn for pending limit orders with a 30-second placement lock (`PENDING_ORDER_LOCK_SECONDS = 30.0`) and 1.0x ATR price drift requirement.
6. **AI Reversal No-Stacking Invariant:** `execute_ai_reversal()` closes conflicting active position and tags `AI_REVERSAL_EXIT` before dispatching the opposing order.
7. **Polars Bitwise Filtering Invariant:** All Polars filters in `walk_forward_trainer.py` and `triple_barrier.py` use bitwise operators (`~pl.col(...)`, `&`, `|`).
8. **Thread-Safe Model Hot-Swapping:** Model and scaler bundle replacements in `LiveEngine` are synchronized under `_bundle_lock` (`threading.RLock`).

---

### 🟡 DOCUMENTED BUT UNVERIFIED

1. **50ms Execution Latency Guarantee:** `LiveEngine` loops with `await asyncio.sleep(0.05)`, but tick execution latency is not instrumented or measured with timers.

---

### 🟠 PARTIALLY IMPLEMENTED

1. **Telegram Secret Redaction:** Redacts bot tokens from structlog messages, but raw tokens may still exist in YAML configuration files if hardcoded.

---

### ❌ FALSE / STALE DOCUMENTATION

1. **Legacy Order Manager File:** `src/nexus_scalp/features/order_manager.py` (234 lines) is dead/stale code and is not imported anywhere in `src/` or `tests/`.
2. **Stale CLI Training Script:** `src/cli/train_model.py` hardcodes `range(18)` (18D features), which crashes if executed against `WalkForwardTrainer` (which expects 50D).

---

## 15. Documentation vs. Reality Audit

| Existing Claim | Actual Code Implementation | Status | Evidence | Recommendation |
| :--- | :--- | :--- | :--- | :--- |
| **50D Feature Contract** | `scalp_features.py` defines 50 feature names, `ScalpNet(num_features=50)`. | ✅ VERIFIED | `len(FEATURE_NAMES) == 50` in `scalp_features.py:166` | Preserve 50D contract across all tools. |
| **Purged Walk-Forward Training** | `WalkForwardTrainer` implements purged time-series splits, scaling, class weights. | ✅ VERIFIED | `walk_forward_trainer.py:137` | Maintain purging gap and embargo logic. |
| **Online Fine-Tuning** | Implemented with Quality Gate (accuracy gain, sell dominance cap, class recall). | ✅ VERIFIED | `walk_forward_trainer.py:270` | Preserve multi-metric Quality Gate checks. |
| **Triple-Barrier Labeling** | `TripleBarrierLabeler` implements TP/SL/time barriers with spread friction and MAE safeguard. | ✅ VERIFIED | `triple_barrier.py:44` | Preserve friction deduction and MAE check. |
| **AI Position Reversal Protocol** | Closes opposing position before flipping, tags `AI_REVERSAL_EXIT`, avoids stacking. | ✅ VERIFIED | `order_manager.py:447` | Maintain no-stacking close-then-flip sequence. |
| **Pending Order 30s Lock** | Re-quotes blocked if age <= 30s OR price drift < 1.0x ATR. | ✅ VERIFIED | `order_manager.py:238`, `policy.py:249` | Do not reduce lock time below 30s. |
| **50ms Hot-Path Guaranteed** | Event loop sleeps 50ms, but execution latency is unmeasured (hardcoded in logs). | ⚠️ PARTIALLY VERIFIED | Static float literals in `order_manager.py` | Add real `time.perf_counter()` instrumentation. |
| **CLI Training Script Contract** | `src/cli/train_model.py` hardcodes `range(18)` (18D), causing a runtime crash. | ❌ FALSE / STALE | `src/cli/train_model.py:103` | Document discrepancy; update to 50D in future task. |
| **Single Active Order Manager** | Codebase contains two `order_manager.py` files (`execution/` vs `features/`). | ❌ FALSE / STALE | `src/nexus_scalp/features/order_manager.py` | Document `features/order_manager.py` as dead code. |

---

## 16. Future Engineering Recommendations

*Note: The recommendations below are for future engineering reference only. DO NOT implement any code changes as part of this read-only audit.*

### 🔴 Critical Recommendations

1. **Quarantine / Deprecate Stale CLI Training Script:**
   - **Problem:** `src/cli/train_model.py` attempts to extract 18 features (`range(18)`), which violates `WalkForwardTrainer.NUM_FEATURES = 50` and causes an immediate `ValueError` crash.
   - **Affected File:** `src/cli/train_model.py`.
   - **Consequence:** Users or developers trying to train a model via CLI will experience pipeline failure.
   - **Recommended Direction:** Update `src/cli/train_model.py` to extract all 50 features matching `FEATURE_NAMES`.

2. **Remove / Deprecate Dead Legacy File:**
   - **Problem:** `src/nexus_scalp/features/order_manager.py` is an orphaned 234-line file that is not imported anywhere in `src/` or `tests/`.
   - **Affected File:** `src/nexus_scalp/features/order_manager.py`.
   - **Consequence:** Confusion for developers and AI agents regarding which order manager is active.
   - **Recommended Direction:** Delete or deprecate `src/nexus_scalp/features/order_manager.py` in a future cleanup task.

---

### 🟠 High Recommendations

1. **Add Real Latency Instrumentation to Hot Path:**
   - **Problem:** `audit.log_order()` receives hardcoded static float literals (e.g. `0.012`, `0.015`) for latency instead of real timing measurements.
   - **Affected File:** `src/nexus_scalp/execution/order_manager.py`.
   - **Consequence:** Audit logs report inaccurate execution latencies, obscuring IPC or network performance degradations.
   - **Recommended Direction:** Wrap order dispatch calls with `time.perf_counter()` to measure and record real microsecond-level execution latencies.

---

### 🟡 Medium Recommendations

1. **Consolidate Duplicate `order_manager.py` References in Packaging:**
   - **Problem:** `pyproject.toml` and package build scripts list `src/nexus_scalp/features/order_manager.py` in source lists.
   - **Affected File:** `pyproject.toml` / package manifests.
   - **Consequence:** Includes dead code in package distribution wheels.
   - **Recommended Direction:** Clean up package manifest to point exclusively to `src/nexus_scalp/execution/order_manager.py`.

---

### 🔵 Low Recommendations

1. **Enhance Debug Hub Metrics Visualization:**
   - **Problem:** Debug Hub endpoints (`/api/debug/features`, `/api/debug/health`) provide rich JSON diagnostics, but frontend chart components can be expanded to display real-time feature health histograms.
   - **Affected File:** `Web/app.js`.
   - **Recommended Direction:** Add a feature anomaly widget to the Web UI dashboard.
