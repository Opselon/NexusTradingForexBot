# ⚡ Nexus Scalp Engine (NSE) v7.0 — Institutional Quantitative Infrastructure

[![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-gold.svg?style=for-the-badge&logo=metatrader5&logoColor=white)](https://www.mql5.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.tech/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal_Event--Driven-purple.svg?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Production-Hardened-success.svg?style=for-the-badge)]()

**Nexus Scalp Engine (NSE)** is a production-grade, event-driven quantitative high-frequency scalp trading infrastructure engineered specifically for **XAUUSD (Gold)** and major FX pairs. Built natively in **Python 3.11+**, **PyTorch (Deep Learning TCN + Self-Attention)**, and direct **Win32 C++ IPC driver bindings** to MetaTrader 5, the engine provides predictive Smart Money Concepts (SMC) execution, zero-lookahead feature extraction, an invariant-driven risk engine, and a real-time HTML5 Canvas Web Control Center.

---

## 🏛️ System Architecture

NSE follows a strict **Hexagonal (Ports-and-Adapters) Event-Driven Architecture**, isolating execution platforms, machine learning models, and network adapters into modular, self-healing subsystems.

```text
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                                 NEXUS RUNTIME CORE (PYTHON 3.11+)                         │
│                                                                                           │
│  ┌────────────────────────┐   ┌────────────────────────┐   ┌───────────────────────────┐  │
│  │ 50D Causal Feature     │   │ PyTorch ScalpNet v3    │   │  Cascading Risk Engine    │  │
│  │ Pipeline (SMC / OFI)   │───│ (TCN + Self-Attention) │───│  (Fixed Dollar / Margin)  │  │
│  └───────────┬────────────┘   └───────────┬────────────┘   └─────────────┬─────────────┘  │
│              │                            │                              │                │
│              └────────────────────────────┼──────────────────────────────┘                │
│                                           │                                               │
│                                 ┌─────────▼─────────┐                                     │
│                                 │   Policy Engine   │                                     │
│                                 │(30-Rule SMC Matrix)                                     │
│                                 └─────────┬─────────┘                                     │
│                                           │                                               │
│                                 ┌─────────▼─────────┐                                     │
│                                 │ Order Manager &   │                                     │
│                                 │ Financial Ledger  │                                     │
│                                 └─────────┬─────────┘                                     │
└───────────────────────────────────────────┼───────────────────────────────────────────────┘
                                            │ Win32 C++ Direct IPC
                                            v
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                               METATRADER 5 TERMINAL PROCESS                               │
│                                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐  │
│  │    Direct Institutional Execution Path (LMAX / Pepperstone / IC Markets / FXCM)    │  │
│  └─────────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────┘

## 🏛️ System Architecture

NSE follows a strict **Hexagonal (Ports-and-Adapters) Event-Driven Architecture**, isolating execution platforms, machine learning models, and network adapters into modular, self-healing subsystems.

```text
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                                 NEXUS RUNTIME CORE (PYTHON 3.11+)                         │
│                                                                                           │
│  ┌────────────────────────┐   ┌────────────────────────┐   ┌───────────────────────────┐  │
│  │ 50D Causal Feature     │   │ PyTorch ScalpNet v3    │   │  Cascading Risk Engine    │  │
│  │ Pipeline (SMC / OFI)   │───│ (TCN + Self-Attention) │───│  (Fixed Dollar / Margin)  │  │
│  └───────────┬────────────┘   └───────────┬────────────┘   └─────────────┬─────────────┘  │
│              │                            │                              │                │
│              └────────────────────────────┼──────────────────────────────┘                │
│                                           │                                               │
│                                 ┌─────────▼─────────┐                                     │
│                                 │   Policy Engine   │                                     │
│                                 │(30-Rule SMC Matrix)                                     │
│                                 └─────────┬─────────┘                                     │
│                                           │                                               │
│                                 ┌─────────▼─────────┐                                     │
│                                 │ Order Manager &   │                                     │
│                                 │ Financial Ledger  │                                     │
│                                 └─────────┬─────────┘                                     │
└───────────────────────────────────────────┼───────────────────────────────────────────────┘
                                            │ Win32 C++ Direct IPC
                                            v
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│                               METATRADER 5 TERMINAL PROCESS                               │
│                                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────────────┐  │
│  │    Direct Institutional Execution Path (LMAX / Pepperstone / IC Markets / FXCM)    │  │
│  └─────────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────┘
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

---

## 📂 Repository Layout

```text
NexusTradingForexBot/
├── .github/workflows/check_and_build.yml    # CI/CD Workflow (Ruff, Mypy, Pytest)
├── configs/
│   ├── base.yaml                            # System parameters, risk limits, & model configs
│   └── live.yaml                            # Production live environment configurations
├── src/nexus_scalp/
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

The engine includes an extensive, hardened test suite covering unit logic, PyTorch tensor contracts, risk clamps, and integration workflows.

### Run All Unit & Integration Tests:
```bash
pytest tests/unit/ tests/integration/ -v
```

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
