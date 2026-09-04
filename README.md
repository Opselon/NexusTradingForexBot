<p align="center">
  <h1 align="center">⚡ Nexus Scalp Engine</h1>
  <p align="center">
    <b>Research-driven quantitative trading engine</b><br>
    <sub>Causal features · Governed models · Invariant risk · Deterministic research · Forensic observability</sub><br>
    <sup>Hexagonal · Event-driven · MetaTrader 5 · XAUUSD M1 · Python 3.11</sup>
  </p>
  <p align="center">
    <a href="https://opselon.github.io/NexusTradingForexBot/">English</a> ·
    <a href="https://opselon.github.io/NexusTradingForexBot/fa/">فارسی</a> ·
    <a href="https://opselon.github.io/NexusTradingForexBot/es/">Español</a> ·
    <a href="https://opselon.github.io/NexusTradingForexBot/ar/">العربية</a> ·
    <a href="https://opselon.github.io/NexusTradingForexBot/de/">Deutsch</a> ·
    <a href="https://opselon.github.io/NexusTradingForexBot/">📚 Documentation</a>
  </p>
</p>

<p align="center">
  <a href="https://github.com/Opselon/NexusTradingForexBot/releases"><img src="https://img.shields.io/github/v/tag/Opselon/NexusTradingForexBot?label=release&sort=semver&style=for-the-badge" /></a>
  <a href="https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml"><img src="https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml/badge.svg?style=for-the-badge" /></a>
  <a href="https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml"><img src="https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml/badge.svg?style=for-the-badge" /></a>
  <br>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11-3776AB.svg?style=for-the-badge&logo=python&logoColor=white" /></a>
  <a href="https://pytorch.org/"><img src="https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white" /></a>
  <a href="https://opselon.github.io/NexusTradingForexBot/"><img src="https://img.shields.io/badge/Docs-GitHub_Pages-168AFF?style=for-the-badge&logo=github&logoColor=white" /></a>
  <a href="docs/project/status.md"><img src="https://img.shields.io/badge/Research-50D_live_·_70D_candidate-8A63D2?style=for-the-badge" /></a>
</p>

<p align="center">
  <img src="pics/web.png" alt="Nexus Trading Control Center" width="100%">
</p>

> [!IMPORTANT]
> **Research and engineering platform — not a promise of profit.** Every status claim is evidence-graded, and this repository publishes its *negative* results alongside wins. Leveraged XAUUSD scalping carries extreme financial risk.

---

### 🧭 Start here

<p align="center">
  <sub>Pick your path — five doors, one engine</sub>
</p>

<div align="center">

<a href="#-quick-start" style="display:inline-block; width:148px; vertical-align:top; margin:6px; padding:18px 10px; border:1px solid #30363d; border-radius:14px; text-decoration:none;">
<div style="font-size:26px; line-height:1;">🚀</div>
<div style="font-weight:800; font-size:14px; margin:10px 0 2px 0; color:#e6edf3;">Quick Start</div>
<div style="font-size:11px; line-height:1.3; color:#8b949e;">Run in under<br>5 minutes</div>
</a>

<a href="#-architecture-at-a-glance" style="display:inline-block; width:148px; vertical-align:top; margin:6px; padding:18px 10px; border:1px solid #30363d; border-radius:14px; text-decoration:none;">
<div style="font-size:26px; line-height:1;">🏗️</div>
<div style="font-weight:800; font-size:14px; margin:10px 0 2px 0; color:#e6edf3;">Architecture</div>
<div style="font-size:11px; line-height:1.3; color:#8b949e;">System map &<br>data flow</div>
</a>

<a href="#-research--validation" style="display:inline-block; width:148px; vertical-align:top; margin:6px; padding:18px 10px; border:1px solid #30363d; border-radius:14px; text-decoration:none;">
<div style="font-size:26px; line-height:1;">🔬</div>
<div style="font-weight:800; font-size:14px; margin:10px 0 2px 0; color:#e6edf3;">Research</div>
<div style="font-size:11px; line-height:1.3; color:#8b949e;">Claims &<br>evidence</div>
</a>

<a href="#-cli" style="display:inline-block; width:148px; vertical-align:top; margin:6px; padding:18px 10px; border:1px solid #30363d; border-radius:14px; text-decoration:none;">
<div style="font-size:26px; line-height:1;">📖</div>
<div style="font-weight:800; font-size:14px; margin:10px 0 2px 0; color:#e6edf3;">Reference</div>
<div style="font-size:11px; line-height:1.3; color:#8b949e;">CLI · API</div>
</a>

