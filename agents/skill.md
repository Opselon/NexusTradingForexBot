# 🧠 Nexus Scalp Engine (NSE) - Forensic Master Skill & Context Anchor

> **TARGET AUDIENCE:** AI Coding Agents (Cursor, Copilot, ChatGPT, Claude, Jules).
> **PURPOSE:** Authoritative, repository-grounded Master Skill & Architecture Map for the Nexus Scalp Engine repository.
> **SOURCE OF TRUTH:** Actual Executable Codebase (verified via forensic audit).
> **ABSOLUTE DIRECTIVE:** READ-ONLY for codebase files. Do NOT modify any file in this repository except `agents/skill.md`.

---

## 📑 Table of Contents

1. [AI Agent Knowledge Map: Read This First](#1-ai-agent-knowledge-map-read-this-first)
2. [Forensic Classification & Verification Badges](#2-forensic-classification--verification-badges)
3. [Repository Architecture & File-by-File Inventory](#3-repository-architecture--file-by-file-inventory)
4. [Actual Entrypoints & Startup Execution Path](#4-actual-entrypoints--startup-execution-path)
5. [AI / ML Forensic Audit & Model Lifecycle Map](#5-ai--ml-forensic-audit--model-lifecycle-map)
   - 5.1 [AI/ML Component Inventory](#51-aiml-component-inventory)
   - 5.2 [Exact ML Execution Code Sites](#52-exact-ml-execution-code-sites)
   - 5.3 [ScalpNet Neural Architecture](#53-scalpnet-neural-architecture)
   - 5.4 [Label / Model Output Class Compatibility Audit](#54-label--model-output-class-compatibility-audit)
   - 5.5 [Feature Engineering (50D Master Contract)](#55-feature-engineering-50d-master-contract)
   - 5.6 [Cost-Aware Triple-Barrier Labeling Engine](#56-cost-aware-triple-barrier-labeling-engine)
   - 5.7 [Purged Walk-Forward Training & Validation](#57-purged-walk-forward-training--validation)
   - 5.8 [Online Fine-Tuning, Quality Gate, & Atomic Hot-Swapping](#58-online-fine-tuning-quality-gate--atomic-hot-swapping)
6. [Signal Pipeline & Rule Matrix Forensics](#6-signal-pipeline--rule-matrix-forensics)
7. [Risk Engine & Lot Sizing Forensics](#7-risk-engine--lot-sizing-forensics)
8. [Execution & MT5 Integration Forensics](#8-execution--mt5-integration-forensics)
9. [Position Protection, State Machine, & Adaptive Exit Engine](#9-position-protection-state-machine--adaptive-exit-engine)
10. [Hot-Path Latency & Event-Loop Forensics](#10-hot-path-latency--event-loop-forensics)
11. [Web UI, FastAPI, SSE, WebSocket, & Debug Hub Forensics](#11-web-ui-fastapi-sse-websocket--debug-hub-forensics)
12. [Observability, Database Persistence, & Ledger Autopsy](#12-observability-database-persistence--ledger-autopsy)
13. [Configuration Architecture & Dynamic Propagation](#13-configuration-architecture--dynamic-propagation)
14. [Testing & CI/CD Pipeline Audit](#14-testing--cicd-pipeline-audit)
15. [Documentation vs. Reality Audit Matrix](#15-documentation-vs-reality-audit-matrix)
16. [Dead, Legacy, & Discrepant Code Inventory](#16-dead-legacy--discrepant-code-inventory)
17. [Prioritized Engineering Recommendations (P0-P3)](#17-prioritized-engineering-recommendations-p0-p3)

---

## 1. AI Agent Knowledge Map: Read This First

If you are an AI coding agent tasked with inspecting or extending this codebase in the future, **read this section before reading or touching any code file**.

### 🗺️ Layer Mapping & Interface Invariants

Before modifying any file, verify its architectural layer and dependencies:

| Layer | Directories / Files | Role & Responsibilities | Forensic Status | Invariants & Rules |
| :--- | :--- | :--- | :--- | :--- |
| **Domain** | `src/nexus_scalp/domain/` (`models.py`, `enums.py`) | Immutable data contracts (`TickData`, `TradeProposal`, `Position`, `TradeOrder`, `AccountInfo`). | 🟢 VERIFIED | **NEVER MUTATE.** All domain models use Pydantic `frozen=True`. Use `.model_copy(update={...})`. |
| **Ports** | `src/nexus_scalp/ports/` (`mt5_port.py`, `gateway_port.py`) | Protocol interfaces defining dependency inversion boundaries. | 🟢 VERIFIED | Any signature change in `IMT5Port` MUST be updated across `DirectMT5Adapter`, `RemoteGatewayAdapter`, and `PaperAdapter`. |
| **Adapters** | `src/nexus_scalp/adapters/` (`mt5/`, `paper/`, `database/`) | External IPC, Win32 MT5 bindings, paper simulation, SQLite WAL persistence (`AuditRepository`). | 🟢 VERIFIED | DB writes are queued asynchronously via a dedicated background worker thread (`_worker_thread`). |
| **Features** | `src/nexus_scalp/features/` (`scalp_features.py`, `regime_classifier.py`) | 50D Feature Vector calculation and Market Regime classification. | 🟢 VERIFIED | **Contract: exactly 50 float features.** Values must be finite and clipped to `[-3.0, +3.0]`. |
| **Models** | `src/nexus_scalp/models/` (`scalp_net.py`) | PyTorch deep neural network (`ScalpNet`). | 🟢 VERIFIED | Dual-path: 2D MLP for single-tick, 3D TCN + Self-Attention for sequences. Inputs: `(Batch, 50)`. Output head: 4 logits (`0=NO_TRADE`, `1=BUY_MARKET`, `2=SELL_MARKET`, `3=WAIT`). |
| **Training** | `src/nexus_scalp/training/` (`walk_forward_trainer.py`), `src/nexus_scalp/labeling/` (`triple_barrier.py`) | Purged walk-forward trainer, triple-barrier labeler, online fine-tuning. | 🟢 VERIFIED | Uses Polars. When filtering Polars DataFrames, **ALWAYS use bitwise operators** `~pl.col(...)`, NEVER Python `not`. Labeler outputs 3 classes (`0, 1, 2`), mapped dynamically to 4-class ScalpNet head. |
| **Signals** | `src/nexus_scalp/signals/` (`policy.py`, `rule_matrix.py`) | Multi-confluence policy routing, SMC God Mode, predictive limit generation, 30+ rule matrix. | 🟢 VERIFIED | Generates `TradeProposal`. Respects Regime Guardian Gate (unsafe regimes return `NO_TRADE`). |
| **Risk** | `src/nexus_scalp/risk/` (`risk_engine.py`) | Capital allocation, dynamic lot sizing, Almgren-Chriss slippage bounds, margin clamping. | 🟢 VERIFIED | All entry proposals pass through `calculate_dynamic_volume()`. Clamped to free margin (20%) and account tier caps. |
| **Execution** | `src/nexus_scalp/execution/` (`order_manager.py`) | 60-scenario router, 11 position lifecycles, breakeven lock, profit giveback, AI reversal. | 🟢 VERIFIED | Enforces `HARD_MAX_LOTS = 10.0` and `MAX_TOTAL_EXPOSURE = 1`. Restricts pending re-quotes with a 30s lock and 1.0x ATR drift. |
| **Application**| `src/nexus_scalp/application/` (`live_engine.py`) | Main async event loop, bar aggregation, state sync, retrain orchestrator. | 🟢 VERIFIED | **NEVER BLOCK THE EVENT LOOP.** No synchronous I/O or model training inside `_process_tick_pipeline()`. |
| **Web / API** | `src/nexus_scalp/web/` (`server.py`), `Web/` | FastAPI REST endpoints, WebSocket (`/web`), SSE (`/api/ticks/stream`), Debug Hub. | 🟢 VERIFIED | Enum instances in JSON responses MUST be serialized via `serialize_enums()`. Background tasks stored in `app.state.background_tasks`. |

---

## 2. Forensic Classification & Verification Badges

Every architectural claim, file, component, and invariant in this document is tagged with one of the following forensic status badges:

* 🟢 **VERIFIED:** Directly confirmed by actual executable source code and runtime wiring.
* 🟡 **PARTIALLY VERIFIED:** Behavior exists but differs slightly in limits, configuration defaults, or operational constraints.
* 🔵 **DOCUMENTATION ONLY:** Stated in comments or docs but not backed by executable logic.
* 🟠 **NEEDS INVESTIGATION:** Evidence exists in code but cannot conclusively determine runtime behavior without real MT5 connection.
* 🔴 **CONTRADICTED:** Documentation / prior claims explicitly conflict with actual implementation.
* ⚫ **DEAD / UNUSED:** Unreachable, orphaned, or obsolete file/code.
* 🚧 **NOT IMPLEMENTED:** Planned or described capability is absent.

---

## 3. Repository Architecture & File-by-File Inventory

### 📁 Complete Repository Directory Tree

```text
NexusTradingForexBot/
├── .github/workflows/                 # GitHub CI/CD Pipelines
│   ├── ci.yml                         # 🟢 Quality, Ruff, Mypy, Pytest with coverage
│   ├── docker.yml                     # 🟢 Docker Build & Publish to GHCR
│   ├── release.yml                    # 🟢 Automated GitHub Release on v* tags
│   └── security.yml                   # 🟢 CodeQL Analysis & Trivy Security Scans
├── Web/                               # 🟢 Modern Frontend Control Center UI
│   ├── app.js                         # 🟢 Interactive Dashboard UI logic
│   ├── index.html                     # 🟢 Visualizer & Control Panel Markup
│   └── styles.css                     # 🟢 Premium Dark Glassmorphism Styling
├── configs/                           # Application Configurations
│   ├── base.yaml                      # 🟢 Default base settings
│   └── live.yaml.example              # 🟢 Example live runtime configuration
├── docker/                            # Containerization Scripts
│   ├── entrypoint.sh                  # 🟢 Container entrypoint script
│   └── healthcheck.sh                 # 🟢 Container health check script
├── src/
│   ├── cli/
│   │   └── train_model.py             # ⚫ Legacy CLI Training Script (Stale 18D contract)
│   └── nexus_scalp/
│       ├── adapters/                  # External Infrastructure Integration
│       │   ├── database/
│       │   │   └── audit_repository.py# 🟢 SQLite WAL Audit Repository & Ledger Autopsy
│       │   ├── mt5/
│       │   │   ├── mt5_adapter.py     # 🟢 Direct Win32 MetaTrader 5 IPC Binding
│       │   │   └── remote_gateway.py  # 🟢 ZMQ Remote Gateway Adapter
│       │   └── paper/
│       │       └── paper_adapter.py   # 🟢 Paper Trading / Simulation Adapter
│       ├── application/
│       │   └── live_engine.py         # 🟢 Main Async Live Orchestrator & Hot Path
│       ├── cli/
│       │   └── main.py                # 🟢 Primary Typer CLI Application (`nse`)
│       ├── configuration/
│       │   └── config.py              # 🟢 Pydantic Settings & YAML Config Parser
│       ├── domain/
│       │   ├── enums.py               # 🟢 Domain Enumerations
│       │   └── models.py              # 🟢 Domain Data Contracts (Pydantic frozen=True)
│       ├── execution/
│       │   └── order_manager.py       # 🟢 Authoritative Order Lifecycle Manager
│       ├── features/
│       │   ├── order_manager.py       # ⚫ DEAD / UNUSED Legacy Order Manager File (234 lines)
│       │   ├── regime_classifier.py   # 🟢 Market Regime Classifier (10 Regimes)
│       │   └── scalp_features.py      # 🟢 50D Master Feature Vector Pipeline
│       ├── labeling/
│       │   └── triple_barrier.py      # 🟢 Cost-Aware Purged Triple-Barrier Labeler
│       ├── models/
│       │   └── scalp_net.py           # 🟢 ScalpNet Deep Neural Network
│       ├── ports/
│       │   ├── gateway_port.py        # 🟢 Remote Gateway Protocol Port
│       │   └── mt5_port.py            # 🟢 MT5 Adapter Protocol Port
│       ├── risk/
│       │   └── risk_engine.py         # 🟢 Quantitative Risk & Lot Sizing Engine
│       ├── signals/
│       │   ├── policy.py              # 🟢 Signal Policy Engine (SMC God Mode)
│       │   └── rule_matrix.py         # 🟢 DB-Driven 30+ Rule Matrix Engine
│       ├── training/
│       │   └── walk_forward_trainer.py# 🟢 Purged Walk-Forward & Online Fine-Tuner
│       └── web/
│           └── server.py              # 🟢 FastAPI Control Center & Streaming Server
├── tests/                             # Pytest Verification Suite
│   ├── integration/                   # 🟢 End-to-End Pipeline & DB Integration Tests
│   └── unit/                          # 🟢 Unit Tests across Subsystems
├── Dockerfile                         # 🟢 Production Multi-stage Docker Container
├── main.py                            # 🟢 Root Python Entrypoint Redirect
├── NexusTradingForexBot.py            # ⚫ Legacy Script Entrypoint Redirect
├── pyproject.toml                     # 🟢 Project Build Metadata & Dependencies
└── requirements.txt                   # 🟢 Frozen Python Package Requirements
```

---

### 📑 Comprehensive Master File Inventory

| File Path | Layer | Responsibility | Key Classes / Symbols | Called By | Calls | Inputs | Outputs | Status | Risk |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `src/nexus_scalp/domain/enums.py` | Domain | Strongly-typed enumerations | `ExecutionMode`, `ActionType`, `OrderType`, `OrderStatus`, `SystemHealth` | Entire System | Standard `enum` | None | Enum values | 🟢 VERIFIED | Low |
| `src/nexus_scalp/domain/models.py` | Domain | Frozen Pydantic data contracts | `TickData`, `SymbolInfo`, `AccountInfo`, `TradeProposal`, `TradeOrder`, `Position` | Entire System | Pydantic | Tick dicts / tuples | Immutable objects | 🟢 VERIFIED | Low |
| `src/nexus_scalp/ports/mt5_port.py` | Ports | Abstract MT5 interface | `IMT5Port` | Adapters, OrderManager | Abstract | Method signatures | Abstract results | 🟢 VERIFIED | Low |
| `src/nexus_scalp/ports/gateway_port.py` | Ports | Abstract Remote Gateway interface | `IGatewayPort` | Adapters | Abstract | Method signatures | Abstract results | 🟢 VERIFIED | Low |
| `src/nexus_scalp/adapters/mt5/mt5_adapter.py` | Adapters | Direct MetaTrader 5 Win32 IPC | `DirectMT5Adapter` | `LiveEngine` | `MetaTrader5` PyPI | Trade orders, symbols | OrderResult, Ticks | 🟢 VERIFIED | High (IPC/Platform dependency) |
| `src/nexus_scalp/adapters/mt5/remote_gateway.py` | Adapters | Remote ZMQ Gateway Adapter | `RemoteMT5GatewayAdapter` | `LiveEngine` | `zmq` | JSON over TCP/ZMQ | OrderResult, Ticks | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/adapters/paper/paper_adapter.py` | Adapters | Simulated paper trading adapter | `PaperMT5Adapter` | `LiveEngine`, Tests | Internal state | TickData, Orders | Simulated OrderResult | 🟢 VERIFIED | Low |
| `src/nexus_scalp/adapters/database/audit_repository.py` | Adapters | SQLite WAL Audit & Autopsy Ledger | `AuditRepository` | `LiveEngine`, Server, OrderManager | `sqlite3` | Signals, Orders, Account, Autopsies | DB Records, Summaries | 🟢 VERIFIED | Low (Async Queue) |
| `src/nexus_scalp/models/scalp_net.py` | Models | PyTorch Deep Neural Network | `ScalpNet`, `CausalConv1d`, `SinusoidalPositionalEncoding` | `LiveEngine`, `WalkForwardTrainer` | `torch.nn` | 50D Tensor `(B, 50)` | 4 Logits `(B, 4)` | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/features/scalp_features.py` | Features | 50D Master Feature Engineering | `ScalpFeatureEngine`, `FeatureVector`, `FeaturePipelineFrozenError` | `LiveEngine`, Trainer, Tests | `numpy`, `polars` | Bar list, TickData | 50D FeatureVector | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/features/regime_classifier.py` | Features | Market Regime Classification | `MarketRegimeClassifier`, `MarketRegimeState`, `RegimeType` | `LiveEngine`, Policy | Internal math | Ticks, Spreads, Volatility | MarketRegimeState | 🟢 VERIFIED | Low |
| `src/nexus_scalp/features/order_manager.py` | Features | Legacy/Dead Order Manager | `OrderLifecycleManager` | None | None | None | None | ⚫ DEAD / UNUSED | Low (Orphaned file) |
| `src/nexus_scalp/labeling/triple_barrier.py` | Labeling | Cost-Aware Purged Triple Barrier | `TripleBarrierLabeler` | Trainer, `LiveEngine` online retrain | `polars`, `numpy` | OHLCV DataFrame | Labeled DataFrame (`0, 1, 2`) | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/training/walk_forward_trainer.py` | Training | Purged Walk-Forward & Online Fine-Tuner | `WalkForwardTrainer`, `ScalpDataset`, `FocalLossWithSmoothing` | CLI, `LiveEngine` async task | `torch`, `polars`, `sklearn` | Labeled DataFrame | Trained ScalpNet, ScalerBundle | 🟢 VERIFIED | High (Torch CUDA/CPU load) |
| `src/nexus_scalp/signals/policy.py` | Signals | Signal Policy Engine & SMC God Mode | `SignalPolicy` | `LiveEngine` | `RuleMatrixEngine` | Features, Regime, AlgoConfig | `TradeProposal` | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/signals/rule_matrix.py` | Signals | DB-driven 30+ Scalping Rule Matrix | `RuleMatrixEngine` | `SignalPolicy` | `AuditRepository` | Context, Rules config | Rule Evaluation / VETO | 🟢 VERIFIED | Low |
| `src/nexus_scalp/risk/risk_engine.py` | Risk | Quantitative Dynamic Lot Sizing | `RiskEngine` | `LiveEngine`, OrderManager | Internal math | Proposal, Account, Equity | Dynamic Volume (float) | 🟢 VERIFIED | High (Capital Protection) |
| `src/nexus_scalp/execution/order_manager.py` | Execution | Authoritative Order & Position Manager | `OrderLifecycleManager`, `PositionProtectionState`, `SmartPositionMetrics` | `LiveEngine` | `IMT5Port`, `RiskEngine` | Tick, Account, Proposal | Executed Orders, State updates | 🟢 VERIFIED | High (Order Dispatch) |
| `src/nexus_scalp/application/live_engine.py` | Application| Live System Async Orchestrator | `LiveEngine`, `ModelBundle`, `ScalerBundle` | `main.py`, CLI | All Subsystems | Ticks, Accounts, Config | Live System Loop & Telemetry | 🟢 VERIFIED | Critical (System Engine) |
| `src/nexus_scalp/configuration/config.py` | Config | Pydantic Settings & YAML Parser | `AppConfig`, `AlgoConfig`, `ExecutionConfig`, `RiskConfig` | Entire System | `pydantic_settings`, `yaml` | YAML file, ENV vars | Validated AppConfig | 🟢 VERIFIED | Low |
| `src/nexus_scalp/web/server.py` | Web | FastAPI REST, SSE & WebSocket Server | `ServerState`, FastAPI app | Main CLI, Docker | Subsystems | REST HTTP, WS | Telemetry, JSON API | 🟢 VERIFIED | Medium |
| `src/nexus_scalp/observability/telegram_notifier.py` | Observability| Asynchronous Telegram Bot Alerting | `TelegramNotifier` | `LiveEngine`, OrderManager | `httpx` | Markdown text | Telegram Message | 🟢 VERIFIED | Low |
| `src/nexus_scalp/observability/logging.py` | Observability| Structured JSON Logger | `logger`, `setup_logging` | Entire System | `structlog` | Log calls | Formatted Console/File Logs | 🟢 VERIFIED | Low |
| `src/nexus_scalp/cli/main.py` | CLI | Typer CLI Application Entrypoint | `app` CLI commands | Shell / User | LiveEngine, Trainer | CLI Commands | Shell Output | 🟢 VERIFIED | Low |
| `src/cli/train_model.py` | Script | Legacy CLI Training Script (18D contract) | `train_scalpnet_cli` | Manual Execution | Legacy imports | Raw Ticks | Model weights | ⚫ DEAD / STALE | High (18D dimensional mismatch) |

---

## 4. Actual Entrypoints & Startup Execution Path

### 🚀 Runtime Entrypoints Inventory

| Entrypoint File | Invocation Command | Purpose | Production Ready? | Status |
| :--- | :--- | :--- | :--- | :--- |
| `src/nexus_scalp/cli/main.py` | `python -m nexus_scalp.cli.main run --mode LIVE` | **Primary Production CLI Entrypoint** | Yes | 🟢 VERIFIED |
| `main.py` | `python main.py` | Root execution wrapper forwarding to `nexus_scalp.cli.main:app` | Yes | 🟢 VERIFIED |
| `NexusTradingForexBot.py` | `python NexusTradingForexBot.py` | Legacy convenience redirect forwarding to `main.py` | Legacy Redirect | 🟢 VERIFIED |
| `src/nexus_scalp/web/server.py` | `uvicorn nexus_scalp.web.server:app` | Direct FastAPI server runner | Debug / Development | 🟢 VERIFIED |
| `docker/entrypoint.sh` | Docker Container Startup | Containerized execution wrapper | Container | 🟢 VERIFIED |
| `src/cli/train_model.py` | `python -m cli.train_model` | Stale legacy 18D training script | No (Hardcoded 18D) | ⚫ DEAD / STALE |

---

### 📊 Startup & Dependency Construction Flow

```text
                  ┌───────────────────────────────┐
                  │   python -m nexus_scalp.cli   │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │    AppConfig.load_config()    │ (YAML + ENV)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  Adapter & Port Construction  │ (MT5 / Gateway / Paper)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  Subsystem Dependency Wiring  │
                  │  - ScalpFeatureEngine (50D)   │
                  │  - MarketRegimeClassifier     │
                  │  - SignalPolicy & RuleMatrix  │
                  │  - RiskEngine                 │
                  │  - OrderLifecycleManager      │
                  │  - AuditRepository (SQLite)   │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │    Model & Scaler Loading     │ (bundle_lock protection)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │  FastAPI & Web Server Launch  │ (Background task)
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────────┐
                  │     LiveEngine.start()        │
                  │  (Async Tick Processing Loop) │
                  └───────────────────────────────┘
```

---

## 5. AI / ML Forensic Audit & Model Lifecycle Map

### 5.1 AI/ML Component Inventory

* **Neural Architecture:** `ScalpNet` (`src/nexus_scalp/models/scalp_net.py`) 🟢 VERIFIED
* **Feature Pipeline:** `ScalpFeatureEngine` (`src/nexus_scalp/features/scalp_features.py`) 🟢 VERIFIED
* **Regime Classifier:** `MarketRegimeClassifier` (`src/nexus_scalp/features/regime_classifier.py`) 🟢 VERIFIED
* **Labeling Engine:** `TripleBarrierLabeler` (`src/nexus_scalp/labeling/triple_barrier.py`) 🟢 VERIFIED
* **Training Orchestrator:** `WalkForwardTrainer` (`src/nexus_scalp/training/walk_forward_trainer.py`) 🟢 VERIFIED
* **Inference Pipeline:** `LiveEngine._infer_probabilities()` (`src/nexus_scalp/application/live_engine.py`) 🟢 VERIFIED
* **Online Fine-Tuning Task:** `LiveEngine._trigger_async_online_fine_tune()` (`src/nexus_scalp/application/live_engine.py`) 🟢 VERIFIED

---

### 5.2 Exact ML Execution Code Sites

To ensure complete clarity for AI coding agents, here are the exact code locations for every ML operation:

* **Model Construction Site:** `src/nexus_scalp/training/walk_forward_trainer.py::WalkForwardTrainer._create_model()` (Constructs `ScalpNet(num_features=50, num_classes=4)`). 🟢 VERIFIED
* **Dataset Construction Site:** `src/nexus_scalp/training/walk_forward_trainer.py::ScalpDataset` & `ScalpWeightedDataset`. 🟢 VERIFIED
* **Dataloader Construction Site:** `src/nexus_scalp/training/walk_forward_trainer.py::DataLoader(dataset, batch_size=..., shuffle=...)`. 🟢 VERIFIED
* **Loss Function Calculation Site:** `src/nexus_scalp/training/walk_forward_trainer.py::FocalLossWithSmoothing.forward()` (or `nn.CrossEntropyLoss(weight=weights_tensor)`). 🟢 VERIFIED
* **`backward()` Execution Site:** `src/nexus_scalp/training/walk_forward_trainer.py` inside `_train_epoch()` (`loss.backward()`). 🟢 VERIFIED
* **`optimizer.step()` Execution Site:** `src/nexus_scalp/training/walk_forward_trainer.py` inside `_train_epoch()` (`optimizer.step()`). 🟢 VERIFIED
* **Validation & Evaluation Site:** `src/nexus_scalp/training/walk_forward_trainer.py::WalkForwardTrainer._evaluate_model()`. 🟢 VERIFIED
* **Checkpoint & Artifact Saving Site:** `src/nexus_scalp/training/walk_forward_trainer.py::WalkForwardTrainer.save_model_bundle()` & `LiveEngine._save_model_weights_atomic()`. 🟢 VERIFIED
* **Live Model Loading Site:** `src/nexus_scalp/application/live_engine.py::LiveEngine._load_model_bundle()`. 🟢 VERIFIED
* **Live Inference Execution Site:** `src/nexus_scalp/application/live_engine.py::LiveEngine._infer_probabilities()`. 🟢 VERIFIED
* **Online Fine-Tuning Trigger Site:** `src/nexus_scalp/application/live_engine.py::LiveEngine._trigger_async_online_fine_tune()`. 🟢 VERIFIED

---

### 5.3 ScalpNet Neural Architecture

`ScalpNet` (`src/nexus_scalp/models/scalp_net.py`) features a dual-path architecture designed to handle both single-tick 50D feature tensors and temporal bar sequences.

```text
                        ┌──────────────────────────────┐
                        │ Input Tensor: (Batch, 50)    │
                        └──────────────┬───────────────┘
                                       │
                         Is 2D Input? ──┴── Is 3D Input?
                          (Batch, 50)        (Batch, Seq, 50)
                               │                  │
                               ▼                  ▼
                    ┌────────────────────┐ ┌────────────────────┐
                    │ Snapshot 2D Path   │ │ Temporal 3D Path   │
                    │ - Linear(50, 128)  │ │ - Conv1d (Causal)  │
                    │ - LayerNorm, GELU  │ │ - Residual Blocks  │
                    │ - Dropout (0.10)   │ │ - Sinusoidal Pos  │
                    │ - Residual Block   │ │ - Self-Attention   │
                    └──────────┬─────────┘ └──────────┬─────────┘
                               │                      │
                               └──────────┬───────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ Head Linear Layer    │
                               │ Linear(128, 4)       │
                               └──────────┬───────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ Logits: (Batch, 4)   │
                               │ 0: NO_TRADE          │
                               │ 1: BUY_MARKET        │
                               │ 2: SELL_MARKET       │
                               │ 3: WAIT              │
                               └──────────────────────┘
```

* **Input Dimensions:** `num_features = 50` 🟢 VERIFIED
* **Output Dimensions:** `num_classes = 4` (`0=NO_TRADE`, `1=BUY_MARKET`, `2=SELL_MARKET`, `3=WAIT`) 🟢 VERIFIED
* **Activation:** GELU activation with LayerNorm and 10% Dropout. 🟢 VERIFIED

---

### 5.4 Label / Model Output Class Compatibility Audit

A critical forensic finding in this repository is the interface boundary between labeling and model inference:

* **Labeling Engine Output:** `TripleBarrierLabeler` produces a **3-Class taxonomy**:
  - `0`: `NO_TRADE`
  - `1`: `BUY_MARKET`
  - `2`: `SELL_MARKET`
* **Model Output Head:** `ScalpNet` constructs a **4-Class classification head**:
  - Index `0`: `NO_TRADE`
  - Index `1`: `BUY_MARKET`
  - Index `2`: `SELL_MARKET`
  - Index `3`: `WAIT`

#### 🔍 Forensic Resolution & Code Evidence:
In `src/nexus_scalp/training/walk_forward_trainer.py`, the `WalkForwardTrainer` bridges this exact boundary:
1. `NUM_CLASSES = 3` is defined as the active training label set (`NO_TRADE`, `BUY_MARKET`, `SELL_MARKET`).
2. In `_build_class_weights()`, the trainer dynamically retrieves `num_classes = int(self._model_num_classes)` (or checks `ScalpNet` output dimension = 4).
3. The loss weighting tensor is built with 4 elements (`[w_no_trade, w_buy, w_sell, 1.0]`), assigning unit weight (`1.0`) to index 3 (`WAIT`).
4. During live inference, index 3 (`WAIT`) represents synthetic advisory/neutral output.

This design guarantees that PyTorch loss computation and tensor dimensions remain perfectly aligned without throwing dimension mismatch exceptions. 🟢 VERIFIED

---

### 5.5 Feature Engineering (50D Master Contract)

The 50D feature vector (`src/nexus_scalp/features/scalp_features.py`) is computed on every tick from M1 completed bars and incoming tick price action.

| Index | Feature Name | Source | Lookback / Formula | Normalization / Bounds |
| :---: | :--- | :--- | :--- | :--- |
| `0` | `returns` | M1 Close | `(Close_t - Close_{t-1}) / Close_{t-1}` | Z-Score clipped `[-3.0, +3.0]` |
| `1` | `log_returns` | M1 Close | `ln(Close_t / Close_{t-1})` | Z-Score clipped `[-3.0, +3.0]` |
| `2` | `volatility_atr` | M1 Bar | 14-period ATR / Close | Scaled float |
| `3` | `rsi_14` | M1 Close | 14-period RSI | Scaled `(RSI - 50)/25` |
| `4` | `macd_line` | M1 Close | 12/26 EMA Difference | Scaled float |
| `5` | `macd_signal` | MACD Line | 9-period EMA of MACD | Scaled float |
| `6` | `macd_hist` | MACD Diff | `MACD_line - MACD_signal` | Scaled float |
| `7` | `bb_upper` | M1 Close | 20-period BB Upper | Price relative |
| `8` | `bb_middle` | M1 Close | 20-period SMA | Price relative |
| `9` | `bb_lower` | M1 Close | 20-period BB Lower | Price relative |
| `10` | `bb_width` | BB Bands | `(Upper - Lower) / Middle` | Scaled float |
| `11` | `bb_pband` | M1 Close | `(Close - Lower) / (Upper - Lower)` | Scaled `[0.0, 1.0]` |
| `12` | `adx_14` | M1 Bar | 14-period ADX | Scaled `ADX / 50.0` |
| `13` | `plus_di` | M1 Bar | 14-period +DI | Scaled float |
| `14` | `minus_di` | M1 Bar | 14-period -DI | Scaled float |
| `15` | `stoch_k` | M1 Bar | 14-period Stochastic %K | Scaled `(%K - 50)/25` |
| `16` | `stoch_d` | Stoch %K | 3-period SMA of %K | Scaled `(%D - 50)/25` |
| `17` | `obv` | M1 Volume | On-Balance Volume | Normalized slope |
| `18` | `vwap` | M1 Bar | Volume-Weighted Average Price | Price relative |
| `19` | `spread_norm` | Tick Spread | `Spread / ATR` | Scaled float |
| `20-25` | `wick_anatomy` | M1 Bar | Upper/Lower Wicks, Body Ratios | Ratio `[0.0, 1.0]` |
| `26-31` | `ofi_microstructure` | Ticks | Order Flow Imbalance, Tick Velocity | Scaled float |
| `32-39` | `multi_tf_momentum` | M15/M30/H1/H4 | Trend & Momentum Alignment | Scaled `[-1.0, +1.0]` |
| `40-45` | `sr_clustering` | Support/Res | Dynamic S/R Proximity & Density | Scaled float |
| `46` | `smc_bos` | Structure | Break of Structure Flag | Binary `-1.0, 0.0, +1.0` |
| `47` | `smc_equilibrium` | Impulse Leg | 50% Impulse Zone Distance | Distance Z-Score |
| `48` | `smc_liquidity_sweep` | Wicks | Liquidity Pool Piercing | Binary `-1.0, 0.0, +1.0` |
| `49` | `smc_ote_align` | Secondary Leg | 50%-61.8% OTE Zone Alignment | Scaled `[0.0, 1.0]` |

#### 🛡️ Feature Safety & Fallback Invariants:
* All 50 calculated features pass through `validate_and_fallback()`.
* If any feature contains `NaN`, `Inf`, or violates bounds, a `FeaturePipelineFrozenError` is logged and deterministic fallback values are applied gracefully. 🟢 VERIFIED

---

### 5.6 Cost-Aware Triple-Barrier Labeling Engine

The `TripleBarrierLabeler` (`src/nexus_scalp/labeling/triple_barrier.py`) generates training labels using dynamic ATR barriers adjusted for spread friction:

```text
                     ▲ Upper Barrier: Entry + (ATR * profit_mult)
                     │
         ─── Upper Friction Adjustment (Entry + Spread) ───
                     │
 Entry Price ────────┼─────────────────────────── Horizontal Horizon (e.g. 15 bars)
                     │
         ─── Lower Friction Adjustment (Entry - Spread) ───
                     │
                     ▼ Lower Barrier: Entry - (ATR * stop_mult)
```

1. **Upper Profit Barrier:** `Upper = Entry + (ATR * mult)` -> Outcome `1` (`BUY_MARKET`).
2. **Lower Stop Barrier:** `Lower = Entry - (ATR * mult)` -> Outcome `2` (`SELL_MARKET`).
3. **Vertical Horizon Barrier:** If neither barrier is touched within `max_holding_bars` (default 15 bars), outcome defaults to `0` (`NO_TRADE`).
4. **Stride Downsampling:** Uses `no_trade_stride_bars = 3` to jump samples after a `NO_TRADE` label, mitigating class imbalance before training. 🟢 VERIFIED

---

### 5.7 Purged Walk-Forward Training & Validation

The `WalkForwardTrainer` (`src/nexus_scalp/training/walk_forward_trainer.py`) implements temporal walk-forward validation with strict embargo and purging to prevent lookahead leakage:

```text
Fold 1: [ Train Block 1 ] [Purge] [ Validation Block 1 ] [Embargo]
Fold 2:      [ Train Block 2 ] [Purge] [ Validation Block 2 ] [Embargo]
Fold 3:           [ Train Block 3 ] [Purge] [ Validation Block 3 ] [Embargo]
```

* **Purging:** Drops samples whose triple-barrier evaluation horizons overlap into the validation set.
* **Embargo:** Drops a buffer period immediately following validation to eliminate temporal correlation leakage.
* **Fresh Model Initialization:** Every walk-forward fold constructs a fresh `ScalpNet` model to evaluate out-of-sample generalization. 🟢 VERIFIED

---

### 5.8 Online Fine-Tuning, Quality Gate, & Atomic Hot-Swapping

When the live rolling buffer reaches 300 completed feature records, `LiveEngine` triggers background online fine-tuning:

```text
LIVE INFERENCE LOOP
       │
       ├─► (Active Model Bundle in _bundle_lock remains serving live ticks)
       │
ASYNC RETRAIN TASK (to_thread)
       │
       ▼
 1. Label recent 300 records with TripleBarrierLabeler
       │
       ▼
 2. Call WalkForwardTrainer.fine_tune_online(model_clone, df_labeled, ...)
       │
       ▼
 3. Quality Gate Evaluation:
    - Dominance check: max class ratio <= 95%
    - Anti-collapse check: active class recall > 0.0%
       │
    ┌──┴─────────┐
    ▼            ▼
 [ PASS ]     [ FAIL ] ──► Discard candidate, keep live model.
    │
    ▼
 4. Save model weights atomically (_save_model_weights_atomic)
    │
    ▼
 5. Acquire _bundle_lock and HOT-SWAP _bundle = ModelBundle(...)
```

* **Atomic Hot-Swapping:** Hot-swapping occurs inside `with self._bundle_lock:`, ensuring zero tick drops or race conditions during live inference. 🟢 VERIFIED

---

## 6. Signal Pipeline & Rule Matrix Forensics

### 🚦 Decision Chain Routing

```text
Tick + Bars
     │
     ▼
ScalpFeatureEngine (50D Vector)
     │
     ▼
MarketRegimeClassifier (10 Regimes)
     │
     ▼
Regime Guardian Gate:
  Is Unsafe Regime? (HIGH_SPREAD_CHOP, MARKET_HALTED, NEWS_LOCK, etc.)
  ├── YES ──► ActionType.NO_TRADE (Reason: BLOCKED_BY_GUARDIAN)
  └── NO  ──► Proceed
     │
     ▼
ScalpNet Neural Inference (Probabilities)
     │
     ▼
RuleMatrixEngine (30+ DB-Configured Scalping Rules)
  ├── VETO Rule Triggered ──► ActionType.NO_TRADE
  └── Passed / FORCE      ──► Proceed
     │
     ▼
SMC God Mode Confluence Evaluation
  - BOS/CHOCH Confirmation
  - 50% Impulse Equilibrium
  - Liquidity Sweep Piercing
     │
     ▼
Generate TradeProposal (BUY / SELL / BUY_LIMIT / SELL_LIMIT)
```

#### 🔒 Pending Order Lock & Drift Invariants:
* Newly evaluated limit orders (`BUY_LIMIT` / `SELL_LIMIT`) are checked against active pending orders.
* **Pending Lock Duration:** 30 seconds.
* **Min Price Drift:** Requires price drift of at least `1.0 * ATR` before modifying pending limit price. Otherwise, returns `ActionType.NO_TRADE` with reason `PENDING_ORDER_LOCKED`. 🟢 VERIFIED

---

## 7. Risk Engine & Lot Sizing Forensics

### 📐 Authoritative Dynamic Lot-Sizing Equation

The `RiskEngine` (`src/nexus_scalp/risk/risk_engine.py`) evaluates lot sizes through a cascading series of risk bounds:

$$ \text{Raw Risk Amount (\$)} = \text{Equity} \times \text{Risk Percentage} $$

$$ \text{Raw Volume (Lots)} = \frac{\text{Raw Risk Amount}}{\text{Stop Loss Distance (Points)} \times \text{Tick Value}} $$

```text
                  Raw Volume (Lots)
                          │
                          ▼
             Floor to Broker volume_step (e.g. 0.01)
                          │
                          ▼
             Clamped by Account Tier Ceiling:
             - Equity < $100:     Max 0.02 Lots
             - Equity < $1,000:   Max 0.50 Lots
             - Equity < $10,000:  Max 2.00 Lots
             - Equity >= $10,000: Max 10.0 Lots (HARD_MAX_LOTS)
                          │
                          ▼
             Clamped by Free Margin Limit:
             Margin Required <= 20% of Free Margin
                          │
                          ▼
             Final Executable Volume (Lots)
```

#### 🛡️ Execution Boundary Absolute Clamp:
In `OrderLifecycleManager` (`src/nexus_scalp/execution/order_manager.py`), every order dispatch passes through `_clamp_volume()`:
* **Absolute Ceiling:** `HARD_MAX_LOTS = 10.0`.
* No order sent to MT5 can exceed 10.0 lots regardless of configuration. 🟢 VERIFIED

---

## 8. Execution & MT5 Integration Forensics

### 🔌 MT5 Adapters & Dispatch Router

The execution layer supports 3 interchangeable adapters implementing `IMT5Port`:

1. **DirectMT5Adapter (`src/nexus_scalp/adapters/mt5/mt5_adapter.py`):** Uses Win32 MetaTrader 5 C-extension API (`import MetaTrader5`). Direct IPC calls. 🟢 VERIFIED
2. **RemoteMT5GatewayAdapter (`src/nexus_scalp/adapters/mt5/remote_gateway.py`):** Communicates with a remote Windows gateway over ZeroMQ (ZMQ) sockets using JSON protocol. 🟢 VERIFIED
3. **PaperMT5Adapter (`src/nexus_scalp/adapters/paper/paper_adapter.py`):** In-memory simulated execution engine with realistic spread and latency simulation. 🟢 VERIFIED

#### ⚡ Dispatch Router & Circuit Breaker:
* **Unified Dispatch Router:** `OrderLifecycleManager.dispatch_order()` routes all proposals.
* **Circuit Breaker:** Tracks consecutive broker rejections. After 3 consecutive order rejections, the engine transitions to `SystemHealth.SAFE_MODE` and halts order dispatch. 🟢 VERIFIED

---

## 9. Position Protection, State Machine, & Adaptive Exit Engine

The `OrderLifecycleManager` (`src/nexus_scalp/execution/order_manager.py`) runs a hybrid position state machine on every single tick update.

### 🔄 11 Explicit Position States (`PositionState`)

```text
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│   PROPOSED   ├────►│  SUBMITTED   ├────►│       OPEN       │
└──────────────┘     └──────────────┘     └────────┬─────────┘
                                                   │
        ┌──────────────────────────────────────────┼──────────────────────────────────────────┐
        │                                          │                                          │
        ▼                                          ▼                                          ▼
┌──────────────┐                          ┌──────────────────┐                       ┌──────────────────┐
│  IN_DRAWDOWN │                          │ PROFIT_PROTECTION│                       │     TRAILING     │
└───────┬──────┘                          └────────┬─────────┘                       └────────┬─────────┘
        │                                          │                                          │
        ▼                                          ▼                                          ▼
┌──────────────┐                          ┌──────────────────┐                       ┌──────────────────┐
│RECOVERY_LOCKED                          │  PARTIAL_CLOSED  │                       │ REVERSAL_PENDING │
└───────┬──────┘                          └────────┬─────────┘                       └────────┬─────────┘
        │                                          │                                          │
        └──────────────────────────────────────────┼──────────────────────────────────────────┘
                                                   │
                                                   ▼
                                          ┌──────────────────┐
                                          │      CLOSED      │
                                          └──────────────────┘
```

### 🧠 Dynamic Hold Score & Emergency Bailout

For positions in drawdown, `OrderLifecycleManager` calculates a multi-dimensional risk `hold_score`:

$$\text{Hold Score} = 100 - (\text{Drawdown Penalty}) - (\text{Time Decay}) - (\text{Spread Expansion Penalty})$$

* **Evaluation Throttling:** Evaluated every 500ms per position.
* **Critical Breach Bailout:** If `hold_score < 30.0` while in drawdown, triggers immediate early risk exit (`S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT`). 🟢 VERIFIED

---

## 10. Hot-Path Latency & Event-Loop Forensics

### ⏱️ "50ms Hot Path" Forensic Audit

* **Claim in Documentation:** "50ms guaranteed HFT execution loop."
* **Forensic Finding:** The architecture is designed around an async event loop target of 50ms per tick pulse. However, **no guaranteed end-to-end 50ms latency SLA exists**.
* **Async Event-Loop Safety:**
  - Feature calculations and regime classifications are CPU-bound Python operations.
  - Model inference runs in PyTorch (`torch.inference_mode()`).
  - Heavy tasks (online fine-tuning) are offloaded off the main thread via `asyncio.to_thread()`.
  - Database persistence is offloaded via an async background queue in `AuditRepository`.
* **Status:** 🟡 PARTIALLY VERIFIED (Design target is non-blocking async, but actual latency depends on Win32 IPC / ZMQ network latency).

---

## 11. Web UI, FastAPI, SSE, WebSocket, & Debug Hub Forensics

### 🌐 Web Server Architecture (`src/nexus_scalp/web/server.py`)

* **Framework:** FastAPI with Uvicorn backend.
* **Frontend Control Panel:** Located in `Web/` (`index.html`, `app.js`, `styles.css`) rendering real-time candlestick charts, SMC visual overlays, active position tables, and rule matrix toggles.

```text
                               ┌───────────────────────────┐
                               │     Web Browser / UI      │
                               └─────────────┬─────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       │                                           │
                       ▼                                           ▼
            ┌─────────────────────┐                     ┌─────────────────────┐
            │ REST API Endpoints  │                     │ SSE / WebSocket     │
            │ GET /api/status     │                     │ /api/ticks/stream   │
            │ POST /api/rules     │                     │ /web (WebSocket)    │
            └──────────┬──────────┘                     └──────────┬──────────┘
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                               ┌───────────────────────────┐
                               │  FastAPI server.py        │
                               │  serialize_enums()        │
                               └───────────────────────────┘
```

#### 🛡️ SSE Stream Enum Serialization Invariant:
To prevent SSE stream JSON serialization crashes when returning domain objects, `server.py` recursively serializes all Enum instances (such as `ActionType`, `OrderStatus`, `ExecutionMode`) using `serialize_enums()` before broadcasting telemetry payloads. 🟢 VERIFIED

---

## 12. Observability, Database Persistence, & Ledger Autopsy

### 💾 SQLite WAL Audit Repository (`src/nexus_scalp/adapters/database/audit_repository.py`)

* **Database Mode:** SQLite in Write-Ahead Logging (WAL) mode (`PRAGMA journal_mode=WAL;`).
* **Asynchronous Queue:** DB writes are placed on an internal thread-safe queue (`_db_queue`) and written sequentially by a background worker thread (`_worker_thread`), preventing I/O lag on the live event loop.

#### 📊 Core Database Tables:
1. `audit_signals`: Records all generated trade proposals, model probabilities, regime metrics, and rule evaluation payloads. Features multi-key deduplication on `(symbol, timeframe, candle_time, model_action, entry_zone)` to prevent DB bloat. 🟢 VERIFIED
2. `audit_orders`: Tracks order lifecycle state transitions (`SUBMITTED`, `OPEN`, `MODIFY_SL_TP`, `CLOSED`). 🟢 VERIFIED
3. `audit_account_snapshots`: High-frequency account balance/equity snapshots (throttled to write only on balance change or >= 60s interval). 🟢 VERIFIED
4. `trading_rules_config`: Stores dynamic enablement states and JSON parameters for 30+ scalping rules. 🟢 VERIFIED
5. `financial_ledger`: Autopsy table recording completed trade performance metrics (MAE, MFE, slippage, SL shift tracking, gross PnL) exactly once upon trade closure. 🟢 VERIFIED

---

## 13. Configuration Architecture & Dynamic Propagation

### ⚙️ Pydantic Configuration Flow (`src/nexus_scalp/configuration/config.py`)

```text
configs/base.yaml  +  ENV Variables
           │
           ▼
AppConfig.load_config()
           │
           ▼
AppConfig (Pydantic BaseSettings)
 ├── execution: ExecutionConfig
 ├── risk: RiskConfig
 ├── model: ModelConfig
 ├── mt5: MT5Config
 ├── telegram: TelegramConfig
 └── algo: AlgoConfig (Dynamic Quantitative Parameters)
           │
           ▼
Tick Sync in LiveEngine._process_tick_pipeline():
  - Synchronizes AlgoConfig to SignalPolicy, OrderManager, and RiskEngine on every tick pulse.
```

* **Hot-Swapping:** `AlgoConfig` parameters (such as `min_risk_reward_ratio`, `high_confidence_threshold`, `atr_sl_buffer_multiplier`) can be updated dynamically via REST API `POST /api/config/algo` without restarting the engine. 🟢 VERIFIED

---

## 14. Testing & CI/CD Pipeline Audit

### 🧪 Test Suite Structure

* **Unit Tests (`tests/unit/`):**
  - `test_domain_models.py`: Validates frozen Pydantic domain immutability.
  - `test_scalp_features.py`: Validates 50D feature calculation, bounds, and fallback behavior.
  - `test_walk_forward_trainer.py`: Validates walk-forward splitting, loss functions, and online fine-tuning quality gates.
  - `test_policy.py`: Validates signal policy routing and SMC God Mode.
  - `test_risk_engine.py`: Validates dynamic lot sizing, account tier caps, and margin checks.
  - `test_order_manager.py`: Validates position state machine and `HARD_MAX_LOTS` clamping.
  - `test_rule_matrix.py`: Validates DB hot-reloading and rule evaluation.
* **Integration Tests (`tests/integration/`):**
  - `test_signal_pipeline_health.py`: End-to-end signal pipeline health verification.
  - `test_database_execution_audit.py`: Validates SQLite WAL persistence and ledger autopsies.
  - `test_playwright_e2e.py`: Playwright end-to-end web visualizer verification. 🟢 VERIFIED

### ⚙️ CI/CD Workflows (`.github/workflows/`)

* `ci.yml`: Runs Python 3.11 environment, Ruff linter/formatter check, Mypy type validation, and Pytest with code coverage reports. 🟢 VERIFIED
* `docker.yml`: Builds multi-stage Docker container and publishes to GitHub Container Registry (GHCR). 🟢 VERIFIED
* `security.yml`: Executes GitHub CodeQL SAST and Trivy container vulnerability scanning. 🟢 VERIFIED
* `release.yml`: Automates releases on GitHub tag creation (`v*`). 🟢 VERIFIED

---

## 15. Documentation vs. Reality Audit Matrix

This matrix explicitly audits claims made in prior documentation against actual codebase evidence:

| Claimed Feature / Parameter | Documented Claim | Actual Repository Evidence | Forensic Status | Forensic Finding |
| :--- | :--- | :--- | :--- | :--- |
| **Feature Tensor Dimension** | 50D Feature Vector | `NUM_FEATURES = 50` in `scalp_features.py` and `walk_forward_trainer.py` | 🟢 VERIFIED | Exactly 50 float features computed. |
| **Max Lot Size Limit** | Hard max lots = 2.0 | `HARD_MAX_LOTS = 10.0` in `order_manager.py` | 🔴 CONTRADICTED | Code was upgraded to allow up to 10.0 lots on equity >= $10,000. |
| **Model Output Classes** | 4 Classes (`0, 1, 2, 3`) | `ScalpNet` output head `num_classes = 4` (`NO_TRADE`, `BUY`, `SELL`, `WAIT`) | 🟢 VERIFIED | ScalpNet head is 4-class; labeler outputs 3 classes mapped dynamically. |
| **Hot Path Latency** | Guaranteed 50ms execution | No hard real-time latency guarantee in code | 🟡 PARTIALLY VERIFIED | 50ms is an async event-loop target, not a real-time hardware SLA. |
| **Position States** | 11 Position lifecycles | `PositionState` enum defines exactly 11 states in `order_manager.py` | 🟢 VERIFIED | 11 explicit lifecycles managed in hybrid state machine. |
| **Pending Order Protection** | 30s lock & 1.0x ATR drift | `policy.py` checks 30s lock and 1.0 * ATR drift | 🟢 VERIFIED | Prevents high-frequency pending order churn on MT5 terminal. |
| **Legacy Order Manager** | Active order manager | `src/nexus_scalp/features/order_manager.py` is orphaned | ⚫ DEAD / UNUSED | Active production order manager is in `src/nexus_scalp/execution/order_manager.py`. |
| **Legacy CLI Training Script** | Active training script | `src/cli/train_model.py` hardcodes 18D features | ⚫ DEAD / STALE | Hardcoded 18D range crashes against 50D ScalpNet contract. Use `WalkForwardTrainer`. |

---

## 16. Dead, Legacy, & Discrepant Code Inventory

During the forensic audit, the following legacy or unreferenced files were identified:

1. **`src/nexus_scalp/features/order_manager.py` (234 lines) ⚫ DEAD / UNUSED:**
   - **Finding:** Unreferenced legacy file. Active production order management is strictly handled by `src/nexus_scalp/execution/order_manager.py` (1792 lines).
   - **Action:** Retained read-only; documented as dead code.

2. **`src/cli/train_model.py` ⚫ DEAD / STALE:**
   - **Finding:** Hardcodes an 18-dimensional feature loop (`range(18)`), which violates the 50D feature contract enforced by `WalkForwardTrainer` (`NUM_FEATURES = 50`) and causes runtime crashes if invoked.
   - **Action:** Retained read-only; documented as stale legacy script.

3. **`NexusTradingForexBot.py` 🟢 VERIFIED:**
   - **Finding:** Minimal 11-line convenience wrapper forwarding execution to `main.py`.

---

## 17. Prioritized Engineering Recommendations (P0-P3)

The following backlog details prioritized architectural and operational recommendations for future development tasks. **No code changes have been made per the read-only constraint.**

### 🔴 P0 — Critical (Capital Safety & Integrity)
1. **Deprecate or Remove `src/cli/train_model.py`:**
   - *Problem:* Hardcodes 18D features, conflicting with the 50D `ScalpNet` contract.
   - *Impact:* Invoking this script corrupts model weights or crashes during training.
   - *Fix:* Remove file or rewrite it to wrap `WalkForwardTrainer`.

### 🟠 P1 — High (Reliability & Architecture Cleanliness)
2. **Remove Dead Legacy File `src/nexus_scalp/features/order_manager.py`:**
   - *Problem:* Duplicate filename causes developer confusion with `src/nexus_scalp/execution/order_manager.py`.
   - *Impact:* Maintainers might accidentally import from the wrong package layer.
   - *Fix:* Delete `src/nexus_scalp/features/order_manager.py`.

3. **Add Explicit Warmup Safeguard for HTF Features:**
   - *Problem:* Multi-timeframe features (H1/H4) require sufficient historical bars on startup.
   - *Impact:* First few ticks after cold start may receive neutral fallback values until bars complete.
   - *Fix:* Implement history bootstrapping in `LiveEngine` startup.

### 🟡 P2 — Medium (Performance & Maintainability)
4. **Optimize Polars DataFrame Allocation in Rolling Buffer:**
   - *Problem:* `_trigger_async_online_fine_tune()` constructs Polars DataFrame from list of dicts on every retrain trigger.
   - *Impact:* Minor memory allocation churn during online fine-tuning.
   - *Fix:* Maintain a pre-allocated rolling Polars DataFrame buffer.

### 🔵 P3 — Nice-To-Have
5. **Expand Playwright Web Visualizer E2E Test Coverage:**
   - *Problem:* Web visualizer test verifies basic loading and API response.
   - *Fix:* Add automated Playwright interaction tests for rule matrix toggling and configuration updates.
