# ⚡ Nexus Scalp Engine

**A research-driven quantitative trading engine — causal features, governed
models, invariant risk, deterministic research, forensic observability.**
Hexagonal, event-driven scalping runtime for MetaTrader 5 · primary market
XAUUSD (Gold) M1 · Python 3.11.

**English** · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) · 📚 [Documentation](https://opselon.github.io/NexusTradingForexBot/)

[![Release](https://img.shields.io/github/v/tag/Opselon/NexusTradingForexBot?label=release&sort=semver&style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/releases)
[![CI](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml/badge.svg?style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml)
[![Security](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml/badge.svg?style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Docs](https://img.shields.io/badge/Docs-GitHub_Pages-168AFF?style=for-the-badge&logo=github&logoColor=white)](https://opselon.github.io/NexusTradingForexBot/)
[![Research status](https://img.shields.io/badge/Research-50D_live_·_70D_candidate-8A63D2?style=for-the-badge)](docs/project/status.md)
[![License](https://img.shields.io/badge/License-Proprietary-grey?style=for-the-badge)](#license--disclaimer)

<p align="center">
  <img src="pics/web.png" alt="Nexus Trading Control Center" width="100%">
</p>

> [!IMPORTANT]
> **Research and engineering platform — not a promise of profit.** Every status
> claim is evidence-graded, and this repository publishes its *negative*
> results alongside its wins. Leveraged scalping carries extreme financial risk.

## Start here

| I want to… | Go to |
| :--- | :--- |
| Run Nexus in under 5 minutes | [Quick Start](#quick-start) |
| Understand the architecture | [Architecture at a glance](#architecture-at-a-glance) |
| Evaluate the research claims | [Research & validation](#research--validation) |
| Read the reference | [CLI reference](docs/reference/cli-reference.md) · [API](docs/guides/api.md) |
| Contribute | [Contributing](#contributing) |

## Quick Start

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1    |    Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"

nexus doctor                # read-only diagnostics + suggested fixes
nexus start                 # PAPER mode (default, safe) → Control Center at http://127.0.0.1:8080
nexus start --mode shadow   # live market feed, ZERO order authority — the recommended evaluation mode
nexus stop
```

The Control Center dashboard opens at `http://127.0.0.1:8080`. More entry paths
and the first-run wizard: [installation](docs/getting-started/installation.md) ·
[quickstart](docs/getting-started/quickstart.md).

## What is Nexus?

**Nexus Scalp Engine (NSE)** unifies deep-learning inference, real-time
market-microstructure analysis and high-frequency execution into one auditable
pipeline. A **causal feature engine**, a **dual-path TCN + self-attention model
(ScalpNet)**, an **SMC policy matrix** (Order Blocks, Fair Value Gaps,
Liquidity Sweeps), an **invariant risk engine** and a **hard-clamped execution
router** run on a hexagonal core that executes on MetaTrader 5 (Win32 IPC, ZMQ
remote gateway, or paper simulator).

What distinguishes Nexus is not a claim of alpha — it is **how claims are
treated**: identity fingerprints on every dataset/model/run, hard
out-of-sample gates, bit-exact replay requirements, immutable ledgers, and a
public forensic bug ledger. Rejected candidates stay published.

## Installation

1. **End users — packaged release (no Python):** download
   `NexusScalpEngine-<version>-win-x64-setup.exe` (or portable `.zip`) from
   [Releases](https://github.com/Opselon/NexusTradingForexBot/releases). The
   first-run wizard defaults to **PAPER — never LIVE silently**.
2. **PowerShell bootstrap (no Python, no Git, no admin):**

   ```powershell
   iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
   ```

   `. install.ps1 -DryRun` shows the plan · `-Repair` repairs without touching
   user data · `-Commit <sha>` pins a reproducible version. Installs under
   `%LOCALAPPDATA%\Nexus`.
3. **Developers — from source:** see [Quick Start](#quick-start).

<details>
<summary>Advanced installer details (rollback, manifests, SHA-256, internals)</summary>

Full reference: [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md) ·
architecture: [docs/INSTALLER_ARCHITECTURE.md](docs/INSTALLER_ARCHITECTURE.md).
The release pipeline ships SHA-256 manifests + SBOM with post-publish
verification; `nexus update verify|rollback` covers the installed lifecycle.

</details>

## Why Nexus — engineering philosophy

Enforced in code and contracts, not just stated:

- **Evidence before claims** — metrics without evidence render `n/a`, never fake zeros; runtime claims are graded.
- **No lookahead** — purged + embargoed walk-forward; strictly causal features (INV-008).
- **Causal parity** — live = replay = training feature semantics, protected by schema hashing.
- **Runtime truth** — broker truth wins over stale local state; gates are runtime authorities, settings are intent (INV-011).
- **Layered isolation** — hexagonal ports/adapters; research/learning components hold **zero order authority** (INV-002); the tick hot path never blocks on DB, training or network (INV-001).
- **Validation before promotion** — OOS failure ⇒ REJECTED; promotion is strictly operator-gated; candidates never promote themselves.

Deep technical detail: [architecture overview](docs/architecture/overview.md) ·
[agents/runtime_invariants.md](agents/runtime_invariants.md).

## Architecture at a glance

```text
 Market Data (MT5 · ZMQ gateway · paper)
      ↓
 Causal Features ── 50D live contract (scalp_v1) · governed 70D research assembly
      ↓
 Inference Validator ── schema hash · scaler dim · bounds (loud rejection, never silent)
      ↓
 ScalpNet (TCN + self-attention) ── 4 logits → confidence gate → Regime Guardian
      ↓
 SMC Policy Matrix ──► Risk Engine (Kelly sizing · margin/tier clamps)
      ↓
 OrderManager ── scenario router · position-state machine · hard clamps
      ↓
 IMT5Port adapter ──► MetaTrader 5 (live) · paper simulator · zero-order shadow
      ↓
 Accounting (immutable SQLite WAL ledger) ──► Experience & autopsy intelligence
      ↓
 Observability (logs · incidents · forensics)  ↔  Research loop (walk-forward
      ↓                                          → OOS gate → operator promotion)
 Control Center (FastAPI · REST/SSE/WS dashboard)
```

Every stage above is isolated behind hexagonal ports, and every promotion
between them requires an operator decision on reproducible evidence.

Deep dive: [overview](docs/architecture/overview.md) ·
[system map](docs/architecture/system-map.md) ·
[data flow](docs/architecture/data-flow.md) ·
[model pipeline](docs/architecture/model-pipeline.md) ·
authoritative internal map: [`agents/skill.md`](agents/skill.md).

## Safety & Operating Modes

| Mode | Market data | Orders | Purpose |
| :--- | :--- | :--- | :--- |
| **PAPER** *(default)* | simulated | simulated | first run, development, UI work |
| **SHADOW** | live feed | **none — zero order authority** | evaluate model/signals/gates on real data, risk-free |
| **LIVE** | live feed | real | **real financial risk**; explicit interactive confirmation + full risk panel |

- The live adapter refuses a terminal logged into a different account than
  configured (**account-identity fail-safe**).
- Recommended progression: **DEMO MT5 account → SHADOW for days → small LIVE**.
  Full walkthrough: [docs/getting-started/first-run.md](docs/getting-started/first-run.md).

> [!WARNING]
> **This bot places REAL trades with REAL money in LIVE mode.** The engine's
> hard clamps protect the strategy — not your capital from market volatility.

## Control Center (Web UI)

`nexus start` serves the **Control Center** at `http://127.0.0.1:8080`: a
live M1 chart with Order Block / FVG / swept-liquidity overlays and
entry-SL-TP lines with risk tooltips, plus Overview · Strategy Research ·
News Intelligence · Scalping Rules · Account · Debug Hub pages. Telemetry
streams over SSE, the dashboard over WebSocket, and the algorithm tuner
applies changes without a restart. UI documentation:
[docs/architecture/control-center-ui.md](docs/architecture/control-center-ui.md).

<p align="center">
  <img src="pics/_shot_final_1920.png" alt="Account Center — 1920px" width="49%">
  <img src="pics/_shot_final_768.png" alt="Account Center — 768px" width="49%">
  <img src="pics/_shot_deep_state.png" alt="Deep state (expanded intelligence)" width="49%">
  <img src="pics/_case_many.png" alt="Many metrics view" width="49%">
</p>

## CLI

The bundled `nexus` console command is the operational control surface
(`nse` is a legacy alias; from source `python -m nexus_scalp.cli.main`).

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

Stable exit codes: `0` success · `1` runtime/validation · `2` usage ·
`3` environment blocked · `4` release verification · `5` update.
Full reference: [docs/reference/cli-reference.md](docs/reference/cli-reference.md).

<details>
<summary>Research / database / forensics commands</summary>

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

## Docker

```bash
cp .env.example .env       # optional — safe defaults exist
docker compose up -d --build
```

Starts the engine + Control Center in container-safe **PAPER** mode; dashboard
at `http://localhost:9090`; `/health` readiness (READY/DEGRADED = healthy).
Full reference: [docs/docker.md](docs/docker.md).

## Research & validation

```text
DATA ─► FEATURES ─► MODEL ─► STRATEGY ─► BACKTEST ─► WALK-FORWARD ─► OOS GATE
     ─► ROBUSTNESS ─► COUNTERFACTUAL ─► REPLAY ─► VALIDATION ─► OPERATOR PROMOTION
```

- **Philosophy:** reproducibility, no future leakage, deterministic replay,
  identity/fingerprint tracking, dataset & model provenance, execution
  fidelity — [methodology](docs/research/methodology.md) ·
  [validation](docs/research/validation.md) ·
  [runtime path](docs/architecture/runtime.md).
- **Negative results are published, not buried.** The flagship 70D research
  series (`scalp_v3`) was returned **NOT_ELIGIBLE** by the hard OOS gate on
  real-data walk-forward evidence and was **not promoted**. The live contract
  remains the governed live contract; experimental variants never promote
  themselves. Evidence: [docs/TASK-05-70D-SHADOW-FINAL.md](docs/TASK-05-70D-SHADOW-FINAL.md)
  · [docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md](docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md)
  · [status](docs/project/status.md).

## Engineering quality

```bash
pytest tests/unit -q              # unit corpus (critical subset: tests/critical_suite.txt)
pytest tests/unit tests/integration -q
./beforePush.sh -SkipPush         # full gate: ruff · format · mypy · critical suite · forensic deploy gate
```

CI runs ruff · mypy · pytest (critical suite) · CodeQL · Trivy · OSV · JS
tests. Contributors: keep commits atomic (`<Name>: <summary>`); reuse > extend
> refactor > create. Quality docs:
[docs/engineering/quality.md](docs/engineering/quality.md).

## Documentation

**Documentation site (GitHub Pages):** <https://opselon.github.io/NexusTradingForexBot/>

| Category | Entry points |
| :--- | :--- |
| Getting started | [quickstart](docs/getting-started/quickstart.md) · [installation](docs/getting-started/installation.md) |
| Architecture | [overview](docs/architecture/overview.md) · [system map](docs/architecture/system-map.md) |
| Research | [methodology](docs/research/methodology.md) · [validation](docs/research/validation.md) |
| Engineering & quality | [quality gates](docs/engineering/quality.md) · [release process](docs/engineering/release-process.md) |
| Guides | [troubleshooting](docs/guides/troubleshooting.md) · [common workflows](docs/guides/common-workflows.md) |
| Reference | [CLI reference](docs/reference/cli-reference.md) · [FAQ](docs/reference/faq.md) |
| Contributing | [contribution guide](docs/contributing/contribution-guide.md) · [add a language](docs/contributing/add-language.md) |

**Languages:** English (source) · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) — RTL layouts for Persian/Arabic; partial translations are marked honestly.

<details>
<summary>Engineering memory & internal maps (maintainers)</summary>

- [`agents/skill.md`](agents/skill.md) — authoritative architecture map
- [`agents/bugs.md`](agents/bugs.md) — public forensic bug ledger (BUG-001…)
- [`agents/runtime_invariants.md`](agents/runtime_invariants.md) — INV registry
- [`agents/contracts.md`](agents/contracts.md) · [`agents/taskboard.md`](agents/taskboard.md)
- [`docs/forensics/README.md`](docs/forensics/README.md) — forensic recovery & provenance index (timeline, evidence, status)
- [`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) — 70D research contract
- [`docs/RELEASE.md`](docs/RELEASE.md) — release process
- [`docs/architecture/qa-assurance-contract.md`](docs/architecture/qa-assurance-contract.md) — QA contract

</details>

<details>
<summary>Repository structure</summary>

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

## Contributing

> **Nexus is actively evolving and welcomes contributors** in quantitative
> research · ML / time-series modeling · market microstructure · trading-system
> engineering · frontend / Control Center · testing / QA · documentation and
> localization.

1. Read the [contribution guide](docs/contributing/contribution-guide.md).
2. Claim a task in [`agents/taskboard.md`](agents/taskboard.md) or open an
   issue with a `[Research]` / `[Proposal]` tag.
3. Fork & submit a PR — atomic commits (`<Name>: <summary>`), Ruff, strict
   mypy, pytest coverage. **Evidence-based changes only:** the quality gates
   enforce it, and the engineering memory is part of the review surface.

## Status & Limitations

Versioning is single-sourced in [`pyproject.toml`](pyproject.toml) (semver,
stamped into every artifact) — the release badge reads the latest tag
dynamically. Current state and the full evidence-graded matrix:
**[docs/project/status.md](docs/project/status.md)** ·
[capabilities](docs/project/capabilities.md).

Known limitations (published, not hidden):

- 70D research series is candidate-only with **negative OOS evidence so far**;
  the live contract stays 50D until a candidate clears all gates and an
  operator promotes it.
- Packaged release is **Windows x64 only**.
- News engine is opt-in (disabled until a `news:` config block exists).
- No CLI hot-swap to the PAPER adapter — risk-free validation goes through
  SHADOW or a demo account.

## Roadmap

Current priorities:

- Rebuild 70D candidate evidence under corrected confidence semantics (CHG-0042) — under evaluation.
- Counterfactual evidence deepening (CHG-0041) → policy review.
- Large-file decomposition with golden tests (CHG-0032-A1).
- 70D promotion decision — operator-gated, or honest retirement.

Full roadmap with dependencies & completion gates:
[docs/project/roadmap.md](docs/project/roadmap.md).

## License & Disclaimer

**DISCLAIMER:** Algorithmic trading — especially leveraged XAUUSD/Gold scalping
— carries immense financial risk. This engine is provided strictly for
educational, academic research and simulation purposes; it does not guarantee
profit and is not financial advice. Always perform rigorous backtesting and
forward paper-trading before committing capital. In LIVE mode it can place
real trades — operator responsibility.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*

---

[Documentation](https://opselon.github.io/NexusTradingForexBot/) ·
[Roadmap](docs/project/roadmap.md) ·
[Status](docs/project/status.md) ·
[Issues](https://github.com/Opselon/NexusTradingForexBot/issues) ·
[Releases](https://github.com/Opselon/NexusTradingForexBot/releases)