<a href="#-contributing" style="display:inline-block; width:148px; vertical-align:top; margin:6px; padding:18px 10px; border:1px solid #30363d; border-radius:14px; text-decoration:none;">
<div style="font-size:26px; line-height:1;">🤝</div>
<div style="font-weight:800; font-size:14px; margin:10px 0 2px 0; color:#e6edf3;">Contribute</div>
<div style="font-size:11px; line-height:1.3; color:#8b949e;">Join the<br>research</div>
</a>

</div>

---

## 🚀 Quick Start

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1
# Linux / macOS:       source .venv/bin/activate
pip install -e ".[dev]"

nexus doctor                # read-only diagnostics + suggested fixes
nexus start                 # PAPER mode (default, safe) → http://127.0.0.1:8080
nexus start --mode shadow   # live feed, ZERO order authority — recommended for evaluation
nexus stop
```

> Control Center opens at `http://127.0.0.1:8080` — live M1 chart, Order Blocks, FVGs, risk overlays, and tuner.
> More entry paths + first-run wizard → [installation](docs/getting-started/installation.md) · [quickstart](docs/getting-started/quickstart.md)

---

## 💡 What is Nexus?

**Nexus Scalp Engine (NSE)** unifies deep-learning inference, market-microstructure analysis, and high-frequency execution into one **auditable pipeline**:

> **Causal Feature Engine** → **Dual-path TCN + Self-Attention (ScalpNet)** → **SMC Policy Matrix** (Order Blocks · Fair Value Gaps · Liquidity Sweeps) → **Invariant Risk Engine** → **Hard-Clamped Execution Router** — all on a hexagonal core that talks to MetaTrader 5 via Win32 IPC, ZMQ gateway, or a paper simulator.

What makes Nexus different is not a claim of alpha — it's **how claims are treated**:

`identity fingerprints` on every dataset / model / run · `hard out-of-sample gates` · `bit-exact replay` · `immutable ledgers` · `public forensic bug ledger` — rejected candidates stay published.

---

## 📦 Installation

<p align="center">
  <sub>Three paths — same safety guarantee · First run always <b>PAPER</b>, never LIVE silently</sub>
</p>

<!-- Card 1: End users -->
<div style="border:1px solid #30363d; border-radius:14px; padding:18px 20px; margin:14px 0; border-left:4px solid #238636;">

#### 🎁 End users — packaged release
<sup style="color:#8b949e;">No Python needed · Windows x64 · double-click install</sup>

