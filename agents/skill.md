# 🧠 Nexus Scalp Engine (NSE) - Forensic Master Skill & Context Anchor

> **TARGET AUDIENCE:** AI Coding Agents (Cursor, Copilot, ChatGPT, Claude, Jules).
> **PURPOSE:** Authoritative, repository-grounded Master Skill & Architecture Map for the Nexus Scalp Engine repository.
> **SOURCE OF TRUTH:** Actual Executable Codebase (verified via forensic audit).
> **BUG LEDGER:** For historical bug forensics, root causes, evidence, and regression guards, see `agents/bugs.md`.
> **ABSOLUTE DIRECTIVE:** Every update you make to this repo, write a SHORT skill entry here (in this file) so ALL agents inherit it.
> READ-ONLY for codebase files is lifted for `agents/skill.md` and `agents/bugs.md` only — all other files follow the contract gates below.

---

## ⚠️ MULTI-AGENT COMPLIANCE (MANDATORY — READ FIRST)

> This compliance gate is part of the MASTER MULTI-AGENT CONTRACT (v2).
> Full contract: `agents/multi-agent-git-contract.md`. Registries: `agents/contracts.md`,
> `agents/runtime_invariants.md`, `agents/change_control.md`, `agents/taskboard.md`,
> `agents/repository_state.md`, `agents/locks.yaml`, `agents/decisions/`.

### Before coding

- Read `multi-agent-git-contract.md`
- Read `contracts.md`
- Read `runtime_invariants.md`
- Read `change_control.md`
- Read `taskboard.md`
- Read `repository_state.md`
- Inspect git status/log
- Preserve unrelated WIP

### Before completion

