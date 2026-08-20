# 👑 Nexus Scalp Engine (NSE) v9.0

*Production-grade, event-driven quantitative scalping runtime for MetaTrader 5 — XAUUSD (Gold) and major FX pairs.*

> **Next-gen model pipeline (Phase 13+):** production models now ship through an artifact-first **Model Factory** — datasets, experiments and models are versioned filesystem artifacts with manifests; inference needs no database. ScalpNet remains as the legacy baseline (control group) for benchmarking. The research candidate series is the **70D causal feature contract** (`scalp_v3`): Base 50D + News context 10D + Liquidity intelligence 10D.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5_Terminal-2962FF.svg?style=for-the-badge&logo=metatrader5&logoColor=white)](https://www.mql5.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.tech/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL_Ledger-003B57.svg?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Architecture](https://img.shields.io/badge/Architecture-Hexagonal_Event--Driven-6f42c1.svg?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-Production_Hardened-success.svg?style=for-the-badge)]()

<p align="center">
  <img src="pics/web.png" alt="Nexus Trading Control Center" width="100%">
</p>

**Nexus Scalp Engine** unifies deep-learning inference, real-time market-microstructure analysis and high-frequency execution into one self-healing framework. It is driven by the **ScalpNet dual-path TCN + self-attention model**, a **50-dimensional causal feature engine** (extended by the 70D research contract with news + liquidity intelligence), an **SMC policy matrix** (Order Blocks, Fair Value Gaps, Liquidity Sweeps), and a **bounded news-intelligence gate** — all wired through a hexagonal, event-driven core that executes directly on MetaTrader 5.

---

## Quick Start (TL;DR)

| You want to… | Do this |
| :--- | :--- |
| **Run it now** (no install) | `nexus start` — defaults to **PAPER** mode, never LIVE |
| **Run safely on real data** | `nexus start --mode shadow` — live market feed, zero order authority |
| **Check your system** | `nexus doctor` / `nexus health` |
| **Open the dashboard** | `nexus start` → browser at `http://127.0.0.1:8080` |
| **Live trading (explicit)** | `nexus start --mode live` — requires interactive confirmation |
| **Stop** | `nexus stop` (background engine) — or `Ctrl+C` in the foreground |

> 💡 The safest first-run path is **Demo MT5 account → SHADOW → small LIVE** — see [First-Run Safety](#-first-run-safety-demo--shadow--live).

---

## Download / Get Started

Two ways:

1. **End users — packaged Windows installer (no Python required).**
   Download `NexusScalpEngine-<version>-win-x64-setup.exe` (or the portable ZIP
   `NexusScalpEngine-<version>-win-x64.zip`) from **GitHub Releases**.
   The installer bundles the full Python runtime (PyInstaller) — you never touch
   Python, pip or PyTorch. First run opens the **setup wizard** (`nexus setup`):
   compatibility report → mode (**default: PAPER**, never silently LIVE) →
   symbol → health check.
   > ⚠️ *No release has been published yet* — the release pipeline is fully built
   > and CI-ready (`.github/workflows/release.yml`, tag `vX.Y.Z` = publish), but
   > GitHub currently has **zero releases** until the first version tag is pushed.

2. **Developers — run from source (see below).**

Release details: [`docs/RELEASE.md`](docs/RELEASE.md) · build pipeline:
`.github/workflows/release.yml` · build scripts: `scripts/build/`.

---

## Requirements

- **OS:** Windows 10/11 **x64** for the native MT5 adapter. **Windows ARM64 is NOT supported**
  (PyTorch/Polars/MetaTrader5 ship no ARM64 wheels — the installer and `nexus doctor` report this explicitly).
  Linux x64 = developer/Docker (remote-gateway adapter) only.
- **Broker:** MetaTrader 5 Terminal, logged into a Live or **Demo** account with
  **`Tools → Options → Expert Advisors → tick "Allow Algo Trading"`** enabled.
  The bot refuses to start if the terminal is missing/not running, or a config check fails (pre-flight doctor runs automatically).
- **Python (source run only):** 3.11.x. Not needed for the packaged release.

---

## Installation

### End users (packaged)

1. Run the installer (per-user, no admin) or unpack the portable ZIP.
2. `nexus setup` — first-run wizard (also `nexus install`).
3. User data (config/logs/databases/models) lives in `%LOCALAPPDATA%\NexusScalpEngine`
   and **survives upgrades, repairs and uninstalls**.

### Developers (source)

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

pip install --upgrade pip
pip install -e .[dev]

# Smoke-test the whole toolchain — no broker needed
pytest tests/unit -q
```

> 💡 Windows tip: run commands from the repo root after activating `.venv`; if `pip`
> complains about an external environment, use `python -m pip`.

---

## How to Run

### Start the application

```text
nexus start                      # PAPER mode (default, safe)
nexus start --mode shadow        # live market data, zero orders
nexus start --mode live          # REAL execution — explicit confirmation required
nexus start --config configs/live.yaml --port 8080
nexus start --daemon             # background process
```

From source (same commands via the installed console script, or):

```text
python -m nexus_scalp.cli.main start --mode shadow
python NexusTradingForexBot.py --doctor                # legacy launcher diagnostics
python NexusTradingForexBot.py --config configs/live.yaml   # legacy full launch
python NexusTradingForexBot.py --symbol EURUSD         # symbol override
python NexusTradingForexBot.py --gateway               # force ZMQ remote-gateway mode
```

### Safe / Paper / Shadow Mode

- `nexus start` **never** defaults to LIVE — PAPER is the default.
- `--mode shadow` streams the same live feature vector to the Champion/Challenger
  pair and records decisions as `simulated=True` — **zero order authority**: no
  order can be placed, modified or closed. This is the recommended way to evaluate
  the engine against real market data.
- Use a **demo MT5 account** for the first execution exercise.

### Web UI

`nexus start` serves the **Control Center** at **`http://127.0.0.1:8080`**
(`--port` to change). FastAPI REST `:8080/api/...`, tick stream over SSE
(`/api/ticks/stream`), live dashboard over WebSocket (`/web`).

### Stop

`nexus stop` (stops a `--daemon` background engine — pidfile-based) ·
`nexus restart` · foreground runs stop with `Ctrl+C` (graceful teardown).

### Update / Repair / Remove

`nexus update` · `nexus update check` · `nexus repair` · `nexus uninstall`
(user data preserved unless `--no-keep-data`).

---

## CLI

The bundled `nexus` console command is the operational control surface
(verified against the live CLI; `nse` is a legacy alias, `python -m nexus_scalp.cli.main` from source).

```text
nexus start     --mode paper|shadow|live  Start engine (default paper; live needs confirmation)
nexus stop      /  restart                Control the background engine
nexus status                              Full status: health + environment + version
nexus health                              Quick health summary (READY / DEGRADED / NOT READY)
nexus doctor                              Sub-system diagnostics (19 categories) + suggested fixes
nexus setup     /  install                First-run wizard (compat, install, DB, model, mode, health)
nexus logs      [--tail N] [--errors]     Tail / filter / export engine logs
nexus config    [--validate path]         Inspect / validate configuration (--show/--json)
nexus test      --mode quick|unit|integration   Run test suites (never live-broker tests)
nexus update    [check|latest|download|install|verify|status|history|rollback|doctor]
                                                       Update / rollback the installation
nexus release   [info]                                 Installed release metadata
nexus repair                              Repair non-destructive derived state (never deletes data)
nexus export-diagnostics                  Sanitized diagnostics ZIP (never contains secrets)
nexus db        hygiene status|plan|run   Database schema + retention hygiene (audit-only by default)
nexus model-dataset-build / model-experiment-create / model-train / model-validate / model-replay
                                          Artifact-first model factory (research/candidates)
nexus uninstall                           Remove the installation (user data preserved)
```

Exit codes (stable contract): `0` success · `1` runtime/validation failure ·
`2` invalid usage · `3` environment blocked (e.g. ARM64) · `4` release verification failure · `5` update not applicable/failed.

`--json` / `--plain` / `--no-color` flags available for CI and automation.

---

## What the Engine Can Do (Core Capabilities)

- **50D causal feature engine** — microstructure, order flow, multi-timeframe momentum, S/R clustering, SMC structure (BOS, equilibrium, liquidity sweeps, OTE). Fully causal: no lookahead, live = replay = training. NaN/Inf fall back deterministically, never crash. Research series extends this to **70D** with news context + liquidity intelligence (`scalp_v3`, candidate-only).
- **ScalpNet: dual-path TCN + self-attention model** — 2D snapshot path for single ticks, 3D temporal path for sequences; clamped inverse class-frequency weighting; atomic checkpoint rollbacks after online fine-tuning.
- **SMC policy matrix** — 30+ DB-driven rules, God-Mode confluence, Regime Guardian Gate, SMC veto power.
- **Dynamic risk & position sizing** — fractional-Kelly lot sizing, Almgren-Chriss slippage bounds, margin clamps (≤20% free margin), single-position exposure cap, `HARD_MAX_LOTS = 10.0`.
- **Execution safeguards** — 60-scenario router, 11 position lifecycles, breakeven lock, profit giveback, adaptive exit protection, circuit breaker → SAFE_MODE after 3 rejections, order-churn throttle.
- **Accounting / ledger** — one canonical SQLite WAL ledger (`artifacts/audit.db`): signals, orders, snapshots, post-trade autopsies (MAE/MFE, exit mechanism), equity. Metrics without evidence render `n/a` — never fake zeros.
- **Experience-driven intelligence** — immutable experience ledger, pre-trade gates, behavior detection, trade autopsy (a `MANAGED_LOSS` is never mistaken for a broken strategy), strategy evolution.
- **Strategy research, backtest & validation** — causal-safe dataset builder, friction-aware deterministic backtests, purged walk-forward, hard OOS gate, robustness stress, content-addressed strategy registry.
- **Controlled model/challenger pipeline** — candidate staging (Champion never overwritten), 12 validation gates, shadow deployment with zero order authority, promotion strictly operator-gated.
- **News intelligence (opt-in)** — RSS/Atom ingestion → dedup → analysis → bounded gate (confidence boost ≤ 0.05, penalty ≤ 0.10 — news can *never* force a trade or bypass risk). Disabled by default.
- **FastAPI/WebSocket control center** — live chart with OB/FVG/Sweep overlays, all-8-tab dashboard, live algorithm tuner (`PUT /api/algo/config`, no restart).
- **MetaTrader 5 integration** — direct Win32 IPC adapter, ZMQ remote gateway, paper simulator.

**Safety invariants:** research/strategy/news workers never hold order authority · candidates never promote themselves · OOS failure ⇒ REJECTED · schema mismatch fails loudly · the live tick path never blocks on analytics work.

---

## Architecture (compact)

**Hexagonal (Ports-and-Adapters), event-driven** — execution platforms, models and network adapters are isolated behind port contracts (`IMT5Port`, `IGatewayPort`).

```text
50D/70D Causal Features ─► PyTorch ScalpNet ─► SMC Policy Matrix + Risk Engine
        ▲                                         │
    Ticks & Bars                                  ▼
Live Engine Loop ◄─ Web Control Center (FastAPI + WS/SSE) ─► Invariant Risk Engine
        │
        ▼
        IMT5Port Adapter ──► Win32 IPC / ZMQ Gateway / Paper
        ▼
   LOCAL METATRADER 5 TERMINAL (Live/Demo account)
```

Key subsystems: `domain` (frozen Pydantic contracts) · `ports`/`adapters` (MT5 Win32 IPC, ZMQ remote, paper simulator, SQLite WAL audit repo) · `features` (causal engine + schema registry) · `models`/`training`/`labeling` (ScalpNet, purged walk-forward, triple-barrier labeler) · `signals`/`risk`/`execution` (policy matrix, Kelly sizing, order manager) · `application` (async live engine) · `accounting`/`experience`/`intelligence`/`research`/`model_lifecycle`/`shadow`/`news` (closed-loop intelligence) · `model_generation` (artifact-first factory) · `web` (FastAPI/WS/SSE) · `release` (installer/update/diagnostics).

The closed intelligence loop: live trade → accounting → experience → autopsy → research → candidate training → shadow comparison → future decisions (gated). Everything rebuildable from immutable ledgers.

Deep architecture: [`agents/skill.md`](agents/skill.md) (authoritative map) · [`docs/architecture/`](docs/architecture) · 70D series docs under [`docs/70D_*.md`](docs/70D_DATA_CONTRACT.md).

---

## Safety & Validation

- Risk controls: exposure caps, margin clamps, drawdown stop (`risk.max_account_drawdown_pct`), kill switch via dashboard/CLI.
- `nexus start --mode live` prints the full risk panel and **requires an explicit interactive confirmation**.
- Backtesting: deterministic friction-aware backtests + purged/embargoed walk-forward + hard **OOS gate** (OOS failure ⇒ REJECTED despite in-sample performance) + robustness stress (spread/slippage/latency).
- Controlled promotion: candidates are stored `CHALLENGER` (shadow-eligible) only; production authority stays with the operator-gated process. Shadow runs carry `simulated=True` and hold **no order authority**.
- Pre-flight doctor gates every launch: missing MT5 terminal, invalid config, or unsupported platform ⇒ refuses to start.
- Quality gates: `beforePush.sh` / `beforePush.ps1` (ruff lint + format, mypy strict, full unit suite) · CI: `ci.yml` (ruff/mypy/pytest+coverage), `security.yml` (CodeQL + Trivy), `release.yml`.
- Test suite: 28 unit suites + 10 integration suites (~700+ tests) covering risk clamps, exposure limits, causality/OOS, shadow safety contracts, news-gate bounds.

> 🚨 **This bot places REAL trades with REAL money in LIVE mode.** Always verify every setting twice, start on a demo account, and keep the bot supervised during early live runs. The engine's hard clamps protect the strategy — not your capital from market volatility.

---

## First-Run Safety (Demo → Shadow → Live)

| Step | Action | Why |
| :--- | :--- | :--- |
| 1️⃣ | Log into a **DEMO account** in MT5 (`File → Open an Account → Practice/Demo`), confirm the account number/label before touching the bot | LIVE and demo accounts can look identical in MT5's login window |
| 2️⃣ | MT5: `Tools → Options → Expert Advisors → tick "Allow Algo Trading"` | Without this, orders are rejected |
| 3️⃣ | Copy `configs/live.yaml` → `configs/demo.yaml`; set demo credentials and confirm `risk.max_concurrent_positions: 1`, `risk.risk_per_trade_pct` (e.g. 0.5), `risk.max_account_drawdown_pct` | Never touch a live config while real money can be reached |
| 4️⃣ | Run **SHADOW** first (`nexus start --mode shadow`) for days; review dashboard + Telegram reports | Proves model, signals and gates before any execution |
| 5️⃣ | Run the test suite weekly (`pytest tests/unit tests/integration`) | Catches regressions before they reach a live account |
| 6️⃣ | Only then consider **LIVE** with a small balance you can afford to lose, `risk_per_trade_pct: 0.25` or lower | Leveraged Gold (XAUUSD) scalping carries extreme risk |

---

## Web UI / Screenshots

The **Control Center** (`http://127.0.0.1:8080`) shows: live M1 chart (900-bar window) with order-block / FVG / swept-liquidity overlays and entry-SL-TP lines with risk tooltips; tabs for Overview · Strategy Research · News Intelligence · Scalping Rules · Account (Performance & Intelligence) · Debug Hub; live algorithm tuner.

<p align="center">
  <img src="pics/_shot_final_1920.png" alt="Account Center — 1920px" width="49%">
  <img src="pics/_shot_final_768.png" alt="Account Center — 768px" width="49%">
  <img src="pics/_shot_deep_state.png" alt="Deep state (expanded intelligence)" width="49%">
  <img src="pics/_case_many.png" alt="Many metrics view" width="49%">
</p>

---

## Known Issues & Limitations

- **No published release yet** — the release/installer/update pipeline is complete and CI-ready, but GitHub has zero releases until the first `v*` tag is pushed. Until then, run from source or build locally.
- **Windows x64 only** for the packaged release — ARM64 is explicitly unsupported (dependency stack).
- **News engine is opt-in** — disabled by default until a `news:` block is added to the YAML config.
- **No CLI hot-swap to the PAPER adapter** — risk-free validation is done via SHADOW mode or a demo MT5 account (the paper adapter is exercised through the test suite).
- **Research status (70D series):** the 70D liquidity feature series (`scalp_v3`) is **candidate-only** — real-data walk-forward and shadow benchmarks came back **negative / inconclusive** (OOS NOT_ELIGIBLE), and the live contract deliberately stays 50D (`scalp_v1`). The champion remains under governance review; nothing is auto-promoted. See `docs/70D_*` and `agents/taskboard.md`.
- **Update channel:** `nexus update check` truthfully reports `RELEASE_NOT_FOUND` while no release exists.
- Full forensic regression and bug ledger (118+ entries, incl. resolved BUG-054 retention/DB growth, BUG-091/092/093 release/data-safety, BUG-106 liquidity performance, BUG-111 dataset overwrite guard): **[`agents/bugs.md`](agents/bugs.md)**.

---

## Documentation

| Document | What it is |
| :--- | :--- |
| [`agents/skill.md`](agents/skill.md) | Authoritative architecture map: layers, files, invariants, pitfalls |
| [`agents/bugs.md`](agents/bugs.md) | Forensic bug ledger — root causes, evidence, regression guards |
| [`docs/RELEASE.md`](docs/RELEASE.md) | Release, installer, CLI, update & rollback guide |
| [`docs/DATABASE_MIGRATIONS.md`](docs/DATABASE_MIGRATIONS.md) | Schema migration & recovery strategy |
| [`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) | 70D causal feature contract (series: `docs/70D_*.md`, `docs/LIQUIDITY_*`) |
| [`docs/FORENSIC_AUDIT_PHASES_08_11.md`](docs/FORENSIC_AUDIT_PHASES_08_11.md) | Cross-phase forensic audit report |
| [`docs/architecture/`](docs/architecture) | Architecture & dependency-map audits |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | Stabilization & bug-fix engineering report |
| CLI | `nexus <command> --help` for every command's exact options |

---

## Technology Stack

Python 3.11 · PyTorch (TCN + self-attention) · FastAPI + WebSockets/SSE · MetaTrader 5 (native Win32 IPC + ZMQ gateway) · SQLite WAL · Polars/PyArrow · Pydantic · structlog · Typer/Rich CLI · Docker (dev/gateway) · GitHub Actions CI (ruff, mypy, pytest, CodeQL, Trivy, release).

---

## Repository Structure

```text
src/nexus_scalp/   Core engine (hexagonal packages: domain, ports, adapters, features,
                   models, signals, risk, execution, application, research, shadow, news, …)
tests/             Unit + integration suites (tests/unit, tests/integration, tests/helpers)
Web/               Control Center UI (index.html, app.js, styles.css)
agents/            Agent architecture docs, bug ledger, contracts, taskboard
docs/              Deep technical documentation (release, migrations, 70D series, forensics)
configs/           base.yaml · live.yaml.example
scripts/           Build/release scripts (scripts/build/), quality gates, docker wrappers (start/doctor/reset/backup)
pics/              Screenshots
docker/            entrypoint.sh · healthcheck.sh
docker-compose.yml  Core + redis stack (SQLite; no postgres) — see docs/docker.md
.env.example       Environment contract (safe defaults, no secrets)
scratch/           One-off diagnostic probes (not part of the application)
```

---

## 🐳 Docker quick start

```bash
cp .env.example .env       # optional — safe defaults exist
docker compose up -d --build
```

The stack starts the engine + Web UI/API in container-safe **PAPER** mode with
the canonical SQLite databases (no PostgreSQL), runs the migration gate, and
exposes the dashboard at http://localhost:9090. Health/readiness is served at
`/health` (READY/DEGRADED = healthy). Full reference: **`docs/docker.md`**
(env contract, startup sequence, persistence, volumes, reset, backup,
troubleshooting). Windows helpers: `scripts/start.ps1`, `scripts/doctor.ps1`,
`scripts/reset-dev.ps1`, `scripts/backup-db.ps1`.

---

## Status

- **Version:** 9.0.0 (semver, single canonical source: `pyproject.toml` → stamped into every build artifact).
- **State:** Production-hardened runtime; packaged **release pending first v-tag publish** (pipeline complete).
- **Major capabilities:** live MT5 execution, shadow/candidate pipeline, closed-loop research, news gate, control-center UI, self-update/rollback.
- **Active limitations:** no published release yet · 70D research series candidate-only with negative OOS evidence · Windows x64 only · news engine opt-in.

---

## Collaboration

We are expanding NSE into a global open-core quantitative framework and invite quantitative ML researchers, low-latency C++/Rust systems engineers, and institutional traders to collaborate. Fork & PR (PEP 8, strict mypy, pytest coverage — the quality gates will enforce it), or open an issue with `[Research]` / `[Proposal]` tags.

---

## License & Disclaimer

**DISCLAIMER:** Algorithmic trading — especially leveraged XAUUSD/Gold scalping — carries immense financial risk. This engine is provided strictly for educational, academic research and simulation purposes. Always perform rigorous backtesting and forward paper-trading before committing capital.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*