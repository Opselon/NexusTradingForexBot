# 🧠 Nexus Scalp Engine (NSE) - Forensic Master Skill & Context Anchor

> **TARGET AUDIENCE:** AI Coding Agents (Cursor, Copilot, ChatGPT, Claude, Jules).
> **PURPOSE:** Authoritative, repository-grounded Master Skill & Architecture Map for the Nexus Scalp Engine repository.
> **SOURCE OF TRUTH:** Actual Executable Codebase (verified via forensic audit).
> **BUG LEDGER:** For historical bug forensics, root causes, evidence, and regression guards, see `agents/bugs.md`.
> **ABSOLUTE DIRECTIVE:** READ-ONLY for codebase files. Do NOT modify any file in this repository except `agents/skill.md` and `agents/bugs.md`.

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
11. [Web UI, REST API, SSE, WebSocket, & Debug Hub Forensics](#11-web-ui-rest-api-sse-websocket--debug-hub-forensics)
12. [Observability, Database Persistence, & Ledger Autopsy](#12-observability-database-persistence--ledger-autopsy)
13. [Configuration Architecture & Dynamic Propagation](#13-configuration-architecture--dynamic-propagation)
14. [Unified Accounting & Performance Intelligence Core (PHASE 08)](#14-unified-accounting--performance-intelligence-core-phase-08)
15b. [Trade Intelligence Brain (PHASE 09)](#15b-trade-intelligence-brain-phase-09)
15c. [Strategy Research, Backtest & Validation Engine (PHASE 09B)](#15c-strategy-research-backtest--validation-engine-phase-09b)
15d. [Controlled Model Training & Challenger Engine (PHASE 10)](#15d-controlled-model-training--challenger-engine-phase-10)
15e. [Challenger Shadow Trading & Champion Evaluation (PHASE 11)](#15e-challenger-shadow-trading--champion-evaluation-phase-11)
15f. [News Intelligence Engine (PHASE 12)](#15f-news-intelligence-engine-phase-12)
15. [Known Engineering Pitfalls & Invariants](#15-known-engineering-pitfalls--invariants)
16. [Testing & CI/CD Pipeline Audit](#16-testing--cicd-pipeline-audit)
17. [Documentation vs. Reality Audit Matrix](#17-documentation-vs-reality-audit-matrix)
18. [Code Inventory & Active Wrappers](#18-code-inventory--active-wrappers)
19. [Prioritized Engineering Recommendations (P0-P3)](#19-prioritized-engineering-recommendations-p0-p3)

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
├── agents/                            # Agent Documentation & Bug Ledger
│   ├── bugs.md                        # 🟢 Bug Ledger & Forensic History
│   └── skill.md                       # 🟢 Authoritative Master Skill & Architecture Map
├── configs/                           # Application Configurations
│   ├── base.yaml                      # 🟢 Default base settings
│   └── live.yaml.example              # 🟢 Example live runtime configuration
├── docker/                            # Containerization Scripts
│   ├── entrypoint.sh                  # 🟢 Container entrypoint script
│   └── healthcheck.sh                 # 🟢 Container health check script
├── src/
│   ├── cli/
│   │   └── train_model.py             # 🟢 CLI Training Script (50D contract aligned)
│   └── nexus_scalp/
│       ├── accounting/                # 🟢 PHASE 08: UNIFIED ACCOUNTING & PERFORMANCE INTELLIGENCE CORE
│       │   ├── __init__.py            # 🟢 Package exports (models, core, worker)
│       │   ├── models.py              # 🟢 Canonical value objects (TradeRecord, PeriodReport, DrawdownReport, ...)
│       │   ├── periods.py             # 🟢 Deterministic UTC DAY/WEEK/MONTH/YEAR boundaries
│       │   ├── normalize.py           # 🟢 Ledger row -> canonical TradeRecord normalization
│       │   ├── aggregation.py         # 🟢 Pure period & drawdown aggregation math
│       │   ├── core.py                # 🟢 AccountingCore: single read facade + derived cache
│       │   └── worker.py              # 🟢 AccountingWorker: background derived refresh (idempotent, isolated)
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
│       ├── experience/                # 🟢 PHASE 08: EXPERIENCE-DRIVEN STRATEGY INTELLIGENCE
│       │   ├── __init__.py            # 🟢 Subsystem exports
│       │   ├── models.py              # 🟢 Immutable memory contracts (records, outcomes, scores, provenance)
│       │   ├── ledger.py              # 🟢 Append-only experience ledger + dedup + corrections
│       │   ├── evaluator.py           # 🟢 Statistical scoring, confidence, lifecycle, self-heal rebuild
│       │   ├── intelligence.py        # 🟢 Pre-trade decision gate + post-trade outcome recorder
│       │   ├── retriever.py           # 🟢 Bounded context fingerprinting & top-K retrieval
│       │   ├── quality.py             # 🟢 Deterministic outcome decomposition + behavioral flags
│       │   └── provenance.py          # 🟢 Model registry (metadata only, never weights)
│       ├── intelligence/              # 🟢 PHASE 09: TRADE INTELLIGENCE BRAIN
│       │   ├── __init__.py            # 🟢 Subsystem exports
│       │   ├── models.py              # 🟢 Position-lifecycle / autopsy / behavior / evolution contracts
│       │   ├── lifecycle.py           # 🟢 PositionLifecycleTracker: immutable position timeline
│       │   ├── autopsy.py             # 🟢 TradeAutopsyEngine: WHY did this trade win/lose?
│       │   ├── behavior.py            # 🟢 BehaviorDetectionEngine: measurable patterns
│       │   ├── evolution.py           # 🟢 StrategyEvolutionEngine: controlled candidate discovery
│       │   ├── gate.py                # 🟢 PreTradeIntelligenceGate: WARN/suitability decision
│       │   ├── worker.py              # 🟢 IntelligenceWorker: isolated background refresh
│       │   └── store.py               # 🟢 Bounded read facade over intelligence tables
│       ├── features/
│       │   ├── regime_classifier.py   # 🟢 Market Regime Classifier (10 Regimes)
│       │   ├── scalp_features.py      # 🟢 50D Master Feature Vector Pipeline
│       │   └── schema.py              # 🟢 Feature Schema Registry (50D active; 60D/350D forward-declared)
│       ├── labeling/
│       │   └── triple_barrier.py      # 🟢 Cost-Aware Purged Triple-Barrier Labeler
│       ├── models/
│       │   └── scalp_net.py           # 🟢 ScalpNet Deep Neural Network
│       ├── model_lifecycle/           # 🟢 PHASE 10: CONTROLLED MODEL TRAINING & CHALLENGER ENGINE
│       │   ├── __init__.py            # 🟢 Subsystem exports
│       │   ├── models.py              # 🟢 Immutable contracts (TrainingRun, TrainingDataset, ModelStatus)
│       │   ├── dataset.py             # 🟢 Deterministic causal training dataset builder
│       │   ├── integrity.py           # 🟢 Artifact hash/dimension/class-count/scaler compatibility
│       │   ├── champion.py            # 🟢 Champion loading + verification (production only)
│       │   ├── trainer.py             # 🟢 ChallengerTrainer: offline candidate training (staging paths)
│       │   ├── gates.py               # 🟢 12 validation gates + collapse protection
│       │   ├── comparison.py          # 🟢 Champion vs Challenger multi-dim comparison
│       │   ├── registry.py            # 🟢 Additive lifecycle status over experience_model_registry
│       │   ├── store.py               # 🟢 Immutable training_runs + model_comparisons persistence
│       │   ├── orchestrator.py        # 🟢 End-to-end controlled training pipeline
│       │   └── worker.py              # 🟢 Isolated/bounded/cancellable background training worker
│       ├── ports/
│       │   ├── gateway_port.py        # 🟢 Remote Gateway Protocol Port
│       │   └── mt5_port.py            # 🟢 MT5 Adapter Protocol Port
│       ├── research/                   # 🟢 PHASE 09B: STRATEGY RESEARCH, BACKTEST & VALIDATION ENGINE
│       │   ├── __init__.py             # 🟢 Subsystem exports
│       │   ├── models.py               # 🟢 Immutable research domain contracts (samples, results, score, registry)
│       │   ├── dataset.py              # 🟢 Deterministic causal dataset builder from experience ledger
│       │   ├── splitting.py            # 🟢 Temporal splits + walk-forward with purge/embargo
│       │   ├── leakage.py              # 🟢 Future/leakage guards (fit-on-train, embargo/purge)
│       │   ├── metrics.py              # 🟢 Pure performance/risk statistics
│       │   ├── backtest.py             # 🟢 Deterministic friction-aware backtest engine
│       │   ├── walkforward.py          # 🟢 Walk-forward validation engine
│       │   ├── oos.py                  # 🟢 Hard out-of-sample gate
│       │   ├── robustness.py           # 🟢 Spread/slippage/latency stress engine
│       │   ├── scoring.py              # 🟢 Explainable multi-dimension strategy score
│       │   ├── candidates.py           # 🟢 Strategy candidate contract + content-derived versioning
│       │   ├── discovery.py            # 🟢 Bounded context-family candidate discovery
│       │   ├── lifecycle.py            # 🟢 Research lifecycle state machine
│       │   ├── registry.py             # 🟢 Enduring strategy registry persistence
│       │   ├── pipeline.py             # 🟢 End-to-end validation orchestrator
│       │   ├── worker.py               # 🟢 Isolated/restart-safe background research worker
│       │   └── store.py                # 🟢 Bounded read facade over research tables
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
├── NexusTradingForexBot.py            # 🟢 Legacy Script Entrypoint Redirect
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
| `src/cli/train_model.py` | Script | CLI Training Script (50D contract) | `train` | Manual Execution | Imports | Raw Ticks | Model weights | 🟢 VERIFIED | Low |

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
| `src/cli/train_model.py` | `python -m cli.train_model` | CLI Training script (50D contract aligned) | Yes (50D) | 🟢 VERIFIED |

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

A critical forensic boundary in this repository is the interface between labeling and model inference:

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

$$\text{Hold Score} = 100 - (\text{Drawdown Penalty}) - (\text{Time Decay}) - (\text{Spread Expansion Penalty}) + (\text{trend bonus, suppressed underwater})$$

* **Evaluation Throttling:** Evaluated every 500ms per position.
* **CONVEX DRAWDOWN PENALTY:** `80 * ratio^1.5` (capped at 80). A 50%-of-risk
  drawdown removes ~28 points, a 90% drawdown ~68, so the engine de-risks
  gracefully well before the emergency horizon. (Linear `ratio*40` previously
  pegged deep drawdowns at 97-100 - see BUG-013.)
* **Bonus Suppression:** `TREND_ALIGNMENT_BONUS (+10)` is suppressed whenever the
  drawdown ratio is >= 0.30, so a favourable higher-timeframe trend can never
  mask a real loss.
* **Profit-Shield Floor:** `max(85, score)` applies only when `pos.profit >= 0.0`
  AND the position is not underwater; it is disabled for a genuinely losing
  position.
* **Critical Breach Bailout:** If `hold_score < 30.0` while in drawdown, triggers immediate early risk exit (`S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT`). 🟢 VERIFIED
* **Split-Order Sync:** an emergency close of one leg closes every sibling leg
  sharing the same originating order_id (`_close_sibling_legs`), and emergency
  states (`LOSS_HARD_EXIT` / `PROFIT_GIVEBACK_CRITICAL`) are honored even on the
  FIRST observation of a ticket (see BUG-018).

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

## 11. Web UI, REST API, SSE, WebSocket, & Debug Hub Forensics

### 🌐 Web Server Architecture & Endpoints (`src/nexus_scalp/web/server.py`)

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

### 📡 Full REST API Route Specifications

| Route | Method | Purpose | Request Body | Response Payload | Status |
| :--- | :---: | :--- | :--- | :--- | :--- |
| `/api/status` | `GET` | Live telemetry & system status | None | System state, balance, regime, visual overlays | 🟢 VERIFIED |
| `/api/rules` | `GET` | Get rule matrix configuration | None | JSON map of 30+ scalping rules & enabled states | 🟢 VERIFIED |
| `/api/rules/toggle` | `POST` | Toggle specific rule state | `ToggleRuleRequest` | Updated rule matrix state | 🟢 VERIFIED |
| `/api/account/summary` | `GET` | Canonical account summary via `AccountingCore.live_state` + ledger totals (no synthetic zeros; null when unavailable) | None | balance/equity/margin/win rate/profit factor/total trades | 🟢 VERIFIED |
| `/api/account/trades` | `GET` | Paginated closed trade autopsies | Query params | List of completed trade autopsies | 🟢 VERIFIED |
| `/api/account/growth` | `GET` | Historical account equity curve | Query params | Time-series equity/balance array | 🟢 VERIFIED |
| `/api/account/performance` | `GET` | Canonical live + period performance overview | None | Live state, 4 periods, drawdown, worker telemetry, totals | 🟢 VERIFIED |
| `/api/account/performance/{kind}` | `GET` | Single period report (DAY/WEEK/MONTH/YEAR) | Path param | Canonical `PeriodReport` JSON | 🟢 VERIFIED |
| `/api/account/performance/{kind}/series` | `GET` | Bounded consecutive-period series | Query `count` | Ordered period list for charts | 🟢 VERIFIED |
| `/api/account/equity-curve` | `GET` | Balance/equity/drawdown + cumulative PnL series | Query `lookback_days` | Time-series arrays | 🟢 VERIFIED |
| `/api/account/drawdown` | `GET` | Canonical drawdown state | Query `lookback_days` | DrawdownReport JSON | 🟢 VERIFIED |
| `/api/account/trades/{trade_id}` | `GET` | Forensic trade reconstruction | Path param | Ledger+orders+experience trace | 🟢 VERIFIED |
| `/api/account/strategies` | `GET` | Per-strategy contribution joined to Intelligence | Query `limit` | Strategy contribution list | 🟢 VERIFIED |
| `/api/engine/toggle` | `POST` | Pause or Resume LiveEngine loop | `ToggleRequest` | Engine execution state | 🟢 VERIFIED |
| `/api/config` | `GET` | Retrieve active system config | None | `AppConfig` serialized JSON | 🟢 VERIFIED |
| `/api/config` | `POST` | Update active system config | `AlgoConfigRequest` | Success status & updated config | 🟢 VERIFIED |
| `/api/algo/config` | `GET`/`PUT` | Get/Set dynamic quantitative parameters | `AlgoConfigRequest` | Hot-swapped `AlgoConfig` JSON | 🟢 VERIFIED |
| `/api/chart/history` | `GET` | Bootstrap 150+ bar visualizer chart | Query params | OHLC bar array + candidate zones | 🟢 VERIFIED |
| `/api/positions/modify` | `POST` | Manual SL/TP position update | `ModifyPositionRequest` | Execution result | 🟢 VERIFIED |
| `/api/positions/close` | `POST` | Manual market close position | `ClosePositionRequest` | Execution result | 🟢 VERIFIED |
| `/api/simulation/tick` | `POST` | Direct tick injection (Paper mode) | `SimulationTickRequest` | Processed tick telemetry | 🟢 VERIFIED |
| `/api/replay/toggle` | `POST` | Start/Pause historical tick replay | `ToggleReplayRequest` | Replay status | 🟢 VERIFIED |
| `/api/debug/features` | `GET` | Inspect live 50D feature vector | None | 50 feature names & calculated values | 🟢 VERIFIED |
| `/api/debug/model-test` | `POST` | Test arbitrary feature tensor against ScalpNet | `ModelTestRequest` | Probabilities & argmax decision | 🟢 VERIFIED |
| `/api/debug/health` | `GET` | Detailed subsystem diagnostics | None | Feature, Risk, Model, MT5 health | 🟢 VERIFIED |
| `/api/debug/ipc-telemetry` | `GET` | MT5 IPC socket/latency stats | None | Latency metrics & reconnect count | 🟢 VERIFIED |
| `/api/observability/stats` | `GET` | Overall engine observability stats | None | Memory, uptime, tick count | 🟢 VERIFIED |

### 🔄 WebSocket & SSE Streaming Specifications

1. **SSE Stream Endpoint (`GET /api/ticks/stream`):**
   - Broadcasts real-time Server-Sent Events (SSE) `data: JSON` containing tick updates, SMC overlay boxes, and live PnL.
2. **WebSocket Channels (`/web` and `/ws`):**
   - Bidirectional real-time WebSocket connection for low-latency visualizer charts and interactive UI triggers.

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

## 14. Unified Accounting & Performance Intelligence Core (PHASE 08)

### 📐 Overview

The **`nexus_scalp/accounting/`** package is the SINGLE canonical accounting
authority. The REST API, the dashboard, the background worker, and Experience
Intelligence all read performance truth through `AccountingCore`; none of them
computes PnL, drawdown, or period boundaries independently.

```
MT5 / Paper Adapter  -> live balance/equity/margin/positions (real data)
audit_account_snapshots -> historical equity & balance series
audit_ledger         -> closed-trade financial records (authoritative)
audit_orders         -> order lifecycle events (forensic trace)
audit_experiences(+outcomes) -> decision/strategy/model identity chain
strategy_intelligence_registry -> lifecycle & confidence (READ, never recomputed)

        ALL READ EXCLUSIVELY THROUGH AccountingCore (src/nexus_scalp/accounting/)
        ──► canonical TradeRecord / PeriodReport / DrawdownReport / curves
        ──► consumed by REST API, dashboard, worker, Experience layer
```

### 🔒 Accounting Invariants (hard rules)

1. **NO SYNTHETIC NUMBERS.** Every metric that cannot be derived from stored
   evidence is `None`, never a fabricated `0.0` — an unavailable metric renders
   as "n/a" or "NO ACCOUNT HISTORY AVAILABLE", never as a fake zero row.
   Enforced on ALL dashboard/API endpoints incl. the legacy `/api/status` and
   `/api/account/summary` (BUG-020); the frontend renders `n/a` for null fields.
2. **WIN/LOSS IS DECIDED BY REALIZED MONEY ONLY.** A stop that had been moved
   to breakeven is STILL a stop-out; it is classified `BREAKEVEN_STOP` and its
   `outcome` reflects net PnL (which may be LOSS after costs). It is NEVER
   reclassified as a WIN because the SL happened to sit at entry.
3. **ONE PERIOD POLICY.** All DAY/WEEK/MONTH/YEAR boundaries are half-open UTC
   intervals resolved in `accounting/periods.py`. A trade at 00:00:00 UTC
   belongs to the NEW period, never to both. No consumer uses local time.
4. **ONE DRAWDOWN METHODOLOGY.** `compute_drawdown()` in `aggregation.py` is
   the only drawdown implementation (peak-to-trough on the equity snapshot
   series). Dashboard, API and risk never disagree.
5. **NET PNL COMPUTED EXACTLY ONCE.** `normalize_trade_row()` computes
   `net = gross - commission - swap` and stores it; consumers read it.
6. **IDEMPOTENT CLOSURE.** `audit_ledger` upserts on `ticket`; the outcome
   table's UNIQUE `idempotency_key` discards duplicate close callbacks. No
   duplicate financial records can be created.
7. **DERIVED AGGREGATES ARE REBUILDABLE.** Raw snapshots + ledger + outcomes
   can always rebuild every period report; nothing patches totals.
8. **MODEL REBUILD NEVER ERASES MEMORY.** Experiences carry their own
   `feature_schema_id`/`feature_dimension`/`model_id`/`model_version` at
   decision time (via `features/schema.py` registry + `experience/provenance.py`).
   The live contract remains 50D (`scalp_v1`); 60D (`scalp_v2`) and 350D
   (`scalp_v3`) are forward-declared for future widening.

### 🗄️ Canonical Tables (all in `audit.db`, SQLite WAL)

- `audit_ledger` — ONE row per trade lifecycle (opened placeholder + closed
  upsert). Entry context, SL geometry, MAE/MFE in points and USD, net PnL.
- `audit_account_snapshots` — throttled (balance change or 60s) equity/balance
  series with running `peak_equity`.
- `audit_experiences` — IMMUTABLE decision rows keyed by `idempotency_key`
  (`exp_<request_id>`). Column `execution_id` is EMPTY by design.
- `audit_experience_outcomes` — append-only outcome events; carries the broker
  ticket in `execution_id`. **The trade→strategy identity bridge.**
- `strategy_intelligence_registry` — derived strategy scores (lifecycle,
  confidence, expectancy_r), rebuildable via `ExperienceEngine.self_heal()`.
- `experience_model_registry` — model metadata only; never model weights.

### 🔗 Trade → Strategy Identity Chain (CRITICAL)

```
audit_ledger.ticket == audit_experience_outcomes.execution_id
audit_experience_outcomes.idempotency_key == audit_experiences.idempotency_key
```

`AccountingCore._attach_identity()` AND `_attach_experience_detail()` (forensic
trace quality decomposition) join through the OUTCOME table (never
`audit_experiences.execution_id`, which is empty by design — see BUG-008 and
BUG-021 in `agents/bugs.md`).

### ⚙️ AccountingWorker (`accounting/worker.py`)

- Background derived-refresh loop, kicked via `asyncio.to_thread` from
  `LiveEngine.run_loop` — NEVER on the tick hot path.
- Idempotent (repeating a cycle with no new data is a no-op), restartable
  (`start`/`stop` state machine), failure-isolated (a cycle failure is logged
  with `[ACCOUNTING_WORKER] event=FAILURE` and the worker continues).
- Owns NO tables: it writes nothing to the audit tables; its only side effect
  is the in-process derived-report cache of `AccountingCore`.
- Logs: `[ACCOUNTING_WORKER] event=START/UPDATE/FAILURE/RECOVERY` with cycle
  counts, durations, and updated periods.

### 🌐 Accounting REST API (in `web/server.py`)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/account/performance` | GET | Live state + all 4 current periods + drawdown + worker telemetry |
| `/api/account/performance/{kind}` | GET | Single period report (DAY/WEEK/MONTH/YEAR) |
| `/api/account/performance/{kind}/series` | GET | Bounded period series for charts |
| `/api/account/equity-curve` | GET | Balance/equity/drawdown time series + cumulative PnL |
| `/api/account/drawdown` | GET | Canonical drawdown state |
| `/api/account/trades/{trade_id}` | GET | Forensic reconstruction (ledger+orders+experience) |
| `/api/account/strategies` | GET | Per-strategy contribution joined to Intelligence |

Every endpoint returns REAL data from authoritative tables. When no data
exists the response carries `available`/`has_data` flags and the dashboard
renders an explicit empty state.

### 🖥️ Dashboard (Web/)

The Account tab contains the **Account Performance & Intelligence** panel:
period tabs (DAY/WEEK/MONTH/YEAR), live metrics (balance, equity, floating
PnL, drawdown, free margin, open positions), canvas charts (equity growth,
drawdown, cumulative PnL, period performance), the Strategy Attribution
table, and the Trade Forensics inspector (by ticket). All data comes from the
canonical `/api/account/*` endpoints; no frontend-side recomputation.

### 🧪 Tests

- `tests/unit/test_accounting_core.py` — 64 unit tests: periods, snapshots,
  aggregation, drawdown/recovery, closure classification, normalization,
  attribution, loss attribution, forensics, worker, self-healing, provenance.
- `tests/integration/test_accounting_api.py` — 13 integration tests: REST
  endpoints return real data, worker wiring in LiveEngine, cold-start
  survival without a model artifact.

---

## 15b. Trade Intelligence Brain (PHASE 09)

### 📐 Overview

The **`nexus_scalp/intelligence/`** package turns the engine from *"a system that
executes strategies"* into *"a system that understands its own trading
behavior."* It is the adaptive-strategy-evolution + position-lifecycle layer
built on top of the Phase 08 experience ledger.

```
Live path (tick)  ->  PositionLifecycleTracker.observe_position()
                              |
                              v
              immutable position_lifecycle_events  (timeline)
                              |
        TradeAutopsyEngine (WHY did it win/lose?)
        BehaviorDetectionEngine (measurable patterns)
        StrategyEvolutionEngine (candidate discovery, never live until validated)
        PreTradeIntelligenceGate (WARN/suitability, layered on Phase 08 gate)
        IntelligenceWorker (isolated background refresh, self-heal)
```

### 🔒 Non-Negotiable Safety Contract (PHASE 09)

Learning intelligence MUST NEVER:
- place orders, modify orders, or modify SL/TP directly
- bypass RiskEngine / exposure limits / kill switches
- force execution or disable protections

Learning can only **analyze, score, recommend, and reject before execution
through bounded interfaces**. Every intelligence engine owns NO adapter, NO
order manager and NO risk engine by construction (verified by
`tests/unit/test_intelligence_phase09.py::TestSafetyContract`).

Required flow:
```
Existing Strategy -> Existing Signal -> Trade Intelligence Brain
  -> Suitability/Quality Decision -> RiskEngine -> OrderManager -> MT5
```

### 🗄️ New Canonical Tables (all in `audit.db`, SQLite WAL)

- `position_lifecycle_events` — IMMUTABLE append-only position timeline, keyed by
  `event_key` (ticket|sequence|type). Replaying an upstream observation is a no-op.
- `trade_autopsies` — ONE forensic narrative row per closed ticket (upsert),
  answering "why did this trade win/lose?".
- `behavior_detections` — append-only measurable behavioral patterns
  (GREED_PATTERN, PANIC_EXIT_PATTERN, EARLY_EXIT_PATTERN, LATE_EXIT_PATTERN,
  BAD_RECOVERY_PATTERN, OVERTRADING_PATTERN, ...).
- `strategy_evolution_candidates` — discovered-but-unvalidated strategy
  variations. A candidate is NEVER live until backtested and validated.
- `intelligence_worker_state` — restart-safe worker bookkeeping (checkpoint persisted on `stop()`, restored on `start()`; verifiable via `test_worker_checkpoint_persists_across_restart`).

### 🧠 Position Lifecycle Events

Every open position emits a timeline of immutable observations:
`POSITION_CREATED`, `POSITION_OPENED`, `POSITION_MOVING`,
`POSITION_EXPECTATION_CONFIRMED`, `POSITION_MFE_REACHED`,
`POSITION_PROFIT_GIVEBACK`, `POSITION_DEGRADING`,
`POSITION_RECOVERY_ATTEMPT`, `POSITION_EXITED`, `POSITION_MODIFIED`.

Each event carries identity (ticket, trade_id, experience_id, event_key),
market context (symbol, timeframe, session, regime, volatility, ATR, spread),
position snapshot (entry/current/volume/SL/TP/floating/realized PnL),
performance (MFE, MAE, max profit/loss, giveback %, holding duration) and the
decision context that produced the trade (strategy_id, version, schema, model).

### 🩺 Trade Autopsy Verdict Model

| Verdict | Meaning |
| :--- | :--- |
| `CLEAN_WIN` | profitable, correct thesis, sound execution/management |
| `LUCKY_WIN` | profitable but thesis/entry evidence was poor |
| `MANAGED_LOSS` | negative but stop/risk respected (acceptable loss) |
| `COSTLY_LOSS` | negative AND a management/execution failure amplified it |
| `EVEN` | closed around breakeven |

This separation prevents "losing trade" from collapsing into "bad strategy":
a well-managed stop-out is a `MANAGED_LOSS`, not evidence the strategy is broken.

### 🧠 Strategy Evolution

The system discovers strategy variations from history:
```
Historical Experience -> Pattern Discovery -> Strategy Candidate
  -> Backtest -> Validation -> Memory (operator-promoted)
```
Candidates are produced by `StrategyEvolutionEngine.scan()` and persisted as
`strategy_evolution_candidates`. They NEVER affect live trading until backtested
and validated (`validate_candidate()` requires a positive expectancy over a
sample floor). Promotion to real strategy memory is a separate, operator-gated
action.

### ⚙️ IntelligenceWorker (`intelligence/worker.py`)

- Background derived-refresh loop, kicked via `asyncio.to_thread()` from
  `LiveEngine.run_loop` — NEVER on the tick hot path.
- Restart-safe (`start`/`stop` state machine with persisted checkpoint in
  `intelligence_worker_state`), failure-isolated (each cycle is
  wrapped; a failure logs `[INTELLIGENCE_WORKER] event=FAILURE` and the worker
  continues), idempotent (repeating a cycle with no new data is a no-op).
- Owns NO tables for raw truth; its side effects are the derived intelligence
  tables and its own worker-state checkpoint.

### 🌐 Intelligence REST API (in `web/server.py`)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/intelligence/summary` | GET | Aggregated brain telemetry + worker status + last suitability |
| `/api/intelligence/positions/{ticket}/timeline` | GET | Immutable position lifecycle timeline |
| `/api/intelligence/autopsies` | GET | Bounded autopsy listing (optionally by strategy) |
| `/api/intelligence/autopsies/{ticket}` | GET | Single forensic autopsy |
| `/api/intelligence/behavior` | GET | Measurable behavioral detections |
| `/api/intelligence/evolution` | GET | Evolution candidates |
| `/api/intelligence/evolution/scan` | POST | Run a bounded evolution discovery pass |
| `/api/intelligence/evolution/validate` | POST | Record a backtest result (candidate → VALIDATED/REJECTED) |
| `/api/intelligence/self-heal` | POST | Rebuild all derived intelligence from the immutable ledger |

### 🧪 Phase 09 Tests

- `tests/unit/test_intelligence_phase09.py` — 18 tests covering lifecycle
  tracking, MFE/MAE, giveback, decomposition, bad-management≠bad-strategy,
  degradation, recovery, similarity, pre-trade rejection, schema migration,
  self-heal rebuild, worker isolation, and the no-bypass safety contract.
- `tests/integration/test_intelligence_api.py` — 7 tests covering LiveEngine
  wiring, timeline/autopsy/behavior/evolution endpoints, worker restart-safety
  and no-MT5 operation.

---

## 15c. Strategy Research, Backtest & Validation Engine (PHASE 09B)

### 📐 Overview

The **`nexus_scalp/research/`** package is the evidence-driven strategy
discovery + backtest + walk-forward + OOS + robustness + validation layer built
on top of the Phase 08 experience ledger and the Phase 09 intelligence brain.

It implements the research process:

```
EXPERIENCE -> RESEARCH -> CANDIDATE -> BACKTEST -> WALK-FORWARD
    -> OUT-OF-SAMPLE -> ROBUSTNESS -> STATISTICAL SCORE
    -> VALIDATED / SHADOW -> OPERATOR-APPROVED (NEVER automatic)
```

### 🗄️ Canonical Research Engine (`src/nexus_scalp/research/`)

| File | Responsibility |
| :--- | :--- |
| `models.py` | Immutable domain contracts: `ResearchSample`, `ResearchDataset`, `BacktestResult`, `WalkForwardResult`, `OOSResult`, `RobustnessResult`, `StrategyScore`, `StrategyRegistryEntry`, `ResearchRun`, `ExecutionAssumptions`, `CandidateLifecycle`. |
| `dataset.py` | `ResearchDatasetBuilder` — deterministic, causally-ordered dataset from the immutable experience ledger; provenance + feature-schema preserved per sample. |
| `splitting.py` | `split_temporal()` + `walk_forward_folds()` — temporal TRAIN/VALIDATION/OOS with purge + embargo. Never random splits. |
| `leakage.py` | Causal guards: `assert_no_future_decisions()`, `fit_forward_stats()` (fit-on-train-only), `validate_no_train_leakage()`. |
| `metrics.py` | Pure deterministic statistics (expectancy, PF, drawdown, MAE/MFE, consecutive losses, friction modeling). |
| `backtest.py` | `BacktestEngine` — deterministic, friction-aware backtest over recorded trades. |
| `walkforward.py` | `WalkForwardEngine` — repeated temporal re-evaluation, tracks fold stability/degradation. |
| `oos.py` | `OOSGate` — hard out-of-sample gate; OOS failure ⇒ REJECTED even with high win rate. |
| `robustness.py` | `RobustnessEngine` — spread / slippage / latency stress; measures degradation, not just "still profitable". |
| `scoring.py` | `compute_strategy_score()` — decomposable multi-dimension score with small-sample protection. |
| `candidates.py` | `StrategyCandidate` — deterministic content-addressed identity + immutable versioning. |
| `discovery.py` | `discover_candidates()` — bounded context-family discovery (no micro-strategy explosion). |
| `lifecycle.py` | Research lifecycle state machine (DISCOVERED → … → VALIDATED → SHADOW → ACTIVE; REJECTED/DEGRADED/RETIRED). |
| `registry.py` | `StrategyRegistry` — enduring persistence of validation truth, independent of model files. |
| `pipeline.py` | `ResearchPipeline` — orchestrates dataset → discovery → gates → score → registry. |
| `worker.py` | `ResearchWorker` — isolated, restart-safe, idempotent background research loop. |
| `store.py` | Bounded read facade for the research tables. |

### 🔒 Safety Contract (PHASE 09B)

- Research NEVER places, modifies or closes an order: the package holds no
  adapter, no order manager and no risk engine (verified by tests).
- A candidate NEVER becomes LIVE automatically. Only
  SHADOW/VALIDATED → ACTIVE via a deliberate operator-gated `approve_for_live()`.
- A strategy that fails OOS is REJECTED regardless of in-sample / win rate.
- A modified strategy is a NEW VERSION and must be revalidated.
- 50D (`scalp_v1`) works today; 60D/350D are supported at the schema/provenance
  layer via `feature_schema_id` + `feature_dimension` per candidate/registry row.

### 🗄️ New Canonical Tables (all in `audit.db`, SQLite WAL)

- `strategy_registry` — enduring validation truth keyed by
  `(strategy_id, strategy_version)` UNIQUE; preserves backtest / walk-forward /
  OOS / robustness / score / confidence / lifecycle / lineage. Independent of
  the current model file (survives model rebuilds + schema-width changes).
- `research_runs` — append-only record of every validation run (reproducibility).
- `research_worker_state` — restart-safe worker checkpoint.

### ⚙️ LiveEngine Wiring

`LiveEngine` constructs `strategy_registry`, `research_dataset_builder`,
`research_pipeline` and `research_worker`. The worker is kicked via
`asyncio.to_thread()` from the periodic loop — NEVER inside
`_process_tick_pipeline()` — and is failure-isolated (cannot stop trading).

### 🌐 Research REST API (in `web/server.py`)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/research/summary` | GET | Candidate count / status / lifecycle distribution + worker status |
| `/api/research/registry` | GET | Bounded registry listing |
| `/api/research/registry/{strategy_id}` | GET | Single registry entry |
| `/api/research/runs` | GET | Append-only validation run records |
| `/api/research/discover` | POST | Build dataset + bounded candidate discovery |
| `/api/research/validate` | POST | Full gate chain for one candidate (never ACTIVE) |
| `/api/research/self-heal` | POST | Rebuild derived research state |

### 🧪 Phase 09B Tests

- `tests/unit/test_research_phase09b.py` — 45 tests covering dataset causality,
  provenance, future-leakage defense, deterministic identity/versioning,
  deterministic backtest + friction, temporal folds, purge/embargo, OOS gate
  (in-sample success + OOS failure ⇒ REJECTED), robustness stress, multi-dim
  scoring + small-sample protection, lifecycle, versioning immutability, safety
  (no RiskEngine/OrderManager/MT5), worker isolation/restart, and full-pipeline
  non-promotion to ACTIVE.
- `tests/integration/test_research_api.py` — 7 tests covering LiveEngine wiring,
  summary/registry/discover/validate/self-heal endpoints, worker restart-safety
  and no-MT5/no-risk-engine exposure.

---

## 15d. Controlled Model Training & Challenger Engine (PHASE 10)

### 📐 Overview

The **`nexus_scalp/model_lifecycle/`** package implements controlled OFFLINE
model training + Champion/Challenger management built on top of Phases 08/09
(experience ledger, strategy research, validation gates, feature schema
registry, existing WalkForwardTrainer).

```
VERIFIED EXPERIENCE -> TRAINING DATASET -> CANDIDATE MODEL
    -> VALIDATION GATES (1..12) -> CHAMPION COMPARISON
    -> CHALLENGER (shadow-eligible)   [NEVER auto-promoted]
```

The production Champion is NEVER touched by candidate training. A Challenger
can never reach the production inference path by accident; only the existing
controlled promotion process may move a Challenger toward production.

### 🗄️ Canonical Model Lifecycle Engine (`src/nexus_scalp/model_lifecycle/`)

| File | Responsibility |
| :--- | :--- |
| `models.py` | Immutable contracts: `TrainingRun` (full lineage), `TrainingDataset`, `TrainingDatasetRow`, `GateResult`, `ModelArtifactInfo`, `ModelStatus` (CANDIDATE/CHALLENGER/CHAMPION/REJECTED/ARCHIVED/INVALID), `TrainingRunStatus`. |
| `dataset.py` | Deterministic, causally-safe training dataset builder from the experience ledger (all outcome classes represented; no future leakage). |
| `integrity.py` | Artifact hash/dimension/class-count/scaler compatibility; explicit SchemaCompatibilityError on mismatch. |
| `champion.py` | Champion loading + verification; production model only; corrupted artifact never silently loaded. |
| `trainer.py` | `ChallengerTrainer` — offline candidate training reusing `WalkForwardTrainer`; writes to candidate/staging paths only. |
| `gates.py` | 12 validation gates (dataset, schema, labels, stability, validation, walk-forward, OOS, robustness, risk, comparison, artifact, reproducibility) + collapse protection. |
| `comparison.py` | Champion vs Challenger multi-dimension comparison + eligibility (improvement without critical degradation). |
| `registry.py` | Additive lifecycle-status extension of the canonical `experience_model_registry` (NO duplicate registry). |
| `store.py` | Immutable persistence for `training_runs` + `model_comparisons` tables. |
| `orchestrator.py` | End-to-end controlled training pipeline wiring dataset → train → gates → compare → registry. |
| `worker.py` | Isolated, bounded, cancellable, restart-safe background training worker. |

### 🔒 Safety Contract (PHASE 10)

- Training is OFFLINE/BACKGROUND only; heavy PyTorch work NEVER runs inside
  `LiveEngine._process_tick_pipeline()` (worker threads via `asyncio.to_thread`).
- A candidate is written to `candidate/staging` paths; the Champion artifact is
  NEVER overwritten (verified by test: champion hash unchanged during training).
- A failed/interrupted training run stays FAILED/INCOMPLETE — never VALIDATED.
- No auto-promotion: a validated Challenger is stored `CHALLENGER`
  (shadow-eligible); production authority stays with the controlled process.
- Schema mismatch fails explicitly (50D today; 60D/350D additive via
  `feature_schema_id`; never silent reshape/truncate).
- The package holds no adapter, no order manager and no risk engine.

### 🗄️ New Canonical Tables (append/additive)

- `training_runs` — append-only immutable record of every controlled training
  execution (dataset, schema, hyperparameters, seed, ranges, embargo, parent
  champion, artifacts, metrics, gates, status).
- `model_comparisons` — Champion-vs-Challenger comparison lineage.
- `experience_model_registry` — additive columns: `lifecycle_status`,
  `training_run_id`, `parent_model_id/version`, `promotion_reason`,
  `gate_summary`, `validation_run_ids` (existing Phase 08 registry reused).

### ⚙️ LiveEngine Wiring

`LiveEngine` constructs `champion_manager`, `training_run_store`,
`model_lifecycle_orchestrator` and `training_worker`. The worker is kicked via
`asyncio.to_thread()` from the periodic loop (NEVER in the tick pipeline),
with `auto_train_enabled=False` by default (operator-triggered training).

### 🌐 Model Lifecycle REST API (in `web/server.py`)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/models/summary` | GET | Registry + run + worker + champion status |
| `/api/models` | GET | Bounded model registry listing (by status) |
| `/api/models/champion` | GET | Production Champion metadata + integrity |
| `/api/models/challengers` | GET | Validated Challengers (shadow-eligible) |
| `/api/models/runs` | GET | Append-only training-run records |
| `/api/models/runs/{run_id}` | GET | Single run with gates + artifacts |
| `/api/models/comparison/{run_id}` | GET | Champion vs Challenger comparison |
| `/api/models/train` | POST | One controlled training pass (candidate only) |
| `/api/models/worker/start` | POST | Start background training worker |
| `/api/models/worker/stop` | POST | Stop background training worker |
| `/api/models/worker/cancel` | POST | Cancel in-flight training safely |

### 🧪 Phase 10 Tests

- `tests/unit/test_model_lifecycle_phase10.py` — 32 tests covering dataset
  reproducibility/provenance/temporal-order/no-future-leakage/schema-identity,
  wins+losses+neutral representation, strategy context retention, 50D
  compatibility + future-schema boundary, dimension/scaler mismatch rejection,
  gate failures (OOS/robustness/drawdown/collapse), Champion hash invariance,
  rejected-challenger-cannot-become-champion, lineage immutability, worker
  isolation/cancellation/restart, and Phase 08/09 regression.
- `tests/integration/test_model_lifecycle_api.py` — 7 tests covering LiveEngine
  wiring, summary/models/champion/challengers/runs endpoints, worker
  start/stop/cancel, and no-MT5/no-risk-engine exposure.

---

## 15e. Challenger Shadow Trading & Champion Evaluation (PHASE 11)

> **NOTE (2026-08-16 Forensic Audit):** This section was ADDED by the Phase
> 08-11 forensic audit. The shadow subsystem existed in code (uncommitted at
> audit time) but was undocumented in this file. Audit also fixed BUG-025 /
> BUG-026 / BUG-027 / BUG-028 / BUG-029 (see `agents/bugs.md`): shadow
> decisions are now persisted correctly, the audit queue can no longer
> deadlock on insert errors, and the Champion/Challenger comparison derives
> each side's R from its OWN action (absolute + relative degradation floors).

### 📐 Overview

The **`nexus_scalp/shadow/`** package evaluates a validated Challenger model
under the SAME live market state as the production Champion, entirely in
shadow: zero order authority, every result marked `simulated=True`.

```
VALIDATED CHALLENGER -> attach (operator) -> shadow run (same live feature
vector as Champion) -> shadow decisions (bounded, queued persistence)
-> aggregate comparison (expectancy/drawdown/calibration/regime/strategy)
-> promotion evaluation (hard vetoes) -> NEVER auto-promoted
```

### 🗄️ Canonical Shadow Engine (`src/nexus_scalp/shadow/`)

| File | Responsibility |
| :--- | :--- |
| `models.py` | Immutable contracts: `ShadowRun`, `ShadowDecisionRecord` (always `simulated=True`), `ShadowComparison`, `PromotionEvaluation`, `ShadowModelRef`, `SharedInputRef`. |
| `challenger.py` | `ChallengerRuntime` — loads a validated Challenger artifact with integrity checks; `infer()` returns a hypothetical proposal ONLY. |
| `engine.py` | `ShadowEngine` — records one parallel Champion/Challenger decision per live tick on the SAME feature vector; schema-safety gate; bounded in-memory decision list. |
| `comparison.py` | `ShadowComparer` — multi-dimension comparison + explainable promotion evaluation with hard vetoes. |
| `store.py` | Persistence for `shadow_runs`, `shadow_decisions`, `shadow_comparisons`, `shadow_promotions` (queued writes, schema guarded once per process). |
| `worker.py` | `ShadowWorker` — isolated, restart-safe, cancellable background aggregation; finalizes runs at `finalize_after_decisions` (default 30). |

### 🔒 Safety Contract (PHASE 11)

- A Challenger has ZERO order authority: `shadow/` imports no adapter, no
  order manager, no risk engine (tested).
- Every recorded decision is flagged `simulated=True`; nothing is ever
  presented as real account PnL.
- Schema mismatch (feature_schema_id / feature_dimension) ⇒ decision marked
  invalid, never used in comparison.
- Shadow evaluation NEVER blocks trading: write path is the audit queue;
  aggregation runs in the worker via `asyncio.to_thread` (never in the tick
  pipeline); a shadow failure is logged and ignored.
- Promotion is NEVER automatic: `evaluate_promotion()` produces eligibility +
  vetoes; no code path moves a Challenger into the live bundle.

### 🗄️ New Canonical Tables (all in `audit.db`, SQLite WAL)

- `shadow_runs` — one bounded shadow evaluation run (status RUNNING/COMPLETED/INCOMPLETE).
- `shadow_decisions` — one parallel Champion/Challenger decision per row (marked simulated).
- `shadow_comparisons` — aggregated multi-dimension comparison snapshot per run.
- `shadow_promotions` — explainable promotion evaluation + vetoes per run.

### ⚙️ LiveEngine Wiring

`LiveEngine` constructs `shadow_store`, `shadow_engine`, `shadow_worker` and
starts the worker in `run_loop` (bounded/throttled via `asyncio.to_thread`).
`_record_shadow_decision()` runs after the Phase 08/09 gates on the SAME live
feature vector; it is fully exception-isolated (a Challenger fault can never
affect production).

### 🌐 Shadow REST API (in `web/server.py`)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/models/shadow/summary` | GET | Runs + decisions + promotions + worker + active challenger |
| `/api/models/shadow/runs` | GET | Append-only shadow run history |
| `/api/models/shadow/decisions` | GET | Shadow decision records (all simulated) |
| `/api/models/shadow/compare/{run_id}` | GET | Multi-dimension Champion vs Challenger comparison |
| `/api/models/shadow/promotion/{run_id}` | GET | Promotion evaluation (eligibility + vetoes) |
| `/api/models/shadow/attach` | POST | Attach a validated Challenger (integrity-checked) and start a run |
| `/api/models/shadow/evaluate-promotion` | POST | Compute + persist promotion evaluation for a run |
| `/api/models/shadow/worker/start` | POST | Start the shadow worker |
| `/api/models/shadow/worker/stop` | POST | Stop the shadow worker |

### 🧪 Phase 11 Tests

- `tests/unit/test_shadow_phase11.py` — 35 tests: basic loads, identical
  inputs, schema mismatch rejection, corrupt artifact rejection, shadow
  decision cannot submit MT5, simulated marking, persistence round-trip,
  champion unchanged during shadow, metrics comparison, small-sample
  insufficiency, OOS/drawdown/robustness/strategy-regression/calibration
  vetoes, regime-specific comparison, critical regime degradation not
  averaged away, strategy-specific comparison, retired strategy blocked,
  failure isolation (challenger and DB failures cannot stop trading),
  worker restart/cancellation, invalid challenger exclusion, model-rebuild
  history survival, feature-schema provenance, lineage preservation, and
  Phase 08/09/10 + hot-path regression.

---

## 15f. News Intelligence Engine (PHASE 12)

> The system collects, deduplicates, classifies, analyzes, scores, ages, and
> correlates financial/news events with the existing Nexus trading
> intelligence. XAUUSD/GOLD is the primary target; USD, EUR, GBP, JPY, CHF
> and major FX are secondary. News is CONTEXTUAL INTELLIGENCE ONLY — it can
> never generate a BUY/SELL, never place an order, and never bypass
> RiskEngine/OrderManager protections.

### Architecture
```
src/nexus_scalp/news/
  models.py        domain contracts (NewsArticle, NewsImpact, NewsState,
                   CurrentNewsContext, NewsConsensus, NewsNovelty, ...)
  config.py        NewsConfig: LOCAL_ONLY/API_ONLY/HYBRID, decay half-lives,
                   impact caps (max_confidence_boost 0.05 / penalty 0.10),
                   queue size, polling intervals, AI timeout
  database.py      DEDICATED SQLite artifacts/news.db (WAL, 13 tables +
                   indexes). Trading/accounting DB untouched.
  seed.py          deterministic, idempotent, versioned seed (v2):
                   11 sources (7 official Tier-1 + 4 RSS), asset profiles
  sources/         base.py (Source/Fetcher/Normalizer contract, RSS 2.0 +
                   Atom parsing, official adapter, HTML extraction)
  ingest/          deduplicator.py (article_hash + normalized-title +
                   publication-time syndication window), fetcher.py
                   (rate-limit/backoff/jitter/source-health/scheduler)
  analysis/        local.py (entities/topics/XAUUSD+USD relevance/direction/
                   importance), consensus.py (tier-weighted), decay.py
                   (BREAKING/MACRO/POLICY/STRUCTURAL half-lives),
                   pipeline.py (multi-stage local -> optional external AI ->
                   schema validation -> fallback -> persist)
  memory/          post_event.py (predicted-vs-actual impact validation)
  context.py       CurrentNewsContext cache (worker-refreshed, cache-only
                   on the tick path — NO per-tick DB access)
  gate.py          NewsGate (bounded confidence adjustment, position-
                   protection actions never gated)
  engine.py        orchestrator (ingest_cycle/analysis_cycle/self_heal/
                   health/record_market_response/link_trade)
  worker.py        NewsWorker (bounded priority queue, dedup, expiry,
                   retries<=3, checkpoint/restart-safe, asyncio.to_thread)
```

### Database (artifacts/news.db)
news_sources, news_articles, news_article_versions, news_entities,
news_topics, news_analysis, news_impacts, news_consensus, news_analysis_runs,
news_worker_state, news_event_links, news_trade_links, news_health.
Indices on article_hash, title_hash, article_id, published_at, asset.

### Source registry (seed v2)
- Tier-1 official: Fed, BLS, BEA, ECB, BoE, CFTC (disabled — no public RSS),
  U.S. Treasury. Verified live URLs 2026-08-16.
- Tier-2: Reuters, MarketWatch. Tier-3: ForexLive, ZeroHedge.
Broken/unreachable sources become disabled or are marked unhealthy by the
fetcher's health tracker (consecutive failures -> exponential backoff).

### Deduplication
One canonical event = multiple source evidence. Identity = deterministic
article_hash (content + URL + source) + normalized-title hash + publication
time (60s bucket) + syndication merge window (1h by publication proximity,
not ingestion wall-clock). Updated articles create versions, never silent
overwrites.

### Local analysis (NO API key required)
Entity/topic extraction, XAUUSD relevance (drivers: USD, real yields,
inflation, rates, geopolitics, safe-haven, energy, liquidity — context
matters: "gold medal" != XAUUSD event), USD relevance, directional
hypothesis, importance, source trust. Fully functional in LOCAL_ONLY.

### External AI (optional, fail-safe)
IExternalNewsAnalyzer interface. HYBRID default: local first, external only
for high-importance/ambiguous/conflicting/high-XAUUSD-relevance articles.
Missing key -> LOCAL_ONLY. Rate limit/timeout/invalid JSON/provider failure
-> LOCAL fallback (never fabricate). Final record flags LOCAL_ONLY / API /
COMBINED / FAILED.

### Impact & bounded gate
NewsImpact = SourceTrust x Importance x AssetRelevance x Novelty x Consensus
x Freshness x DirectionalConfidence. Decay classes BREAKING/MACRO/POLICY/
STRUCTURAL. NewsGate: alignment -> bounded boost (<= +0.05), conflict ->
bounded penalty (<= -0.10), HIGH_IMPACT/BREAKING state -> CAUTION,
position-protection actions NEVER gated, stale/unavailable news -> no-op.
Only the proposal's confidence field is adjusted via model_copy — the
action/direction is never changed.

### LiveEngine integration (non-blocking)
Ordering in _process_tick_pipeline: Phase 08 experience gate -> Phase 09
intelligence gate -> PHASE 12 news gate -> risk sizing -> OrderManager.
NewsWorker runs via asyncio.to_thread in run_loop; context refreshed
off-loop by the worker; tick path reads the cached CurrentNewsContext only.

### Post-event learning & attribution
record_market_response() stores predicted-vs-actual direction/magnitude/
timing/persistence with regime context (evidence only — never trains the
production model). link_trade() connects trade_id/experience_id/news_event_id/
strategy_id/model_version for Experience Intelligence.

### API (14 routes)
GET /api/news, /api/news/latest, /api/news/{id}, /api/news/impact,
/api/news/state, /api/news/sources, /api/news/health,
/api/news/analysis/{id}, /api/news/trades/{trade_id};
POST /api/news/analyze/{id} (async job), /api/news/refresh,
/api/news/self-heal. Disabled subsystem returns available=False (honest, no
synthetic data).

### Web UI
News Intelligence tab (#tab-news): live feed, state badge
(NORMAL/ELEVATED/HIGH_IMPACT/CONFLICTED/BREAKING/STALE), XAUUSD relevance,
bullish/bearish scores, active-event count, "Analyze with AI" button
(async, LOCAL/API/COMBINED status), Fetch News trigger.

### Safety invariants (tested)
- News can never force BUY/SELL (action unchanged by gate)
- News can never bypass RiskEngine/OrderManager/exposure protections
- No per-tick DB query (context cached; worker refreshes off-loop)
- Worker/DB failure never stops trading (safe defaults, failure-isolated)
- No fake confidence: empty evidence -> available=False, confidence 0.0
- Self-healing rebuilds derived state from authoritative raw records only

### Tests
- tests/unit/test_news_phase12.py — 63 tests: ingestion, dedup, decay,
  local analysis, external-AI fallback, gate safety, memory, worker, DB
  idempotency, self-heal, engine, regressions.
- tests/integration/test_news_api.py — 14 tests: real API data, state,
  health (no synthetic), detail, async analyze, refresh, self-heal,
  sources, disabled availability, worker isolation, DB independence,
  analysis persistence, trade links.

---

## 15f. Release Engineering & Distribution System (RELEASE, not a trading phase)

> **NOTE (2026-08-16):** This section was ADDED by the Release Engineering
> build. It is additive operational infrastructure — it does NOT modify
> trading logic. The release system is described in full in
> `docs/RELEASE.md`; this is the architecture map for agents.

### 📐 Overview

The release system turns the repository into a user-installable Windows
product while keeping the canonical trading engine untouched. Everything
lives in `src/nexus_scalp/release/` (packaging/health/CLI concerns) plus
`scripts/build/`, `installer/`, `.github/workflows/release.yml`.

```
pyproject.toml (version = 9.0.0)  <- SINGLE canonical version source
        │
        ├── src/nexus_scalp/release/metadata.py   (reads pyproject; build-info.json)
        ├── src/nexus_scalp/release/paths.py      (AppData separation: user data)
        ├── src/nexus_scalp/release/environment.py (OS/arch/Python/RAM/disk/GPU/MT5/...)
        ├── src/nexus_scalp/release/evaluate.py    (PASS/WARNING/BLOCKED/UNKNOWN)
        ├── src/nexus_scalp/release/health.py      (19-category HealthEngine)
        ├── src/nexus_scalp/release/repair.py      (non-destructive repairs)
        ├── src/nexus_scalp/release/diagnostics.py (sanitized ZIP export)
        ├── src/nexus_scalp/release/update.py      (safe update/rollback planner)
        ├── src/nexus_scalp/release/packaging.py   (manifest + SHA-256 + SBOM)
        ├── src/nexus_scalp/release/verify.py      (release self-check incl. secrets)
        ├── src/nexus_scalp/release/cli_shim.py    (nexus console script)
        └── src/nexus_scalp/release/packaged_main.py (PyInstaller EXE entrypoint)
```

### 🔌 CLI (`nexus`, legacy `nse` still works)

`version · doctor · health · status · test (quick|unit|integration|health|release|all)
· logs (--tail/--errors/--worker/--export) · config · repair · diagnostics /
export-diagnostics · verify-release · update · install/setup · start (--mode
paper|shadow|live) · stop · restart · uninstall · run · config-validate`

**Safety contract (tested):**
- `nexus start` defaults to PAPER. `--mode live` prints account/broker/symbol/
  risk/kill-switch and requires explicit confirmation. First-run wizard
  defaults PAPER.
- `--json` / `--plain` / `--no-color` supported for CI (JSON never contains
  ANSI).
- Heavy engine imports (torch/polars/MetaTrader5) are lazy in cli/main.py so
  the slim onefile CLI (which excludes them) works for version/health/
  diagnostics.

### 🏗️ Build & packaging (PyInstaller, onedir + onefile)

- `scripts/build/build_release.ps1` orchestrates: validate version → git state
  → gates → PyInstaller onedir (full engine + Web/configs/docs assets) →
  onefile CLI (no torch/polars) → EXE smoke → stage portable tree → Inno
  Setup installer → checksums + manifest + SBOM + secrets scan →
  verify-release. Any failure = STOP RELEASE.
- **Entrypoints:** packaged EXE runs `packaged_main.py` (the Typer CLI), NOT
  the argparse launcher. CLI onefile runs `cli_shim.py`.
- **ARM64 is explicitly UNSUPPORTED** (torch/polars/pyarrow/MetaTrader5 have
  no Windows ARM64 wheels). The evaluator BLOCKs it, the update planner
  refuses ARM64 artifacts, CI reports it loudly. Only windows-x64 is
  published.
- User data (config/logs/databases/models) lives in
  `%LOCALAPPDATA%\NexusScalpEngine` — outside the install dir. Upgrades,
  repairs and uninstalls preserve it (installer `uninsneveruninstall`,
  uninstall deletes only with an explicit checkbox; silent uninstall always
  preserves).

### 🦺 Secrets & user-data protection

- CI validate job scans the tree for secret-shaped strings; `verify-release`
  scans the staged tree (API keys, bot tokens, JWTs, quoted passwords).
- `configs/live.yaml` ships with an EMPTY telegram `bot_token` — never a real
  token (found & sanitized during build).
- `artifacts/audit.db`, dev DBs, credentials, private config never packaged.
- `export-diagnostics` redacts secrets and contains only metadata + recent
  logs.

### 🧪 Release tests

- `tests/unit/test_release_system.py` — 22 tests: canonical version vs
  pyproject, manifest/checksum round-trips + corruption detection, arm64
  BLOCKED, healthy categories, repair never deletes, update safety (digest
  required, prerelease refused, ARM64 refused), no-LIVE-by-default, CLI
  contract, diagnostics sanitized.
- `tests/unit/test_release_build_system.py` — 6 tests: scripts/installer/
  workflow exist, packaged entrypoint referenced, safe installer defaults,
  requirements cover web/news runtime.

### 🌐 CI/CD

- `ci.yml`: quality gates on push/PR (ruff, mypy, pytest unit + integration
  without browser/MT5).
- `release.yml` (v* tag): validate (tag==pyproject version, secrets scan) →
  gates → build windows-x64 → EXE smoke → stage → installer → checksums +
  manifest + SBOM → verify-release → publish (assets attached). ARM64 job
  reports explicit non-support. Prereleases for `vX.Y.Z-*`.

---

## 15g. Model Generation Migration — Artifact-First Model Factory (PHASE 13)

> Legacy ScalpNet is classified as **LEGACY BASELINE** (control group) — NOT
> deleted, remains loadable/reproducible for benchmarking. The new centre of
> the ML system is the **MODEL ARTIFACT** on the filesystem. Databases are
> history/telemetry/registry only — **model inference requires NO database**.

### Architecture
```
src/nexus_scalp/model_generation/
  models.py          explicit contracts: LabelSchema (3-class: NO_TRADE/BUY/
                     SELL; WAIT is policy-derived), NewsContextSchema (12
                     versioned numeric fields), SampleContract / SetupContract /
                     StrategyContract (independently versionable), ModelManifest
                     (identity+architecture+input+labels+training+strategy+
                     news+validation+integrity), DatasetManifest, ExperimentConfig
  artifact_store.py  filesystem hierarchy artifacts/model_generation/
                     {datasets,experiments,models}/<id>/* + hashing + atomic
                     writes + verify_artifact()
  sample_factory.py  raw bars -> features (FeatureSchemaRegistry) -> regime ->
                     causal news context -> setup -> 3-class labels -> Sample
  dataset_factory.py deterministic DatasetArtifact (parquet + manifest; temporal
                     split; purge/embargo preserved; no live-model dependency)
  model_factory.py   architecture registry (LEGACY_SCALPNET_V1 baseline, MLP_V2,
                     TCN_V2/TCN_ATTENTION_V1/TRANSFORMER_V1 registered-not-built)
  experiment_factory.py bounded explainable experiment space + persistence
  training.py        CandidateTrainer (candidate artifacts only; Champion never
                     overwritten; failures are FAILED, never CHALLENGER)
  validation.py      ValidationFactory: label integrity, class-collapse, regime
                     results, calibration (ECE), OOS floor, news ablation
  runtime.py         LocalModelRuntime: load/validate/predict/health — NO DB
  replay.py          SampleReplay (reconstruct context + prediction) + drift
                     detection (feature/prediction) — alert only, never auto-retrain
```

### Contracts (all immutable, independently versionable)
- **LabelSchema** `triple_barrier_3class_v1`: class_count=3,
  {NO_TRADE:0, BUY_MARKET:1, SELL_MARKET:2}; WAIT is rejected (`encode("WAIT")`
  raises). The legacy ScalpNet 4th logit is a POLICY bridge, never a label.
- **ModelManifest**: model_id/version/role/status, architecture_id+params,
  feature_schema_id+dimension, label schema, dataset_id+hash, training run,
  strategy_id/version, news schema + news_enabled + provenance,
  validation statuses, artifact/manifest/scaler hashes, created_at.
- **DatasetManifest**: dataset_id/version, source hash, row counts, temporal
  range, symbol/timeframe, feature/label schema ids, split/purge/embargo
  hashes, news schema + range, dataset_hash.
- **NewsContextSchema** `news_context_v1`: 12 normalized numeric fields
  (active_high_impact_events, xauusd_relevance, usd_relevance, bullish/bearish
  pressure, conflict, novelty, freshness, confidence, source_consensus,
  news_state, time_since_event). Causally correct: only events published at or
  before the sample timestamp enter the vector (epoch-us comparison, tz-safe).

### Safety invariants (code + test verified)
- Inference NEVER touches the DB: `LocalModelRuntime` reads only the artifact
  dir; integration test blocks the sqlite3 import and prediction still works.
- Candidate training NEVER overwrites Champion (candidate ids; legacy
  artifacts/models/scalp path untouched).
- Corrupted artifacts / schema mismatches / label mismatches FAIL loudly
  (ManifestValidationError), never silently reshape.
- News-aware candidates record the exact neural input width
  (`build_metadata.input_dimension`) so they reload+replay correctly.
- Class collapse (>=95% one class) is detected; calibration (ECE) is
  measured; regime results surface per-regime catastrophes.
- News vs no-news experiments compare on identical split/labels/friction
  (compare_news_ablation); news features must EARN their place.
- Drift detection is an ALERT/RESEARCH trigger — no automatic retrain.
- No MT5/order-manager/risk-engine imports in model_generation.

### CLI (existing Typer app)
`model-dataset-build --bars --symbol --timeframe --schema [--with-news --news]`
· `model-experiment-create --dataset --template`
· `model-train --experiment [--model-id]`
· `model-inspect --model` · `model-validate --model --dataset`
· `model-replay --dataset --sample [--model]` · `model-doctor --model`

### Legacy classification
- `models/scalp_net.py` = LEGACY BASELINE (control group). Zero new
  architecture claims superiority until it beats the baseline on the SAME
  dataset/split/labels/friction via the new pipeline.

### Tests
- `tests/unit/test_model_generation_phase13.py` — 63 tests (legacy loads,
  manifest/hash/corruption, dataset determinism/provenance/purge/embargo/news
  causality, sample/setup/strategy identity, factory builds, 3-class contract,
  class collapse, calibration, news train on/off, validation gates, no-DB
  runtime, mismatch/corruption rejection, atomic swap, replay/drift, safety,
  failure isolation, NaN/Inf training rejection (BUG-041), Phase 08-12
  regression).
- `tests/integration/test_model_generation.py` — 3 tests (full artifact flow
  with DB-blocked prediction, CLI registration, baseline reproducibility).

---

## 15h. First New Architecture Benchmark — TCN_ATTENTION_V1 (PHASE 13B)

> STATUS (2026-08-16/17): benchmarked on a shared synthetic-style dataset —
> **NEW_MODEL_WORSE, NEWS_INCONCLUSIVE**. TCN_ATTENTION_V1 is implemented,
> artifact-complete, loadable DB-free, but did NOT beat the legacy baseline
> (val_acc 0.745-0.764 vs 0.819; macro-F1 0.25 vs 0.29). All four A/B/C/D
> candidates REJECTED by the validation gates. NO Challenger promoted.
> Champion untouched. Remainder of this section = the verified architecture.

### Architecture
`model_generation/architectures.py` — `TCNAttentionV1` (ARCHITECTURE_VERSION
1.0.0): Linear projection → N dilated CAUSAL conv blocks (residual +
LayerNorm, dilation 2^i) → multi-head self-attention → final-state pooling
(last timestep) → **3-logit head** (no 4-head WAIT bridge). Configurable
hidden_dim/blocks/kernel_size/attention_heads/dropout/max_seq_len with
deterministic init under a caller-set torch seed.

### Sequence contract
`model_generation/sequence.py` — `SequenceBuilder`: strict causality
(timestamp_0 < ... < timestamp_N), same-symbol/same-timeframe windows,
configurable max-gap (µs), deterministic; **frame-order columns** (never
lexicographic — feat_10 must follow feat_2). `news_enabled=False` removes
news_* columns entirely (50D); `news_enabled=True` appends the 12
NewsContext fields (62D). No sequence crosses a symbol/timeframe boundary.

### Trainer + benchmark
`sequence_training.py` — `SequenceCandidateTrainer`: train-only scaler fit,
NaN/Inf loss/gradient gates (FAILED never COMPLETED), seed control.
`benchmark.py` — `BenchmarkRunner`: ONE shared dataset for A (legacy no
news) / B (legacy news) / C (TCN no news) / D (TCN news); reports JSON+MD
to `artifacts/model_generation/model_benchmark_report.{json,md}`; fair
comparison (same labels/splits/purge/embargo/friction, from the labeler).

### Decision rule
Point estimates only at n≈800 → LOW EVIDENCE. A candidate earns CHALLENGER
only via the validation gates (class collapse ≤95%, OOS floor, regime
coverage, calibration) — none passed. No auto-promotion ever.

### Tests
- `tests/unit/test_model_benchmark_phase13b.py` — 28 tests (build/config,
  sequence ordering/boundaries/causality, NaN/Inf/loss gates, news 50D/62D,
  causality, manifest provenance, fair-matrix/dataset parity, OOS rejection,
  class collapse, calibration/confusion, champion safety, DB-free load,
  artifact integrity/corruption, no-execution-access, Phase 08-12 imports).

---

## 15g. Release Engineering Hardening — Runtime-Tested Distribution System (2026-08-16)

Additive to §15f. This hardening pass was driven by REAL artifact runtime
tests (not source inspection). Active architecture:

### Runtime test suites (REAL artifacts; the acceptance gate)
- `tests/runtime/test_packaged_cli.ps1` — onefile CLI: help/version/health/doctor/status,
  JSON validity, no traceback, exit codes.
- `tests/runtime/test_packaged_engine.ps1` — onedir EXE: version/health/doctor/setup +
  **LIVE-safety negative test** (start --mode live must abort without confirmation).
- `tests/runtime/test_health_runtime.ps1` — 19 categories, real verdicts.
- `tests/runtime/test_no_python_dependency.ps1` — strips PATH to System32; proves
  self-containment (both EXEs must run without system Python).
- `tests/runtime/test_installer.ps1` — silent install → version → health →
  reinstall (idempotent) → silent uninstall → user-data preserved.
- `tests/runtime/test_repair.ps1` — destroys config+logs dirs, `nexus repair`
  restores from template without touching user data.
- `tests/runtime/run_runtime_tests.ps1` — runs all of the above (direct named
  args; PowerShell 7 splatting of [string[]] into child scripts mangles args —
  call each script with explicit `-Param value`).

### Invariants learned (encode these before changing release code)
1. **PyInstaller is not a cross-compiler; artifacts are layout-sensitive.**
   configs/ and Web/ land under `_internal/` in onedir — every file lookup
   (repair template, verify assets, identity) must check both
   `<root>/<rel>` and `<root>/_internal/<rel>` (BUG-038).
2. **Frozen onefile console = active code page.** Any non-ASCII char
   (em dash, arrow) in a Typer `help=` string aborts `--help` with
   `UnicodeEncodeError` → exit 1. Keep ALL Typer help strings ASCII-only
   (BUG-037/039). Regression test: `test_cli_help_strings_are_ascii_safe`.
3. **Never bundle a real credential.** `configs/live.yaml` carried a real
   Telegram bot token and shipped inside the artifact (BUG-036).
   build_release.ps1 + CI now HARD-REFUSE to build with a real
   `bot_token` (masked placeholder only). Secrets scanner decodes STRICTLY
   (errors='ignore' transmogrifies Persian UTF-8 comments into fake ASCII
   "tokens" → false positives; the tight pattern requires a quoted
   ≥25-char alnum body).
4. **Entrypoints**: engine build = `src/nexus_scalp/release/packaged_main.py`;
   CLI build = `src/nexus_scalp/release/cli_shim.py`. NEVER regress to
   `NexusTradingForexBot.py` (argparse) — BUG-033.
5. **Exit-code contract (stable)**: 0 success · 1 runtime/validation ·
   2 usage · 3 environment blocked · 4 release verification failure.
   `src/nexus_scalp/release/exit_codes.py` is the single source.
6. **PowerShell $LASTEXITCODE is clobbered by pipelines** (`| Out-String`,
   `ForEach-Object`). Capture it immediately after direct invocation.
7. **build_release.ps1 hardened**: deletes stale release/build before
   PyInstaller (WinError-32 lock guard — kills only processes whose image
   path is under the release tree), uses native `$Root\...` paths.
8. **verify_release checks**: EXE exists/launches/version · Web/assets
   (incl. build-info.json) · Checksums/manifest (invocation-location
   independent) · Identity (build-info.json vs manifest: version, arch,
   channel, commit) · Secrets scan · No-LIVE-by-default.
9. Built artifacts must never be committed (gitignored); checksums file
   paths are relative to the release root (`portable/...`, `cli/...`);
   verify works from any invocation directory.
10. `git_commit`/`architecture`/`channel` in the manifest MUST come from
    the stamped build-info.json (single source of truth), never from
    runtime introspection.

### Release self-verification
- `nexus verify-release --root release/v9.0.0/windows/x64/portable`
- PowerShell: `.\scripts\build\build_release.ps1 -Version 9.0.0`
  (runs gates → secret guard → PyInstaller onedir + onefile → stage →
  installer → checksums → manifest → SBOM → verify) and
  `.\scripts\build\verify_release.ps1`.
- CI: tag `vX.Y.Z` → validate (incl. tag/version consistency + secret scan)
  → gates → build → smoke → installer → checksums/manifest/SBOM → verify →
  publish. ARM64 job reports UNSUPPORTED explicitly.

---

## 16. Known Engineering Pitfalls & Invariants

If you are an AI coding agent making changes to this repository in the future, **memorize these active engineering rules and constraints**:

### 🚨 1. Polars Boolean Expression Pitfall
* **Pitfall:** Using standard Python `not` or `and` inside Polars DataFrame filters.
* **Symptom:** Raises `ComputeError` or evaluates silently to invalid boolean masks.
* **Rule:** **ALWAYS use bitwise tilde `~` and bitwise operators `&` / `|`** when filtering Polars DataFrames (e.g., `df.filter(~pl.col("is_deleted") & (pl.col("vol") > 0))`).

### 🚨 2. Pydantic Domain Immutability Constraint
* **Pitfall:** Attempting directly to modify attributes on domain objects (e.g., `position.sl = new_sl`).
* **Symptom:** Raises `ValidationError: "Position" object is frozen and immutable`.
* **Rule:** All domain models in `src/nexus_scalp/domain/models.py` use `frozen=True`. Use `.model_copy(update={"sl": new_sl})`.

### 🚨 3. Feature Vector 50D Contract Alignment
* **Pitfall:** Truncating or hardcoding feature selection indices in training scripts or model inputs.
* **Symptom:** Crashes with tensor dimension mismatch error (`ValueError: 50D feature contract violation`).
* **Rule:** All training scripts and inference pipelines MUST generate and select all 50 feature columns (`feat_0` .. `feat_49`) matching `WalkForwardTrainer.NUM_FEATURES = 50` and `ScalpNet`.

### 🚨 4. SSE / JSON Stream Enum Serialization Constraint
* **Pitfall:** Pushing raw domain models containing Enums directly into FastAPI SSE or WebSocket generators.
* **Symptom:** Raises `TypeError: Object of type ActionType is not JSON serializable`.
* **Rule:** Pass all telemetry dictionary payloads through `serialize_enums(data)` before streaming.

### 🚨 5. Event Loop Blocking Avoidance in Hot Path
* **Pitfall:** Performing synchronous file I/O, heavy PyTorch model fitting, or synchronous network calls inside `LiveEngine._process_tick_pipeline()`.
* **Symptom:** Freezes live tick processing loop, causes tick stagnation watchdog warnings and order slippage.
* **Rule:** All heavy or blocking work MUST be offloaded using `asyncio.to_thread()`, background worker threads, or async HTTP clients (`httpx`).

### 🚨 6. Web Error Hygiene — Never Return Exception Text to Clients (BUG-040)
* **Pitfall:** `except Exception as e: return {"error": str(e)}` anywhere in `web/server.py` leaks internal paths/SQL/exception classes (CodeQL py/stack-trace-exposure).
* **Rule:** All routes use the centralized helpers in `src/nexus_scalp/web/errors.py`:
  `_err(code, **kw)` → sanitized `{available, success, error:{code, message, request_id}}` envelope;
  `_log_err(exc, msg, endpoint=...)` → full traceback to structured logs ONLY.
* **Correlation:** every HTTP response carries `X-Request-ID`; the browser API client (`Web/api_client.js`) preserves it for log correlation; unhandled 500s become the safe envelope via middleware.
* **SSE/WS:** sanitized error paths, bounded exponential reconnect, stale detection; never stream `str(e)`.
* **No synthetic dashboard data:** chart history must never fabricate random candles or mock SMC rectangles (removed in BUG-040).

---

## 17. Testing & CI/CD Pipeline Audit

### 🧪 Test Suite Structure

* **Unit Tests (`tests/unit/`):**
  - `test_domain_models.py`: Validates frozen Pydantic domain immutability.
  - `test_scalp_features.py`: Validates 50D feature calculation, bounds, and fallback behavior.
  - `test_walk_forward_trainer.py`: Validates walk-forward splitting, loss functions, and online fine-tuning quality gates.
  - `test_policy.py`: Validates signal policy routing and SMC God Mode.
  - `test_risk_engine.py`: Validates dynamic lot sizing, account tier caps, and margin checks.
  - `test_order_manager.py`: Validates position state machine and `HARD_MAX_LOTS` clamping.
  - `test_rule_matrix.py`: Validates DB hot-reloading and rule evaluation.
  - `test_train_model_cli.py`: Validates CLI 50D feature extraction and dataset validation alignment.
  - `test_order_manager_audit.py`: Validates legacy file deletion and active order manager imports.
  - `test_htf_warmup_gate.py`: Validates cold-start HTF warmup gate state transitions and inference blocking.
  - `test_accounting_core.py`: Validates the Phase 08 unified accounting core (periods, snapshots, drawdown/recovery, closure classification, attribution, forensics, worker, self-healing, provenance).
  - `test_intelligence_phase09.py`: Validates the Phase 09 Trade Intelligence Brain (position lifecycle timeline, MFE/MAE, giveback, quality decomposition, bad-management≠bad-strategy, degradation, recovery, similarity, pre-trade rejection, schema migration, self-heal rebuild, worker isolation, no-bypass safety, no-MT5).
  - `test_research_phase09b.py`: Validates the Phase 09B Strategy Research / Backtest / Validation Engine (dataset causality/provenance, future-leakage defense, deterministic candidate identity/versioning, deterministic friction-aware backtest, temporal folds, purge/embargo, OOS gate rejection on OOS failure, robustness stress, multi-dimension scoring + small-sample protection, lifecycle, versioning immutability, safety, worker isolation/restart, full-pipeline non-promotion).
  - `test_model_lifecycle_phase10.py`: Validates the Phase 10 Controlled Model Training / Challenger Engine (dataset reproducibility/provenance/temporal-order/no-future-leakage/schema-identity, wins+losses+neutral representation, 50D + future-schema compatibility, dimension/scaler mismatch rejection, OOS/robustness/drawdown/collapse gate failures, Champion hash invariance, rejected-challenger-cannot-become-champion, lineage immutability, worker isolation/cancellation/restart, Phase 08/09 regression).
* **Integration Tests (`tests/integration/`):**
  - `test_signal_pipeline_health.py`: End-to-end signal pipeline health verification.
  - `test_database_execution_audit.py`: Validates SQLite WAL persistence and ledger autopsies.
  - `test_accounting_api.py`: Validates the Phase 08 accounting REST API + worker wiring end-to-end.
  - `test_intelligence_api.py`: Validates the Phase 09 intelligence REST API + LiveEngine wiring end-to-end (timeline, autopsy, behavior, evolution, worker restart-safety, no-MT5).
  - `test_research_api.py`: Validates the Phase 09B research REST API + LiveEngine wiring end-to-end (summary, registry, discover, validate, self-heal, worker restart-safety, no-MT5 / no-risk-engine exposure).
  - `test_model_lifecycle_api.py`: Validates the Phase 10 model-lifecycle REST API + LiveEngine wiring end-to-end (summary, models, champion, challengers, runs, comparisons, worker start/stop/cancel, no-MT5 / no-risk-engine exposure).
  - `test_playwright_e2e.py`: Playwright end-to-end web visualizer verification. 🟢 VERIFIED

### ⚙️ CI/CD Workflows (`.github/workflows/`)

* `ci.yml`: Runs Python 3.11 environment, Ruff linter/formatter check, Mypy type validation, and Pytest with code coverage reports. 🟢 VERIFIED
* `docker.yml`: Builds multi-stage Docker container and publishes to GitHub Container Registry (GHCR). 🟢 VERIFIED
* `security.yml`: Executes GitHub CodeQL SAST and Trivy container vulnerability scanning. 🟢 VERIFIED
* `release.yml`: Automates releases on GitHub tag creation (`v*`). 🟢 VERIFIED

---

## 18. Documentation vs. Reality Audit Matrix

This matrix explicitly audits claims made in prior documentation against actual codebase evidence:

| Claimed Feature / Parameter | Documented Claim | Actual Repository Evidence | Forensic Status | Forensic Finding |
| :--- | :--- | :--- | :--- | :--- |
| **Feature Tensor Dimension** | 50D Feature Vector | `NUM_FEATURES = 50` in `scalp_features.py` and `walk_forward_trainer.py` | 🟢 VERIFIED | Exactly 50 float features computed. |
| **Max Lot Size Limit** | Hard max lots = 2.0 | `HARD_MAX_LOTS = 10.0` in `order_manager.py` | 🔴 CONTRADICTED | Code was upgraded to allow up to 10.0 lots on equity >= $10,000. |
| **Model Output Classes** | 4 Classes (`0, 1, 2, 3`) | `ScalpNet` output head `num_classes = 4` (`NO_TRADE`, `BUY`, `SELL`, `WAIT`) | 🟢 VERIFIED | ScalpNet head is 4-class; labeler outputs 3 classes mapped dynamically. |
| **Hot Path Latency** | Guaranteed 50ms execution | No hard real-time latency guarantee in code | 🟡 PARTIALLY VERIFIED | 50ms is an async event-loop target, not a real-time hardware SLA. |
| **Position States** | 11 Position lifecycles | `PositionState` enum defines exactly 11 states in `order_manager.py` | 🟢 VERIFIED | 11 explicit lifecycles managed in hybrid state machine. |
| **Pending Order Protection** | 30s lock & 1.0x ATR drift | `policy.py` checks 30s lock and 1.0 * ATR drift | 🟢 VERIFIED | Prevents high-frequency pending order churn on MT5 terminal. |
| **Legacy Order Manager** | Active order manager | `src/nexus_scalp/features/order_manager.py` was dead/orphaned | 🟢 VERIFIED (REMOVED) | Dead legacy file deleted; active production order manager preserved in `src/nexus_scalp/execution/order_manager.py`. |
| **Legacy CLI Training Script** | Active training script | `src/cli/train_model.py` aligned to 50D feature contract | 🟢 VERIFIED | Updated to pass full 50D feature vector to `WalkForwardTrainer`. |

---

## 19. Code Inventory & Active Wrappers

Active execution entrypoints and wrappers within the repository:

1. **`src/nexus_scalp/execution/order_manager.py` 🟢 VERIFIED:**
   - Active production order lifecycle engine implementing position protection and dispatch routing.

2. **`src/cli/train_model.py` 🟢 VERIFIED:**
   - CLI training script aligned to the 50D feature contract (`feat_0` .. `feat_49`).

3. **`NexusTradingForexBot.py` 🟢 VERIFIED:**
   - Minimal convenience wrapper forwarding execution to `main.py`.

---

## 20. Prioritized Engineering Recommendations (P0-P3)

The following backlog details prioritized architectural and operational recommendations for future development tasks:

### 🔴 P0 — Critical (Capital Safety & Integrity)
1. **Maintain 50D Contract Consistency Across Custom Pipelines:**
   - Ensure all future custom training scripts adhere strictly to `WalkForwardTrainer.NUM_FEATURES = 50`.

### 🟠 P1 — High (Reliability & Architecture Cleanliness)
2. **HTF Warmup Gate Monitoring:**
   - Continue monitoring cold-start hydration times on slow network connections.

### 🟡 P2 — Medium (Performance & Maintainability)
3. **Optimize Polars DataFrame Allocation in Rolling Buffer:**
   - Maintain a pre-allocated rolling Polars DataFrame buffer in `_trigger_async_online_fine_tune()` to reduce memory allocation churn during online fine-tuning.

### 🔵 P3 — Nice-To-Have
4. **Expand Playwright Web Visualizer E2E Test Coverage:**
   - Add automated Playwright interaction tests for rule matrix toggling and configuration updates.