- Update required registries
- Add/update regression tests
- Create handoff
- Make coherent agent-labelled commit
- Report exact verification state
- Report unresolved risks

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
15j. [Outcome Correlation, Broker Reconstruction & Break-Even Learning (PHASE 14)](#15j-outcome-correlation-broker-reconstruction--break-even-learning-phase-14)
15k. [Adaptive Model Intelligence — 60D Challenger Path (TASK-5)](#15k-adaptive-model-intelligence--60d-challenger-path--continuous-learning-forensics-task-5-2026-08-18)
15m. [Liquidity Intelligence & 70D Canonical Tensor Contract (TASK-01..07)](#15m-liquidity-intelligence--70d-canonical-tensor-contract-task-0107-2026-08-19)
15. [Known Engineering Pitfalls & Invariants](#15-known-engineering-pitfalls--invariants)
16. [Docker Runtime & Startup Contract](#16-docker-runtime--startup-contract-docker-repair-2026-08-20)
17. [Testing & CI/CD Pipeline Audit](#17-testing--cicd-pipeline-audit)
18. [Documentation vs. Reality Audit Matrix](#18-documentation-vs-reality-audit-matrix)
19. [Code Inventory & Active Wrappers](#19-code-inventory--active-wrappers)
20. [Prioritized Engineering Recommendations (P0-P3)](#20-prioritized-engineering-recommendations-p0-p3)

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
│   ├── entrypoint.sh                  # 🟢 Container entrypoint (env-validate → bootstrap → db migrate gate → exec)
│   └── healthcheck.sh                 # 🟢 Container health check (GET /health verdict READY|DEGRADED)
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
├── .env.example                     # 🟢 Environment contract (safe dev defaults, no secrets)
├── .dockerignore                    # 🟢 Build-context exclusions
├── docker-compose.yml               # 🟢 Canonical compose stack: core + redis (SQLite, no postgres)
├── Dockerfile                       # 🟢 Multi-stage non-root container (builder → runtime)
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
| `src/nexus_scalp/models/scalp_net.py` | Models | PyTorch Deep Neural Network | `ScalpNet`, `CausalConv1d`, `SinusoidalPositionalEncoding` | `LiveEngine`, `WalkForwardTrainer` | `torch.nn` | 50D Tensor `(B, 50)` (serving champion); 70D tensor `(B, 70)` for scalp_v3 candidates | 4 Logits `(B, 4)` | 🟢 VERIFIED | Medium |
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
* **Regime Classifier calibration (BUG-132, 2026-08-21):** thresholds are XAUUSD-evidenced from 100k real M1 bars (data/raw/XAUUSD_M1.parquet); see agents/bugs.md BUG-132. Five regimes (RANGING_MEAN_REVERSION, TRENDING_MOMENTUM, VOLATILITY_EXPANSION, HIGH_SPREAD_CHOP, MACRO_NEWS_FREEZE) — NOT 10. `tick_velocity` is a feed-activity CONTEXT field, NOT a volatility proxy; VOLATILITY_EXPANSION is price-based (rv_5m). The hysteresis gate requires the confidence margin only when ESCALATING into a more-active regime, so special regimes are reachable and non-absorbing. Recalibration probe: scratch/calibrate_regime_realdata.py. Tests: tests/unit/test_regime_calibration_bug132.py.

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

The 50D feature vector (`src/nexus_scalp/features/scalp_features.py`) is computed on every tick from M1 completed bars and incoming tick price action. The canonical contract is the **executable `FEATURE_NAMES` tuple** (line 147) — the table below is the VERIFIED contract as of 2026-08-18 (forensic audit, BUG-082; see `tests/unit/test_scalp_features_forensic_bug082.py`).

| Index | Feature Name | Source | Lookback / Formula | Normalization / Bounds |
| :---: | :--- | :--- | :--- | :--- |
| `0` | `upper_wick_ratio` | M1 Bar | `(High - body_top) / range`, `body_top = max(Open, Close)`, `range = max(High-Low, 0.01)` | `[0,1]` then clip `[-3,+3]` |
| `1` | `lower_wick_ratio` | M1 Bar | `(body_bottom - Low) / range` | `[0,1]` then clip `[-3,+3]` |
| `2` | `body_to_range_ratio` | M1 Bar | `|Open - Close| / range` | `[0,1]` then clip `[-3,+3]` |
| `3` | `is_doji` | M1 Bar | `1.0 if body_ratio <= 0.12 else 0.0` | `{0,1}` |
| `4` | `pinbar_sig` | M1 Bar | hammer `min(2, lw*2)`, shooting star `max(-2, -uw*2)` | `[-2,+2]` |
| `5` | `engulfing_sig` | M1 Bar | bullish `min(2, 1+body_ratio)`, bearish `max(-2, -(1+body_ratio))` | `[-2,+2]` |
| `6` | `close_location_value` | M1 Bar | `((C-L)-(H-C))/range` | `[-1,+1]` |
| `7` | `consecutive_momentum_count` | M1 Bar | `clip((consecutive_count * dir)/5, -1, 1)` over last 10 bars | `[-1,+1]` |
| `8` | `norm_displacement` | Tick+Bar | `(mid - last_close)/max(ATR14, 0.20)` | Z-score-like, clip `[-3,+3]` |
| `9` | `rapid_reversal_spike_val` | Tick+Bar | `1.0 if |disp| > 0.6*ATR and disp*logret < 0 else 0.0` | `{0,1}` |
| `10` | `dist_to_swing_high_20` | M1 Bar | `(max(H[-20:-1]) - mid)/ATR` | clip `[-3,+3]` |
| `11` | `dist_to_swing_low_20` | M1 Bar | `(mid - min(L[-20:-1]))/ATR` | clip `[-3,+3]` |
| `12` | `price_compression_flag_ratio` | M1 Bar | `clip(range5/range20, 0, 2)`, +1e-8 floors | `[0,2]` |
| `13` | `extreme_sig` | M1 Bar | `+1 if range_pos>=0.95, -1 if <=0.05`, 50-bar range | `{-1,0,1}` |
| `14` | `stop_hunt_depth` | M1 Bar | penetration depth of liquidity sweep / ATR | clip `[-3,+3]` |
| `15` | `liquidity_sweep_signal` | M1 Bar | `+1` low-sweep reclaim, `-1` high-sweep reject | `{-1,0,1}` |
| `16` | `session_tokyo` | Tick UTC hour | `0 <= hour < 8` | `{0,1}` |
| `17` | `session_london` | Tick UTC hour | `7 <= hour < 15` | `{0,1}` |
| `18` | `session_ny` | Tick UTC hour | `13 <= hour < 21` | `{0,1}` |
| `19` | `session_overlap_london_ny` | Tick UTC hour | `13 <= hour < 15` | `{0,1}` |
| `20` | `lag_1_log_return` | M1 Close | `ln(C[-2]/C[-3]) * 100` | clip `[-3,+3]` |
| `21` | `lag_2_log_return` | M1 Close | `ln(C[-3]/C[-4]) * 100` | clip `[-3,+3]` |
| `22` | `lag_3_log_return` | M1 Close | `ln(C[-4]/C[-5]) * 100` | clip `[-3,+3]` |
| `23` | `lag_1_atr_ratio` | M1 Bar | `TR_lag1 / ATR`, `TR_lag1 = max(H-L, |H-C_prev|, |L-C_prev|)` | clip `[-3,+3]` |
| `24` | `lag_1_volume_z` | M1 Volume | `(V[-2] - mean(V[-21:-1])) / std(V[-21:-1])` (+1e-8 floors) | clip `[-3,+3]` |
| `25` | `lag_1_clv` | M1 Bar | CLV of previous bar `[-1,+1]` | `[-1,+1]` |
| `26` | `fvg_sig` | M1 Bar | `(L[-1]-H[-3])/ATR` bullish, `-(L[-3]-H[-1])/ATR` bearish, threshold `0.20*ATR` | clip `[-3,+3]` |
| `27` | `order_block_type` | M1 Bar | `+1/-1/0` OB classification (× volume/vol_mean = strength) | clip `[-3,+3]` |
| `28` | `choch_sig` | M1 Bar | `+1` CHoCH bull, `-1` CHoCH bear (EMA20/50 + 20-bar swing) | `{-1,0,1}` |
| `29` | `breakout_sig` | Tick+Bar | `+1` mid>H[-1], `-1` mid<L[-1] | `{-1,0,1}` |
| `30` | `norm_tk_diff` | M1 Bar | `(Tenkan - Kijun)/ATR` | clip `[-3,+3]` |
| `31` | `tk_cross_signal` | M1 Bar | Tenkan/Kijun cross: `+1` bull, `-1` bear | `{-1,0,1}` |
| `32` | `kumo_sig` | M1 Bar | `+1` above Kumo, `-1` below, else 0 | `{-1,0,1}` |
| `33` | `norm_kumo_width` | M1 Bar | `(SpanA - SpanB)/ATR` | clip `[-3,+3]` |
| `34` | `norm_rsi` | M1 Close | `(RSI14 - 50)/16.66` — **NOTE: divisor is 16.66 in code, docs previously said /25 (see BUG-082)** | clip `[-3,+3]` |
| `35` | `dist_to_ema_21` | Tick+Bar | `(mid - EMA21)/ATR` (EMA seed=first, alpha=2/(n+1)) | clip `[-3,+3]` |
| `36` | `dist_to_ema_50` | Tick+Bar | `(mid - EMA50)/ATR` | clip `[-3,+3]` |
| `37` | `cross_asset_z_score` | Tick+Bar | rolling 20-bar z-score with current tick appended | clip `[-3,+3]` |
| `38` | `norm_dist_to_tenkan` | M1 Bar | `(Tenkan - Kijun)/(2*ATR)` — exact negation of feat_39 | clip `[-3,+3]` |
| `39` | `norm_dist_to_kijun` | M1 Bar | `(Kijun - Tenkan)/(2*ATR)` | clip `[-3,+3]` |
| `40` | `htf_h4_trend` | H4 agg | EMA3 of H4 closes, `+1`/`-1` | `{-1,1}` |
| `41` | `htf_h1_momentum` | H1 agg | `(H1_close[-1] - H1_close[-2])/ATR` | clip `[-3,+3]` |
| `42` | `htf_m30_structure` | M30 agg | EMA5 of M30 closes, `+1`/`-1` | `{-1,1}` |
| `43` | `htf_m15_confirmation` | M15 agg | engulfing + close-vs-open on last two M15 bars | `{-1,1}` |
| `44` | `support_zone_dist` | S/R fractal | `(mid - nearest_support)/ATR`, fractal window 3, 50-bar | `>= 0`, clip `[-3,+3]` |
| `45` | `resistance_zone_dist` | S/R fractal | `(nearest_resistance - mid)/ATR` | `>= 0`, clip `[-3,+3]` |
| `46` | `feat_ob_valid_bos` | SMC | `1.0` OB BOS, `0.5` CHoCH/break, else 0 | `{0, 0.5, 1}` |
| `47` | `feat_ob_equilibrium_ratio` | SMC | `(ob_price - last_sl)/(last_sh - last_sl)`, clip `[0,1]` | `[0,1]` |
| `48` | `feat_ob_liquidity_swept` | SMC | `1.0` sweep confirmed else 0 | `{0,1}` |
| `49` | `feat_ob_fib_50_60_alignment` | SMC | `clip(1 - |eq_ratio - 0.55|/0.35, 0, 1)` | `[0,1]` |

> **Forensic note (2026-08-18):** the executable contract (above) is the ONLY truth. The historical §5.5 table (returns/log_returns/MACD/Bollinger/ADX/Stoch/OBV/VWAP/spread_norm) never matched the code — no MACD/BB/ADX/OBV/VWAP exist in the 50D at all. `norm_rsi` divisor is 16.66 (not 25), and `feat_38`/`feat_39` are exact negations (corr -1.0 over 215 stored experiences). All 50 dims independently verified: 7 fixtures × 50 = 350/350 PASS, determinism ×100 PASS, causality T-1 PASS, dataset/live replay parity PASS, float32 model-input roundtrip err ≤ 8.6e-8.

#### 🛡️ Feature Safety & Fallback Invariants:
* All 50 calculated features pass through `validate_and_fallback()`.
* If any feature contains `NaN`, `Inf`, or violates bounds, a `FeaturePipelineFrozenError` is logged and deterministic fallback values are applied gracefully. 🟢 VERIFIED
* Runtime `_validate_50d_tensor()` additionally clips to `[-3,+3]` and zero-fills non-finite values before inference (live_engine.py:2932). 🟢 VERIFIED

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
             Clamped by Account Tier Ceiling (code truth,
             risk_engine.py calculate_dynamic_volume Step 6):
             - Equity < $100:     Max 0.02 Lots
             - Equity < $1,000:   Max 0.10 Lots
             - Equity < $10,000:  Max 1.00 Lots
             - Equity >= $10,000: Max 10.0 Lots (HARD_MAX_LOTS / tier cap
                                   min(10.0, symbol volume_max))
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
* **Split-Fill Context Inheritance (BUG-081, 2026-08-18):** entry context is
  staged in a BOUNDED registry keyed by order/request id
  (`_pending_context_registry` + TTL 3600s + capacity 64) instead of a single
  slot, so EVERY sibling ticket of a broker split-fill resolves the SAME
  immutable context (order_id, reason, confidence, regime, setup snapshot).
  Provenance gaps (`NO_STAGED_CONTEXT`) are logged via `[TRADE_LINEAGE]` and
  recorded in `_unbound_ticket_contexts` — never silently confidence 0.0.
  Family contexts prune on final-sibling close (`_prune_bound_context`).
  Regression: `tests/unit/test_bug081_forensics.py`.
* **Broker-Truth Exit Classification (BUG-081):** a stop at entry is
  `RISK_FREE_SL_HIT` / `BREAK_EVEN_SL_HIT` ONLY when the engine proves the SL
  moved (`was_sl_modified=True`); never-moved stops are `HARD_SL_HIT`. Mirrors
  accounting/normalize.py `_classify_stop`. Regression: classifier CASE A-D
  tests in test_bug081_forensics.py.
* **Retention Analytics (BUG-081):** `accounting/retention.py` provides
  `mfe_capture_ratio` / `giveback` / `giveback_ratio` / `cohort_capture_report`
  (MFE<=0 → None, never synthetic 0.0); the reporting insights layer emits an
  MFE-capture insight. Offline R-reach analysis:
  `artifacts/scripts/retention_analysis.py` (measured: +1.0R reach → 97%
  scratch on the Aug-18 cohort — the BE lock squeezes winners).
* **Telegram Consumes the Canonical Outcome (BUG-081):** close notifications
  use `TelegramNotifier.notify_canonical_close(...)` — built from the SAME
  `exit_mechanism` the classifier writes to the ledger (evidence:
  ENGINE_SL_MODIFICATION / BROKER_DEAL_REASON / BROKER_DEAL_COMMENT), never
  re-inferred from the broker reason code, never defaulted to MANUAL.
  `_exit_label()` maps the canonical ExitReason taxonomy to human labels.
  All 3 close-notification call sites in order_manager.py use it; the legacy
  `notify_manual_close` def remains but has NO callers. Regression:
  `tests/unit/test_bug081_telegram_canonical.py`. Live incident that proved
  the gap: ticket 152500222827 (SELL 4358.48, SL 4368.11→4358.15, exit
  4358.17, +$5.27, 44s) — Telegram said "MANUAL POSITION CLOSE DETECTED /
  MT5 Closing Reason Code Unknown"; the broker truth was BREAK_EVEN_SL_HIT.

### 🔧 Phase 15 Exit-Behavior Audit & Repair (2026-08-17)

Forensic audit of "why losing / reversed positions don't exit correctly" — full
evidence package (DB census, flagship reconstructions, log excerpts, code sites)
lives in the `position-exit-forensics` skill. Root causes found and repaired:

1. **Model/regime blindness (BUG-054)** — `probs` and `regime_state` now thread
   from `live_engine` into `manage_active_positions`; the AI direction-flip exit
   is live again; giveback logic uses the CURRENT regime; `[POSITION_EXIT_EVAL]`
   structured logs record every evaluation verdict.
2. **LOSS_HARD_EXIT arbitration gap (BUG-055)** — Level-2 arbitration now emits
   `[EXIT TRACE] LOSS_HARD_EXIT triggered` and dispatches a real broker close
   past the 60s grace.
3. **Clock/age bug** — all durations derive from the CURRENT TICK timestamp
   threaded through the management loop, never the host wall clock (which was
   hours ahead of the broker and produced negative ages that suppressed every
   time-based exit).
4. **Min-loss EV inversion (BUG-056)** — the recovery payoff was growing with
   the loss (`max(15, |pnl|*2)`) while expected remaining loss shrank, so EV
   became MORE positive the deeper the drawdown and the min-loss exit could
   never fire. Now anchored at entry: `expected_recovery = initial_risk * RRR`
   (RRR from `algo_config.min_risk_reward_ratio`, default 1.8), expected
   additional loss = full planned risk. EV decreases monotonically with
   drawdown depth.
5. **Giveback close suppression** — the VOLATILITY_EXPANSION + breakeven-locked
   guard only suppresses a market close when `was_sl_modified` AND the broker SL
   is at/beyond the locked breakeven level; without a locked protective SL the
   giveback close ALWAYS dispatches.

Regression suite: `tests/unit/test_exit_behavior_forensic.py` — 8 behavioral
scenarios (D1..D8: strong reversal, long drawdown, AI flip, regime invalidation,
fast MFE→giveback, early BE→full stop, healthy continuation, isolated sweep) +
4 execution-integrity regressions (R1: hold_score<30 dispatches close; R2:
giveback close not suppressed without locked SL; R3: time-in-trade decay; R4/R4b:
EV breach fires / doesn't fire).

Known limits carried forward: TIME_IN_LOSS_DECAY only fires when >70% of holding
time was in drawdown (conservative by design); `is_winning_trade` profit-shield
can still mask a loser on the legacy S-scenario path (the adaptive path closes
regardless); broker-side 10014/10025 rejection retry semantics unchanged (see
`mt5-broker-integration` skill).

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
| `/api/chart/history` | `GET` | Bootstrap 900-bar visualizer chart (+ engine resync after downtime, BUG-058) | Query `count` (default 900, bounded 1..5000) | OHLC bar array + candidate zones + resync provenance | 🟢 VERIFIED |
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
1. `audit_signals`: Records all generated trade proposals, model probabilities, regime metrics, and rule evaluation payloads. **BUG-054:** persistent, database-enforced dedup via deterministic `signal_dedup_key` (sha256 of symbol|M1-candle|model_action|decision_stage|execution_mode|reason_code) + UNIQUE index + `ON CONFLICT DO NOTHING` — restart/race-safe, no SELECT-then-INSERT on the hot path. Payload is a minimal 8-field forensic JSON (~250B, not the full proposal dump). 🟢 VERIFIED
2. `audit_guard_telemetry`: Lightweight counter table (window_start, symbol, reason_code, count; UPSERT per minute) for high-frequency guard rejections (`TICK_DUPLICATE_SUPPRESSED`, `ORDER_FREQUENCY_THROTTLED`) — answers "how often / when / which symbol / why" at ~40B/event instead of a heavy signal row. 🟢 VERIFIED
3. `audit_orders`: Tracks order lifecycle state transitions (`SUBMITTED`, `OPEN`, `MODIFY_SL_TP`, `CLOSED`). 🟢 VERIFIED
4. `audit_account_snapshots`: High-frequency account balance/equity snapshots (throttled to write only on balance change or >= 60s interval). 🟢 VERIFIED
5. `trading_rules_config`: Stores dynamic enablement states and JSON parameters for 30+ scalping rules. 🟢 VERIFIED
6. `financial_ledger`: Autopsy table recording completed trade performance metrics (MAE, MFE, slippage, SL shift tracking, gross PnL) exactly once upon trade closure. 🟢 VERIFIED

#### 🧹 Retention (BUG-054)
`AuditRepository.purge_old_audit_data()` runs bounded (500-row) batched deletes: signals >7d, POSITION_MOVING >3d, guard telemetry >13d. NEVER touches ledger/experiences/autopsies/research. Manual: `nse audit-purge`. Position lifecycle MOVING events are time+event throttled (≥60s, SL/TP changed, or ≥15% risk drift). 🟢 VERIFIED

#### 📡 Telegram Reporting (BUG-059 → BUG-076: full lifecycle)
`TelegramNotifier` (observability/telegram_notifier.py) sends HTML-formatted,
QUEUE + WORKER-backed alerts with a full observable lifecycle. Every
notification carries `notification_id`/`correlation_id`/`event_type`/`priority`/
`target_class` and logs ENQUEUED → SEND_START → SEND_RESULT/SEND_FAILED →
DELIVERED | FAILED_FINAL (never silent). HTTP 200 is VERIFIED against the JSON
`ok` field (200+ok=false = failure); 429/5xx bounded-retry; 400-class never
retried; explicit error taxonomy (AUTH/TARGET/NETWORK/TIMEOUT/RATE_LIMIT/
SERVER/HTTP/API/SERIALIZATION/QUEUE/WORKER/UNKNOWN) each with retryable+severity+
safe_message. Worker heartbeats every 5s; `health_state()` → READY/DEGRADED/
STOPPED + queue/sent/failed/last_success/last_failure/failure_category.
`get_me()` probe + `send_diagnostic()` labeled test. Templates: startup/stop,
order open/close (profit/loss), break-even, trailing-stop, risk/survival/
kill-switch, error, market summary, test, engine_stopped, engine_error
(CRITICAL), audit_purge, warmup (one-shot), daily_summary (from AccountingCore
PeriodKind.DAY, never synthetic). Wired in live_engine: purge → 6h; warmup →
READY; daily → 24h. Web: `POST /api/telegram/test` returns the REAL worker
verdict (delivered message_id OR category); `GET /api/settings/telegram/status`
+ `/api/observability/stats` expose truthful worker health. Network I/O only in
the worker — Telegram can never block the tick path. Token never logged/leaked
(`_redact_secrets`). 🟢 VERIFIED

#### ⚙️ Isolated Settings Architecture (BUG-077)
`src/nexus_scalp/settings/` is the canonical user/installation settings
subsystem — Telegram credentials NEVER come from live.yaml at runtime.
- `secret_store.py` — `SecureSecretStore`: Windows DPAPI (CryptProtectData via
  ctypes), ciphertext anchored to the OS user; no plaintext, no XOR/base64-key.
- `service.py` — `SettingsDatabase` (isolated `app_settings.db` under
  `%LOCALAPPDATA%\NexusScalpEngine\databases\`, tables `application_settings`/
  `configuration_metadata`/`settings_audit`) + `SettingsService` (precedence:
  SYSTEM DEFAULT < INSTALLATION SETTINGS < SAFE ENV OVERRIDES < RUNTIME HOT).
  Mutability classes: HOT_SAFE / HOT_RESTRICTED / RESTART_REQUIRED /
  INSTALLATION_ONLY / SECRET. Explicit degraded states (SETTINGS_DB_CORRUPT,
  SECRET_UNAVAILABLE, MIGRATION_REQUIRED, ...) — never fake READY.
- Legacy migration: live.yaml telegram.bot_token/admin_id → secure store,
  verified write-back → blanked from YAML; idempotent, restart-safe, failure-safe.
- Web: `GET /api/settings` (masked token), `GET /api/settings/telegram/status`,
  `POST /api/settings/telegram` (persists + hot-rebuilds the notifier),
  `POST /api/settings/validate`; `GET /api/config` masks bot_token.
- CLI: `nexus settings` (masked status + provenance). Doctor: TELEGRAM check.
- LiveEngine: `self.settings_service`; `[TELEGRAM_CONFIG]` startup log with
  enabled/configured/token_present/source; env override (NEXUS_TELEGRAM_*) remains
  the diagnosis escape hatch. 🟢 VERIFIED

#### 🕯️ Candle Intelligence (BUG-061)
Local, isolated, database-backed candle-close analysis + trade-decision module at `src/nexus_scalp/candle_intelligence/`. The candle close is a GATE: close-quality classification (body/wick ratios, close-position, strength, rejection/continuation/reversal/indecision/momentum-decay) decides bullish/bearish continuation, reversal, indecision, trapped/false breakout, exhaustion — weak/contradictory closes block entry and accelerate exit. 29-pattern engine (hammer/engulfing/star/doji/soldiers/harami/cloud/methods/double-top-bottom/H&S/flag/pennant/wedge/triangle/gap) with multi-factor context weights. Rule hierarchy: hard veto → regime → close validation → pattern → risk → execution. Isolated SQLite `artifacts/candle_intel.db` (12 tables, full audit columns, deterministic serialization, no network). Wired into live_engine `_on_new_bar` (each completed M1 bar). Holds no adapter/order manager; advisory only. Config: `candle_intel:` section. 🟢 VERIFIED

---

## 13. Configuration Architecture & Runtime Hot Reload (BUG-126)

### ⚙️ AUTHORITATIVE RUNTIME CONFIGURATION (since 2026-08-20, BUG-126)

```text
UI (Tuner / Config UI)
        │  PUT /api/algo/config · POST /api/config · POST /api/runtime-config/apply
        ▼
Configuration API  (web/server.py)
        │
        ▼
Validation (typed bounds + cross-field, §30)
        │
        ▼
Persistent Config Store  (settings DB — app_settings.db, PersistentConfigStore)
        │
        ▼
Version N+1  (monotonic, RuntimeConfigStore)
        │
        ▼
ConfigurationChanged  (ConfigChangeEvent bus)
        │
        ▼
Runtime Config Snapshot — IMMUTABLE (frozen RuntimeConfiguration)
        │  atomic swap (lock-free reads; old snapshot finishes in-flight work)
        ▼
Strategy · Risk · Execution · Rule Matrix · News · Model services
        │  LiveEngine._sync_runtime_config() per tick + on every apply
        ▼
All NEW evaluations use the new snapshot (no restart, same PID)
```

**SOURCE OF TRUTH:** `nexus_scalp/configuration/runtime_config.py` →
`RuntimeConfigStore` + frozen `RuntimeConfiguration` snapshot. The settings DB
(`application_settings` table) is the persistent store; the in-memory immutable
snapshot is the runtime authority. `live.yaml` is **BOOTSTRAP / IMPORT / EXPORT /
COMPATIBILITY ONLY** — never re-read by the engine after startup, never the hidden
runtime authority (BUG-126 root cause).

**Apply pipeline (§27):** REQUEST → VALIDATION (per-field bounds + cross-field
`risk.risk_per_trade_pct <= risk.max_account_drawdown_pct`) → PERSISTENCE →
VERSION++ → BUILD IMMUTABLE SNAPSHOT → PUBLISH `ConfigChangeEvent` → ATOMIC
SWAP → CONFIRM (`ConfigurationApplyReport`: success / persisted / runtime_applied /
version / correlation_id). Any failure rejects the WHOLE request — the last
known-good snapshot stays active (never partial apply, §28/§69/§70).

**Effective scopes (§55):** every setting declares NEXT_DECISION / NEXT_SIGNAL /
NEXT_ORDER / ACTIVE_POSITION / NEXT_SESSION / RESTART_REQUIRED (see audit table
below). Hot reload NEVER retroactively mutates open positions; the smallest safe
scope is used.

**Service re-sync:** `LiveEngine._sync_runtime_config()` re-syncs
`signal_policy.algo_config`, `order_manager.algo_config`, `risk_engine`
(min_risk_reward_ratio / min_rr_high_confidence / high_confidence_threshold /
max_allowed_lots / max_margin_usage_pct / config risk section) and the feature
engine SMC tunables (`_fvg_mitigation_sensitivity`, `_order_block_lookback_bars`)
from the current snapshot — per tick (cheap assignments) and after every apply.
`engine.apply_runtime_update(updates, source=...)` is the single entry point for
web + tests.

**Model artifact hot swap (§31/§32):** `LiveEngine.hot_swap_model(path)` loads +
validates + warms the NEW bundle in isolation, computes a sha256 artifact hash,
then swaps under `_bundle_lock` (in-flight inference finishes on the old bundle).
On any failure the healthy model keeps serving (`MODEL_HOT_SWAP_FAILED` logged).

**Telegram (§34):** changes route via `SettingsService.set_telegram` +
SecureSecretStore (never live.yaml, BUG-072/080); the notifier is rebuilt only
after the new credential set validates (`/api/settings/telegram` + config-route
path). Secrets are masked everywhere (`mask_token`); never logged.

**Boot (§59/§60):** AppConfig from live.yaml = bootstrap only; the settings DB is
hydrated over it (restart persistence / crash recovery — last known-good
returns); version continues monotonically across restarts.

**Endpoints:**
- `GET  /api/runtime-config` — effective runtime snapshot (what the engine
  ACTUALLY uses, incl. configuration_version + diagnostics).
- `GET  /api/runtime-config/diagnostics` — persistent_version vs runtime_version
  vs live.yaml hash + mismatch flag.
- `POST /api/runtime-config/apply` — unified apply `{"updates": {...}}`.
- `POST /api/runtime-config/model-swap` — model artifact hot swap.
- `PUT  /api/algo/config` — Algorithm Live Tuner save → runtime store +
  live.yaml projection (export). Returns version + runtime_applied.
- `POST /api/config` — execution/risk/model sections → runtime store;
  telegram via settings service; live.yaml written as compatibility projection.

**LIVE-TUNABLE PARAMETER MATRIX (§54/§81):**

| Setting | Persistent | Validated | Versioned | Hot Reload | Runtime Consumer | Method Consumer | Effective Scope | Restart | Tested |
|---|---|---|---|---|---|---|---|---|---|
| algo.atr_sl_buffer_multiplier | ✅ settings DB | ✅ 0.5–4.0 | ✅ | ✅ | SignalPolicy, OrderManager, RiskEngine | SL buffer math (policy 585/606/929/1348, OM 2865) | NEXT_SIGNAL | no | ✅ |
| algo.min_risk_reward_ratio | ✅ | ✅ 1.0–5.0 | ✅ | ✅ | SignalPolicy, RiskEngine | RR gate (policy 620/943/997/1360, risk 374) | NEXT_ORDER | no | ✅ |
| algo.ai_zone_confidence_threshold | ✅ | ✅ 0.50–0.99 | ✅ | ✅ | SignalPolicy | zone-quality gate (policy 698/1292) | NEXT_SIGNAL | no | ✅ |
| algo.fvg_mitigation_sensitivity | ✅ | ✅ 0.1–1.0 | ✅ | ✅ | ScalpFeatureEngine | FVG formation threshold (compute_from_bars) | NEXT_SIGNAL | no | ✅ |
| algo.order_block_lookback_bars | ✅ | ✅ 10–100 | ✅ | ✅ | ScalpFeatureEngine | SMC swing scan window | NEXT_SIGNAL | no | ✅ |
| execution.symbol / timeframe / magic_number | ✅ | ✅ | ✅ | ⚠️ runtime-applied but adapter restart needed | LiveEngine | — | RESTART_REQUIRED | yes | ◐ |
| execution.max_slippage_points | ✅ | ✅ | ✅ | ✅ | OrderManager | slip checks | NEXT_ORDER | no | ◐ |
| risk.max_account_drawdown_pct | ✅ | ✅ + cross-field | ✅ | ✅ | RiskEngine/engine stop logic | drawdown gate | NEXT_DECISION | no | ✅ |
| risk.risk_per_trade_pct | ✅ | ✅ + cross-field | ✅ | ✅ | RiskEngine | `calculate_position_size` (424/549) | NEXT_ORDER | no | ✅ |
| risk.max_concurrent_positions | ✅ | ✅ | ✅ | ✅ | RiskEngine | portfolio gate (300) | NEXT_ORDER | no | ◐ |
| risk.max_spread_points | ✅ | ✅ | ✅ | ✅ | RiskEngine | spread gate (362) | NEXT_ORDER | no | ✅ |
| risk.max_allowed_lots | ✅ | ✅ | ✅ | ✅ | RiskEngine | exposure clamp (max_allowed_lots) | NEXT_ORDER | no | ◐ |
| risk.enforce_stop_loss | ✅ | ✅ | ✅ | ✅ | RiskEngine/OrderManager | stop enforcement | NEXT_ORDER | no | ◐ |
| model.confidence_threshold | ✅ | ✅ 0–1 | ✅ | ✅ | SignalPolicy (via sync) | confidence gate | NEXT_SIGNAL | no | ◐ |
| model.model_artifact_path | ✅ | ✅ | ✅ | ✅ hot swap | LiveEngine bundle | `hot_swap_model` | NEXT_SIGNAL | no | ◐ |
| telegram.enabled / bot_token / admin_id | ✅ (secret store) | ✅ | ✅ | ✅ | TelegramNotifier rebuild | notifier swap | NEXT_SESSION | no | ◐ |

**Tests:** `tests/unit/test_runtime_config_hot_reload.py` (§65/§68: same
deterministic op before/after save → output changes, PID unchanged, event
emitted, invalid/cross-field/unknown rejected, live.yaml file-edit does NOT
change runtime, restart restores persisted values) +
`tests/unit/test_runtime_engine_hot_reload.py` (RiskEngine spread/lot/RR gates,
SignalPolicy SL buffer, FeatureEngine FVG/OB — all change with the snapshot).

**BUG-126** (bugs.md) documents the full root-cause chain: live.yaml as hidden
authority, decorative tuner fields with no consumers, startup-only copies.

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
   The SERVING champion remains 50D (`scalp_v1`); the canonical research/dataset
   schema is `scalp_v3` = 70D (Base 0..49 | News 50..59 | Liquidity 60..69,
   see §15m). `scalp_v2` = 60D (candidate-only, Liquidity at 50..59) is FROZEN.
   The old "350D forward-declared" note is OBSOLETE — no 350D artifact ever
   existed; the declaration was superseded (TEST-29).

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


### 📊 Phase 16 — Pro Win/Loss-Rate Reconciliation & Loss-Persistence Intelligence

Account Performance & Intelligence panel now audits the win/loss story from
THREE reconciled angles, all derived in the accounting core (never in JS):

| Metric | Definition | Why it matters |
|--------|------------|----------------|
| `win_rate` | wins / (wins + losses), decided only | classic; the 9.4% BUG-067 headline |
| `loss_rate_decided` | losses / (wins + losses) | complement; always 100 - win_rate |
| `win_rate_all` | wins / ALL trades incl. breakevens | scratches can hide the loss rate |
| `loss_rate_all` | losses / ALL trades | breakeven-heavy samples surface the bleed |
| `pnl_weighted_win_rate` | gross_profit / (gross_profit + gross_loss) | dollar-weighted counterpart |
| `win_rate_denominator` | DECIDED | ALL_TRADES | NONE | explicit source of truth |

Also added: `expectancy_breakeven_incl`, `avg_pnl_per_decided`, `total_costs`
(comm + swap), `cost_drag_pct`, `stop_loss_share` (fraction of losses closed
at a protective stop), `avg_loss_r`, `avg_r_multiple`, `avg_mae_r`,
`avg_mfe_r`, `win_mae_capture_pct`, `loss_efficiency_pct`, `profit_skew`,
`loss_skew`, `avg_hold_sec`, `volume_total`, `commission_total`, `swap_total`,
`avg_risk_usd`, `r_coverage_ratio`.

Both `PeriodReport.to_dict()` and `compute_advanced_metrics()` carry the
same denominators; the dashboard renders an explicit `denominator:` badge
under the classic Win Rate card and a Performance Intelligence info-text
block (win/loss reconciliation, cost drag, stop discipline, excursion
quality, breakeven-inclusive expectancy verdict).

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
| `/api/account/performance/intelligence` | GET | Deterministic Performance Intelligence report (structured JSON contract consumed by the Telegram daily report) |

Every endpoint returns REAL data from authoritative tables. When no data
exists the response carries `available`/`has_data` flags and the dashboard
renders an explicit empty state.

### 📊 Performance Intelligence Reporting (`reporting/` package)

The **`nexus_scalp/reporting/`** package is a READ-ONLY enrichment layer over
the canonical AccountingCore — it never writes financial truth, never opens/
closes trades, never modifies risk/model/news gates. It powers the upgraded
Telegram daily report and the `/api/account/performance/intelligence` endpoint.

- `reporting/models.py` — frozen dataclass JSON contract: `ReportContainer`
  with sections (account, performance, distribution, r, excursion, holding,
  exits, streaks, risk, drawdown, strategies, regimes, sessions, model,
  execution, news, behavioral, loss_drivers, profit_drivers, period_compare,
  anomalies, health_score, insights, trend, evidence). `None` = cannot derive
  (honesty rule), never 0.0-as-placeholder.
- `reporting/engine.py` — `PerformanceReportEngine` deterministic multi-stage
  generator (SNAPSHOT -> OUTCOMES -> PROFIT_DECOMPOSITION -> DISTRIBUTION ->
  R_MULTIPLE -> EXCURSION -> HOLDING/EXIT -> STREAK -> RISK -> DRAWDOWN ->
  STRATEGY -> REGIME -> SESSION -> MODEL -> EXECUTION -> NEWS -> BEHAVIORAL ->
  LOSS/PROFIT DRIVERS -> PERIOD_COMPARE -> ANOMALY -> HEALTH -> INSIGHTS).
  Logs `[TELEGRAM_REPORT] event=START/COMPLETE/FAILURE`.
- `reporting/insights.py` — sample-size policy (DO_NOT_RANK <5 / LOW_EVIDENCE
  5-19 / USABLE 20-49 / STRONGER_EVIDENCE 50+), multi-metric trend
  classification (IMPROVING/STABLE/DETERIORATING — never single-metric),
  robust anomaly detection (loss-streak / large-loss share / expectancy
  degradation / latency / concentration), deterministic 0-100 account health
  score (profitability/risk/consistency/execution/strategy_stability, each
  /25, report-only), 13 deterministic insight sentences, session
  classification (ASIAN_TOKYO/LONDON/LONDON_NY_OVERLAP/NEW_YORK/OFF_HOURS).
- `reporting/telegram_format.py` — `format_telegram_daily` (compact MESSAGE 1)
  and `format_deep_report` (deep MESSAGE 2/3) consume the contract; numbers
  are NEVER re-derived in string code. Oversized deep reports split
  deterministically on paragraph boundaries (`_split_telegram_report` in
  live_engine, max 3500 chars, lossless rejoin).
- Wired in `live_engine.py` daily tick: replaces the thin BUG-057 summary
  with compact + deep (split) delivery. Canonical period numbers (trades,
  wins, losses, net PnL, win rate, drawdown) come EXACTLY from
  `AccountingCore.period_report` — the upgrade enriches, never changes truth.
- Tests: `tests/unit/test_performance_report_intelligence.py` (29 tests:
  basic/zero/only-wins/only-losses/mixed/breakeven/MAE-MFE-missing/
  strategy/regime/exit attribution/sample-size/period-compare/anomaly/
  health/deterministic-id/telegram-format/split/json-contract/model-funnel/
  execution/news/session/insights) +
  `tests/integration/test_accounting_api.py::test_performance_intelligence_endpoint`.

### 🗄️ Database Migration System (TASK-10, 2026-08-19)

Automatic, deterministic, idempotent schema evolution for every persistent
domain (audit/news/candle_intel). No DB deletion, no manual SQL, no data loss.

- **Engine**: `src/nexus_scalp/database/` — models/manifest/registry/engine/
  gate. Per-domain independent schema versions (§2); application version ≠
  DB version (§19).
- **Migrations**: versioned + checksummed in `database/registry.py`
  (AUDIT/NEWS/CANDLE-xxxx). Additive-first; indexes via
  `CREATE INDEX IF NOT EXISTS`; columns via guarded `ALTER TABLE ADD COLUMN`;
  destructive changes require operator review (§26/§34).
- **Baseline detection** (§5): legacy DBs without schema_meta are inspected
  and baseline-recorded — never recreated.
- **Startup gate** (§6/§7): `cli/main.py::_run_engine` runs
  `run_startup_migration_gate()` before READY; failure → BLOCKED, engine
  refuses to start. Cheap version check when current (§28).
- **WAL-safe backup** (§29/§30): SQLite streaming backup API captures
  uncheckpointed WAL; backups in `<db>/backups/`.
- **Lock** (§18): OS-level `<db>.migrate.lock` prevents concurrent migration.
- **CLI**: `nexus db status|plan|migrate|verify|migrations|history|repair`
  (+ `--json`), same engine as startup (§24/§25/§53/§54).
- **Update integration** (§21): TASK-9 updater runs the same engine post-install.
- **Health/API** (§38/§39): DATABASE health includes migration state;
  `/api/db/status` exposes per-domain schema/migration/integrity.
- **Developer rule** (§51): NEVER add DDL to bootstrap SQL outside migration
  control — SCHEMA CHANGE → CHANGE-ID → MIGRATION → TEST → MANIFEST → GATE.
- Tests: `tests/unit/test_database_migrations_phase18.py` (TEST-DBM-01..40),
  `tests/unit/test_cli_db_phase18.py`; large-DB probe in
  `scratch/probe_db_migration_scale.py` (100k rows → 0.24s, index 240× faster).

### 🧠 Behavioral & Anomaly Intelligence (TASK-2, 2026-08-18)

Evidence-driven behavioral/anomaly layer, wired end-to-end (BUG-094 FIXED).

- **Detector engine**: `nexus_scalp/intelligence/behavior.py` —
  `BehaviorDetectionEngine.analyze()` now invoked by `IntelligenceWorker._refresh_behavior()`
  (off hot path, bounded 200 trades/cycle, idempotent). Evidence-gated detectors:
  OVERHOLD_LOSER, EXCESSIVE_HOLD_TIME, PROFIT_GIVEBACK, MISSED_BREAKEVEN,
  PREMATURE_BREAKEVEN, MODEL_REVERSAL_IGNORED, REGIME_CHANGE_IGNORED,
  LIQUIDITY_REVERSAL_IGNORED, RISK_DEVIATION, EXIT_CLASSIFICATION_ANOMALY,
  STRATEGY_CONTEXT_LOSS, DUPLICATE_ECONOMIC_OUTCOME (+ legacy EARLY/LATE_EXIT).
  Every flag carries evidence with `threshold`/`actual`/`expected`/`explanation`
  + severity + confidence. NO generic labels (no ACTIVE_TRADER etc.).
- **Thresholds centralized** at module top (`OVERHOLD_MIN_SECONDS`,
  `GIVEBACK_PCT_MIN`, `MISSED_BE_MIN_MFE_R`, `PREMATURE_BE_MAX_MFE_R`,
  `RISK_DEVIATION_TOLERANCE`, ...). Versions `behavior-v1` / `anomaly-v1`;
  bump when semantics change (old analysis stays reproducible).
- **Persistence** (audit_repository): `behavior_analysis` (analysis_key =
  ticket|behavior_version|anomaly_version — idempotent) and `anomaly_events`
  (deterministic anomaly_id for duplicates). Batch driver
  `analyze_canonical_trades()` + `BehaviorAnalysisBackfiller` (bounded,
  offline, drain-queue join at end).
- **Report truth states** (`reporting/`): `behavioral.state` and
  `anomaly_state.state` ∈ NO_DATA (analysis never ran) / CLEAR (analyzed,
  zero flags) / FLAGS_FOUND / ANOMALIES_FOUND / ANALYSIS_FAILED /
  INSUFFICIENT_EVIDENCE. Evidence coverage (complete/partial context) and
  engine versions surface in every report + Telegram. NEVER `n/a (no
  behavioral flags recorded)` / `none detected` by silence.
- **API**: `/api/account/performance/intelligence` returns compact
  `intelligence` block (§23 shape); `/api/intelligence/anomalies` lists
  evidence events.
- **Tests**: `tests/unit/test_behavior_anomaly_intelligence_phase16.py`
  (TEST-BHV-01..20, 26 cases) + integration contracts in
  `test_accounting_api.py` / `test_intelligence_api.py`.
- Hot path invariant: analysis runs ONLY in the 30s intelligence worker
  via asyncio.to_thread — zero DB scans in `_process_tick_pipeline`.

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
| `evidence.py` (TASK-21) | First-class observability contracts: `ResearchGate`, `ResearchRunSnapshot`, `ResearchEvent`, `EvidenceArtifact`, `OutcomeLineage`, `GateStatus` (PENDING/QUEUED/RUNNING/PASSED/FAILED/SKIPPED/BLOCKED/ERROR/CANCELLED), `RunStatus`, `WorkerHealth`, `FailureClass` (TECHNICAL vs RESEARCH vs DATA). `build_run_snapshot()` captures reproducibility fingerprint (spec 9/45/64). |
| `observability.py` (TASK-21) | `ResearchObservabilityStore` — persistence facade for gates/events/evidence/snapshots + worker heartbeat (`beat()` + `worker_health()` HEALTHY/DEGRADED/STUCK/FAILED) + queue census + failure heatmap + family analytics + one-click `trace()`. In-memory gate cache makes start/finish async-queue-safe. |
| `strategies/` (PHASE 15C) | Seedable built-in bar-based strategy engines: `base.py` (Strategy protocol + `StrategySignal` + content-addressed candidates), `ichimoku.py` (Ichimili "Final" + "Spaced" variants translated from Pine, pure signal generators, NO order authority), `seeder.py` (`seed_builtin_candidates()` — idempotent registry upserts that preserve existing validation results). Registered automatically; `builtin_candidates()` produces deterministic `StrategyCandidate`s for backtest/walk-forward/OOS like any discovered candidate. Worker step `seed` runs before dataset/discovery each cycle. |

### 🔒 Safety Contract (PHASE 09B)

- Research NEVER places, modifies or closes an order: the package holds no
  adapter, no order manager and no risk engine (verified by tests).
- A candidate NEVER becomes LIVE automatically. Only
  SHADOW/VALIDATED → ACTIVE via a deliberate operator-gated `approve_for_live()`.
- A strategy that fails OOS is REJECTED regardless of in-sample / win rate.
- A modified strategy is a NEW VERSION and must be revalidated.
- 50D (`scalp_v1`) works today; 60D/350D are supported at the schema/provenance
  layer via `feature_schema_id` + `feature_dimension` per candidate/registry row.

### 🔍 Research Observability & Evidence Traceability (TASK-21, 2026-08-20)

The Strategy Research Engine is a fully observable, phase-by-phase research
laboratory. Every candidate has a complete, immutable, inspectable lifecycle:

- **First-class gates**: `STATIC_VALIDATION → BACKTEST → WALK_FORWARD → OOS →
  ROBUSTNESS → SCORING`, each a `research_gates` row with explicit status
  (PENDING/QUEUED/RUNNING/PASSED/FAILED/SKIPPED/BLOCKED/ERROR/CANCELLED) and
  `failure_class` (TECHNICAL vs RESEARCH vs DATA).
- **Immutable runs**: each validation attempt gets a unique `RUN-…` id; runs are
  append-only, never overwritten (spec 8/63 idempotency via research hash).
- **Reproducibility**: `research_run_snapshots` captures strategy definition
  hash, dataset id+hash, schema, model version, seed, config hash and engine
  version at run start (spec 9/45).
- **Persisted timeline**: every gate event lands in `research_events` (no fake
  timestamps; ordered by insertion id).
- **Evidence vault**: every gate stores an immutable `research_evidence`
  artifact (content-hash addressed, `EV-…`).
- **Blocked reasons explicit**: `_registry_blocked_reason()` resolves WHY a
  DISCOVERED strategy has not moved (gate/status/reason/required).
- **Registry invariants**: `StrategyRegistry.invariant_check()` — VALIDATED
  requires ALL gates PASSED + score verdict VALIDATED; REJECTED requires a
  failed gate (never the default for unprocessed strategies).
- **Worker heartbeat**: `research_worker_heartbeat` + `worker_health()`
  classification HEALTHY/DEGRADED/STUCK/FAILED (spec 29/30).
- **Queue observability**: `queue_snapshot()` census + `api/research/queue`.
- **No auto-promotion maintained**: VALIDATED never becomes ACTIVE
  automatically (invariant verified by tests).

### 🗄️ New Canonical Tables (all in `audit.db`, SQLite WAL)

- `strategy_registry` — enduring validation truth keyed by
  `(strategy_id, strategy_version)` UNIQUE; preserves backtest / walk-forward /
  OOS / robustness / score / confidence / lifecycle / lineage. Independent of
  the current model file (survives model rebuilds + schema-width changes).
- `research_runs` — append-only record of every validation run (reproducibility); now carries status/run_outcome/snapshot_id/gates/completed_at (TASK-21).
- `research_worker_state` — restart-safe worker checkpoint.
- `research_gates` (TASK-21) — first-class gate rows (gate_id/strategy_id/run_id/gate_type/status/started/completed/duration/config_version/dataset_version/engine_version/result/failure_reason/failure_class/evidence_id/retryable/order_index).
- `research_events` (TASK-21) — persisted gate timeline (event_type/message/payload/occurred_at; ordered by insertion id).
- `research_evidence` (TASK-21) — immutable evidence vault (evidence_id/content_hash/kind/dataset_version/engine_version).
- `research_run_snapshots` (TASK-21) — reproducibility fingerprint per run (strategy_definition_hash/dataset_hash/model_hash/random_seed/configuration_hash/research_hash).
- `research_worker_heartbeat` (TASK-21) — worker heartbeat row feeding health classification.

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
| `/api/research/detail/{strategy_id}` | GET | TASK-21 one-click trace: runs/gates/events/evidence/snapshot + blocked-reason + invariant |
| `/api/research/trace` | GET | TASK-21 trace by strategy_id / research_run_id / gate_id / evidence_id |
| `/api/research/gates` | GET | TASK-21 first-class gate list (status/reason/evidence/class) |
| `/api/research/events` | GET | TASK-21 persisted gate timeline |
| `/api/research/evidence` | GET | TASK-21 immutable evidence vault listing |
| `/api/research/worker` | GET | TASK-21 worker heartbeat + health classification |
| `/api/research/queue` | GET | TASK-21 gate queue census (queued/running/last-errors) |
| `/api/research/analytics` | GET | TASK-21 failure heatmap + family analytics |
| `/api/research/preflight` | GET | TASK-21 validation pre-flight (data/registry/candidate checks, never starts a run) |
| `/api/research/retry-gate` | POST | TASK-21 safe retry of TECHNICAL/DATA failures (RESEARCH failures never retried) |
| `/api/research/cancel` | POST | TASK-21 cancel a run -> CANCELLED (never FAILED), preserves completed gates |
| `/api/research/diagnostics` | GET | TASK-21 final debug view: worker health/queue/heatmap/blocked gates |

### 🧪 Phase 09B Tests

- `tests/unit/test_research_phase09b.py` — 45 tests covering dataset causality,
  provenance, future-leakage defense, deterministic identity/versioning,
  deterministic backtest + friction, temporal folds, purge/embargo, OOS gate
  (in-sample success + OOS failure ⇒ REJECTED), robustness stress, multi-dim
  scoring + small-sample protection, lifecycle, versioning immutability, safety
  (no RiskEngine/OrderManager/MT5), worker isolation/restart, and full-pipeline
  non-promotion to ACTIVE.
- `tests/unit/test_research_observability_phase21.py` — 24 tests covering gate
  creation/status lifecycle/failure-class, persisted timeline, evidence vault
  immutability, append-only runs, reproducibility snapshots, blocked-reason
  resolution, registry invariants (VALIDATED/REJECTED), worker heartbeat
  HEALTHY/STUCK, queue census and end-to-end lifecycle (VALIDATED never ACTIVE,
  rejection path, blocked path, one-click trace).
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

## 15e1. Live Model Governance & Shadow Runtime (TASK-6 / CHG-0003)

> TASK-6 owns the boundary between a validated model and LIVE execution.
> The `governance/` package (new) is observability-only: it imports NO
> order manager / risk engine / adapter and can never place, modify or
> close an order (INV-002/003/014).

### Key modules
- `governance/load_gate.py` — deterministic 10-gate model load gate
  (ARTIFACT_EXISTS..LIFECYCLE_ALLOWS_SHADOW..LOAD); exact failing gate
  reported, never silent fallback (spec 4).
- `governance/alignment.py` — same-input 50D→60D/72D alignment with the
  REAL 10 scalp_v2 extras from `features/schema_augment.py` (TASK-5);
  zero-fill of extras is REJECTED; NEWS_CONTEXT_HASH canonical identity.
- `governance/shadow_runtime.py` — bounded, failure-isolated parallel
  comparison: same feature vector, latency budget, parity telemetry,
  drop/timeout counters (spec 10/11/12/13/36).
- `governance/engine.py` — truthful registry reconciliation + promotion
  state machine (RESEARCH..CHAMPION, operator-approved, SHADOW→CHAMPION
  illegal) + rollback (evidence preserved).
- `governance/evidence.py` — eventual outcome linkage, live calibration
  buckets (Brier/ECE), drift alerts, backtest-vs-live divergence.
- `governance/store.py` — append-only model_governance_events / _state /
  _shadow_comparisons / _runtime_health in canonical audit.db.
- `governance/reporting.py` — canonical Telegram MODEL SHADOW UPDATE
  (never claims ready unless READY_FOR_REVIEW).

### Wiring
- LiveEngine: `governance_store` + `governance_engine` +
  `_governance_shadow`; startup registry truthfulness sync; periodic
  health snapshot (~5 min, off hot path); `_record_shadow_decision`
  runs the governance comparison on the SAME 50D vector (spec 8).
- Attach endpoint runs the load gate BEFORE loading a Challenger;
  MODEL_LOAD_REJECTED with failing_gate is returned.
- Golden fixtures: `tests/golden/golden_50d.json`, golden_60d_extras,
  golden_alignment; generator `scripts/gen_governance_golden.py`.

### Tests
- `tests/unit/test_model_governance_phase16.py` — 29 tests
  (TEST-LG-01..30). `tests/integration/test_model_lifecycle_api.py`
  TestGovernanceAPI — 6 API tests. Golden fixtures protect against
  preprocessing/runtime drift (TEST-LG-16/25/26).

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

### API (15 routes)
GET /api/news, /api/news/latest, /api/news/{id}, /api/news/impact,
/api/news/state, /api/news/sources, /api/news/health,
/api/news/keywords, /api/news/analysis/{id}, /api/news/trades/{trade_id};
POST /api/news/analyze/{id} (async job), /api/news/refresh,
/api/news/self-heal. Disabled subsystem returns available=False (honest, no
synthetic data).

### Keyword analysis dataset (2026-08-18 expansion)
- `src/nexus_scalp/news/analysis/keywords.py`: deterministic dataset of 189
  keywords across 8 categories (currency/asset/institution/macro/geopolitics/
  energy/directional/fx_pair). Each keyword carries topics, XAUUSD direction
  bias, weight, aliases, negatives ("GOLD MEDAL" != gold). Pure functions,
  no I/O.
- Coverage analytics: `analyze_keyword_coverage()` returns per-keyword
  article hits / mentions / share / direction distribution over dict rows OR
  NewsArticle models.
- Feed rows carry `keyword_hits` (per-article explainability); the News tab
  has a Keyword Analysis Dataset panel (stats + search/filter table).

### Web UI
News Intelligence tab (#tab-news): live feed, state badge
(NORMAL/ELEVATED/HIGH_IMPACT/CONFLICTED/BREAKING/STALE), XAUUSD relevance,
bullish/bearish scores, active-event count, "Analyze with AI" button
(async, LOCAL/API/COMBINED status), Fetch News trigger, Keyword Analysis
Dataset panel (dataset size, articles scanned, total mentions, active
keywords, bull/bear/neutral distribution, searchable keyword table).

### UI error contract + canonical served bundle (BUG-079, 2026-08-18)
- ONE canonical web source: `Web/` at repo root. The runtime serves it via
  `FileResponse` (per-request file read), so frontend edits are live without
  an engine restart; the packaged release must reproduce it byte-identical.
- Served-bundle identity: `GET /app.js` carries `X-UI-Bundle-Sha256` +
  `X-UI-Bundle-Source` (REPO|PACKAGED) once per process (UI forensics).
- Frontend never swallows API failures: every loader emits
  `[UI_API] endpoint=... event=REQUEST/SUCCESS` and
  `[UI_ERROR] component=... status=...` on failure, with a visible DOM error
  state (never a silent `--`/empty panel).
- Research registry score decoding is defensive (`safeScoreObj`/`safeScore`):
  absent, literal `"null"`, `{}`, valid, or malformed score JSON can never
  crash the panel (BUG-075/079).
- News feed marks unanalyzed articles `PENDING` (honest); only analyzed rows
  show direction/importance/relevance.
- Release freshness: `build_release.ps1` stamps `web_asset_hash`/
  `web_index_hash` (SHA-256 of source Web/app.js + index.html) into
  build-info.json; `verify.py::_asset_web` FAILS a stale bundle.

### Safety invariants (tested)
- News can never force BUY/SELL (action unchanged by gate)
- News can never bypass RiskEngine/OrderManager/exposure protections
- No per-tick DB query (context cached; worker refreshes off-loop)
- Worker/DB failure never stops trading (safe defaults, failure-isolated)
- No fake confidence: empty evidence -> available=False, confidence 0.0
- Self-healing rebuilds derived state from authoritative raw records only

### Tests
- tests/unit/test_news_phase12.py — 66 tests: ingestion, dedup, decay,
  local analysis, external-AI fallback, gate safety, memory, worker, DB
  idempotency, self-heal, engine, regressions.
- tests/unit/test_news_keywords_dataset.py — 17 tests: dataset size >= 150,
  determinism, categories, direction bias sets, aliases + negatives
  suppression, dict-row support, coverage math, per-article hits,
  local-analyzer alignment.
- tests/integration/test_news_api.py — 26 tests: real API data, state,
  health (no synthetic), detail, async analyze, refresh, self-heal,
  sources, keywords endpoint + filters, feed keyword_hits, disabled
  availability, worker isolation, DB independence, analysis persistence,
  trade links.

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

## 15i. News ↔ Model Bridge + Real-News Benchmark Readiness (PHASE 13B CONTINUATION)

> STATUS (2026-08-17): The Phase 13B News Forensic upgrade COMPLETED the
> previously-missing connection between the News Intelligence subsystem
> (Phase 12) and model generation (Phase 13). The original A/B/C/D benchmark
> is retroactively classified **SYNTHETIC_NEWS_BENCHMARK** (its 10-row news
> fixture made `NEWS_INCONCLUSIVE` meaningless). A real news benchmark is
> BLOCKED until the readiness gate is GREEN.

### Bridge (`src/nexus_scalp/model_generation/news_bridge.py`)
- `normalize_news_frame()` — coerces engine/DB rows into the canonical
  12-field `NewsContextSchema` numeric matrix; categorical encoding
  (`news_state`: NORMAL=0…STALE=5; `novelty`: NEW=0…STALE=4); aliases
  (`bullish_score`→`bullish_pressure`, `active_event_count`→
  `active_high_impact_events`); NaN/Inf replaced with safe 0.0 (BUG-043);
  unknown categoricals → explicit 0.0 (never NaN/random).
- `news_context_at()` — causally-correct snapshot: only events published
  **at or before T** enter; the LATEST prior event defines the vector
  (sorted before `tail(1)` — duplicate timestamps deterministic);
  `time_since_event_sec` computed per-sample (T − published_at);
  `_safe_epoch_sec` Windows-safe (BUG-044: Polars scalar / numpy
  datetime64 / ISO string / naive UTC).
- `build_news_frame_from_db()` — exports `news_analysis` (+ impacts JSON,
  article timestamps) into the normalized frame; invalid/missing values
  degrade safely to 0.0; multiple sources preserved.
- `news_quality_diagnostics()` + `news_benchmark_readiness()` — real
  computed metrics (total/valid/invalid rows, XAUUSD-relevant, non-neutral,
  distinct events, dead-zero fields, per-field stats) and the strict gate
  (spec 20): real DB exists, analysis records > 0, non-neutral > 0,
  XAUUSD > 0, ≥ 2 event timestamps, no-synthetic (≥ 2 distinct vectors),
  schema valid (core fields not structurally dead).

### Wiring
- `SampleFactory.news_context_at` delegates to the bridge.
- `DatasetFactory.build` records news provenance in the manifest
  (`news_version`, `news_data_range`, content digest) and folds news content
  into the deterministic dataset id — news changes ⇒ new dataset identity
  (never overwrites the old synthetic artifact).
- CLI `model-dataset-build --with-news --news-db <path>` exports the real
  DB, runs the readiness gate, warns loudly on empty/no-data; never
  silently fakes news.

### Readiness gate artifacts
- `scripts/news_readiness_report.py` → `artifacts/model_generation/
  news_benchmark_readiness.{json,md}` — currently RED (no real news.db yet).
  The A/B/C/D benchmark MUST NOT run while RED (spec 22).

### Tests
- `tests/unit/test_news_bridge_phase13b.py` (11) +
  `tests/unit/test_news_bridge_contract_phase13b.py` (19) — schema
  completeness, dead-zero detection, categorical encoding, causal
  boundaries (T-1/T/T+1), duplicate-timestamp determinism, future-event
  rejection, Windows timestamp safety, NaN/Inf, DB export
  (valid/invalid/empty/missing/multi-source), quality diagnostics,
  readiness gate green/red, provenance + dataset identity.

### Synthetic benchmark retirement (spec 19)
- `model_benchmark_report.{json,md}` + `dataset_manifest.json` now carry
  `classification: SYNTHETIC_NEWS_BENCHMARK` with the dead-zero-field list —
  never citable as evidence that "news has no value".

---

---

## 15j. Outcome Correlation, Broker Reconstruction & Break-Even Learning (PHASE 14)

> **STATUS (2026-08-17):** Forensic completion of the closed-trade → Experience →
> Strategy-Intelligence path (BUG-045). A closed trade is DATA — WIN / LOSS /
> BREAK_EVEN must ALL reach the ledger; zero PnL is an outcome class, never an
> invalidation. Fully verified by `tests/unit/test_outcome_correlation_phase14.py`
> (26 tests) and a runtime reproduction probe.

### Outcome correlation provenance (`experience/outcome_recovery.py`)

Every closed outcome carries a deterministic `correlation_source` (persisted in
the outcome payload):

- **ORIGINAL_REQUEST** — request_id present at close; key `exp_<request_id>`.
- **POSITION_STATE** — request_id lost (restart/reconciliation); recovered from
  the IMMUTABLE ledger via `ExperienceLedger.get_experiences_by_order_id`
  (matches request_id/decision_id/execution_id/experience_id columns). The
  ledger is the position-state authority for correlation.
- **BROKER_TICKET_FALLBACK** — only the broker ticket exists; deterministic key
  `exp_bt_<ticket>` with explicit provenance (never pretends to be the original
  request id).
- Ambiguity (multiple candidate experiences) → explicit
  `[EXPERIENCE_OUTCOME] event=CORRELATION_FAILED` with diagnostics — never
  silent reuse across tickets.

`ExperienceIntelligenceEngine.record_trade_outcome` attempts recovery BEFORE
declaring INVALID; `correlation_source`/`correlation_detail` survive into the
persisted outcome payload.

### Protective exit taxonomy (`classify_exit_reason`)

Broker DEAL_REASON + SL/TP geometry + `was_sl_modified` + protective context
map every close to exactly one mechanism:

| Mechanism | Meaning |
| :--- | :--- |
| `BREAK_EVEN_SL_HIT` | Stop parked at/above entry (±BE buffer) after a protective move |
| `TRAILING_STOP_HIT` | Protective stop strictly beyond entry (locked profit) / trailing comment |
| `HARD_SL_HIT` | Stop below entry, no protective move |
| `RISK_FREE_SL_HIT` | Stop at/above entry without in-process modification flag (legacy alias) |
| `TAKE_PROFIT_HIT` | TP touched |
| `MANUAL_CLOSE` | Genuine broker client close (DEAL_REASON_CLIENT) with no protective evidence |
| `SYSTEM_CLOSE` / `RECONCILIATION_CLOSE` / `BROKER_CLOSE` / `UNKNOWN` | Non-protective fallbacks, never fabricated |

A broker stop-out is NEVER labeled MANUAL_CLOSE merely because the internal
state machine performed protection logic first. `accounting/normalize.py`
`_MECHANISM_MAP` maps the new strings to the canonical `ExitClassification`
(BREAKEVEN_STOP / TRAILING_STOP / STRATEGY_EXIT).

### Broker outcome reconstruction (`reconstruct_broker_outcome`)

Authoritative realized result comes from the broker DEAL path whenever
available: **all** close deals for a position are aggregated (gross profit,
commission, swap, volume, deal_ids) — partial closes merge into one outcome,
never double-counted. Missing deal evidence is flagged
`reconstruction_source=NONE` — never silently written as zero (zero and unknown
are not equivalent). The typed `BrokerOutcome` (entry/exit, SL/TP timeline,
reason code/comment, durations) is stored in the outcome payload so it survives
position-state cleanup.

### Break-even as a first-class outcome

`OutcomeClass` (WIN/LOSS/BREAK_EVEN) with `BREAKEVEN_R_BAND=0.05` matching the
evaluator's ±0.05 thresholds. A break-even outcome is recorded, decomposed
(strategy/entry/execution/management/exit quality), attributed to the strategy
and counted (`breakeven_count`) — it is NEVER skipped, NEVER INVALID, and never
ignored by strategy memory. Evaluator minimum-sample protection unchanged.

### SL modification timeline survives close

`_entry_sls` is frozen at the OPEN value; broker-side SL advances in
`_last_modify_sl` + `_sl_modified_flags`. Autopsy rows now carry a real
timeline: `initial_sl_price` (at entry) vs `final_sl_price` (at close) —
previously the modification detector overwrote the entry SL, making
initial==final on every row.

### Reconciliation close-loop (`OrderLifecycleManager.reconcile_missed_closes`)

Discovers broker-closed positions missing from internal state (restart gap):
broker history deals + ledger OPENED placeholder + no close + no tracking →
restores the originating request_id from the OPENED row → emits the same
autopsy + experience outcome path. Wired BEFORE the dead-ticket sweep in
`manage_active_positions` (runs even with zero open positions) so the
`_entry_timestamps` guard prevents an async-write race that would double-record
closes. `_reconcile_seen` dedups; fully exception-isolated (learning never
blocks protective execution).

### Idempotency

The `audit_experience_outcomes` UNIQUE `idempotency_key` constraint remains the
authoritative dedup — duplicate close callbacks, reconnect replays and
reconciliation replays collapse to exactly one outcome row. The in-engine
`has_outcome` pre-check is best-effort (races the async queue); the DB is the
source of truth.

### Logging contract (no secrets)

- `[EXPERIENCE_OUTCOME] event=CORRELATION_RECOVERY ticket=... fallback=... status=RECOVERED`
- `[EXPERIENCE_OUTCOME] event=CORRELATION_FAILED ticket=... reason=NO_CORRELATABLE_EXPERIENCE identifiers_available=...`
- `[EXPERIENCE_OUTCOME] event=RECORD_FAILED ticket=... reason=NO_DECISION_SNAPSHOT ...`
- `[POSITION] STRATEGY_ATTRIBUTION strategy_id=... execution_id=... realized_r=...` (existing)
- `[RECONCILIATION] missed close recorded ticket=... exit_mechanism=... pnl=...`

---


## 15k. Canonical Trade Lifecycle / Exit Evidence / Learning Lineage (TASK-3, 2026-08-18)

> **STATUS:** BUG-088 (MT5 DEAL_REASON inversion + broker-outcome double-count)
> and BUG-089 (lifecycle finalize + reversal capture) FIXED. Regression suite
> `tests/unit/test_trade_lifecycle_task3.py` (28 tests). Read-only lineage
> reconstruction: `artifacts/scripts/task3_trade_lineage_forensic.py`.

### Canonical trade identity (reused, NOT duplicated)
- decision/request id = `request_id` (policy) → staged in
  `_pending_context_registry` → every fill sibling binds the SAME order_id
  (BUG-081 family registry) → ledger `order_id` → experience
  `idempotency_key = exp_<order_id>` → outcome `execution_id`.
- Split-fill siblings are NOT separate economic trades: they share the
  parent order/request id and inherit strategy/confidence/regime/setup/
  model schema metadata (BUG-081 fix, verified 2026-08-18).

### Exit classification contract (EXIT_CLASSIFICATION v3)
- `classify_exit_with_evidence()` returns (reason, evidence_source,
  evidence_detail, confidence). Persisted on the closing autopsy row:
  `exit_reason_source`, `exit_evidence`, `exit_reason_confidence`.
- MT5 DEAL_REASON codes (authoritative): CLIENT=0/1/2, EXPERT=3, SL=4, TP=5,
  SO=6, ROLLOVER=7, VMARGIN=8, SPLIT=9. **reason 4 is SL — never TP**
  (BUG-088; the old mapping was inverted).
- BE/trailing labels require `was_sl_modified` proof (BUG-081 rule stays).
- `reconstruct_broker_outcome` dedupes the matched deal by ticket
  (BUG-088) — a deal inside `history_deals` is never summed twice.

### Position lifecycle timeline (BUG-089)
- `OrderLifecycleManager(lifecycle_tracker=...)` — the closing dead-ticket
  sweep calls `finalize_exit(ticket, realized_pnl, realized_r,
  exit_mechanism)` → POSITION_EXITED event with canonical realized values.
- `_observe_positions` propagates (decision_context, trade_id, experience_id)
  into every event; each event carries the originating order id.
- `_capture_reversal_state` snapshots entry probabilities + regime baseline
  and records MODEL_REVERSAL / REGIME_REVERSAL / LIQUIDITY_REVERSAL events
  (bounded 12/ticket), persisted as `reversal_events_json` on the autopsy row
  — "model/regime reversed while open" is reconstructable, never guessed.

### Telegram / accounting / experience consistency
- Telegram close notifications consume the canonical `exit_reason` +
  evidence source string from the classifier result (never re-infer).
- `realized_r` sent to Telegram = net_pnl / initial_risk (bug fixed:
  previously sent risk/price — not a multiple).
- Accounting `normalize.py` computes net PnL once (gross − costs) and
  classifies stops from SL geometry + modification flags (unchanged).

---


## 15l. Database Hygiene / Retention / Legacy Pruning (TASK-11, 2026-08-18)

> **STATUS:** DatabaseHygieneWorker shipped (BUG-099 FIXED). Non-destructive
> by default: AUDIT_ONLY first run; SAFE_CLEAN operator opt-in; AGGRESSIVE
> requires explicit activation. 37 regression tests
> (tests/unit/test_database_hygiene_task11.py). Policy:
> docs/DATABASE_HYGIENE.md; per-table matrix:
> docs/DATABASE_HYGIENE_MATRIX.md.

- Pipeline: OBSERVE → CLASSIFY → PLAN → VALIDATE → CLEAN → VERIFY.
- Tiers TIER-0..8; TIER-0/1 (broker truth, canonical audit) NEVER
  auto-deleted; unknown tables default KEEP (spec §73).
- Duplicate detection uses canonical identities only (idempotency_key,
  article_hash+verified duplicate_of, trade_id, article_id+run_id).
  Split-fill families (same order_id) are PROTECTED, never duplicates.
- Orphan detection reports EXPECTED_ORPHAN/RECOVERABLE/REBUILDABLE/
  CORRUPTION/UNKNOWN; never auto-deletes (3,372 pre-BUG-045 broker-trade
  ledger-gap rows are EXPECTED orphans, preserved).
- Retention: BUG-054 evidence windows (signals 7d, MOVING 3d, guard 13d) +
  candle derived 30d + cache 7d + active-state 1d + news health 90d +
  worker state 30d. Archive-before-delete at
  artifacts/archive/<db>/<table>/<archive_id>.jsonl with sha256 verify.
- Budgets: 200k rows scanned / 2k deleted / 5k archived / 30s / 2s lock —
  global across tables; exceeded → STOP/DEFER.
- CLI: `nexus db hygiene status|plan|run|pause|resume|history` (--json);
  destructive requires `--mode SAFE_CLEAN|AGGRESSIVE_CLEAN --apply`.
- API: `GET /api/db/hygiene` — real sizes/state/plans.
- live_engine calls the worker via asyncio.to_thread at a 6h throttle;
  LIVE execution mode stays conservative; BUSY → DEFER (never force).
- Crash recovery: IN_PROGRESS runs marked INTERRUPTED at startup; a
  destructive batch is never resumed blindly (spec §66).
- Schema DESTRUCTIVE operations (DROP TABLE/COLUMN, index changes) go
  through TASK-10 migrations — the hygiene worker never drops schema.

## 15j. MT5 Broker-Aware Runtime & Dashboard Repair (PHASE 14 COMPLETION)

> STATUS (2026-08-17): The MT5 Account / Market Data / Accounting / Dashboard
> repair is COMPLETE end-to-end. Verified against a REAL MetaQuotes-Demo
> terminal (account 10011755849, live XAUUSD tick + 250 real M1 bars +
> broker-native order_calc_*). Every value now carries SOURCE / TIMESTAMP /
> STATE VERSION / FRESHNESS / PROVENANCE / ERROR STATE.

### Architecture: broker-aware provider layer

```
MT5 TERMINAL
  account_info / terminal_info / symbol_info / symbol_info_tick /
  positions_get / orders_get / history_orders_get / history_deals_get /
  copy_rates_* / copy_ticks_* / order_calc_profit / order_calc_margin
        │
        ▼
src/nexus_scalp/adapters/mt5/diagnostics.py   MT5CallDiagnostic + run_mt5_call()
                                              ([MT5_CALL] operation/status/
                                              duration_ms/error_code/message)
                                              MT5ConnectionState (CONNECTED/
                                              CONNECTING/DISCONNECTED/DEGRADED/
                                              AUTHENTICATION_ERROR/TERMINAL_ERROR/
                                              UNKNOWN + last success/failure)
        │
        ▼
src/nexus_scalp/adapters/mt5/providers.py     Typed snapshots with provenance:
                                              AccountSnapshot (full account_info
                                              surface: login/server/company/
                                              currency/leverage/trade_mode/
                                              trade_allowed/trade_expert/credit/
                                              profit/margin_level/... ),
                                              SymbolSnapshot (SPEC block vs
                                              CURRENT TICK block, tick_stale,
                                              tick_freshness_ms, spread),
                                              BrokerTickSnapshot, PositionSnapshot,
                                              OrderSnapshot, HistoryOrderSnapshot,
                                              DealSnapshot (net_result =
                                              profit-|comm|-|swap|-|fee|),
                                              RateBarSnapshot, TickHistorySnapshot,
                                              BrokerCalcSnapshot
                                              normalize_utc() (datetime / numpy
                                              datetime64 / Polars scalar / ISO /
                                              naive-as-UTC — BUG-044 safe)
                                              validate_ohlc_bars() (dup/descending
                                              ts, finite OHLC, high/low bounds,
                                              non-negative volume)
        │
        ▼
src/nexus_scalp/ports/mt5_port.py             IMT5Port + 12 provider methods
                                              (defaults are honest UNAVAILABLE;
                                              adapters override with real calls)
DirectMT5Adapter + PaperMT5Adapter            provider implementations
        │
        ▼
LiveEngine                                   _account_snapshot cache (5s throttle)
                                             _update_runtime_mode() -> runtime_mode
                                             _last_inference_latency_ms
        │
        ▼
web/server.py                                /api/mt5/status (full broker truth:
                                             connection, account, symbol+tick,
                                             all positions, pending orders,
                                             history orders/deals + net results,
                                             broker calc provenance, terminal)
                                             /api/chart/history (authoritative
                                             copy_rates_* + diagnostics)
                                             /api/live/state mt5{} section
                                             /api/status runtime_mode +
                                             tick_stale + account identity
        │
        ▼
LiveUiState.2 + Web/app.js + index.html      runtime-mode badge, STALE/LIVE
                                             tick badge, account identity row,
                                             inference time, real model metadata
```

### CIRUCIAL INVARIANTS (verified by tests)

1. **Failure is NEVER silent**: every MT5 call reports through
   `run_mt5_call()` → structured `[MT5_CALL]` log (operation/status/
   duration_ms/error_code/error_message). Snapshots carry `error_state`.
   No `except Exception: pass` without logging.
2. **No fake values**: unavailable == None + provenance UNAVAILABLE. The
   dashboard renders "WAITING FOR LIVE STATE"/"—", never fabricated numbers.
3. **Mode honesty**: `runtime_mode` derives from connection + account
   permissions; config `LIVE` + disconnected MT5 renders
   `LIVE_CONFIGURED / MT5_DISCONNECTED` (never LIVE_READY).
4. **Chart at the core**: `/api/chart/history` reads official MT5 rate
   history (copy_rates_from_pos/range, UTC, OHLC-validated); engine bars
   only as explicit `ENGINE_STATE` fallback; diagnostics + `[MT5_CHART]`
   server log.
5. **ALL-account views**: `get_all_positions()` never filters by symbol/
   magic (dashboard + accounting); the classic `get_positions()` keeps the
   bot filter for OrderLifecycleManager (separate ALL / BOT / SYMBOL views).
6. **Off tick path**: account snapshot refresh 5s-throttled; history queries
   are API-triggered and bounded; SSE keeps incremental updates.
7. **Broker calc provenance**: `order_calc_*` results are BROKER_NATIVE;
   mathematical estimates are FALLBACK_ESTIMATE, never claimed exact.
8. **Frontend boot contract**: Web/api_client.js defines window.NX and MUST
   load before app.js (server route `/api_client.js` required — BUG-046);
   no CDN runtime deps (local compiled tailwind.css + vendored FontAwesome —
   BUG-047); every getElementById target exists in index.html (DOM contract
   test); no broken local asset refs.
9. **SSE consumption**: endless-stream endpoints cannot be consumed by sync
   TestClient/ASGITransport (they buffer until completion); tests use a real
   uvicorn socket with bounded reads (BUG-051).

### Real-MT5 smoke evidence (read-only, 2026-08-17)

| Operation | Status | Evidence |
| :--- | :--- | :--- |
| account_info | SUCCESS | login 10011755849, MetaQuotes-Demo, USD, 41003.70 |
| terminal_info | SUCCESS | MetaTrader 5, connected, trade_allowed |
| symbol_info XAUUSD | SUCCESS | 2 digits, spread 0.27 |
| symbol_info_tick | SUCCESS | 4390.51/4390.76, not stale |
| positions_get / orders_get | SUCCESS | 0 / 0 |
| history_orders_get | SUCCESS | 48 (1 day) |
| history_deals_get | SUCCESS | 42 (1 day) |
| copy_rates_from_pos | SUCCESS | 250 M1 bars, 02:10→06:19 UTC |
| order_calc_profit | SUCCESS | BROKER_NATIVE 1.5 ($1.50 move, 0.01 lot) |
| order_calc_margin | SUCCESS | BROKER_NATIVE 43.91 (0.01 @ ~4390) |

### Tests

- `tests/unit/test_mt5_providers_phase14.py` — 44 tests (UTC normalization,
  account/symbol/position/deal mapping, bar validation, diagnostics wrapper,
  connection state, risk broker provenance)
- `tests/unit/test_frontend_assets_phase14.py` — 24 tests (NX contract,
  Tailwind local build, local assets, DOM contract, chart contract)
- `tests/unit/test_mt5_status_endpoint.py` — 11 tests (account snapshot,
  live tick, chart source, runtime modes incl. LIVE+DISCONNECTED, state
  version, safe errors, engine offline)
- `tests/unit/test_live_state_contract.py` — 16 tests (updated to
  LiveUiState.2 + provider contract)

## 15k. Adaptive Model Intelligence — 60D Challenger Path & Continuous Learning Forensics (TASK-5, 2026-08-18)

- **Champion freeze**: `artifacts/models/scalp/XAUUSD/v1.0.0` is the control
  group (50D scalp_v1, 4-logit). Hash snapshot: `docs/task5_champion_baseline.json`.
  Candidate training NEVER writes to that path; no promotion path exists.
- **60D schema** (`scalp_v2`, candidate-only): 50D + 10 causal features
  (`features/schema_augment.py::compute_60d_extras`): regime_compression,
  momentum_5_atr, wick_imbalance_5, volume_z_5, range_z_5, clv_avg_5,
  session_phase_enc, price_acceleration, atr_trend_ratio, direction_bias_8.
  All: completed-bars + decision-tick only, deterministic, finite,
  live=replay=training (INV-015). News is OPTIONAL (news_enabled), never
  forced. Dataset producer: `model_generation/schema_v2.py` (compute_60d_frame,
  build_60d_dataset, verify_60d_artifact).
- **Validation gates hardened** (`model_generation/validation.py`): OOS
  accuracy ≥0.30, macro-F1 >0.34 (no-info baseline 0.333), balanced-accuracy
  >0.34, ECE ≤0.15, min-evidence 100 rows. Class collapse / regime-collapse
  / robustness failure → REJECTED, never CHALLENGER.
- **Training safety** (`model_generation/training.py`): non-finite loss or
  exploding gradients (norm > 5.0) → FAILED; deterministic candidate id from
  (experiment, dataset hash, seed); input NaN/Inf → FAILED; invalid labels
  → FAILED.
- **Fair matrix** (`model_generation/benchmark.py` MATRIX): 8 cells
  (50D/60D × news off/on × LEGACY_SCALPNET_V1/TCN_ATTENTION_V1) on identical
  splits/labels/purge/embargo/friction.
- **Worker truthfulness**: `TrainingWorker` status DISABLED/IDLE/RUNNING/
  TRAINING; LiveEngine default auto_train_enabled=False → DISABLED (INV-016).
- **News honesty**: `news_context_at` uses events at-or-before the sample
  only; news that postdates the dataset yields zero vectors and the verdict
  is NEWS_INCONCLUSIVE_NO_OVERLAP — never a fabricated signal.
- **Real-data result (2026-08-18)**: 60D dataset built from 99,946 M5 gold
  bars (~98s); A/B/C/D all REJECTED; 60D acc +0.037..+0.061 and ECE
  −0.034..−0.056 vs 50D (directional, NOT statistically significant);
  no challenger promoted; Champion hash unchanged.


## 15m. Liquidity Intelligence & 70D Canonical Tensor Contract (TASK-01..07, 2026-08-19)

### Canonical 70D tensor geometry — SINGLE source of truth (`features/schema_contract.py`)

The 70D contract is `scalp_v3` (hash `235b8fcc...`). ONE market snapshot
produces ONE canonical 70D vector with identical semantics in dataset,
replay, training, inference, and live (`canonical_feature_names()`,
`feature_schema_hash()`, `validate_70d_vector()`).

| Block | Indices | Family | Source |
|---|---|---|---|
| Base | 0..49 | `base` | `scalp_features.FEATURE_NAMES` (scalp_v1 50D, protected) |
| News | 50..59 | `news` | `news_context_v1` fields 0..8 + `news_state` (idx 10) — NOT a blind first-10 slice; `source_consensus` (idx 9) stays outside the block |
| Liquidity | 60..69 | `liquidity` | `liquidity_engine.LIQUIDITY_FEATURE_NAMES` (identical order to `LiquidityFeatures.as_vector()`) |

- `BASE_START/END = 0/50`, `NEWS_START/END = 50/60`, `LIQUIDITY_START/END = 60/70`.
- `canonical_feature_names()` RAISES at import/test time if any upstream
  contract drifts (a 51st base name, news-field rename, liquidity rename) —
  never silently at inference.
- `scalp_v2` = 60D (Liquidity at 50..59, `liquidity_engine.build_60d_vector`)
  is FROZEN (candidate-only, TASK-5). The 350D forward-declaration is
  OBSOLETE — no artifact ever existed.

### Liquidity 10D canonical names (indices 60..69, exact order)

`bsl_distance_atr` · `ssl_distance_atr` · `eqh_strength` · `eql_strength`
· `htf_liquidity_score` · `internal_liquidity_distance`
· `external_liquidity_distance` · `liquidity_confluence`
· `liquidity_sweep_state` · `post_sweep_displacement` —
all finite, clipped `[-3, +3]` (value gate in `compute_from_engine`).

### Liquidity producer (`features/liquidity_engine.py`, TASK-1)

- `compute_liquidity_features()` = canonical pure-causal 10D producer used by
  training, replay, and live with the SAME inputs.
- Anti-leakage: bars with timestamp > `decision_at` are invisible; fractal
  swing confirmation requires ±`window` bars (`SWING_CONFIRM_BARS`).
- Pool lifecycle: CANDIDATE → CONFIRMED → (usable at `usable_at <= decision`)
  with BSL/SSL sides; equal-high/low strength; HTF (H1/H4/D1) evidence;
  internal/external distance; confluence; reactive sweep + post-sweep
  displacement. Structural information ONLY — never a trade signal.
- Complexity note: `detect_confirmed_swings` is O(n·window) per new bar
  (≈44 ms @ 900 bars, ≈460 ms @ 3500 bars) — bounded by the 4000-bar cap.

### Runtime governor (`features/liquidity_runtime.py`, TASK-2)

- `LiquidityGovernor` owns: enabled flag (persisted via SettingsService
  `model.liquidity_features_enabled`, HOT_RESTRICTED), latest snapshot,
  status/causal derivation. The engine is the PRODUCER; the governor never
  computes in the web thread.
- `compute_from_engine(bars, mid_price, atr, decision_at, source)` — called
  by LiveEngine on new-bar cadence (pure numpy, no I/O, no DB; info-only,
  INV-020). On failure: source → UNAVAILABLE, error stored, exception raised
  (engine hook isolates it).
- Status: ENABLED / DISABLED / DEGRADED / UNAVAILABLE.
  Causal state: VALID / STALE / INVALID (NO snapshot ⇒ INVALID ⇒ UNAVAILABLE).
- `status()` is derived from timestamps: no snapshot ⇒ UNAVAILABLE; error
  after last success ⇒ DEGRADED; age > `LIVE_STALE_AFTER_SEC` ⇒ DEGRADED.
- `snapshot_payload()`: per-value indices from the AUTHORITATIVE registry
  (60..69), NEVER from the active-schema dimension (BUG-111); runtime_enabled
  + NOT_ACTIVE/UNAVAILABLE/STALE_CACHE/AVAILABLE; `reason: NO_LIQUIDITY_SNAPSHOT`.
- `build_runtime_60d_vector()` raises (never pads/zero-fills) when disabled
  or no snapshot (INV-009).
- BUG-111: wall-clock (UTC epoch) timestamps for absolute values; monotonic
  ONLY for age deltas — the 1970-sentinel render bug.
- Model compatibility (BUG-123, INV-022): contract-based verdict from
  `resolve_model_compatibility` — schema-FAMILY gate (ACTIVE=scalp_v1 /
  70D_FAMILY=scalp_v3,scalp_v4 / OTHER=legacy) + declared-dimension gate +
  REAL tensor-width gate (build_metadata.input_dimension; a 72D-news artifact
  is NOT 70D even if the manifest declares 70) + canonical feature-order
  hash (235b8fccc96b7e0e) when the model provides one. Diagnostic reasons:
  `MODEL_INPUT_DIMENSION_MISMATCH` (e.g. 50D champion vs 70D runtime —
  the truthful 2026-08-19 state), `SCHEMA_VERSION_MISMATCH`, `
  MODEL_DIMENSION_EXCEEDS_RUNTIME`, `MODEL_TENSOR_DIMENSION_MISMATCH`,
  `NO_MODEL_METADATA` (UNKNOWN), `SCHEMA_DIMENSION_MATCH` (PASS). The model
  contract is read from the CURRENT artifact (model_registry.current ->
  ChampionManager champion incl. tensor width -> engine class attrs) on every
  report — no stale compatibility cache. The 50D production champion stays
  BLOCK truthfully until a validated scalp_v3 70D model is deployed
  (governance promote with runtime_schema_id=scalp_v3). UI shows
  PASS/BLOCK(reason) + Model Contract cells + State Revision row.
  `report()` exposes `liquidity_contract` (schema/version/dimension/
  feature_order_hash/algorithm_version/indices/normalization/dtype) and
  `snapshot_coherence_revision` (coherent snapshot+verdict epoch).

### API + UI (TASK-2)

- `GET /api/liquidity/state` · `GET /api/liquidity/features` ·
  `POST /api/liquidity/toggle` (persists via SettingsService, INV-010/BUG-080
  discipline — never live.yaml). Canonical `liquidity` section in
  `/api/status` and `/api/live/state` + SSE.
- UI: Liquidity Intelligence tab, ten values with backend indices 60..69,
  chart pool overlays from `report().pools` ONLY.
- Status semantics: engine not running ⇒ no snapshot ⇒ UNAVAILABLE / NOT_RUN
  / `--` timestamps. NEVER hardcode AVAILABLE — it must depend on real engine
  market data.

### UI-controlled engine lifecycle (BUG-119, 2026-08-19)

- The UI is the source of control for LIVE/execution mode. The dashboard's
  `#execution-mode-selector` (LIVE/SIMULATION/REPLAY) posts to
  `POST /api/engine/mode` on change.
- Backend: validates mode, sets `engine.config.execution.mode`, persists via
  SettingsService `db.set('execution.mode', ...)` (HOT_RESTRICTED), returns
  `{mode, engine_running, runtime_mode}`.
- Truthful runtime: `runtime_mode` derives from REAL MT5 connection state
  (`_update_runtime_mode`) — `LIVE_CONFIGURED / MT5_DISCONNECTED` when not
  connected; never fake LIVE.
- Boot: LiveEngine reads `execution.mode` from the settings DB FIRST
  (override), falls back to YAML — a UI-requested mode survives restart.
- Engine start/stop: `POST /api/engine/toggle` runs/stops the loop
  in-process (asyncio task); stop sets `_running=False` (graceful).

## 16. Docker Runtime & Startup Contract (DOCKER-REPAIR, 2026-08-20)

**Full canonical reference: `docs/docker.md`. Entrypoint/healthcheck:
`docker/`. Compose: `docker-compose.yml`. Env contract: `.env.example`.

### Architecture (single stack, SQLite-authoritative)
- `docker compose up -d` starts exactly TWO services: **core** (engine +
  Web UI + REST API + research/training/news/shadow workers, all internally
  gated) and **redis** (internal telemetry/cache; never exposed to host).
- **There is NO PostgreSQL**: persistence is per-domain SQLite
  (`artifacts/{audit,news,candle_intel}.db` + settings DB). Postgres was
  removed (nothing in `src/` consumed it — BUG-125). Do NOT re-add it.
- Volumes: `nexus-artifacts` → `/app/artifacts` (DBs, models, research),
  `nexus-data` → `/app/data`, `redis-data` → `/data`. `docker compose down`
  preserves data; only `down -v` / `scripts/reset-dev.ps1` destroys it
  (reset script asks for `YES`).

### Startup sequence (entrypoint)
`env validation (mode/port/required vars; NSE_EXECUTION__MODE=LIVE rejected —
containers have no MT5) -> dir bootstrap -> canonical db migrate gate
(nexus db migrate --workspace /app, same TASK-10 engine) -> startup summary ->
exec engine (true exit code, no `|| true`)`. Migration failure = container
stays unhealthy; never pretend READY.

### Environment contract
- `AppConfig` is pydantic-settings with `env_prefix="NSE_"`, delimiter `__`:
  `NSE_EXECUTION__MODE` → `execution.mode`, `NSE_MODEL__MODEL_ARTIFACT_PATH`
  → `model.model_artifact_path`. Compose passes exactly these names.
- `NSE_WEB_HOST` (default 127.0.0.1 outside containers) / `NSE_WEB_PORT`
  (default 9090) / `NSE_LOG_LEVEL` (default INFO) are wired in
  `cli/main.py::_start_web_and_engine` (uvicorn bind + configure_logging).
- No secrets in `.env.example` / compose / image. Telegram credentials live
  in the settings secure store; env override `NEXUS_TELEGRAM_BOT_TOKEN`.
- `live.yaml` in the container is a bootstrap surface (image ships
  `base.yaml` + `live.yaml.example`); the runtime settings DB remains the
  authoritative configuration store. Docker does NOT reintroduce it.

### Health / readiness
- `GET /health` (web/server.py) runs the canonical `HealthEngine` (same as
  `nexus doctor`): 200 with verdict `READY`|`DEGRADED`; 503 `NOT READY`
  (critical category FAIL — e.g. model missing) or `UNHEALTHY`.
- `docker/healthcheck.sh`: process alive → `/health` reachable → verdict
  ∈ {READY, DEGRADED}. Never blind `sleep`; start_period 10s.

### Model path
Container paths only: `NSE_MODEL__MODEL_ARTIFACT_PATH=artifacts/models/...`
(relative to /app). Never Windows paths in Docker configs.

### Dev workflow
`cp .env.example .env` → `scripts/doctor.ps1` (host pre-flight) →
`docker compose up -d --build` → `docker compose ps` →
`docker compose logs -f core` → `docker compose down`. Reset:
`scripts/reset-dev.ps1`. Backup: `scripts/backup-db.ps1`.
Windows/POSIX wrappers (`start.ps1`/`start.sh`) are thin and share the same
compose single source of truth.

### Tests
`tests/unit/test_docker_startup_phase21.py` TEST-DOCKER-01..12 — compose
contract, .env contract + no-secrets, Dockerfile shape, HealthEngine
verdicts, NSE_*→AppConfig mapping. Keep them green on docker changes.

## 17. Known Engineering Pitfalls & Invariants

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
* **Rule:** All training scripts and inference pipelines MUST generate and select all 50 feature columns (`feat_0` .. `feat_49`) matching `WalkForwardTrainer.NUM_FEATURES = 50` and `ScalpNet`. For 70D work use the canonical `schema_contract` geometry (Base 0..49 | News 50..59 | Liquidity 60..69) and `build_70d_vector` — NEVER hand-assemble slices.

### 🚨 3b. Champion hot-path caching (BUG-118, 2026-08-19)
* **Pitfall:** `ChampionManager.champion_or_none()` re-verified + re-logged
  `[MODEL] CHAMPION VERIFIED` on EVERY call; web/governance polls at ~2 Hz
  flooded `nse_live.log` and re-read the artifact (hash + scaler) per call.
* **Rule:** the manager caches the verified ChampionModel keyed on the
  artifact fingerprint `(st_size, st_mtime_ns)`; identical polls return the
  memoized instance without re-reading or logging. ANY artifact rewrite
  (retrain/promotion/rollback/rebuild) changes the fingerprint → fresh
  verify + exactly one log. Cold-start None is memoized too (one warning).
  `champion_or_none(force_reload=True)` is the fresh-verify escape hatch.

### 🚨 3c. UI execution-mode lifecycle (BUG-119, 2026-08-19)
* **Pitfall:** the dashboard's execution-mode selector was a dead control
  (no change listener, no persistence) and the engine start/stop button
  never persisted the requested mode — UI selection != persisted != runtime.
* **Rule:** UI is the source of control: `POST /api/engine/mode` (persists
  `execution.mode` via SettingsService, HOT_RESTRICTED; returns real
  `runtime_mode` derived from MT5 connection). Boot reads the settings DB
  FIRST (override) then YAML. The UI badge must show the REAL runtime state
  (e.g. `LIVE_CONFIGURED / MT5_DISCONNECTED`) — never fake LIVE.

### 🚨 4. SSE / JSON Stream Enum Serialization Constraint
* **Pitfall:** Pushing raw domain models containing Enums directly into FastAPI SSE or WebSocket generators.
* **Symptom:** Raises `TypeError: Object of type ActionType is not JSON serializable`.
* **Rule:** Pass all telemetry dictionary payloads through `serialize_enums(data)` before streaming.

### 🚨 5. Event Loop Blocking Avoidance in Hot Path
* **Pitfall:** Performing synchronous file I/O, heavy PyTorch model fitting, or synchronous network calls inside `LiveEngine._process_tick_pipeline()`.
* **Symptom:** Freezes live tick processing loop, causes tick stagnation watchdog warnings and order slippage.
* **Rule:** All heavy or blocking work MUST be offloaded using `asyncio.to_thread()`, background worker threads, or async HTTP clients (`httpx`).

### 🚨 6b. Pending-Order Cancellation Is NOT Complete Until Broker State Confirms Removal (BUG-072/073/074)
* **Invariant:** "I asked MT5 to cancel the pending order" ≠ "MT5 confirmed the pending order no longer exists."
* **Rule:** Pending-order cancellation is not considered complete until broker state
  confirms order removal; exposure slots remain occupied while cancellation state is
  unresolved (ACTIVE/UNKNOWN keeps the lock; only GONE with a DONE send, or a
  positive terminal history state, releases it).
* **Implementation:** `OrderLifecycleManager.cancel_pending_order_verified()`
  (send → `_pending_broker_state()` tri-state → refresh cache on confirm),
  `cancel_pending_order_with_retry()` (bounded ≤3, idempotent),
  `reconcile_pending_state()` (periodic + startup; broker wins; internal view
  repaired on mismatch), `refresh_live_tickets_cache()`.
* **retcode 0 semantics:** 0 is NOT a trade-server retcode (DONE=10009,
  PLACED=10008). It marks a request that never reached the server — the
  exposure slot must stay occupied until broker truth is queried.
* **Function-local `import time` is forbidden** anywhere in `live_engine.py`
  hot paths: it shadows the module attribute and raises UnboundLocalError,
  crashing `_process_tick_pipeline` and freezing the exposure cache (BUG-074).
* **Learning hygiene:** MAX_EXPOSURE_REACHED / PENDING_ORDER_LOCKED NO_TRADE
  rows carry `blocked_by=EXECUTION_STATE_BLOCK`; audit payload includes
  `blocked_by`/`decision_stage` so execution-state blocks are never learned as
  "the model chose not to trade".
* **Regression guard:** `tests/unit/test_pending_cancel_reconciliation.py`
  (17 tests covering verified release, ambiguous lock, idempotency, mismatch
  repair, bounded retry, crash isolation, phantom-ticket non-reuse).
### 🚨 6. Web Error Hygiene — Never Return Exception Text to Clients (BUG-040)
* **Pitfall:** `except Exception as e: return {"error": str(e)}` anywhere in `web/server.py` leaks internal paths/SQL/exception classes (CodeQL py/stack-trace-exposure).
* **Rule:** All routes use the centralized helpers in `src/nexus_scalp/web/errors.py`:
  `_err(code, **kw)` → sanitized `{available, success, error:{code, message, request_id}}` envelope;
  `_log_err(exc, msg, endpoint=...)` → full traceback to structured logs ONLY.
* **Correlation:** every HTTP response carries `X-Request-ID`; the browser API client (`Web/api_client.js`) preserves it for log correlation; unhandled 500s become the safe envelope via middleware.
* **SSE/WS:** sanitized error paths, bounded exponential reconnect, stale detection; never stream `str(e)`.
* **No synthetic dashboard data:** chart history must never fabricate random candles or mock SMC rectangles (removed in BUG-040).

---

## 18. Testing & CI/CD Pipeline Audit

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

* `ci.yml`: Runs Python 3.11 quality gate (Ruff lint/format, Mypy, Pytest **critical suite** + coverage) on every push/PR. The **first job `ci-integrity`** statically analyzes every workflow via `scripts/ci/check_workflows.py` (fails CI on undefined job outputs, local actions without checkout, matrix artifact collisions, empty/always-false `if:`, YAML parse errors) and exercises the canonical change classifier `scripts/ci/classify_changes.py`. Emits `review-status.json`. Weekly cron re-runs the integrity scan (drift detection). 🟢 VERIFIED
* `js-tests.yml`: Vanilla-JS syntax gate (`node --check` on `Web/*.js`) + node unit tests (`tests/js/*.test.js`). 🟢 VERIFIED
* `tests-os.yml`: Critical suite on `windows-latest` + `macos-latest` (cross-OS parity; Ubuntu covered by `ci.yml`). 🟢 VERIFIED
* `docker.yml`: Builds multi-stage Docker container and publishes to GitHub Container Registry (GHCR) on the `docker` branch only. 🟢 VERIFIED
* `security.yml`: GitHub CodeQL (python) + Trivy fs scan; actions pinned to commit SHAs. 🟢 VERIFIED
* `osv-scanner.yml`: Python dependency CVE scan (advisory SARIF). 🟢 VERIFIED
* `lockfile-diff.yml`: PR dependency-manifest diff report (advisory). 🟢 VERIFIED
* `release.yml`: Validates tag/version, quality gates, Windows PyInstaller build + Inno Setup installer + EXE smoke tests, publishes GitHub release. 🟢 VERIFIED

> **CI self-defending contract (2026-08-22):** change classification is CENTRALIZED in `scripts/ci/classify_changes.py` (single lane formula; lanes: python/web/js/docker/ci/deps/scripts/release/docs). Workflows must branch on that classifier, not re-derive globs. CI infrastructure has its own deterministic regression tests in `tests/ci/` (`test_classify_changes.py`, `test_check_workflows.py`). There is NO C#/.NET or native C++ source in this repo — do not add `.NET`/`native` CI lanes (see docs/CI_ARCHITECTURE.md). 🟢 VERIFIED

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
## 15n. Market Structure & Liquidity Intelligence Engine (MSLIE, 2026-08-20 Hermes-MSLIE)

> A Market PERCEPTION layer - the "visual cortex" of the AI trading system.
> Converts raw OHLC/volume/spread into structured machine-readable market
> intelligence consumed by AI models. NOT a signal generator, NOT a trading
> bot: the final decision always stays with strategy models / ScalpNet /
> execution / risk engines. Holds NO adapter/order-manager/risk-engine
> (INV-002), no DB on the tick path (INV-001), strict causality (INV-008),
> and never alters the live 50D/70D feature contract (INV-009).

### Package: `src/nexus_scalp/mslie/`

| File | Responsibility |
| :--- | :--- |
| `models.py` | Frozen dataclass contracts: `MarketIntelligenceFeatureVectorV1` (versioned), `SwingPoint`, `LiquidityZone`, `LiquiditySweepEvent`, `MarketRegimeFeatures`, `BreakoutQuality`, `SmartMoneyFeatures`, `MarketMemoryLevel`, enums (SwingType/BrokenStatus/MarketBias/SweepState/LiquidityRank/ZoneSide). |
| `regime.py` | `compute_regime_features` - trend_direction (-1..+1), trend_strength (ADX-like 0..100), volatility_state (0..1), ranging/expansion/compression probabilities (normalized). EMA-trend + ATR-based. |
| `swing.py` | `detect_swings` - adaptive swings: volatility-adjusted pivot window, ATR prominence threshold, volume confirmation, historical reaction strength, timeframe weight, liquidity_created/taken, broken status. Every swing carries strength_score + importance_score (0..100). |
| `liquidity_map.py` | `build_liquidity_map` - BSL/SSL zones from swing highs/lows, session highs/lows, double tops/bottoms (separated extremes required), range highs/lows; ranked LOW/MEDIUM/HIGH/EXTREME with age, tests, distance, probability-as-target. |
| `sweep.py` | `detect_sweep_events` - stop-hunt detector: requires an existing pool >= 0.8 ATR from price + decisive penetration (>= 0.15 ATR) + rejection/acceptance in later bars + displacement + follow-through. Verdict REVERSAL/CONTINUATION/UNCERTAIN (+ confidence 0..100). Never classifies every wick as manipulation. |
| `breakout.py` | `assess_breakout_quality` - REAL vs FAKE breakout: closing strength, volume, momentum, retest, structure; returns complementary probabilities. |
| `smart_money.py` | `compute_smart_money_features` - OB type/strength, FVG count/strength, displacement, inducement, premium/discount position, last mitigated OB distance. |
| `engine.py` | `MarketStructureEngine` + `IMarketStructureEngine` protocol + `MarketMemory` (bounded multi-month institutional-level memory with event history). Orchestrates the full pipeline and retains the latest vector/context/latency for the UI/API. |

### Runtime wiring

- LiveEngine constructs `mslie_engine` at boot (symbol from execution config) and runs
  `analyze_market(completed_bars, decision_at=last_bar.timestamp, mid_price, atr)` on the
  bar-close cadence in `_on_new_bar` (pure numpy, failure-isolated: `[MSLIE] event=BAR_FEED_FAILED`).
- API: `GET /api/mslie/status` (engine status + market context + liquidity map + last sweep
  + feature vector) and `GET /api/mslie/features` (full vector for developer mode). Standalone
  fallback engine returns honest STANDBY/NO_MSLIE_VECTOR when the live engine has not computed.
- Debug snapshot: `mslie` section in `/api/debug/state` (section key `mslie`, available flag).
- UI: Debug tab "MARKET INTELLIGENCE ENGINE" panel - engine status chips, market context
  (symbol/timeframe/regime/bias/structure/confidence), BSL/SSL liquidity map with strength/rank,
  sweep detection block, breakout quality, smart-money grid, market-memory cards; JSON tree
  viewer/snapshot download cover the feature-vector inspection requirements.
- Determinism: same bars + same decision_at -> identical vector (verified).

### Feature vector contract (`MarketIntelligenceFeatureVectorV1`)

```json
{ "version": "MarketIntelligenceFeatureVectorV1", "symbol": "XAUUSD",
  "timeframe": "M1", "decision_at": "<iso>", "mid_price": 2450.0,
  "regime": { "trend_direction": 1, "trend_strength": 82, "volatility_state": 0.64,
    "ranging_probability": 0.1, "expansion_probability": 0.6, "compression_probability": 0.3 },
  "structure": "BULLISH", "bias": "BULLISH", "structure_confidence": 76.0,
  "nearest_buy_side_liquidity": { "price": 2455.2, "strength_score": 92, "rank": "HIGH", ... },
  "nearest_sell_side_liquidity": { "price": 2428.5, "strength_score": 84, "rank": "MEDIUM", ... },
  "liquidity_map": [...], "last_sweep_event": { "direction": "SELL_SIDE",
    "confidence": 88.0, "after_event_state": "REVERSAL", ... },
  "breakout_quality": { "real_breakout_probability": 0.72, "fake_breakout_probability": 0.28, ... },
  "smart_money": { "order_block_type": 1.0, "fvg_count": 2, "premium_discount_position": 0.4, ... },
  "memory": [...], "swing_count_high": 8, "swing_count_low": 9 }
```

- All values causal, finite, bounded; missing observations are None (never fabricated 0.0).
- Advisory/observability-first: NOT wired into the live 70D tensor (that would change the
  active feature contract - INV-009). Future model pipelines can consume the vector via
  `/api/mslie/features` or by calling `MarketStructureEngine.generate_feature_vector()`.

### Tests

- `tests/unit/test_mslie_phase22.py` - 28 tests: regime (trending/ranging/insufficient
  history/probability normalization/no-leakage), swings (detection/scores/no-leakage/ids),
  liquidity map (sides/ranks/bounds/nearest-BSL-above-SSL-below), sweeps (detection/flat-market
  rejection/chronology), breakout (complement/probability bounds), smart money (bounds/leakage),
  engine (contract/interface/determinism/memory/latency/json-safety).
- `tests/integration/test_mslie_api.py` - 4 tests: /api/mslie/status, /api/mslie/features,
  NO_MSLIE_VECTOR honesty, debug snapshot mslie section.
- Historical validation probe: `scratch/mslie_validate.py` (3 regimes: TRENDING/BULLISH,
  RANGING/NEUTRAL, sweep series with SELL_SIDE REVERSAL) - probe assertions PASS; latency
  10-20ms on 300 bars.

### Known limits (carried forward)

- Trend series with perfect monotonic drift yields 0 fractal swings (mathematically correct -
  structure needs reversals to form pivots).
- The feature vector is observation-first: wiring it into a live model requires a schema
  decision (new feature block would be `mslie_v1` at indices 70..N under INV-009 discipline).

## 15o. Strategy Factory — Autonomous Strategy Creator with Generation API + Strategy-Aware Benchmarks (2026-08-21 Fix & Full Upgrade)

> **The strategy creator with API** (`src/nexus_scalp/strategies/factory/` + `src/nexus_scalp/web/factory_routes.py`).
> Autonomous generation loop: GENERATE (template/diversity/regime/random + optional LLM slice, 70D-confined, causal)
> → VALIDATE (4 structural gates: schema/feature/causality/complexity + dedup) → BACKTEST/WALK-FORWARD/OOS/ROBUSTNESS
> via the authoritative Phase 09B `ResearchPipeline` → SCORE/RANK → ELITE → EVOLUTION. The API surfaces every
> generation/candidate/failure/event/ranking/memory/benchmark for the control room and for AI decisioning.

### Why the 07:15 systemic collapse happened (forensic 2026-08-21)

Every `SF-*` candidate backtested the **same 90-sample full-dataset slice** (ledger mean −0.079 R, drawdown 7.49 R,
OOS −0.14 R) regardless of its DSL hypothesis. Root cause: `StrategyFactory._to_strategy_candidate()` never populated
`discovery_evidence.sample_ids`, so `research/pipeline.py::_select_family()` hit its empty-list fallback and returned
the **whole** `ResearchDataset`. Outcome: all 200+ candidates produced the **identical** `BacktestResult`
(`expectancy_r −0.060669`, `score 0.3516`, `verdict REJECTED`) with two failures each (`WALK_FORWARD_FAILURE` +
`OOS_FAILURE`) at 07:15:30 — the 40-line user dump is not bad strategy DNA, it is a **data-selection bug**.
Evidence: `artifacts/audit.db` `audit_experiences` (334 rows), `artifacts/strategies.db` `factory_candidates` 2701 /
`factory_failures` 866, `strategy_registry` rows show identical `bt`/`oos`/`score` hashes; generations G1..G14 left
`RUNNING` with no `summary`.

### Fix (2026-08-21) — strategy-aware dataset selection

| File | Change |
| :--- | :--- |
| `strategies/factory/benchmark.py` *(new)* | `dsl_matches_snapshot(dsl, values)` — evaluates a DSL's `filters` (gt/lt/between on canonical 70D `feature_id` → ledger 50D `feature_snapshot.values`) over **real** historical `feature_snapshot` vectors (the same vectors the live `ScalpFeatureEngine` produced). `benchmark_subset_for_candidate` + `candidate_coverage_stats` + `build_benchmark_artifact` (deterministic benchmark with coverage, per-gate explainability, `decision` label). Pure, no DB/MT5, no pipeline mutation. |
| `strategies/factory/orchestrator.py` | `StrategyFactory._ledger_snapshot_for_filter()` (bounded 5000-row ledger snapshot) + patched `_to_strategy_candidate` to populate `discovery_evidence.sample_ids` + `sample_coverage` via `benchmark.py`. `evaluate_candidate` now stamps `build_benchmark_artifact` into `factory_runs` + the `CANDIDATE_EVALUATED` event. |
| `strategies/factory/__init__.py` | Re-exports `benchmark_subset_for_candidate`, `build_benchmark_artifact`, `candidate_coverage_stats`. |
| `strategies/factory/store.py` | `record_run` merges `benchmark` into `result_summary` for persistence. |
| `strategies/factory/ranking.py` / `summarizer.py` | Consume registry `score` faithfully — no pipeline change; divergence now comes from per-candidate datasets. |

Verified live: `SF-889A5B96D0` (MOMENTUM) 48.5 % coverage (162/334), `SF-C529A6C996` (MEAN_REVERSION) 30.5 % (102/334),
`SF-F03D462743` (TREND_FOLLOWING) 56.3 % (188/334) and `_select_family` returns exactly those counts — scores now
diverge and are strategy-aware.

### Benchmarks that help AI decide (what the upgrade adds)

Each candidate now carries a **benchmark artifact** (`factory_runs.result_summary.benchmark`) and the event payload:

```json
{
  "benchmark_id": "bm_<12hex>",
  "candidate_id": "SF-…", "family": "MOMENTUM", "decision": "REJECTED | CANDIDATE_ELITE | INCONCLUSIVE_NEEDS_MORE_DATA",
  "eligible_for_next_gen": false,
  "coverage": { "total_ledger_samples": 334, "matched": 162, "coverage_pct": 48.5, "unmatched": 172 },
  "backtest": { "total_trades": 162, "expectancy_r": -0.06, "profit_factor": 0.68, "max_drawdown_r": 7.49 },
  "walk_forward": { "folds": 3, "passes": 1, "pass_rate": 0.33, "degradation": 0.42, "passed": false },
  "oos": { "status": "FAIL", "oos_expectancy_r": -0.14, "reason": "OOS expectancy -0.14R below minimum …", "degradation": 1.3 },
  "robustness": { "status": "PASS", "max_degradation": 0.02 },
  "score": { "final_score": 0.3516, "verdict": "REJECTED", "reasons": […], "dimensions": { "performance_score": 0, "oos_score": 0, … } },
  "primary_failure": "OOS"
}
```

AI consumers use `decision` + `walk_forward.pass_rate` + `oos.degradation` + `coverage.verdict_hint`
(`NO_DATA <8` → skip, `LOW_EVIDENCE 8..19` → caution, `EVALUABLE ≥20`) to rank, promote, or suppress strategies.
The 07:15 identical-score pathology is eliminated — legacy generations before the fix surface via on-demand coverage
computation in the API fallback.

### Generation API & Strategy-Aware benchmark API

`src/nexus_scalp/web/factory_routes.py` (`/api/factory/*`):

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/status` | GET | Loop + worker + generations + `provider_usage` + config + provider (`available`/`model`/`prompt_version`/`usage`). |
| `/generations` | GET | Bounded generation list. |
| `/generations/{id}` | GET | One generation + candidates + failures. |
| `/candidates` | GET | Candidate rows (filter `generation_id`/`lifecycle`). |
| `/benchmarks` | GET | **NEW (2026-08-21):** strategy-aware benchmarks per generation (the AI surface). Query `generation_id`. Returns `{benchmarks: [benchmarkArtifact,…]}`; for legacy generations without `factory_runs` computes coverage on-the-fly. |
| `/events` | GET | Event stream. |
| `/failures` | GET | Structured failure reasons (`WALK_FORWARD_FAILURE`/`OOS_FAILURE`/… + `detail`). |
| `/ranking` | GET | Ranked registry survivors by dimension. |
| `/memory` | GET | Evolution memory (next-gen research summary). |
| `/llm-config` | GET/POST | LLM provider config (encrypted key, hot-rebuild, never returns raw key). |
| `/generate` | POST | Create+generate+validate a generation (and, since BUG-135, auto-evaluate + complete in background). |
| `/evaluate/{id}` | POST | Evaluate one persisted candidate. |
| `/complete/{id}` | POST | Finalize a generation (ranking→elite→summary). |
| `/loop/{start,pause,resume,stop}` | POST | Autonomous loop control (kill switch). |

All routes are wrapped (never raise), use `serialize_enums`, and mirror the research-API conventions.

### Full upgrade cycle (what changes for the AI)

1. **Dataset is strategy-aware** — every backtest grades the candidate's *own* filtered ledger slice (real 50D vectors),
   not the ledger average. Expectations, drawdowns, win rates and OOS results now vary meaningfully.
2. **Walk-forward is walk-forward** — `_select_family` returns exactly `len(sample_ids)` rows; fold pass/fail reflects
   *that* strategy's temporal stability, not a shared losing book.
3. **Benchmark payload is prescriptive** — `GET /api/factory/benchmarks?generation_id=G17` returns strategy-aware
   backtests + walk-forward repr + OOS explain + robustness explain + decision label, so the AI can pick a top-K
   without re-running the pipeline. The control-room UI should render coverage %, `primary_failure` and the `decision`
   badge.
4. **Stuck generations are stitched** — the `factory_routes /generate` route now runs the full cycle (generate →
   validate → evaluate → complete) in a background thread; future generations land `COMPLETED` with a `summary`
   (G15/G17 already do: `avg_score 0.3516` is the last pre-fix cohort's losing baseline — next cohorts will diverge).

### Tests

- Existing `tests/unit/test_mslie_*` suites remain green (unrelated subsystem).
- New unit behaviour is covered by the live probe in this audit (162/102/188 divergence + `_select_family` exactness);
  an explicit `tests/unit/test_factory_benchmark_phase23.py` should be added to lock `dsl_matches_snapshot` (PASS/FAIL),
  `benchmark_subset_for_candidate` coverage math, and determinism.

### 2026-08-21T11:44 full cycle check — liquidity shadow + replay + benchmark parity

| Signal | Evidence | Verdict |
| :--- | :--- | :--- |
| `11:44 FEATURE_CALCULATION_OK source=LIVE_MARKET_STATE latency 43.6ms bars 901` | `features/liquidity_runtime.py` healthy (governor status=ENABLED, pure numpy, no I/O). | 🟢 No throttle. |
| Telegram spam | `DEDUPLICATED duplicate suppressed` flood (~10/s) at 11:32 — expected coalescing of `GENERIC` sends. Downgraded `SEND_FAILED/FAILED_FINAL` for `DEDUPLICATED` to `DEBUG` (`DEDUP_SUPPRESSED`/`DEDUP_FINAL`) so console is not spammed; real failures still `ERROR`. Tracking unaffected. | 🟢 Patched (`observability/telegram_notifier.py: DEDUPLICATED → DEBUG`). |
| Backtest bias | Ledger mean −0.079 R (92 outcomes), pipeline backtest expect −0.06 R. Pre-fix 40 failures identical (07:15). Post-fix `benchmark.py` strategy-aware slices: 162 vs 102 vs 188 / 334; `_select_family` exact. G19 best 0.77 INCONCLUSIVE (1 trade, need 8) vs prior flat 0.35. | 🟢 Diverging; not a bug. |
| Cycle integrity | Tick path: `feature_engine→regime→manage_active_positions(probs)→observe_positions→infer(warmup-gated)→signal_policy→experience→intelligence→news→shadow(50D+70D, observational)→liquidity(901 bars)→hedge→dispatch`. Hedging + shadow + liquidity are failure-isolated. | 🟢 No LIVE impact. |
| Prompt | `factory-dsl-v3.1` now carries benchmark surface (OWN slice, coverage_pct, walk_forward/OOS explain). | 🟢 |
| Skill mandate | `ABSOLUTE DIRECTIVE: Every update must write a SHORT skill entry` enforced. | 🟢 |
## Node.js Runtime Role (AUDIT 2026-08-22 — DECISION: BUILD/DEV/TEST-ONLY, NOT RUNTIME)

- **Fact:** there is NO `package.json`, NO bundler (webpack/vite/esbuild/parcel), and NO Node
  child-process spawned anywhere in the engine (`grep` for node/npm/npx in `src/` = 0 hits).
- **Web UI** (`Web/`): a *buildless* vanilla-JS SPA (`index.html` + `app.js` + `*.js` includes).
  It is served entirely by the Python FastAPI process: `GET /` -> `index.html`, plus `/app.js`,
  `/styles.css`, `/api_client.js`, `/tailwind.css` (route in `src/nexus_scalp/web/server.py`).
  No Node HTTP server, no Node WebSocket gateway, no Node API proxy exists.
- **Why it opens without Node:** the UI is static + browser JS + the committed pre-compiled
  `Web/tailwind.css`; the runtime serves it via FastAPI (port 8080 local, 9090 docker) using only
  Python (uvicorn/FastAPI). Node is not on the runtime path at all.
- **Where Node is actually used (build/dev/test only):**
  1. Tailwind compile: `scripts/build/build_tailwind.py` (pins `tailwindcss@3`, `npx` ephemeral —
     no committed `node_modules`). Committed `Web/tailwind.css` is the artifact; rebuild only on theme change.
  2. JS syntax gate + unit tests: `node --check Web/*.js` and `tests/js/*.test.js` via
     `.github/workflows/js-tests.yml`. Pure node, no browser, no bundler.
  3. E2E: Playwright (habitat in `node_modules/`, `.gitignore`d, CI-only) — never the runtime.
- **Decision (Outcome B):** Node is isolated as a build/dev/test dependency. The official launcher
  (`python NexusTradingForexBot.py` / `nexus start`) does NOT require Node and must NOT spawn Node.
  Do not reintroduce a Node server, gateway, or `npm run` into the runtime launch path.
- **Regression guards:** `tests/unit/test_node_runtime_role.py` (12 tests) — asserts buildless assets,
  no CDN/bundler refs in browser JS, FastAPI-served UI without Node, no `package.json` at root,
  no Node subprocess in engine, build script present + `js-tests.yml` declares `buildless`.
- **See:** `agents/decisions/DEC-0002-nodejs-runtime-role.md`.