Download from **[Releases →](https://github.com/Opselon/NexusTradingForexBot/releases)** and run:

```
NexusScalpEngine-<version>-win-x64-setup.exe   →  installer (recommended)
NexusScalpEngine-<version>-win-x64-portable.zip →  unzip & run
```

<sub>First-run wizard defaults to <b>PAPER</b> mode. Upgrades preserve your config.</sub>

</div>

<!-- Card 2: PowerShell bootstrap -->
<div style="border:1px solid #30363d; border-radius:14px; padding:18px 20px; margin:14px 0; border-left:4px solid #1f6feb;">

#### ⚡ PowerShell bootstrap — zero prerequisites
<sup style="color:#8b949e;">No Python · No Git · No admin · installs to <code>%LOCALAPPDATA%\Nexus</code></sup>

**Paste in PowerShell** — click top-right copy button:

```powershell
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
```

<details>
<summary><code>options</code> — preview, repair, pin</summary>

```powershell
# Preview what it will do (no changes)
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1) -DryRun

# Heal a broken install
.\install.ps1 -Repair

# Pin to an exact commit
.\install.ps1 -Commit <sha>
```

<sub>Idempotent & repeatable · <code>-DryRun</code> · <code>-Repair</code> · <code>-Commit &lt;sha&gt;</code> · portable Git fallback if needed</sub>

</details>

</div>

<!-- Card 3: Developers -->
<div style="border:1px solid #30363d; border-radius:14px; padding:18px 20px; margin:14px 0; border-left:4px solid #8957e5;">

#### 👩‍💻 Developers — from source

See **[🚀 Quick Start](#-quick-start)** above — clone, venv, `pip install -e ".[dev]"`, `nexus start`.

<sub>Requires Python 3.11 + Git. Full dev setup → <a href="docs/getting-started/installation.md">installation guide</a></sub>

</div>

<details>
<summary><b>Advanced — rollback, manifests, SHA-256</b></summary>

Full reference: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) · [docs/INSTALLER_ARCHITECTURE.md](docs/INSTALLER_ARCHITECTURE.md)

The release pipeline ships SHA-256 manifests + SBOM with post-publish verification; `nexus update verify|rollback` covers the installed lifecycle.

</details>

---

## 🛡️ Why Nexus — Engineering Philosophy

*Enforced in code and contracts, not just stated:*

| Principle | Guarantee |
|:---|:---|
| **📊 Evidence before claims** | Metrics without evidence render `n/a`, never fake zeros; runtime claims are graded |
| **⏳ No lookahead** | Purged + embargoed walk-forward; strictly causal features — `INV-008` |
| **🔄 Causal parity** | `live = replay = training` feature semantics, protected by schema hashing |
| **🎯 Runtime truth** | Broker truth wins over stale local state; gates are runtime authorities, settings are intent — `INV-011` |
| **🧱 Layered isolation** | Hexagonal ports/adapters; research holds **zero order authority** — `INV-002`; hot path never blocks on DB/training/network — `INV-001` |
| **✅ Validation before promotion** | `OOS failure ⇒ REJECTED`; promotion is strictly operator-gated; candidates never promote themselves |

> Deep dive → [architecture overview](docs/architecture/overview.md) · [runtime invariants](agents/runtime_invariants.md)

---

## 🏗️ Architecture at a glance

```
 Market Data  ( MT5 · ZMQ gateway · paper )
      ↓
 Causal Features ── 50D live contract (scalp_v1) · governed 70D research assembly
      ↓
 Inference Validator ── schema hash · scaler dim · bounds (loud rejection, never silent)
      ↓
 ScalpNet  ( TCN + self-attention ) ── 4 logits → confidence gate → Regime Guardian
      ↓
 SMC Policy Matrix ──► Risk Engine  ( Kelly sizing · margin / tier clamps )
      ↓
 OrderManager ── scenario router · position-state machine · hard clamps
      ↓
 IMT5Port adapter ──► MetaTrader 5 (live) · paper simulator · zero-order shadow
      ↓
 Accounting  ( immutable SQLite WAL ledger ) ──► Experience & autopsy intelligence
      ↓
 Observability  ( logs · incidents · forensics )  ↔  Research loop
      ↓                                              ( walk-forward → OOS gate → operator promotion )
 Control Center  ( FastAPI · REST / SSE / WS dashboard )
```

Every stage is isolated behind hexagonal ports — every promotion requires an operator decision on reproducible evidence.

<p align="center">
  <a href="docs/architecture/overview.md">Overview</a> ·
  <a href="docs/architecture/system-map.md">System Map</a> ·
  <a href="docs/architecture/data-flow.md">Data Flow</a> ·
  <a href="docs/architecture/model-pipeline.md">Model Pipeline</a> ·
  <a href="agents/skill.md">agents/skill.md</a>
</p>

---

## 🔐 Safety & Operating Modes

| Mode | Market data | Orders | Purpose |
|:---|:---|:---|:---|
| **PAPER** *(default)* | simulated | simulated | First run, development, UI work |
| **SHADOW** | live feed | **none — zero order authority** | Evaluate signals on real data, risk-free |
| **LIVE** | live feed | **real** | Real financial risk — explicit confirmation + full risk panel |

- The live adapter refuses a terminal logged into a **different account** than configured (**account-identity fail-safe**).
- Recommended path: **DEMO MT5 → SHADOW for days → small LIVE** → [first-run guide](docs/getting-started/first-run.md)

> [!WARNING]
> **This bot places REAL trades with REAL money in LIVE mode.** Hard clamps protect the strategy — not your capital from market volatility.

---

## 🖥️ Control Center

`nexus start` serves the **Control Center** at `http://127.0.0.1:8080` — live M1 chart with Order Block / FVG / swept-liquidity overlays, entry-SL-TP lines with risk tooltips, plus Overview · Strategy Research · News Intelligence · Scalping Rules · Account · Debug Hub.

> Telemetry streams over **SSE**, dashboard over **WebSocket**, tuner applies without restart.
> Docs → [control-center-ui.md](docs/architecture/control-center-ui.md)

<p align="center">
  <img src="pics/_shot_final_1920.png" alt="Account Center — 1920px" width="49%">
  <img src="pics/_shot_final_768.png" alt="Account Center — 768px" width="49%">
  <img src="pics/_shot_deep_state.png" alt="Deep state" width="49%">
  <img src="pics/_case_many.png" alt="Many metrics" width="49%">
</p>

---

## ⌨️ CLI

The bundled `nexus` console is the operational control surface (`nse` is a legacy alias; from source `python -m nexus_scalp.cli.main`).

```text
nexus start [--mode paper|shadow|live] [--port N] [--daemon]   # paper default · live needs confirmation
nexus stop / restart                                           # control the background engine
nexus status / health                                          # READY / DEGRADED / NOT READY
nexus doctor                                                   # 19-category diagnostics + suggested fixes
nexus setup / install                                          # first-run wizard
nexus logs [--tail N] [--errors]                               # tail / filter / export logs
nexus config [--validate path]                                 # inspect / validate (secrets masked)
nexus test --mode quick|unit|integration                       # run suites (never live-broker tests)
nexus update check|download|install|verify|rollback            # release lifecycle
nexus export-diagnostics                                       # sanitized diagnostics ZIP (no secrets)
```

Stable exit codes: `0` success · `1` runtime/validation · `2` usage · `3` environment blocked · `4` release verification · `5` update.
Full reference → [CLI reference](docs/reference/cli-reference.md)

<details>
<summary><b>Research / database / forensics commands</b></summary>

```text
nexus model-dataset-build / -experiment-create / -train / -validate / -replay
                                               # artifact-first model factory (research)
nexus db hygiene status|plan|run               # database hygiene (audit-only by default)
nexus forensic --deploy-gate                   # forensic pre-release gate (wired into beforePush)
nexus incidents list|reports|export            # incident center (sanitized exports)
nexus repair                                   # non-destructive derived-state repair
nexus release info                             # installed release metadata
```

</details>

---

## 🐳 Docker

```bash
cp .env.example .env       # optional — safe defaults exist
docker compose up -d --build
```

Starts engine + Control Center in container-safe **PAPER** mode; dashboard at `http://localhost:9090`; `/health` readiness (`READY`/`DEGRADED` = healthy).
Full reference → [docs/docker.md](docs/docker.md)

---

## 🔬 Research & Validation

```
DATA ─► FEATURES ─► MODEL ─► STRATEGY ─► BACKTEST ─► WALK-FORWARD ─► OOS GATE
     ─► ROBUSTNESS ─► COUNTERFACTUAL ─► REPLAY ─► VALIDATION ─► OPERATOR PROMOTION
```

- **Philosophy:** reproducibility, no future leakage, deterministic replay, identity/fingerprint tracking, dataset & model provenance, execution fidelity — [methodology](docs/research/methodology.md) · [validation](docs/research/validation.md) · [runtime path](docs/architecture/runtime.md)
- **Negative results are published, not buried.** The flagship 70D series (`scalp_v3`) was returned **NOT_ELIGIBLE** by the hard OOS gate on real-data walk-forward and was **not promoted**. The live contract remains governed. Evidence: [TASK-05](docs/TASK-05-70D-SHADOW-FINAL.md) · [TASK-09](docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md) · [status](docs/project/status.md)

---

## 🧪 Engineering Quality

```bash
pytest tests/unit -q              # unit corpus (critical subset: tests/critical_suite.txt)
pytest tests/unit tests/integration -q
./beforePush.sh -SkipPush         # full gate: ruff · format · mypy · critical suite · forensic deploy gate
```

CI runs `ruff` · `mypy` · `pytest (critical suite)` · `CodeQL` · `Trivy` · `OSV` · `JS` tests. Keep commits atomic (`<Name>: <summary>`); `reuse > extend > refactor > create`.
Quality docs → [docs/engineering/quality.md](docs/engineering/quality.md)

---

## 📚 Documentation

**Site:** <https://opselon.github.io/NexusTradingForexBot/>

| Category | Entry points |
|:---|:---|
| Getting started | [quickstart](docs/getting-started/quickstart.md) · [installation](docs/getting-started/installation.md) |
| Architecture | [overview](docs/architecture/overview.md) · [system map](docs/architecture/system-map.md) |
| Research | [methodology](docs/research/methodology.md) · [validation](docs/research/validation.md) |
| Engineering & quality | [quality gates](docs/engineering/quality.md) · [release process](docs/engineering/release-process.md) |
| Guides | [troubleshooting](docs/guides/troubleshooting.md) · [common workflows](docs/guides/common-workflows.md) |
| Reference | [CLI reference](docs/reference/cli-reference.md) · [FAQ](docs/reference/faq.md) |
| Contributing | [contribution guide](docs/contributing/contribution-guide.md) · [add a language](docs/contributing/add-language.md) |

**Languages:** English (source) · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) — RTL for Persian/Arabic; partial translations are marked honestly.

