# Nexus Scalp Engine (NSE) — Production Quantitative Scalping Infrastructure

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-red.svg)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-gold.svg)](https://www.mql5.com/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal-purple.svg)]()
[![Build & Test](https://github.com/your-username/NexusTradingForexBot/actions/workflows/check_and_build.yml/badge.svg)](https://github.com/your-username/NexusTradingForexBot/actions/workflows/check_and_build.yml)
[![License](https://img.shields.io/badge/License-Proprietary-green.svg)]()

**Nexus Scalp Engine (NSE)** is an enterprise-grade, high-frequency quantitative scalp trading engine engineered specifically for **XAUUSD (Gold)** and major currency pairs. Built natively with **Python 3.11+**, **PyTorch deep learning**, and direct **C++ IPC bindings** to MetaTrader 5, the system provides zero-data-leakage feature pipelines, ICT/Ichimoku multi-confluence signal evaluation, dynamic margin-based position sizing, and automated thread-replied Telegram telemetry.

---

## 🏛️ System Architecture

The engine implements a **Hexagonal / Ports-and-Adapters Monolith Architecture** prioritizing thread safety, ultra-low execution latency, and complete execution-platform isolation.

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│                          LINUX / WINDOWS CORE RUNTIME                             │
│                                                                                   │
│  ┌───────────────────────┐   ┌───────────────────────┐   ┌─────────────────────┐  │
│  │  Incremental Feature  │   │  Hierarchical PyTorch │   │ Dynamic Risk Engine │  │
│  │ Engine (40 Dimensions)│───│ ScalpNet v3 (TCN+Attn)│───│(Margin/Leverage Cap)│  │
│  └───────────┬───────────┘   └───────────┬───────────┘   └──────────┬──────────┘  │
│              │                           │                          │             │
│              └───────────────────────────┼──────────────────────────┘             │
│                                          │                                        │
│                                ┌─────────▼─────────┐                              │
│                                │ IMT5Port Adapter  │                              │
│                                └─────────┬─────────┘                              │
└──────────────────────────────────────────┼────────────────────────────────────────┘
                                           │ Win32 C++ IPC / Encrypted RPC
                                           v
┌───────────────────────────────────────────────────────────────────────────────────┐
│                              METATRADER 5 TERMINAL                                │
│                                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────┐  │
│  │   Broker Direct Execution Path (LMAX / IC Markets / Pepperstone / FXCM)     │  │
│  └─────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📂 Repository Structure

```text
NexusTradingForexBot/
├── .github/
│   └── workflows/
│       └── check_and_build.yml                # CI/CD Workflow for Ruff, Mypy & Pytest
├── configs/
│   └── base.yaml                              # Default system settings, risk caps, & models config
├── docker/
│   ├── entrypoint.sh                          # Docker container startup script
│   └── healthcheck.sh                         # Docker container containerized health monitor
├── src/
│   └── nexus_scalp/
│       ├── __init__.py                        # Core package initialization
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── database/
│       │   │   └── audit_repository.py        # SQLite/PostgreSQL trading & audits logging DB
│       │   ├── mt5/
│       │   │   ├── mt5_adapter.py             # Win32 C++ IPC Direct Local MT5 Driver
│       │   │   └── remote_gateway.py          # Secure HTTP+HMAC Cross-Platform Gateway client
│       │   └── paper/
│       │       └── paper_adapter.py           # In-Memory Paper trading/replay mock broker
│       ├── application/
│       │   └── live_engine.py                 # Core live trading event-loop & orchestrator
│       ├── cli/
│       │   └── main.py                        # Rich console Typer CLI operations console
│       ├── configuration/
│       │   └── config.py                      # Strongly-typed YAML parsing using Pydantic Settings
│       ├── domain/
│       │   ├── enums.py                       # Pure Domain Action, Order, and Execution enums
│       │   └── models.py                      # Safe Immutable Domain models & invariants
│       ├── execution/
│       │   └── order_manager.py               # Active SL/TP manager, trailing-stops, & telemetry
│       ├── features/
│       │   ├── scalp_features.py              # 40D Zero-lookahead feature engineering engine
│       │   └── regime_classifier.py           # O(1) Schmitt-Trigger Market Regime engine (Module 1)
│       ├── labeling/
│       │   └── triple_barrier.py              # Friction-aware Triple-Barrier Labeling algorithm
│       ├── market_data/
│       │   ├── bar_aggregator.py              # Incremental tick to OHLC M1 candle aggregator
│       │   └── tick_storage.py                # High-speed tick storage using Apache Parquet format
│       ├── models/
│       │   └── scalp_net.py                   # PyTorch deep learning network (3-Layer TCN + Self-Attention)
│       ├── observability/
│       │   ├── logging.py                     # Non-blocking structured logging pipeline using structlog
│       │   └── telegram_notifier.py           # Multi-threaded Telegram notification engine
│       ├── ports/
│       │   ├── gateway_port.py                # Abstract network gateway definition
│       │   └── mt5_port.py                    # Core abstract execution & MT5 contract
│       ├── risk/
│       │   └── risk_engine.py                 # Margin-aware position sizing & dynamic Risk Engine
│       ├── signals/
│       │   └── policy.py                      # Limit/Stop order placement & signal evaluation policy
│       └── training/
│           └── walk_forward_trainer.py        # Safe online Walk-Forward model training & calibration
├── tests/
│   └── unit/
│       ├── test_bar_aggregator.py             # Candle aggregator boundary tests
│       ├── test_domain_models.py              # Domain invariant validation checks
│       ├── test_logging.py                    # Logging and secret redaction checks
│       ├── test_mt5_adapter.py                # Adapter error & disconnection checks
│       ├── test_risk_engine.py                # Risk caps and position sizing checks
│       ├── test_scalp_features.py             # Feature dimension & normalization verification
│       └── test_runtime.py                    # CLI commands & LiveEngine execution runtime integration tests
├── Dockerfile                                 # Linux multi-stage Python 3.11 runtime image
├── docker-compose.yml                         # Core Engine & PostgreSQL database orchestration
├── pyproject.toml                             # Package dependencies & Dev setup (PEP 621)
├── requirements.txt                           # Frozen pip production dependencies
├── NexusTradingForexBot.py                    # Primary system launcher and pre-flight diagnostics
├── NexusTradingForexBot.pyproj                # Visual Studio Python project definition
└── NexusTradingForexBot.slnx                  # Visual Studio 2022 Solution file
```

---

## 📊 Feature Engineering Engine (40 Dimensions)

The system computes a strictly causal, **zero-lookahead**, 40-dimensional sanitized feature tensor on every tick. The feature matrices are split into eight quantitative categories:

1. **Gold Microstructure & Volatility** — Live tick displacement from last closed bar, 14-period Average True Range (ATR).
2. **Price Action & Candlestick Anatomy** — Body-to-range ratios, upper/lower wick ratios, Doji signals, standard Bullish/Bearish Engulfing validation, Hammer/Shooting-star pinbars, and Close Location Value (CLV).
3. **Swing Structure & Chart Patterns** — Normalized distance to 20-bar swing high/lows, 5-to-20-bar price compression flag ratios, extreme range boundaries (top/bottom 5% of 50-bar range), and stop-hunt penetration depth.
4. **Market Sessions** — Real-time active binary session triggers for Tokyo, London, and New York sessions, including London-NY liquidity overlap window detection.
5. **Time-Series Lags** — Log returns of lags 1, 2, and 3; lag-1 ATR ratios; lag-1 volume Z-score; and lag-1 CLV.
6. **ICT Signals & Microstructure** — Bullish/Bearish Fair Value Gaps (FVG), Order Blocks, liquidity sweep patterns, Market Structure Shift/Change of Character (CHoCH), and rapid tick reversal spikes.
7. **Ichimoku Kinko Hyo** — Tenkan-Sen, Kijun-Sen, Senkou Span A/B cloud boundaries, normalized TK diff, TK cross signals, and above/below Kumo cloud identifiers.
8. **Indicators & Stat-Arb** — Normalized RSI-14, normalized distances to Exponential Moving Averages (EMA-21, EMA-50), and Cross-Asset Z-Score spreads against custom benchmarks.

---

## ⚙️ Quick Start Guide

### Prerequisites
- **Operating System:** Windows 10/11 (for local native MT5 IPC) or Linux (for Remote HTTP/HMAC Gateway runs).
- **Python Version:** Python 3.11+
- **Broker Interface:** MetaTrader 5 Terminal logged in to a demo/live account with **Algo Trading** enabled.

### Installation

1. Clone the repository and navigate to the project directory:
   ```bash
   cd NexusTradingForexBot
   ```

2. Create and activate a clean virtual environment:
   ```bash
   python -m venv .venv

   # On Windows (PowerShell)
   .\.venv\Scripts\Activate.ps1

   # On Linux/macOS
   source .venv/bin/activate
   ```

3. Install the dependencies in editable mode:
   ```bash
   pip install --upgrade pip
   pip install -e .[dev]
   ```

### Running Diagnostics

Execute the system pre-flight doctor check to verify host configurations, platform capabilities, and YAML correctness:
```bash
python NexusTradingForexBot.py --doctor
```

Or run via the CLI operational utility:
```bash
nse doctor
```

### Configuration Validation

Verify structural invariants and correctness of any custom configuration YAMLs before engine startup:
```bash
nse config-validate -c configs/base.yaml
```

### Running the Live Scalper Engine

Launch the real-time trading engine using the base configurations:
```bash
# Via primary launcher
python NexusTradingForexBot.py --config configs/base.yaml

# Or via the CLI operational console
nse run --config configs/base.yaml
```

---

## 🧪 Testing Suite & Runtime Validation

NSE maintains an extensive testing suite covering pure domain models, data aggregators, quantitative indicators, risk parameters, CLI controls, and integration workflows:

### Running the Tests
Execute the entire test suite using `pytest`:
```bash
pytest
```

To run with coverage indicators:
```bash
pytest --cov=src --cov-report=term-missing
```

### Runtime Testing Safeguards
Our specialized integration test (`tests/unit/test_runtime.py`) validates critical execution runtime behaviors:
- Emulates broker communication using a fully-conforming `MockMT5Port` to ensure zero-network leakages.
- Verifies system `_preflight_or_raise` validation rules, such as path checks and feature contract assertions.
- Simulates the primary cold-start phase using synthetic OHLC streams.
- Runs on-the-fly bootstrap training checkpoints to confirm model parameter updates and tensor shapes.
- Tests real-time tick ingestion pipeline logic for order triggers and signal policy decisions.

---

## 🚀 CI/CD Automation

This project features automated GitHub Actions workflow checks defined in `.github/workflows/check_and_build.yml` which run on every push and pull request:
1. **Linter & Code Style Checks** — Rapid code style linting via `ruff check .`.
2. **Static Type Safety** — Strict type signature verification via `mypy src/`.
3. **Automated Testing** — Runs all unit and runtime integration tests over isolated clean environments with `pytest`.

---

## 🛡️ License & Operational Safety Disclaimer

This software is designed exclusively for quantitative research and automated simulation. Algorithmic trading carries massive capital risks. Always backtest strategies thoroughly and run them on a demo paper account before deploying live funds.

*Proprietary License — All Rights Reserved.*
