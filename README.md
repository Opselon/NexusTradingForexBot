
# 👑 Nexus Scalp Engine (NSE) v8.0
### *Production-Grade High-Frequency Quantitative Scalping Infrastructure*

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![C++ IPC](https://img.shields.io/badge/C%2B%2B-20_IPC-00599C.svg?style=for-the-badge&logo=cplusplus&logoColor=white)](https://isocpp.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5_Terminal-2962FF.svg?style=for-the-badge&logo=metatrader5&logoColor=white)](https://www.mql5.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.tech/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL_Ledger-003B57.svg?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal_Event--Driven-6f42c1.svg?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Production-Hardened-success.svg?style=for-the-badge)]()
<p align="center">
  <img src="docs/web.png" alt="Nexus Trading Dashboard" width="100%">
</p>

> **Nexus Scalp Engine (NSE)** is an enterprise-class, event-driven quantitative trading runtime engineered for sub-second scalping on **XAUUSD (Gold)** and major currency pairs. NSE unifies deep learning inference, real-time market microstructure analysis, and high-frequency execution into a single, self-healing framework.
> 
> Driven by a **3-Layer TCN + Self-Attention Neural Network (ScalpNet v3)**, a **50-Dimensional Causal Feature Engine**, and an **Autonomous 30-Rule SMC Policy Matrix**, NSE eliminates lookahead bias, prevents catastrophic drawdown, and executes institutional Order Block, Fair Value Gap (FVG), and Liquidity Sweep setups directly on MetaTrader 5 via native C++ IPC bindings.

---

## 🏛️ System Architecture Blueprint

NSE follows a strict **Hexagonal (Ports-and-Adapters) Event-Driven Architecture**, completely isolating execution platforms, machine learning models, and network adapters into modular, self-healing subsystems.

```text
╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║                                 NEXUS HIGH-FREQUENCY CORE                                     ║
║                                                                                               ║
║  ┌───────────────────────┐     ┌───────────────────────┐     ┌─────────────────────────────┐  ║
║  │ 50D Causal Feature    │ ──► │ PyTorch ScalpNet v3   │ ──► │  30-Rule SMC Policy Matrix  │  ║
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
                                               │ Win32 C++ Direct IPC (Zero-Copy)
                                               ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│                              LOCAL METATRADER 5 TERMINAL                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Institutional Liquidity Execution (LMAX / Pepperstone / IC Markets / FXCM Direct Feed)   │  │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔥 Key Technological Innovations

### 1. Predictive SMC "God-Mode" Execution Engine
- **50% Impulse Equilibrium Filtering:** Automatically calculates the 50% midpoint of impulse legs. Short trades below 50% (Discount) or Long trades above 50% (Premium) are hard-gated (`OB_BELOW_50_PERCENT_EQUILIBRIUM`).
- **BOS & CHoCH Validation:** Verifies structural displacement before tagging Order Blocks (OB).
- **Liquidity Sweeps & 50-60% OTE Fibs:** Identifies stop-hunts (`liq`) and secondary sub-leg optimal trade entries.
- **SMC Veto Power:** Pristine SMC setups (OB + Sweep + BOS) override slow Higher-Timeframe trend conflicts.

### 2. PyTorch ScalpNet v3 with Atomic Checkpoint Rollbacks
- **3-Layer TCN + Self-Attention Network:** Processes 50-dimensional normalized feature tensors natively.
- **Inverse Class Frequency Weighting:** Dynamically weights `nn.CrossEntropyLoss` during online fine-tuning to prevent prediction collapse or short-side bias.
- **Atomic Model Rollback System:** Evaluates model validation loss/entropy after online fine-tuning. If a newly trained checkpoint degrades quality, the engine **atomically rolls back** to the previous healthy checkpoint and logs a **CRITICAL RED TERMINAL ALERT** (`\033[41m`).

### 3. Dynamic Position Management & "Falling Knife" Protection
- **Order Churning Throttle:** Implements a 15-second modification throttle to prevent MT5 pending order spam (resolving retcodes `10013`/`10015`).
- **Contextual Order Cancellation:** If an active position (e.g., `SELL`) is in deep profit and accelerating downwards, the Order Manager automatically **cancels or pushes away opposite `BUY_LIMIT` orders** to avoid catching falling knives.
- **Dynamic Lot Sizing Clamps:** Slices lot sizes based on structural ATR stop-loss distances, enforcing broker `volume_max`, `margin_free` pre-checks, and hard lot caps to prevent MT5 retcode `10019` (NO_MONEY).

### 4. Real-Time HTML5 Canvas Visualizer & Live Tuner (`/web`)
- **150+ Bar History Buffer:** High-resolution interactive M1 candlestick chart renderer with auto-scale and zoom.
- **Multi-Layer Transparent Overlays:** Renders transparent colored rectangles for AI-validated liquidity zones:
  - 🟩 **Green Box (Opacity 0.25):** Bullish Order Blocks / Bullish FVGs (`ob 85%`).
  - 🟥 **Red Box (Opacity 0.25):** Bearish Order Blocks / Bearish FVGs.
  - 🟨 **Gold Box (Opacity 0.35):** Swept Liquidity Pools (`liq`).
- **Interactive Execution Target Lines:** Displays solid Blue (Entry), dashed Red (SL), and dashed Green (TP) order lines with real-time dollar risk/reward tooltips.
- **Algorithm Live Tuner Panel:** Exposes internal hyperparameters (`atr_sl_buffer_multiplier`, `min_risk_reward_ratio`, `ai_zone_confidence_threshold`) to REST endpoints (`PUT /api/algo/config`), allowing **2-second in-memory hot-swapping** without restarting the runtime.

### 5. Institutional Financial Accounting Ledger
- **Complete Post-Trade Autopsy:** Logs every closed trade into SQLite (`artifacts/audit.db`) with `MAE` (Maximum Adverse Excursion), `MFE` (Maximum Favorable Excursion), `entry_rule_id`, `exit_mechanism` (`TP_HIT`, `HARD_SL_HIT`, `RISK_FREE_SL_HIT`, `TIME_DECAY_EXIT`), initial/final SL prices, and running equity snapshots.

### 6. Experience-Driven Strategy Intelligence (Phase 08)
- **Immutable Experience Ledger:** Every decision is recorded at decision-time with deterministic `idempotency_key` dedup; every outcome is appended with the broker ticket as the identity bridge. Duplicate callbacks, reconnect replays, and worker retries can never create duplicate evidence.
- **Pre-Trade Experience Gate:** Runs after the signal policy, before risk sizing. A retired strategy family (`PERSISTENT_NEGATIVE_EXPECTANCY`) is REJECTED; a degraded family is confidence-penalized; a validated high-expectancy family earns a bounded boost. The gate only down-ranks, never upgrades, and never touches position-management actions.
- **TTL-Cached + Rate-Limited Hot-Path Lookups:** Score refreshes are cached (30s) and budget-limited so the live tick path never blocks on SQLite.

### 7. Unified Accounting & Performance Intelligence Core (Phase 08)
- **ONE canonical accounting authority:** REST API, dashboard, background worker and Experience all read performance truth through `AccountingCore` — no consumer computes PnL/drawdown/period boundaries independently.
- **NO SYNTHETIC NUMBERS:** any metric without stored evidence renders `n/a`, never a fake `0.0`.
- **ONE PERIOD POLICY (UTC), ONE DRAWDOWN METHODOLOGY, NET PNL COMPUTED EXACTLY ONCE, IDEMPOTENT CLOSURE** — institutional invariants enforced by 64 unit tests.

### 8. Trade Intelligence Brain (Phase 09)
- **Immutable Position Lifecycle Timeline:** every open position emits typed events (`POSITION_CREATED`, `POSITION_PROFIT_GIVEBACK`, `POSITION_DEGRADING`, ...) with full market/decision context.
- **Trade Autopsy Engine:** separates strategy quality, entry quality, risk quality, management quality and exit quality — a `MANAGED_LOSS` is never mistaken for a broken strategy.
- **Behavior Detection:** measurable, deterministic patterns (`EARLY_EXIT`, `PANIC_EXIT`, `OVERTRADING`, ...) with timestamps and severity.
- **Strategy Evolution:** controlled candidate discovery from history; a candidate is NEVER live until backtested and validated.
- **Pre-Trade Intelligence Gate:** layered WARN tier + bounded suitability score — only down-grades.

### 9. Strategy Research, Backtest & Validation Engine (Phase 09B)
- **Causal-safe dataset builder** over the immutable experience ledger (future outcomes can never enter discovery).
- **Deterministic friction-aware backtest** (spread/slippage/commission) + purged/embargoed temporal walk-forward + hard OOS gate (OOS failure ⇒ REJECTED even with high win rate).
- **Robustness engine** (spread/slippage/latency stress, degradation measured) + explainable multi-dimension score with small-sample protection.
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
- **Bounded, failure-isolated:** queued persistence, worker aggregation via `asyncio.to_thread`, schema DDL guarded once per process; a shadow failure can never stop trading.

---

## 📂 Repository Layout

```text
NexusTradingForexBot/
├── .github/workflows/check_and_build.yml    # CI/CD Workflow (Ruff, Mypy, Pytest)
├── configs/
│   ├── base.yaml                            # System parameters, risk limits, & model configs
│   └── live.yaml                            # Production live environment configurations
├── src/nexus_scalp/
│   ├── accounting/                       # PHASE 08: Unified Accounting & Performance Core
│   ├── experience/                       # PHASE 08: Experience-Driven Strategy Intelligence
│   ├── intelligence/                     # PHASE 09: Trade Intelligence Brain
│   ├── research/                         # PHASE 09B: Strategy Research, Backtest & Validation
│   ├── model_lifecycle/                  # PHASE 10: Controlled Model Training & Challenger Engine
│   ├── shadow/                           # PHASE 11: Challenger Shadow Trading & Evaluation
│   ├── adapters/
│   │   ├── database/audit_repository.py      # SQLite WAL ledger & signal telemetry DB
│   │   └── mt5/mt5_adapter.py               # Win32 C++ IPC Direct Local MT5 Driver
│   ├── application/live_engine.py           # Master event loop, Safety State Machine, & Bridge
│   ├── configuration/config.py              # Pydantic Settings & AlgoConfig schema
│   ├── domain/                              # Pure immutable domain models & enums
│   ├── execution/order_manager.py           # In-trade tracker, SL shifts, MAE/MFE, & throttling
│   ├── features/
│   │   ├── scalp_features.py                # 50D Zero-lookahead Causal Feature Engine + SMC
│   │   └── regime_classifier.py             # O(1) Schmitt-Trigger Market Regime Classifier
│   ├── models/scalp_net.py                  # PyTorch TCN + Self-Attention Neural Network
│   ├── risk/risk_engine.py                  # Fractional Kelly, Lot Sizing, & Margin Clamps
│   ├── signals/
│   │   ├── policy.py                        # SMC God-Mode, Pre-trade Gatekeeper, & Signals
│   │   └── rule_matrix.py                   # 30-Rule Matrix Engine with 5s Async Hot-Reload
│   ├── training/walk_forward_trainer.py    # Walk-Forward Fine-Tuning & Atomic Rollbacks
│   └── web/server.py                        # FastAPI Async Server, WebSockets, & REST APIs
├── Web/
│   ├── index.html                           # Control Center UI Dashboard HTML
│   └── app.js                               # HTML5 Canvas Chart Renderer & Live Tuner JS
├── tests/
│   ├── unit/                                # Unit tests for all individual modules
│   └── integration/test_hardened_protocol.py # End-to-end integration & safety tests
├── NexusTradingForexBot.py                  # Primary system launcher & pre-flight doctor
├── Dockerfile & docker-compose.yml          # Containerized orchestration
└── pyproject.toml & requirements.txt        # Production dependencies (PEP 621)
```

---

## 📊 50-Dimensional Feature Matrix

| Feature Index | Name | Category | Description |
| :--- | :--- | :--- | :--- |
| `feat_0` - `feat_7` | Microstructure & Volatility | Microstructure | Live displacement, 14-period ATR, spread ratios, tick velocity. |
| `feat_8` - `feat_15` | Candlestick Anatomy | Price Action | Body-to-range ratio, wick absorption, Pinbars, Engulfing flags, CLV. |
| `feat_16` - `feat_23` | Swing & Structure | Chart Patterns | Distance to 20-bar High/Low, compression ratios, stop-hunt depth. |
| `feat_24` - `feat_27` | Market Sessions | Session Timing | Binary triggers for Asian, London, NY, and London-NY overlap window. |
| `feat_28` - `feat_33` | Time-Series Lags | Stat-Arb | Log returns (lag 1-3), ATR ratio lag-1, Volume Z-score lag-1. |
| `feat_34` - `feat_39` | Ichimoku Kinko Hyo | Trend | Tenkan/Kijun diff, Senkou Span cloud boundaries, TK Cross flags. |
| `feat_40` - `feat_45` | Indicators & Stat-Arb | Oscillators | RSI-14 normalized, EMA-21/50 distances, Cross-Asset Z-score spreads. |
| **`feat_46`** | **`feat_ob_valid_bos`** | **SMC Engine** | Binary flag (1/0) indicating Order Block created a confirmed BOS. |
| **`feat_47`** | **`feat_ob_equilibrium_ratio`**| **SMC Engine** | Position of OB relative to 50% impulse equilibrium (0.0 to 1.0). |
| **`feat_48`** | **`feat_ob_liquidity_swept`** | **SMC Engine** | Binary flag (1/0) indicating liquidity sweep presence (`liq`). |
| **`feat_49`** | **`feat_ob_fib_50_60_align`** | **SMC Engine** | Proximity of OB to 50%-60% Fibonacci OTE retracement zone. |

---

## 🧠 Self-Learning & Validation Loop (Phases 08-11)

The engine forms a real closed learning loop, with every stage evidence-driven
and safety-gated:

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
   └── FUTURE DECISIONS (pre-trade gates consume experience; nothing ever
                  promotes itself to LIVE automatically)
```

**Hard safety invariants across all phases:**

- Research, training, shadow and experience workers NEVER place, modify or
  close an order (`asyncio.to_thread` + no adapter/order-manager/risk-engine
  imports by construction, test-enforced).
- A strategy/model candidate NEVER becomes LIVE automatically — promotion is
  strictly operator-gated and veto-protected.
- OOS failure ⇒ REJECTED regardless of in-sample performance.
- Schema mismatch (feature dimension/class count/scaler) fails explicitly —
  never silent reshape/truncate.
- All derived intelligence (strategy scores, accounting reports, research
  registry, shadow summaries) is REBUILDABLE from the immutable ledger; raw
  ledger rows are never modified.
- The live tick path never blocks on Phase 08-11 work (queued persistence,
  TTL/rate-limited lookups, out-of-loop workers).

---

## 🚀 How To Run & Deploy (Zero to Live in 5 Minutes)

### 1. System Requirements & Prerequisites
- **OS:** Windows 10/11 (for Direct Win32 MT5 IPC) or Linux (for Containerized Gateway runs).
- **Python:** Python 3.11.x installed.
- **Broker:** MetaTrader 5 Terminal logged into a Live/Demo account with **"Allow Algo Trading"** enabled in Terminal settings.

### 2. Installation
Clone the repository and set up a clean Python 3.11 virtual environment:

```bash
# Clone the repository
git clone https://github.com/your-org/NexusTradingForexBot.git
cd NexusTradingForexBot

# Create virtual environment
python -m venv .venv

# Activate Virtual Environment
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Upgrade pip & install dependencies in editable mode
pip install --upgrade pip
pip install -e .[dev]
```

### 3. Pre-Flight Infrastructure Diagnostics
Run the built-in system doctor check to verify host platform, MT5 C++ IPC driver availability, and YAML syntax:

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

### 4. Running the Live Trading Engine
Ensure MetaTrader 5 Terminal is open and logged in, then launch the engine:

```bash
# Launch via primary entry point
python NexusTradingForexBot.py --config configs/live.yaml

# Or via CLI operational command
nse run --config configs/live.yaml
```

### 5. Accessing the Real-Time Control Center
Open your browser and navigate to the Web Dashboard:
👉 **`http://localhost:8080`** (or configured port)

- View live 150+ bar history with green/red Order Blocks and yellow sweep tags.
- Monitor active trades, live PnL, MAE/MFE metrics, and Ichimoku cloud state.
- Adjust sliders in the **"Algorithm Live Tuner"** under Bot Settings to hot-swap parameters in real-time.

---

## 🧪 Verification & Test Suite

The engine includes an extensive, hardened test suite covering unit logic,
PyTorch tensor contracts, risk clamps, accounting invariants, experience
idempotency, research causality/OOS gates, model-lifecycle gates and shadow
safety contracts.

### Run All Unit & Integration Tests:
```bash
pytest tests/unit/ tests/integration/ -v
```

### Phase Suites (Phases 08-11):
```bash
pytest tests/unit/test_accounting_core.py            # 66 tests - accounting core
pytest tests/unit/test_experience_intelligence.py    # Phase 08 experience
pytest tests/unit/test_intelligence_phase09.py       # 18 tests - Phase 09 brain
pytest tests/unit/test_research_phase09b.py          # 45 tests - Phase 09B research
pytest tests/unit/test_model_lifecycle_phase10.py    # 32 tests - Phase 10 training
pytest tests/unit/test_shadow_phase11.py             # 35 tests - Phase 11 shadow
pytest tests/unit/test_log_autopsy_fixes.py          # BUG-013..018 regression guards
```

### Quality Gates (beforePush.sh / beforePush.ps1):
- Ruff lint (`ruff check . --fix --unsafe-fixes`)
- Ruff format (`ruff format .`)
- Mypy strict static analysis (`mypy src/nexus_scalp`)
- Full unit test suite (`pytest tests/unit`)

### Run Coverage Report:
```bash
pytest --cov=src --cov-report=term-missing
```

### Direct Database Audit Verification:
Query the SQLite audit log directly to inspect signal history and trade autopsies:

```bash
# Check action distributions
sqlite3 artifacts/audit.db "SELECT action, COUNT(*) FROM audit_signals GROUP BY action;"

# Inspect full trade autopsy ledger
sqlite3 artifacts/audit.db "SELECT ticket, symbol, net_pnl_usd, exit_mechanism, MAE_usd, MFE_usd FROM audit_ledger ORDER BY close_time DESC LIMIT 5;"
```

---

## 🤝 Call for Open-Source Collaboration & Talent Invitation

We are actively expanding **Nexus Scalp Engine** into a global open-core quantitative framework. We invite world-class engineers, quantitative researchers, and market practitioners to collaborate with us.

### We are seeking contributions in the following specialized domains:

1. **Quantitative ML / PyTorch Researchers:**
   - Enhancing `scalp_net.py` with Spatio-Temporal Graph Neural Networks (GNNs) or Mamba time-series state-space architectures.
   - Developing Transformer-based Order Flow Imbalance (OFI) alpha generators.

2. **Low-Latency C++ & Rust Systems Engineers:**
   - Replacing the Win32 IPC wrapper with a zero-copy, shared-memory C++20 / Rust native extension for sub-millisecond execution.
   - Building direct FIX Protocol 4.4 / 5.0 adapters for LMAX, Saxo, and Interactive Brokers.

3. **Institutional Forex & Crypto Traders:**
   - Refining Smart Money Concepts (SMC) rules, ICT liquidity sweep parameters, and Order Block mitigation logic.
   - Designing multi-asset cross-arbitrage and statistical mean-reversion policies.

### How to Contribute
- **Fork & PR:** Check out open issues or submit feature PRs following PEP 8, strict MyPy typing, and 100% pytest coverage.
- **Join Discussion:** Open an issue with the `[Research]` or `[Proposal]` tag to discuss architectural ideas.

---

## 🛡️ License & Operational Safety Disclaimer

**DISCLAIMER:** Algorithmic trading and quantitative speculation in financial markets (especially leveraged XAUUSD/Gold scalping) carry immense financial risk. This engine is provided strictly for educational, academic research, and simulation purposes. Always perform rigorous backtesting and forward paper-trading before committing capital.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*
```
