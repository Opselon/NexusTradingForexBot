
# 🧠 Nexus Scalp Engine (NSE) - AI Agent Skill & Context Anchor

> **TARGET AUDIENCE:** AI Coding Assistants (Cursor, Copilot, ChatGPT, Claude).
> **DIRECTIVE:** You are a Senior Quantitative Developer and HFT (High-Frequency Trading) Systems Engineer. When modifying this codebase, you must adhere strictly to the invariants, type-safety rules, and architectural boundaries defined in this document. 
> **NEVER compromise the 50ms hot-path execution latency.**

---

## 📑 Table of Contents
1. [Core Toolchain & Validation Rules](#1-core-toolchain--validation-rules)
2. [Project Architecture & Directory Structure](#2-project-architecture--directory-structure)
3. [Deep Dive: Core Modules & File Responsibilities](#3-deep-dive-core-modules--file-responsibilities)
4. [Execution Workflows & Data Pipelines](#4-execution-workflows--data-pipelines)
5. [Hard Invariants & "Never Do This" Rules](#5-hard-invariants--never-do-this-rules)

---

## 1. Core Toolchain & Validation Rules

Before proposing any code changes, ensure they pass the following CI/CD gates.

### 🧹 Ruff (Linter & Formatter)
The project uses strict `ruff` rules configured in `pyproject.toml`.
- **Command (Auto-Fix):** `ruff check . --fix --unsafe-fixes`
- **Command (Format):** `ruff format .`
- **Ignored Rules:** We explicitly ignore ML/Quant standard naming conventions (`N802`, `N803`, `N806` for `X_train`, `F` for `torch.nn.functional`), `E501` (Line length), and `PLR2004` (Magic numbers in tests).
- **Rule:** Module level imports must be at the TOP of the file (Rule `E402`), except in entry point scripts where `sys.path.insert` is used.

### 🛡️ Mypy (Static Type Checking)
Strict typing is enforced for domain logic.
- **Command:** `mypy src`
- **Rule:** Always use explicit type annotations. Use `dict[str, Any]`, `list[dict[str, Any]]`.
- **Rule:** Guard against `None` types explicitly (`Optional[T]` or `T | None`). 
- **Rule:** Third-party libraries (`MetaTrader5`, `fastapi`, `torch`, `polars`) are ignored in `pyproject.toml` overrrides, but internal ports (e.g., `IMT5Port`) must strictly match subclass signatures.

### 🧪 Pytest & Codecov
- **Command:** `pytest --cov=src --cov-report=xml --cov-report=term -q`
- **Rule:** E2E Playwright tests must be skipped gracefully if `playwright` is not installed (`pytest.importorskip`).

---

## 2. Project Architecture & Directory Structure

```text
NexusTradingForexBot/
├── NexusTradingForexBot.py           # Main Production Launcher
├── pyproject.toml                    # Toolchain & Dependency Config
├── .github/workflows/ci.yml          # GitHub Actions Pipeline
├── Web/                              # Frontend UI (HTML, CSS, JS)
├── configs/                          # YAML Configuration (Base & Live)
├── artifacts/
│   ├── logs/                         # Rotated structured JSON/Console logs
│   └── models/scalp/XAUUSD/v1.0.0/   # PyTorch (.pt) & Scaler (.npz) checkpoints
└── src/
    └── nexus_scalp/
        ├── adapters/                 # External Integrations (MT5, SQLite, Gateway)
        ├── application/              # Live Engine & Async Event Loops
        ├── cli/                      # Typer CLI Commands (run, doctor, train)
        ├── configuration/            # Pydantic Settings
        ├── domain/                   # Pure Business Logic (Models, Enums)
        ├── execution/                # Order Manager, Position Protection
        ├── features/                 # Feature Eng, Regime Classifier (50D Tensor)
        ├── labeling/                 # Triple-Barrier Labeling
        ├── market_data/              # OHLC Bar Aggregator
        ├── models/                   # PyTorch Neural Networks (ScalpNet)
        ├── observability/            # Structlog & Telegram Notifier
        ├── ports/                    # Dependency Inversion Interfaces
        ├── risk/                     # Risk Engine (Lot Sizing, Margin)
        ├── signals/                  # Policy Router & Rule Matrix
        ├── training/                 # Walk-Forward Trainer
        └── web/                      # FastAPI Dashboard & SSE Stream
```

---

## 3. Deep Dive: Core Modules & File Responsibilities

### `src/nexus_scalp/domain/models.py`
- **Role:** Pure data structures using Pydantic `ConfigDict(frozen=True)`.
- **Key Objects:** `TickData` (UTC timezone enforced, bid<=ask invariant), `TradeProposal`, `Position`.
- **AI Rule:** NEVER mutate instances of these models. They are immutable. Recreate them using `.model_copy(update={...})` if necessary.

### `src/nexus_scalp/features/scalp_features.py`
- **Role:** Generates the **50-Dimensional Master Feature Vector**.
- **Logic:** Calculates Order Flow Imbalance (OFI), Ichimoku Kumo breakouts, SMC (Smart Money Concepts), ICT Fair Value Gaps (FVG), and liquidity sweeps.

### `src/nexus_scalp/models/scalp_net.py`
- **Role:** The AI Brain. An Institutional Causal Temporal Transformer.
- **Architecture:** 
  - Dual Path: 2D ResNet MLP (for fast snapshots) + 3D Causal TCN (for sequences).
  - Uses `CausalConv1d` (strict left-padding to prevent future data leakage).
  - Outputs 4 classes: `0=NO_TRADE, 1=BUY_MARKET, 2=SELL_MARKET, 3=WAIT`.

### `src/nexus_scalp/training/walk_forward_trainer.py`
- **Role:** End-to-End deep learning orchestrator.
- **Logic:** Purged time-series splits, Focal Loss with Label Smoothing, Random Oversampling for minority classes (BUY/SELL), and Exponential Time-Decay weights.
- **AI Rule:** When filtering Polars dataframes, NEVER use Python's `not`. Always use bitwise `~pl.col(...)` or `.not_()`.

### `src/nexus_scalp/application/live_engine.py`
- **Role:** The Async Orchestrator and Main Event Loop.
- **Logic:** Connects to MT5, processes ticks at 50ms latency, maintains synchronized states for the Web UI, triggers async background online-retraining (fine-tuning), and evaluates the Hedging policy.
- **AI Rule:** Do NOT put heavy blocking I/O (like training or DB queries) in `_process_tick_pipeline`.

### `src/nexus_scalp/execution/order_manager.py`
- **Role:** Wall-Street grade position lifecycle manager.
- **Logic:** 
  - Contains the **60-Scenario Deterministic Router**.
  - Implements **Profit-Shield Guards** (Breakeven locks at +$15/1.5 ATR, MFE 70% retention trailing stops).
  - Detects Local State Features (LSF) desyncs.
  - Implements Almgren-Chriss temporary market impact slippage models.

### `src/nexus_scalp/signals/policy.py`
- **Role:** The Signal Arbitrator.
- **Logic:** 
  - Prevents "Same-Level Duplicate Re-Entries".
  - Implements "AI Position Reversal Protocol" (Closes opposing positions before flipping, never stacks).
  - Merges AI logits with SMC structural data to form final `TradeProposal`s.

### `src/nexus_scalp/observability/telegram_notifier.py`
- **Role:** ThreadPool-backed, async-safe Telegram alert system.
- **Logic:** Redacts secrets, utilizes HTML parsing, handles rate-limits, and supports Telegram Threading (`reply_to_message_id`).

### `src/nexus_scalp/web/server.py`
- **Role:** FastAPI Control Dashboard.
- **Logic:** Exposes REST endpoints, SSE (`/api/ticks/stream`) for zero-latency UI updates, and a "Debug Hub" for instant model-testing. 
- **AI Rule:** Background tasks created in endpoints MUST be strongly referenced (e.g., stored in `app.state.background_tasks`) to prevent Python's garbage collector from killing them (`RUF006`).

---

## 4. Execution Workflows & Data Pipelines

### ⚡ The 50ms Hot-Path Tick Pipeline (Live Mode)
1. `LiveEngine` receives `TickData` from `DirectMT5Adapter`.
2. Tick is pushed to `BarAggregator`. If a minute crosses, a new `BarData` is finalized.
3. `ScalpFeatureEngine` computes the 50D `FeatureVector` in `O(1)`.
4. `MarketRegimeClassifier` updates volatility and spread-chop state.
5. PyTorch `ScalpNet` runs `.forward()` on the scaled 50D tensor -> outputs `Softmax` probabilities.
6. `SignalPolicy` evaluates probabilities + regime + filters -> emits `TradeProposal`.
7. `RiskEngine` clamps lot size (Max allowed lots, margin limits).
8. `OrderLifecycleManager` dispatches order to MT5 and writes to `AuditRepository`.

### 🔄 The AI Position Reversal Workflow
If the bot holds a `BUY` and the AI emits a strong `SELL` signal:
1. `SignalPolicy` detects opposing logic.
2. Emits a `CLOSE_POSITION` proposal with reason `AI_REVERSAL_SIGNAL`.
3. `OrderManager` intercepts this, closes the `BUY` ticket, and stamps the ledger with `AI_REVERSAL_EXIT`.
4. *Only after confirmation of closure*, it dispatches the new `SELL_STOP/SELL_MARKET` order.

### 🧠 The Online Fine-Tuning Workflow
1. `LiveEngine` buffers the last 300+ M1 bars into a Polars DataFrame.
2. Triggered asynchronously every 50 bars.
3. `TripleBarrierLabeler` dynamically labels the buffer (aware of spread friction).
4. `WalkForwardTrainer` creates a cloned PyTorch model, trains it using class-balanced weights.
5. **Quality Gate:** If the new model beats the baseline validation accuracy by >= 3% and maintains healthy SELL dominance, it is atomically saved and hot-swapped into the `LiveEngine` (`_bundle_lock`).

---

## 5. Hard Invariants & "Never Do This" Rules

1. **NEVER BLOCK THE EVENT LOOP:** Do not use `time.sleep()` in `live_engine.py` or `server.py`. Use `await asyncio.sleep()`.
2. **NEVER BYPASS THE RISK ENGINE:** All volume sizing must pass through `risk_engine.get_clamped_position_size()`.
3. **NEVER MUTATE DOMAIN MODELS:** Dataclasses/BaseModels in `models.py` are frozen. 
4. **POLARS LOGIC RULES:** When filtering `pl.DataFrame`, you MUST use bitwise operators (`~`, `&`, `|`), not Python boolean keywords (`not`, `and`, `or`). Example: `df.filter(~pl.col("is_purged"))`.
5. **INTERFACE PARITY:** If you add a parameter to an adapter method (e.g., `close_position(ticket, volume)`), you MUST update the base interface `IMT5Port` first to satisfy Mypy `[override]` rules.
6. **NO MAGIC NUMBERS IN ROUTERS:** Use configurations from `AlgoConfig` (e.g., `config.algo.atr_sl_buffer_multiplier`).
7. **TELEGRAM THREADING:** When writing new Telegram notifications, always accept and pass `reply_to_message_id` to maintain conversation threading.
```