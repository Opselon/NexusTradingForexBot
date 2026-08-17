# 👑 Nexus Scalp Engine (NSE) v9.0
### *Production-Grade High-Frequency Quantitative Scalping Infrastructure*

> **Model Generation Migration (PHASE 13):** ScalpNet is now a **LEGACY
> BASELINE** (control group) inside an artifact-first Model Factory. Models
> and datasets are filesystem artifacts with full manifests; inference
> requires NO database. Explicit 3-class label contract; NewsContext is a
> versioned, causally-correct model input; experiments are bounded and
> explainable; legacy baseline remains reproducible for benchmarking.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5_Terminal-2962FF.svg?style=for-the-badge&logo=metatrader5&logoColor=white)](https://www.mql5.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.tech/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL_Ledger-003B57.svg?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal_Event--Driven-6f42c1.svg?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-Production_Hardened-success.svg?style=for-the-badge)]()
[![Phase](https://img.shields.io/badge/Phases-08_09_09B_10_11_12-6f42c1.svg?style=for-the-badge)]()
<p align="center">
  <img src="docs/web.png" alt="Nexus Trading Dashboard" width="100%">
</p>

> **Nexus Scalp Engine (NSE)** is an enterprise-class, event-driven quantitative trading runtime engineered for sub-second scalping on **XAUUSD (Gold)** and major currency pairs. NSE unifies deep learning inference, real-time market microstructure analysis, and high-frequency execution into a single, self-healing framework.
>
> Driven by a **Dual-Path TCN + Self-Attention Neural Network (ScalpNet)**, a **50-Dimensional Causal Feature Engine**, an **Autonomous 30-Rule SMC Policy Matrix**, and a **News Intelligence Engine (Phase 12)**, NSE eliminates lookahead bias, prevents catastrophic drawdown, and executes institutional Order Block, Fair Value Gap (FVG), and Liquidity Sweep setups directly on MetaTrader 5 via native C++ IPC bindings (or ZMQ remote gateway / paper adapter).

> **📖 Agent Docs:** For the authoritative, repository-grounded architecture map (forensic badge system, layer table, file-by-file inventory, ML code sites, engineering pitfalls), see [`agents/skill.md`](agents/skill.md). For the historical bug ledger (root causes, evidence, regression guards), see [`agents/bugs.md`](agents/bugs.md). Cross-phase audit: [`FORENSIC_AUDIT_PHASES_08_11.md`](FORENSIC_AUDIT_PHASES_08_11.md).

---

## 🏛️ System Architecture Blueprint

NSE follows a strict **Hexagonal (Ports-and-Adapters) Event-Driven Architecture**, completely isolating execution platforms, machine learning models, and network adapters into modular, self-healing subsystems.

```text
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║                                 NEXUS HIGH-FREQUENCY CORE                                     ║
║                                                                                               ║
║  ┌───────────────────────┐     ┌───────────────────────┐     ┌─────────────────────────────┐  ║
║  │ 50D Causal Feature    │ ──► │ PyTorch ScalpNet      │ ──► │  30-Rule SMC Policy Matrix  │  ║
║  │ Engine (SMC/OFI/ATR)  │     │ (TCN + Self-Attention)│     │  (God-Mode & Veto Gate)     │  ║
║  └───────────────────────┘     └───────────────────────┘     └──────────────┬──────────────┘  ║
║              ▲                                                              │                 ║
║              │ Ticks & Bars                                                 ▼                 ║
║  ┌───────────┴───────────┐     ┌───────────────────────┐     ┌─────────────────────────────┐  ║
║  │ Live Engine Event     │ ◄── │ Web Control Center    │ ◄── │ Invariant Risk Engine       │  ║
║  │ Loop & Safety State   │     │ (FastAPI + WebSockets)│     │ (Lot Sizing & Margin Clamp) │  ║
║  └───────────┬───────────┘     └───────────────────────┘     └──────────────┬──────────────┘  ║
║              │                                                              │                 ║
║              └───────────────────────────────┬──────────────────────────────┘                 ║
║                                              ▼                                                ║
║                                 ┌─────────────────────────┐                                   ║
║                                 │ IMT5Port IPC Adapter    │                                   ║
║                                 └────────────┬────────────┘                                   ║
╚══════════════════════════════════════════════╪════════════════════════════════════════════════╝
                                               │ Direct Win32 / ZMQ Gateway / Paper
                                               ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                              LOCAL METATRADER 5 TERMINAL                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Institutional Liquidity Execution (LMAX / Pepperstone / IC Markets / FXCM Direct Feed)   │  │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Layer | Directories / Files | Role | Invariants |
| :--- | :--- | :--- | :--- |
| **Domain** | [`src/nexus_scalp/domain/`](src/nexus_scalp/domain) | Immutable Pydantic data contracts (`frozen=True`): `TickData`, `TradeProposal`, `Position`, `TradeOrder`, `AccountInfo` | NEVER mutate; use `.model_copy(update={...})` |
| **Ports** | [`src/nexus_scalp/ports/`](src/nexus_scalp/ports) | `IMT5Port`, `IGatewayPort` dependency-inversion boundaries | Any `IMT5Port` signature change MUST propagate to all 3 adapters |
| **Adapters** | [`src/nexus_scalp/adapters/`](src/nexus_scalp/adapters) | Direct MT5 Win32 IPC · ZMQ remote gateway · paper simulator · SQLite WAL `AuditRepository` | DB writes queued via dedicated background worker thread (never on tick path) |
| **Features** | [`src/nexus_scalp/features/`](src/nexus_scalp/features) | 50D Feature Vector (`scalp_features.py`) + 10-regime classifier (`regime_classifier.py`) + schema registry (`schema.py`) | **Exactly 50 features**; finite; clipped `[-3.0, +3.0]`; NaN/Inf ⇒ deterministic fallback |
| **Models** | [`src/nexus_scalp/models/`](src/nexus_scalp/models) | PyTorch `ScalpNet` | Dual-path 2D snapshot / 3D temporal (TCN + self-attention); input `(B, 50)`, output 4 logits `NO_TRADE/BUY/SELL/WAIT` |
| **Training** | [`src/nexus_scalp/training/`](src/nexus_scalp/training), [`labeling/`](src/nexus_scalp/labeling) | Purged walk-forward trainer, cost-aware triple-barrier labeler, online fine-tuning + atomic rollback | Polars bitwise ops only (`~`, `&`, `\|` — never Python `not`); labeler 3-class → 4-class head mapped dynamically |
| **Signals** | [`src/nexus_scalp/signals/`](src/nexus_scalp/signals) | Multi-confluence policy (`policy.py`), SMC God Mode, 30+ DB-driven rule matrix (`rule_matrix.py`) | Regime Guardian Gate blocks unsafe regimes; pending orders locked 30s / 1.0×ATR drift |
| **Risk** | [`src/nexus_scalp/risk/`](src/nexus_scalp/risk) | Fractional-Kelly lot sizing, Almgren-Chriss slippage bounds, account-tier caps, margin clamps | All proposals pass `calculate_dynamic_volume()`; margin ≤ 20% of free margin |
| **Execution** | [`src/nexus_scalp/execution/`](src/nexus_scalp/execution) | 60-scenario router, 11 position lifecycles, breakeven lock, profit giveback, adaptive exit (hold-score) protection | `HARD_MAX_LOTS = 10.0`, `MAX_TOTAL_EXPOSURE = 1`; circuit breaker → `SAFE_MODE` after 3 consecutive rejections |
| **Application** | [`src/nexus_scalp/application/`](src/nexus_scalp/application) | Async event loop, bar aggregation, state sync, retrain orchestrator, Phase 08-12 worker wiring | **NEVER block the event loop** — all heavy work via `asyncio.to_thread()` / background workers |
| **Web / API** | [`src/nexus_scalp/web/`](src/nexus_scalp/web), [`Web/`](Web) | FastAPI REST / SSE (`/api/ticks/stream`) / WebSocket (`/web`), Dark Glassmorphism dashboard | Enums in streamed JSON MUST pass `serialize_enums()` before broadcasting; background tasks in `app.state.background_tasks` |

---

## 🔥 Key Technological Innovations

### 1. Predictive SMC "God-Mode" Execution Engine
- **50% Impulse Equilibrium Filtering:** Automatically calculates the 50% midpoint of impulse legs. Short trades below 50% (Discount) or Long trades above 50% (Premium) are hard-gated (`OB_BELOW_50_PERCENT_EQUILIBRIUM`).
- **BOS & CHoCH Validation:** Verifies structural displacement before tagging Order Blocks (OB).
- **Liquidity Sweeps & 50-60% OTE Fibs:** Identifies stop-hunts (`liq`) and secondary sub-leg optimal trade entries.
- **SMC Veto Power:** Pristine SMC setups (OB + Sweep + BOS) override slow Higher-Timeframe trend conflicts.

### 2. PyTorch ScalpNet with Atomic Checkpoint Rollbacks
- **Dual-Path TCN + Self-Attention Network:** 2D snapshot path for single ticks, 3D temporal path (causal Conv1d + residual blocks + sinusoidal positional encoding + self-attention) for sequences. Processes 50-dimensional normalized feature tensors natively.
- **Clamped Inverse Class Frequency Weighting:** `W_c = clamp(N_total / (3×(N_c+1)), 0.5, 2.0)` normalized to mean `1.0` — prevents the loss explosion / prediction-collapse class of bugs (see `PROGRESS.md`).
- **Atomic Model Rollback System:** Evaluates validation loss/entropy after online fine-tuning; a degrading checkpoint is atomically rolled back with a CRITICAL RED TERMINAL ALERT.

### 3. Dynamic Position Management & "Falling Knife" Protection
- **Order Churning Throttle:** 15-second modification throttle prevents MT5 pending-order spam (retcodes `10013`/`10015`).
- **Contextual Order Cancellation:** Deep-profit accelerating positions cancel/push away opposite limits to avoid catching falling knives.
- **Dynamic Lot Sizing Clamps:** Slices lots by structural ATR stop distance, enforcing broker `volume_max`, `margin_free` pre-checks and hard caps (retcode `10019` prevention).
- **Adaptive Exit Engine:** Convex drawdown penalty `80×ratio^1.5`, profit-shield floor, trend-bonus suppression underwater, split-order sync, `S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT`.

### 4. Real-Time HTML5 Canvas Visualizer & Live Tuner (`/web`)
- **150+ Bar History Buffer:** High-resolution interactive M1 candlestick chart with auto-scale and zoom.
- **Multi-Layer Transparent Overlays:** 🟩 Bullish OB/FVG (0.25), 🟥 Bearish OB/FVG (0.25), 🟨 swept Liquidity Pools (0.35).
- **Interactive Execution Target Lines:** Entry (blue), SL (dashed red), TP (dashed green) with dollar risk/reward tooltips.
- **Algorithm Live Tuner Panel:** `PUT /api/algo/config` hot-swaps `atr_sl_buffer_multiplier`, `min_risk_reward_ratio`, `ai_zone_confidence_threshold`, … in memory within ~2 seconds — no restart.

### 5. Institutional Financial Accounting Ledger
- Complete post-trade autopsy in SQLite (`artifacts/audit.db`): MAE/MFE, `entry_rule_id`, `exit_mechanism` (`TP_HIT`, `HARD_SL_HIT`, `RISK_FREE_SL_HIT`, `TIME_DECAY_EXIT`, `BREAKEVEN_STOP`, …), SL geometry, running equity snapshots.

### 6. Experience-Driven Strategy Intelligence (Phase 08)
- **Immutable Experience Ledger:** decision-time rows keyed by deterministic `idempotency_key`; outcomes appended with the broker ticket as identity bridge — duplicates/replays/retries can never create double evidence.
- **Pre-Trade Experience Gate:** after signal policy, before risk sizing. Retired family ⇒ REJECTED; degraded family ⇒ confidence-penalized; validated family ⇒ bounded boost. Only down-ranks, never upgrades.
- **TTL-Cached + Rate-Limited Hot-Path Lookups** (30s refresh, budgeted) — the live tick path never blocks on SQLite.

### 7. Unified Accounting & Performance Intelligence Core (Phase 08)
- **ONE canonical accounting authority** (`AccountingCore`) — REST API, dashboard, worker and Experience never compute PnL/drawdown/period boundaries independently.
- **NO SYNTHETIC NUMBERS:** metrics without stored evidence render `n/a`, never fake `0.0`.
- **ONE PERIOD POLICY (UTC), ONE DRAWDOWN METHODOLOGY, NET PNL COMPUTED EXACTLY ONCE, IDEMPOTENT CLOSURE** — institutional invariants enforced by 64 unit tests + 13 integration tests.

### 8. Trade Intelligence Brain (Phase 09)
- **Immutable Position Lifecycle Timeline:** typed events (`POSITION_CREATED`, `POSITION_PROFIT_GIVEBACK`, `POSITION_DEGRADING`, …) with full market/decision context.
- **Trade Autopsy Engine:** separates strategy/entry/risk/management/exit quality — a `MANAGED_LOSS` is never mistaken for a broken strategy (verdicts: `CLEAN_WIN`, `LUCKY_WIN`, `MANAGED_LOSS`, `COSTLY_LOSS`, `EVEN`).
- **Behavior Detection:** `EARLY_EXIT`, `PANIC_EXIT`, `OVERTRADING`, `GREED_PATTERN`, `BAD_RECOVERY_PATTERN`, … with timestamps and severity.
- **Strategy Evolution:** candidates are NEVER live until backtested and validated; promotion is operator-gated.
- **Pre-Trade Intelligence Gate:** layered WARN tier + bounded suitability score — only down-grades.

### 9. Strategy Research, Backtest & Validation Engine (Phase 09B)
- **Causal-safe dataset builder** over the immutable experience ledger (future outcomes can never enter discovery).
- **Deterministic friction-aware backtest** (spread/slippage/commission) + purged/embargoed temporal walk-forward + **hard OOS gate** (OOS failure ⇒ REJECTED even with high win rate).
- **Robustness engine** (spread/slippage/latency stress) + explainable multi-dimension score with small-sample protection.
- **Content-addressed strategy versioning:** a modified strategy is a NEW version that must be revalidated; old validation records stay immutable.
- **Enduring `strategy_registry`** independent of model files — survives model rebuilds and schema-width changes.

### 10. Controlled Model Training & Challenger Engine (Phase 10)
- **Champion NEVER overwritten:** candidate training writes only to `candidate/<run_id>/` staging paths (hash-verified).
- **12 validation gates** (dataset, schema, labels, stability, validation, walk-forward, OOS, robustness, risk, comparison, artifact, reproducibility) + mono-class collapse protection.
- **Immutable `training_runs` + `model_comparisons`** lineage; additive lifecycle columns on the existing model registry (no duplicate registry).
- **No auto-promotion:** a validated Challenger is stored `CHALLENGER` (shadow-eligible); production authority stays with the controlled process.

### 11. Challenger Shadow Trading & Champion Evaluation (Phase 11)
- **Same-input integrity:** the Challenger runs on the IDENTICAL live feature vector as the Champion, every tick.
- **Zero order authority:** `shadow/` holds no adapter, order manager or risk engine — a Challenger can never place, modify or close an order.
- **Every result marked `simulated=True`** — never presented as real account PnL.
- **Explainable promotion evaluation with hard vetoes** (insufficient evidence, negative OOS, critical drawdown increase, robustness failure, calibration collapse, strategy regressions, tail-risk degradation).
- **Bounded, failure-isolated:** queued persistence, worker aggregation via `asyncio.to_thread`, schema DDL guarded once per process; a shadow failure can never stop trading. *(2026-08-16 audit fixed BUG-025..029, incl. a silent shadow-persistence failure and a worker deadlock — see `agents/bugs.md`.)*

### 12. News Intelligence Engine (Phase 12)
- **Fully isolated subsystem** with its own `artifacts/news.db` — never mixes with the trading audit ledger.
- **Canonical event pipeline:** RSS/Atom sources (Fed, BLS, BEA, ECB, BoE, CFTC, Treasury, Reuters, MarketWatch, ForexLive, …) → ingestion → dedup/syndication collapse → local + optional AI analysis (LOCAL_ONLY / API_ONLY / HYBRID) → entity/topic/impact extraction → XAUUSD/USD relevance → time-decay aging → source consensus.
- **Bounded news gate:** a news alignment may boost confidence by at most `0.05`; a conflict may penalize at most `0.10`; **news can NEVER force BUY/SELL, bypass RiskEngine/exposure/kill-switch, or place/modify/close an order** (test-enforced).
- **Failure-isolated:** the News Worker runs via `asyncio.to_thread` off the tick path; engine or worker startup failure simply disables the subsystem — trading continues unaffected.
- **Opt-in:** disabled by default (`AppConfig.news = None` until a `news:` block is added to YAML); Web UI tab + REST API degrade gracefully (`available=False`) when off.
- **66 behavioral tests** cover ingestion, dedup/consensus, timing/decay, local + external-AI analysis with fallbacks, trading-gate bounds, memory/versioning, worker isolation, dashboard, and regression.

---

## 📂 Repository Layout

> ✅ **Verified 1:1 against the working tree** (2026-08-16) — every path below exists on disk. Click any file to jump to it on GitHub.

| Area | Path | What it is |
| :--- | :--- | :--- |
| **CI/CD** | [`.github/workflows/`](.github/workflows) | `ci.yml` (Ruff/Mypy/Pytest+coverage) · `docker.yml` (GHCR) · `release.yml` (v\* tags) · `security.yml` (CodeQL+Trivy) |
| **Agent Docs** | [`agents/skill.md`](agents/skill.md) | Authoritative Master Skill & Architecture Map (forensic badges, layer table, ML code sites) |
| | [`agents/bugs.md`](agents/bugs.md) | Forensic Bug Ledger — root causes, evidence, regression guards (BUG-xxx) |
| **Config** | [`configs/base.yaml`](configs/base.yaml) | System parameters, risk limits & model configs |
| | [`configs/live.yaml`](configs/live.yaml) | Production live environment configuration |
| | [`configs/live.yaml.example`](configs/live.yaml.example) | Example live runtime configuration |
| **Container** | [`docker/entrypoint.sh`](docker/entrypoint.sh) · [`docker/healthcheck.sh`](docker/healthcheck.sh) | Container entrypoint & health check |
| | [`Dockerfile`](Dockerfile) · [`docker-compose.yml`](docker-compose.yml) | Multi-stage build + orchestration |
| **Assets** | [`docs/web.png`](docs/web.png) | Dashboard screenshot (README banner) |
| **Frontend** | [`Web/index.html`](Web/index.html) | Control Center UI (dark glassmorphism, all 8 tabs) |
| | [`Web/app.js`](Web/app.js) | Canvas chart renderer, live tuner, news panel logic |
| | [`Web/styles.css`](Web/styles.css) | Premium dark styling |
| **Entrypoints** | [`main.py`](main.py) | Root redirect → `nexus_scalp.cli.main:app` |
| | [`NexusTradingForexBot.py`](NexusTradingForexBot.py) | Legacy convenience wrapper (also runs `--doctor`) |
| | [`src/cli/train_model.py`](src/cli/train_model.py) | CLI training script (50D contract aligned) |
| **Quality Gates** | [`beforePush.sh`](beforePush.sh) / [`beforePush.ps1`](beforePush.ps1) | Ruff lint+format, mypy, full unit suite (use **pwsh**, not powershell 5.1) |
| **Project Meta** | [`pyproject.toml`](pyproject.toml) · [`requirements.txt`](requirements.txt) | PEP 621 build metadata + frozen deps |
| | [`PROGRESS.md`](PROGRESS.md) | Stabilization & bug-fix engineering report |
| | [`FORENSIC_AUDIT_PHASES_08_11.md`](FORENSIC_AUDIT_PHASES_08_11.md) | Cross-phase forensic audit report (BUG-025..029, verdict) |

### 🧩 `src/nexus_scalp/` — Core Packages

| Package | Path | Status / Role |
| :--- | :--- | :--- |
| **Accounting** | [`src/nexus_scalp/accounting/`](src/nexus_scalp/accounting) | 🟢 PHASE 08 — single canonical accounting authority (`core.py`, `periods.py`, `aggregation.py`, `normalize.py`, `worker.py`) |
| **Experience** | [`src/nexus_scalp/experience/`](src/nexus_scalp/experience) | 🟢 PHASE 08 — experience-driven strategy intelligence (`ledger.py`, `evaluator.py`, `intelligence.py`, `retriever.py`, `quality.py`, `provenance.py`) |
| **Intelligence** | [`src/nexus_scalp/intelligence/`](src/nexus_scalp/intelligence) | 🟢 PHASE 09 — trade intelligence brain (`lifecycle.py`, `autopsy.py`, `behavior.py`, `evolution.py`, `gate.py`, `worker.py`) |
| **Research** | [`src/nexus_scalp/research/`](src/nexus_scalp/research) | 🟢 PHASE 09B — strategy research, backtest & validation (18 modules: `dataset`, `splitting`, `leakage`, `backtest`, `walkforward`, `oos`, `robustness`, `scoring`, `registry`, `pipeline`, …) |
| **Model Lifecycle** | [`src/nexus_scalp/model_lifecycle/`](src/nexus_scalp/model_lifecycle) | 🟢 PHASE 10 — controlled training + Champion/Challenger (12-module: `champion`, `trainer`, `gates`, `comparison`, `orchestrator`, `worker`, …) |
| **Shadow** | [`src/nexus_scalp/shadow/`](src/nexus_scalp/shadow) | 🟢 PHASE 11 — challenger shadow trading (`challenger.py`, `engine.py`, `comparison.py`, `store.py`, `worker.py`) |
| **News** | [`src/nexus_scalp/news/`](src/nexus_scalp/news) | 🟢 PHASE 12 — news intelligence engine & gate (`engine.py`, `gate.py`, `context.py`, `database.py`, `seed.py`, `worker.py` + `analysis/` `ingest/` `memory/` `sources/`) |
| **Adapters** | [`src/nexus_scalp/adapters/`](src/nexus_scalp/adapters) | Port implementations: `database/audit_repository.py` (SQLite WAL) · `mt5/mt5_adapter.py` (Direct Win32 IPC) · `mt5/remote_gateway.py` (ZMQ) · `paper/paper_adapter.py` (simulator) |
| **Application** | [`src/nexus_scalp/application/live_engine.py`](src/nexus_scalp/application/live_engine.py) | Master async event loop, Safety State Machine, Phase 08-12 worker wiring |
| **CLI** | [`src/nexus_scalp/cli/main.py`](src/nexus_scalp/cli/main.py) | Primary Typer CLI (`nse` / `python -m nexus_scalp.cli.main`) |
| **Configuration** | [`src/nexus_scalp/configuration/config.py`](src/nexus_scalp/configuration/config.py) | Pydantic `AppConfig`/`AlgoConfig` schema (YAML + ENV) |
| **Domain** | [`src/nexus_scalp/domain/`](src/nexus_scalp/domain) | Frozen immutable models & enums (`models.py`, `enums.py`) |
| **Execution** | [`src/nexus_scalp/execution/order_manager.py`](src/nexus_scalp/execution/order_manager.py) | In-trade tracker, SL shifts, MAE/MFE, throttling, 11 position lifecycles |
| **Features** | [`src/nexus_scalp/features/`](src/nexus_scalp/features) | `scalp_features.py` (50D causal engine + SMC) · `regime_classifier.py` (10 regimes) · `schema.py` (50D active; 60D/350D declared) |
| **Labeling** | [`src/nexus_scalp/labeling/triple_barrier.py`](src/nexus_scalp/labeling/triple_barrier.py) | Cost-aware purged triple-barrier labeler |
| **Market Data** | [`src/nexus_scalp/market_data/`](src/nexus_scalp/market_data) | `bar_aggregator.py` · `tick_storage.py` |
| **Models** | [`src/nexus_scalp/models/scalp_net.py`](src/nexus_scalp/models/scalp_net.py) | 🟡 LEGACY BASELINE ScalpNet (control group — kept for benchmarking) |
| **Model Generation** | [`src/nexus_scalp/model_generation/`](src/nexus_scalp/model_generation) | 🟢 PHASE 13 — artifact-first Model Factory: contracts, artifact store, dataset/sample/experiment factories, runtime, replay, drift |
| **Observability** | [`src/nexus_scalp/observability/`](src/nexus_scalp/observability) | `logging.py` (structured JSON) · `telegram_notifier.py` |
| **Ports** | [`src/nexus_scalp/ports/`](src/nexus_scalp/ports) | `mt5_port.py` (`IMT5Port`) · `gateway_port.py` (`IGatewayPort`) |
| **Risk** | [`src/nexus_scalp/risk/risk_engine.py`](src/nexus_scalp/risk/risk_engine.py) | Fractional-Kelly lot sizing, Almgren-Chriss slippage, margin clamps |
| **Signals** | [`src/nexus_scalp/signals/`](src/nexus_scalp/signals) | `policy.py` (SMC God-Mode + pre-trade gates) · `rule_matrix.py` (30+ rule matrix, DB hot-reload) |
| **Training** | [`src/nexus_scalp/training/walk_forward_trainer.py`](src/nexus_scalp/training/walk_forward_trainer.py) | Purged walk-forward trainer + online fine-tuner + atomic rollbacks |
| **Web** | [`src/nexus_scalp/web/server.py`](src/nexus_scalp/web/server.py) | FastAPI async server — REST, SSE (`/api/ticks/stream`), WebSocket (`/web`) |

### 🧪 `tests/` — Verified Test Inventory

| Suite | Path | Scope |
| :--- | :--- | :--- |
| **Unit (28 active suites)** | [`tests/unit/`](tests/unit) | Domain, features, trainer, policy, risk, order manager, rule matrix, hardening, HTF warmup gate, accounting (64), intelligence (18), research (45), model lifecycle (32), shadow (35), news (63), **model generation (52)**, regression guards (BUG-013..018) |
| **Integration (10 suites)** | [`tests/integration/`](tests/integration) | Accounting API · Intelligence API · Research API · Model-Lifecycle API · News API · **Model-Generation API** · DB execution audit · experience execution boundary · signal pipeline health · Playwright E2E |

### 🗄️ Runtime Artifacts

| Path | Contents |
| :--- | :--- |
| `artifacts/audit.db` | SQLite WAL trading ledger — `audit_signals`, `audit_orders`, `audit_account_snapshots`, `audit_ledger`, `financial_ledger`, `trading_rules_config`, Phase 08-11 tables (`audit_experiences`, `strategy_registry`, `training_runs`, `shadow_runs`, …) |
| `artifacts/news.db` | Phase 12 dedicated news DB — articles, analysis, consensus, impacts, sources, trade links (never mixed with audit.db) |
| `artifacts/logs/` | Structured JSON logs |
| `artifacts/models/` | Model bundles: live Champion + `candidate/<run_id>/` staging (Champion never overwritten) |

### 🔬 `scratch/` — One-Off Diagnostic Probes

| Path | Contents |
| :--- | :--- |
| `scratch/` | Disposable forensic/diagnostic scripts from past investigations (see below) |

> **These are NOT part of the application.** They are one-off probe scripts written
> during forensic audits (DB queries against `artifacts/audit.db` in read-only mode,
> log greps, dry-run validations) to work around the inline-`python -c` restriction
> on Windows hosts. Nothing in `src/`, `tests/`, the build, or the CI references
> them; they can be deleted at any time without affecting the project. Their
> findings are preserved in the durable records: `agents/bugs.md`, `agents/skill.md`,
> and the Hermes skills (`position-exit-forensics`, `mt5-broker-integration`).

Naming conventions you may find here:
- `scratch_*.py` / `_scratch_*.py` — Python probes (audit DB reads, log analysis, dry-runs)
- `scratch_*_out.txt` — captured probe output
- `data_gate_*.py` — MT5 data-gate diagnostics (Phase 14/15)
- `capture_mt5_contract.py`, `check_js_tmp.js` — one-off contract/JS verification helpers

---

## 📊 50-Dimensional Feature Matrix

| Index | Name | Category | Description |
| :--- | :--- | :--- | :--- |
| `0` | `returns` / `log_returns` | Microstructure | M1 close returns (Z-scored, clipped `[-3.0, +3.0]`) |
| `2` | `volatility_atr` | Microstructure | 14-period ATR / Close |
| `3` | `rsi_14` | Oscillators | Scaled `(RSI-50)/25` |
| `4-6` | `macd_line/signal/hist` | Trend | 12/26 EMA diff, 9-EMA signal, histogram |
| `7-11` | `bb_upper/middle/lower/width/pband` | Volatility | 20-period Bollinger Bands |
| `12-14` | `adx_14`, `plus_di`, `minus_di` | Trend | ADX/50 scaled, directional indicators |
| `15-16` | `stoch_k`, `stoch_d` | Oscillators | Scaled `(%K-50)/25`, `(%D-50)/25` |
| `17` | `obv` | Volume | Normalized on-balance volume slope |
| `18` | `vwap` | Volume | Price relative to VWAP |
| `19` | `spread_norm` | Microstructure | `Spread / ATR` |
| `20-25` | `wick_anatomy` | Price Action | Upper/lower wick & body ratios `[0,1]` |
| `26-31` | `ofi_microstructure` | Order Flow | Order Flow Imbalance, tick velocity |
| `32-39` | `multi_tf_momentum` | Multi-TF | M15/M30/H1/H4 trend & momentum alignment |
| `40-45` | `sr_clustering` | Structure | Dynamic support/resistance proximity & density |
| `46` | `smc_bos` | SMC | Break-of-Structure flag `{-1,0,+1}` |
| `47` | `smc_equilibrium` | SMC | 50% impulse-zone distance (Z-scored) |
| `48` | `smc_liquidity_sweep` | SMC | Liquidity pool piercing `{-1,0,+1}` |
| `49` | `smc_ote_align` | SMC | 50%-61.8% OTE zone alignment `[0,1]` |

**Contract invariants:** exactly 50 features; every vector passes `validate_and_fallback()` (NaN/Inf/bounds violations fall back deterministically, never crash); schema registry forward-declares 60D (`scalp_v2`) and 350D (`scalp_v3`); the live contract stays 50D (`scalp_v1`).

---

## 🧠 Self-Learning & Validation Loop (Phases 08-12)

The engine forms a real closed learning loop, with every stage evidence-driven and safety-gated:

```text
LIVE TRADE ──► ACCOUNTING (audit_ledger, authoritative PnL)
   ▲              │
   │              ▼
   │          EXPERIENCE (immutable audit_experiences + outcomes,
   │                      broker-ticket identity bridge)
   │              │
   │              ▼
   │          TRADE AUTOPSY (strategy/entry/risk/management/exit quality,
   │                      behavior detection, lifecycle timeline)
   │              │
   │              ▼
   │          STRATEGY INTELLIGENCE (lifecycle + confidence + expectancy R,
   │                      self-heal rebuildable)
   │              │
   │              ▼
   │          RESEARCH (causal dataset ─► backtest ─► walk-forward
   │                      ─► OOS gate ─► robustness ─► score ─► registry)
   │              │
   │              ▼
   │          MODEL TRAINING (candidate staging ─► 12 gates ─► CHALLENGER)
   │              │
   │              ▼
   │          SHADOW TRADING (same live vector, simulated=True,
   │                      comparison ─► promotion vetoes)
   │              │
   │              ▼
   │          NEWS INTELLIGENCE (ingest ─► dedup ─► analyze ─► decay
   │                      ─► bounded news gate on the live tick path)
   │              │
   └── FUTURE DECISIONS (pre-trade gates consume experience + bounded news
                  context; nothing ever promotes itself to LIVE automatically)
```

**Hard safety invariants across ALL phases (08-12):**

- Research, training, shadow and news workers NEVER place, modify or close an order (`asyncio.to_thread` + no adapter/order-manager/risk-engine imports by construction, test-enforced).
- A strategy/model candidate NEVER becomes LIVE automatically — promotion is strictly operator-gated and veto-protected.
- OOS failure ⇒ REJECTED regardless of in-sample performance.
- News influence is a **bounded confidence modifier** only (boost ≤ 0.05, penalty ≤ 0.10) — news can never force a trade or bypass risk/exposure/kill-switch.
- Schema mismatch (feature dimension/class count/scaler) fails explicitly — never silent reshape/truncate.
- All derived intelligence (strategy scores, accounting reports, research registry, shadow summaries, news state) is REBUILDABLE from its immutable ledger; raw ledger rows are never modified.
- The live tick path never blocks on Phase 08-12 work (queued persistence, TTL/rate-limited lookups, out-of-loop workers).

---

## 🚀 How To Run & Deploy

### 1. System Requirements & Prerequisites
- **OS:** Windows 10/11 x64 (for Direct Win32 MT5 IPC) or Linux (for Containerized Gateway runs).
  Windows **ARM64 is not supported** by the dependency stack (PyTorch/Polars/MetaTrader5 ship no
  ARM64 wheels) — the installer and `nexus doctor` report this explicitly.
- **Python:** Python 3.11.x (source install) — **no Python needed for the packaged release**:
  the installer bundles the full runtime via PyInstaller.
- **Broker:** MetaTrader 5 Terminal logged into a Live/Demo account with **"Allow Algo Trading"** enabled in Terminal settings.

### 1b. End-User Installation (packaged release — no Python required)

Normal users download from GitHub Releases:

1. **`NexusScalpEngine-<version>-win-x64-setup.exe`** — installer with
   compatibility check, shortcuts, uninstall entry. Or the **portable ZIP**
   (`NexusScalpEngine-<version>-win-x64.zip`).
2. First run opens the **setup wizard**: compatibility report → mode
   (default **PAPER**, never silently LIVE) → symbol → health check.
3. Operate with the bundled `nexus` CLI:
   `nexus health` · `nexus doctor` · `nexus start [--mode paper|shadow|live]` ·
   `nexus logs` · `nexus repair` · `nexus update` · `nexus uninstall`.
4. User data (config/logs/databases/models) is stored under
   `%LOCALAPPDATA%\NexusScalpEngine` and survives upgrades/uninstalls.

Full details: [`docs/RELEASE.md`](docs/RELEASE.md) · release pipeline:
`.github/workflows/release.yml` · build scripts: `scripts/build/`.

### 2. Developer Installation (source)
```bash
# 1. Clone the repository
git clone https://github.com/your-org/NexusTradingForexBot.git
cd NexusTradingForexBot

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate it
#    Windows PowerShell:
.\.venv\Scripts\Activate.ps1
#    Linux/macOS:
source .venv/bin/activate

# 4. Upgrade pip & install the package in editable mode (+dev tooling)
pip install --upgrade pip
pip install -e .[dev]

# 5. (Recommended) Smoke-test the whole toolchain — no broker needed
python -m pytest tests/unit -q
```

> 💡 **Windows tip:** run every command from the repo root *after* activating `.venv`. If `pip` complains about an external environment, use `python -m pip` instead of bare `pip`.

### 3. Pre-Flight Infrastructure Diagnostics
```bash
python NexusTradingForexBot.py --doctor
```
Expected Output:
```text
System Runtime Diagnostic Summary
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Subsystem             ┃ Status    ┃ Operational Details                                   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Python Runtime        │ PASS      │ Python 3.11.15                                        │
│ Host Platform         │ OK        │ win32                                                 │
│ Native MT5 IPC Driver │ AVAILABLE │ Direct Win32 IPC Available for Local Terminal Process │
│ Configuration File    │ VALID     │ configs\live.yaml (Symbol: XAUUSD)                    │
└───────────────────────┴───────────┴───────────────────────────────────────────────────────┘
All Infrastructure Pre-Flight Checks Passed Successfully!
```
The doctor also runs **automatically** before every launch — the bot refuses to start if any pre-flight check fails. It checks: Python runtime, host platform, native MT5 driver availability, and YAML config validity. If you see `UNAVAILABLE` for the MT5 driver, make sure the **MetaTrader 5 terminal is installed, running and logged in** before relaunching.

### ⚠️ 4. CRITICAL — Read This Before Your First Launch

> 🚨 **This bot places REAL trades with REAL money if you run it in LIVE mode.**
> The default `configs/live.yaml` **already contains your real MT5 credentials
> and `mode: "LIVE"`**. Never let the bot run unattended with live capital
> until you have verified every setting below.

**The safe first-run workflow (Demo → Shadow → Live):**

| Step | What you do | Why it matters |
| :--- | :--- | :--- |
| 1️⃣ | **Log into a DEMO account first** in MetaTrader 5 (`File → Open an Account → Practice/Demo`). Confirm the account number is a **demo** (usually 5-8 digits, labeled "Demo") before touching this bot. | LIVE accounts and demo accounts can look identical in MT5's login window. One wrong credential switch = real money at risk. |
| 2️⃣ | In MT5: **`Tools → Options → Expert Advisors → tick "Allow Algo Trading"`**. | Without this the bot's orders are rejected. |
| 3️⃣ | **Copy `configs/live.yaml` → `configs/demo.yaml`** and edit: set `execution.mode: "DEMO"`-equivalent-safe values for your copy (keep `mode: PAPER`-style safety via the `ExecutionMode`; see note below), set `mt5.account` / `mt5.password` / `mt5.server` to the **demo credentials**, and verify `risk.max_concurrent_positions: 1`, `risk.risk_per_trade_pct` (e.g. 0.5), `risk.max_account_drawdown_pct`. | NEVER edit `live.yaml` while real money can be touched; a separate demo config is disposable. |
| 4️⃣ | **Run the bot in SHADOW (no-execution) first** — see run table below. Shadow streams live market data and makes predictions but **places zero orders**. Watch it for days, review dashboard + Telegram reports. | Proves the model, signals and gates behave before real execution. |
| 5️⃣ | **Run the automated test suite** weekly (`pytest tests/unit tests/integration`) — it validates risk clamps, exposure limits and the Phase 08-12 safety contracts. | Catches regressions that would otherwise surface on a live account. |
| 6️⃣ | **Only then consider LIVE** with a small balance you can afford to lose, starting at `risk_per_trade_pct: 0.25` or lower. | Financial markets, especially leveraged Gold (XAUUSD) scalping, carry extreme risk. Verify every config value twice. |

> 🛡️ **You are always responsible for the account.** The engine enforces hard
> safety clamps (single-position exposure, `HARD_MAX_LOTS = 10.0`, margin
> ≤20% of free margin, circuit breaker → SAFE_MODE after 3 rejections, Max
> Drawdown stop at `risk.max_account_drawdown_pct`) — but these protect the
> strategy, not your capital from market volatility. Test everything on a demo
> account first, and keep the bot supervised during early live runs.

### 🚀 Running the Engine (Demo / Shadow / Live)

| Entrypoint | Command | Purpose | Status |
| :--- | :--- | :--- | :--- |
| **Doctor** | `python NexusTradingForexBot.py --doctor` | Infrastructure diagnostics (also runs automatically before launch) | 🟢 VERIFIED |
| **Primary CLI** | `python -m nexus_scalp.cli.main run --config configs/live.yaml` | Production entrypoint (Typer CLI) | 🟢 VERIFIED |
| Root wrapper | `python main.py` | Redirects to the CLI app | 🟢 VERIFIED |
| Legacy wrapper | `python NexusTradingForexBot.py --config configs/live.yaml` | Full launcher: doctor + symbol override + adapter binding | 🟢 VERIFIED (legacy) |
| **Symbol override** | `python NexusTradingForexBot.py --symbol EURUSD` | Trade a different symbol than the config (e.g. `--symbol GBPUSD`) | 🟢 VERIFIED |
| **Remote gateway** | `python NexusTradingForexBot.py --gateway` | Force ZMQ Remote Gateway mode (non-Windows / remote MT5 host) | 🟢 VERIFIED |
| Direct web server | `uvicorn nexus_scalp.web.server:app` | FastAPI alone (debug/dev) | 🟢 VERIFIED |
| Container | `docker compose up` (entrypoint.sh) | Containerized run | 🟢 VERIFIED |

> **Shadow-first recommendation:** to evaluate the engine against **live market data with ZERO order authority**, run the bot on the SHADOW path (Phase 11) — the Challenger/Champion pair streams the same live feature vectors and records decisions as `simulated=True`, placing no orders. Watch the dashboard's News/Shadow tabs and your Telegram reports for several sessions before any real execution. (Note: `ExecutionMode` also defines `PAPER`/`REPLAY`/`BACKTEST` for simulation; the paper adapter is exercised through the test suite — there is currently no CLI flag to hot-swap to the paper adapter, so use SHADOW or a demo account for risk-free validation.)

**Optional — enable the News Intelligence Engine (Phase 12):** add a `news:` block to your YAML config, e.g.
```yaml
news:
  enabled: true
  db_path: "artifacts/news.db"        # dedicated DB, never mixed with audit.db
  worker_interval_sec: 60
  analysis:
    mode: "HYBRID"                    # LOCAL_ONLY | API_ONLY | HYBRID
    provider: ""                      # openai-compatible / gemini / anthropic / openrouter
    api_base_url: ""
    model: ""
```
Omitting the block leaves the subsystem disabled with graceful degradation (API returns `available=False`, UI shows OFF).

### 5. Accessing the Real-Time Control Center
👉 **`http://localhost:8080`** (or configured port)
- Live 150+ bar chart with green/red Order Blocks, yellow sweep tags, entry/SL/TP lines with risk tooltips.
- Control Center tabs: Overview · Strategy Research · **News Intelligence** · Scalping Rules · Account (Performance & Intelligence) · Debug Hub.
- Algorithm Live Tuner (Bot Settings) hot-swaps parameters without restart.

---

## 🌐 REST API Surface (selected)

| Route | Method | Purpose |
| :--- | :---: | :--- |
| `/api/status` | GET | Live telemetry: state, balance, regime, visual overlays |
| `/api/account/performance`, `/api/account/{summary,trades,growth,drawdown}` | GET | Canonical accounting (Phase 08) — no synthetic numbers |
| `/api/intelligence/*` | GET/POST | Brain summary, timelines, autopsies, behavior, evolution, self-heal (Phase 09) |
| `/api/research/*` | GET/POST | Registry, runs, discover, validate, self-heal (Phase 09B) |
| `/api/models/*` | GET/POST | Champion/Challenger registry, runs, comparisons, worker control (Phase 10) |
| `/api/models/shadow/*` | GET/POST | Shadow runs, decisions, comparisons, promotion vetoes, worker control (Phase 11) |
| `/api/news*` | GET/POST | Live feed, state, sources, health, analyze, refresh, self-heal, trade links (Phase 12) |
| `/api/algo/config` | GET/PUT | Hot-swappable live tuner parameters |
| `/api/rules`, `/api/rules/toggle` | GET/POST | 30+ rule matrix introspection & toggling |
| `/api/positions/modify`, `/api/positions/close` | POST | Manual position management |
| `/api/debug/*` | GET/POST | Features, model test, health, IPC telemetry, observability stats |
| `/api/ticks/stream` | SSE | Zero-latency tick/telemetry stream (enums pre-serialized) |
| `/web`, `/ws` | WS | Bidirectional low-latency visualizer channel |

---

## 🧪 Verification & Test Suite

The engine includes an extensive, hardened test suite covering unit logic, PyTorch tensor contracts, risk clamps, accounting invariants, experience idempotency, research causality/OOS gates, model-lifecycle gates, shadow safety contracts and news-gate bounds.

### Run All Unit & Integration Tests:
```bash
pytest tests/unit/ tests/integration/ -v
```

### Phase Suites (Phases 08-12):
```bash
pytest tests/unit/test_accounting_core.py            # 64 tests - accounting core
pytest tests/unit/test_experience_intelligence.py    # Phase 08 experience
pytest tests/unit/test_intelligence_phase09.py       # 18 tests - Phase 09 brain
pytest tests/unit/test_research_phase09b.py          # 45 tests - Phase 09B research
pytest tests/unit/test_model_lifecycle_phase10.py    # 32 tests - Phase 10 training
pytest tests/unit/test_shadow_phase11.py             # 35 tests - Phase 11 shadow
pytest tests/unit/test_news_phase12.py               # 66 tests - Phase 12 news intelligence
pytest tests/unit/test_log_autopsy_fixes.py          # BUG-013..018 regression guards
pytest tests/integration/test_news_api.py            # Phase 12 API + LiveEngine wiring
```

### Quality Gates (beforePush.sh / beforePush.ps1):
- Ruff lint (`ruff check . --fix --unsafe-fixes`)
- Ruff format (`ruff format .`)
- Mypy strict static analysis (`mypy src/nexus_scalp`)
- Full unit test suite (`pytest tests/unit`)
> Note: on Windows, `beforePush.ps1` renders correctly only under **pwsh (PowerShell 7)** — Windows PowerShell 5.1 mangles the CRLF+emoji script.

### Run Coverage Report:
```bash
pytest --cov=src --cov-report=term-missing
```

### Direct Database Audit Verification:
```bash
# Check action distributions
sqlite3 artifacts/audit.db "SELECT action, COUNT(*) FROM audit_signals GROUP BY action;"

# Inspect full trade autopsy ledger
sqlite3 artifacts/audit.db "SELECT ticket, symbol, net_pnl_usd, exit_mechanism, MAE_usd, MFE_usd FROM audit_ledger ORDER BY close_time DESC LIMIT 5;"

# News intelligence (Phase 12, separate DB)
sqlite3 artifacts/news.db "SELECT state, COUNT(*) FROM articles GROUP BY state;"
```

---

## 🤝 Call for Open-Source Collaboration & Talent Invitation

We are actively expanding **Nexus Scalp Engine** into a global open-core quantitative framework. We invite world-class engineers, quantitative researchers, and market practitioners to collaborate with us.

1. **Quantitative ML / PyTorch Researchers:** Enhancing `scalp_net.py` with Spatio-Temporal Graph Neural Networks (GNNs) or Mamba time-series state-space architectures; Transformer-based Order Flow Imbalance (OFI) alpha generators; news-aware NLP embeddings for the Phase 12 gate.
2. **Low-Latency C++ & Rust Systems Engineers:** Replacing the Win32 IPC wrapper with a zero-copy, shared-memory C++20 / Rust native extension for sub-millisecond execution; direct FIX Protocol 4.4 / 5.0 adapters for LMAX, Saxo, and Interactive Brokers.
3. **Institutional Forex & Crypto Traders:** Refining Smart Money Concepts (SMC) rules, ICT liquidity sweep parameters, and Order Block mitigation logic; multi-asset cross-arbitrage and statistical mean-reversion policies; forward shadow-outcome resolution (Phase 11 continuation).

### How to Contribute
- **Fork & PR:** Check out open issues or submit feature PRs following PEP 8, strict MyPy typing, and pytest coverage.
- **Join Discussion:** Open an issue with the `[Research]` or `[Proposal]` tag to discuss architectural ideas.

---

## 🛡️ License & Operational Safety Disclaimer

**DISCLAIMER:** Algorithmic trading and quantitative speculation in financial markets (especially leveraged XAUUSD/Gold scalping) carry immense financial risk. This engine is provided strictly for educational, academic research, and simulation purposes. Always perform rigorous backtesting and forward paper-trading before committing capital.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*