<details>
<summary><b>Engineering memory & internal maps (maintainers)</b></summary>

- [`agents/skill.md`](agents/skill.md) — authoritative architecture map
- [`agents/bugs.md`](agents/bugs.md) — public forensic bug ledger (BUG-001…)
- [`agents/runtime_invariants.md`](agents/runtime_invariants.md) — INV registry
- [`agents/contracts.md`](agents/contracts.md) · [`agents/taskboard.md`](agents/taskboard.md)
- [`docs/forensics/README.md`](docs/forensics/README.md) — forensic recovery & provenance index
- [`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) — 70D research contract

</details>

<details>
<summary><b>Repository structure</b></summary>

```text
src/nexus_scalp/   Core engine (hexagonal packages: domain, ports, adapters, features,
                   models, signals, risk, execution, application, research, shadow, …)
tests/             Unit + integration + golden + js + installer suites
Web/               Control Center UI — buildless vanilla-JS SPA (no Node runtime)
agents/            Engineering memory: architecture map, bug ledger, contracts, taskboard
docs/              Engineering documentation (this platform's IA tree)
site/              GitHub Pages source (multilingual static site)
configs/           base.yaml · live.yaml.example
scripts/           Build/release scripts, quality gates, docs tooling (scripts/docs/)
installer/         PowerShell bootstrap installer (stage protocol)
docker/            entrypoint.sh · healthcheck.sh
pics/              Screenshots
```

</details>

---

## 🤝 Contributing

> **Nexus is actively evolving and welcomes contributors** in quantitative research · ML / time-series modeling · market microstructure · trading-system engineering · frontend / Control Center · testing / QA · documentation and localization.

1. Read the [contribution guide](docs/contributing/contribution-guide.md).
2. Claim a task in [`agents/taskboard.md`](agents/taskboard.md) or open an issue with a `[Research]` / `[Proposal]` tag.
3. Fork & submit a PR — atomic commits (`<Name>: <summary>`), Ruff, strict mypy, pytest coverage. **Evidence-based changes only.**

---

## 📌 Status & Roadmap

Versioning is single-sourced in [`pyproject.toml`](pyproject.toml) (semver, stamped into every artifact) — the release badge reads the latest tag dynamically.

Current state → **[docs/project/status.md](docs/project/status.md)** · [capabilities](docs/project/capabilities.md)

**Known limitations (published, not hidden):**
- 70D research series is candidate-only with **negative OOS evidence so far**; live contract stays 50D until a candidate clears all gates and an operator promotes it.
- Packaged release is **Windows x64 only**.
- News engine is opt-in (disabled until a `news:` config block exists).
- No CLI hot-swap to the PAPER adapter — risk-free validation goes through SHADOW or a demo account.

**Current priorities:**
- Rebuild 70D candidate evidence under corrected confidence semantics (`CHG-0042`) — under evaluation
- Counterfactual evidence deepening (`CHG-0041`) → policy review
- Large-file decomposition with golden tests (`CHG-0032-A1`)
- 70D promotion decision — operator-gated, or honest retirement

Full roadmap with dependencies & completion gates → [docs/project/roadmap.md](docs/project/roadmap.md)

---

## 📄 License & Disclaimer

**DISCLAIMER:** Algorithmic trading — especially leveraged XAUUSD/Gold scalping — carries immense financial risk. This engine is provided strictly for educational, academic research and simulation purposes; it does not guarantee profit and is not financial advice. Always perform rigorous backtesting and forward paper-trading before committing capital. In LIVE mode it can place real trades — operator responsibility.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*

---

<p align="center">
  <a href="https://opselon.github.io/NexusTradingForexBot/">Documentation</a> ·
  <a href="docs/project/roadmap.md">Roadmap</a> ·
  <a href="docs/project/status.md">Status</a> ·
  <a href="https://github.com/Opselon/NexusTradingForexBot/issues">Issues</a> ·
  <a href="https://github.com/Opselon/NexusTradingForexBot/releases">Releases</a>
</p>
