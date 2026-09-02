# ⚡ NEXUS SCALP ENGINE

**A research-driven quantitative trading platform — causal features, governed models, invariant risk, deterministic research, forensic observability.**

*Hexagonal, event-driven scalping runtime for MetaTrader 5 — primary market XAUUSD (Gold) M1.*

**English** · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) · 📚 [Documentation site](https://opselon.github.io/NexusTradingForexBot/)

[![Release](https://img.shields.io/github/v/tag/Opselon/NexusTradingForexBot?label=release&sort=semver&style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/releases)
[![CI](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml/badge.svg?style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml)
[![Security](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml/badge.svg?style=for-the-badge)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/security.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Docs](https://img.shields.io/badge/Docs-GitHub_Pages-168AFF?style=for-the-badge&logo=github&logoColor=white)](https://opselon.github.io/NexusTradingForexBot/)
[![Research status](https://img.shields.io/badge/Research-50D_live_·_70D_candidate-8A63D2?style=for-the-badge)](docs/project/status.md)
[![License](https://img.shields.io/badge/License-Proprietary-grey?style=for-the-badge)](#-license--disclaimer)

<p align="center">
  <img src="pics/web.png" alt="Nexus Trading Control Center" width="100%">
</p>

> [!IMPORTANT]
> **Research and engineering platform — not a promise of profit.** Every status
> in this README is evidence-graded, and the repository publishes its *negative*
> results alongside its wins. Leveraged scalping carries extreme financial risk.

---

## 🧭 Start here

| You are… | Your question | Go to |
| :--- | :--- | :--- |
| A developer | "What is this?" | [What is Nexus](#-what-is-nexus) · [Vision](docs/project/vision.md) |
| A developer | "How do I run it?" | [Quickstart](#-quickstart) |
| A developer | "How does the architecture work?" | [System at a glance](#%EF%B8%8F-system-at-a-glance) · [Data flow](docs/architecture/data-flow.md) |
| A researcher | "How is validity established?" | [Research & validation](#-research--validation) |
| A contributor | "How do I contribute?" | [Contributing](docs/contributing/contribution-guide.md) |
| A Persian/Arabic/Spanish/German reader | "Where is my language?" | [Documentation hub](#-documentation) |

---

## 📖 What is Nexus

**Nexus Scalp Engine (NSE)** unifies deep-learning inference, real-time
market-microstructure analysis and high-frequency execution into one auditable
pipeline. It is driven by a **causal feature engine** (the live 50D contract,
extended by the 70D research contract with news + liquidity intelligence), a
**dual-path TCN + self-attention model (ScalpNet)**, an **SMC policy matrix**
(Order Blocks, Fair Value Gaps, Liquidity Sweeps), an **invariant risk engine**
and a **60-scenario execution router** — all wired through a hexagonal core
that executes on MetaTrader 5 (Win32 IPC, ZMQ remote gateway, or paper
simulator).

What makes it different is not a claim of alpha — it is **how claims are
treated**: identity fingerprints on every dataset/model/run, hard out-of-sample
gates, bit-exact replay requirements, immutable ledgers, and a public forensic
bug ledger. Rejected candidates stay published — including the flagship 70D
series, which the validation gates returned as **NOT_ELIGIBLE** on real-data
OOS evidence, keeping the live contract honestly at 50D.

## 💡 Why Nexus — engineering philosophy

Enforced in code and contracts, not just stated:

- **Evidence before claims** — metrics without evidence render `n/a`, never fake zeros; runtime claims are graded (CODE / TEST / INTEGRATION / LIVE / RELEASE VERIFIED).
- **No lookahead** — purged + embargoed walk-forward, strictly causal features, REPLACE+ALIGN history (INV-008).
- **Causal parity** — live = replay = training feature semantics, protected by schema hashing.
- **Runtime truth** — broker truth wins over stale local state; gates are runtime authorities, settings are intent (INV-011).
- **Layered isolation** — hexagonal ports/adapters; research & learning components hold **zero order authority** (INV-002).
- **Failure isolation** — the tick hot path never blocks on DB, training or network (INV-001).
- **Validation before promotion** — OOS failure ⇒ REJECTED; promotion is strictly operator-gated; candidates never promote themselves.

## 🗺️ System at a glance

```text
 Market Data (MT5 / ZMQ gateway / paper)
      │
      ▼
 Causal Features ── 50D live contract (scalp_v1) · governed 70D assembly (Base|News|Liquidity)
      │
      ▼
 Inference Validator ── schema hash · scaler dim · bounds  (loud rejection, never silent)
      │
      ▼
 ScalpNet (TCN + self-attention) ── 4 logits → confidence gate → Regime Guardian
      │
      ▼
 SMC Policy Matrix (~30 rules) ──► Risk Engine (Kelly sizing · margin/tier clamps)
      │
      ▼
 OrderManager ── 60-scenario router · 11 position states · hard clamps
      │
      ▼
 IMT5Port adapter ──► MetaTrader 5 (live) · paper simulator · zero-order shadow
      │
      ▼
 Accounting (immutable SQLite WAL ledger) ──► Experience & autopsy intelligence
      │                                          │
      ▼                                          ▼
 Observability (logs · incidents · forensics)   Research loop (datasets → walk-forward
      │                                          → OOS gate → shadow → operator promotion)
      ▼
 Control Center (FastAPI · REST/SSE/WS dashboard)
```

Deep dive: [`docs/architecture/overview.md`](docs/architecture/overview.md) ·
[system map](docs/architecture/system-map.md) ·
[data flow](docs/architecture/data-flow.md) ·
[Model pipeline](docs/architecture/model-pipeline.md) ·
authoritative internal map: [`agents/skill.md`](agents/skill.md).

## 🧱 Core capabilities

Full evidence-graded matrix: [`docs/project/capabilities.md`](docs/project/capabilities.md)

| Capability | Status | Note |
| :--- | :--- | :--- |
| Causal 50D feature engine (`scalp_v1`) | ✅ Certified | live contract · deterministic NaN/Inf fallback |
| Risk engine + execution clamps | ✅ Certified | margin ≤20% · `HARD_MAX_LOTS=10` · circuit breaker |
| Shadow runtime (zero order authority) | ✅ Certified | live feed, `simulated=True`, no orders |
| Walk-forward + hard OOS gate | ✅ Certified | OOS failure ⇒ REJECTED (proven on 70D) |
| Windows installer + update/rollback | ✅ Certified | SHA-256 · manifests · SBOM · post-publish verify |
| Provider health gate (LLM services) | ✅ Certified | bounded retries · auto-disable · secrets redacted |
| ScalpNet + artifact-first Model Factory | 🟢 Implemented | versioned datasets/models with manifests; inference needs no DB |
| Execution (60-scenario router, 11 states) | 🟢 Implemented | golden-tested seams; atomic teardown |
| Immutable accounting + experience loop | 🟢 Implemented | trade autopsy, behavior detection |
| Incident response & forensic health | 🟢 Implemented | deploy gate · correlation · lineage |
| Control Center UI (FastAPI SPA) | 🟢 Implemented | SSE/WS · live chart overlays · debug console |
| 70D contract (`scalp_v3`) + liquidity/news 10D blocks | 🟡 Experimental | candidate-only; **negative OOS evidence so far** |
| News intelligence (bounded gate) | 🟡 Experimental | opt-in; can never force a trade |
| Counterfactual engine (NO_TRADE walk) | 🔵 Research | 2095 decisions walked; stratified evidence |
| Temporal features / MSLIE perception | 🔵 Research | never active without governance |
| Multi-broker | 📌 Planned | `IMT5Port` is the seam; nothing else exists yet |

**Safety invariants (engine-enforced):** research/strategy/news workers never
hold order authority · candidates never promote themselves · OOS failure ⇒
REJECTED · schema mismatch fails loudly · the live tick path never blocks on
analytics work.

## 🔄 Research & validation

```text
DATA ─► FEATURES ─► MODEL ─► STRATEGY ─► BACKTEST ─► WALK-FORWARD ─► OOS GATE
     ─► ROBUSTNESS ─► COUNTERFACTUAL ─► REPLAY ─► VALIDATION ─► OPERATOR PROMOTION
```

- **Validation philosophy:** reproducibility, no future leakage, deterministic
  replay, identity/fingerprint tracking, dataset & model provenance, execution
  fidelity, evidence capture — details in
  [`docs/research/methodology.md`](docs/research/methodology.md) and
  [`docs/research/validation.md`](docs/research/validation.md).
- **Proven it bites:** the 70D candidate failed this exact chain
  (OOS NOT_ELIGIBLE) and was *not* promoted. The gates reject; the rejections
  are public.
- **Runtime path** (live loop, modes, workers): [`docs/architecture/runtime.md`](docs/architecture/runtime.md).

## 🧪 Project maturity

| Tier | Meaning | Examples |
| :--- | :--- | :--- |
| **CERTIFIED** | formal forensic acceptance + reproducible evidence | release pipeline · OOS gate behavior · provider gate live smoke |
| **IMPLEMENTED** | in code with regression coverage | governance · execution · accounting · observability |
| **EXPERIMENTAL** | exists, explicitly off the live path | 70D series · news gate · temporal candidates |
| **RESEARCH** | evidence-generation stage | counterfactuals · MSLIE |
| **PLANNED** | registered with completion criteria | multi-broker · translation completion |

Never: PLANNED presented as IMPLEMENTED · EXPERIMENTAL presented as
production-ready. Status authority: [`docs/project/status.md`](docs/project/status.md).

## 🗓️ Roadmap (honest)

Full roadmap with dependencies & completion gates:
[`docs/project/roadmap.md`](docs/project/roadmap.md)

| Horizon | Stream | Item | Status |
| :--- | :--- | :--- | :--- |
| NOW | VALIDATION | Rebuild 70D candidate evidence (corrected confidence semantics, CHG-0042) | Under Evaluation |
| NOW | RESEARCH | Counterfactual evidence deepening (CHG-0041) | In progress |
| NOW | ARCHITECTURE | Large-file decomposition with golden tests (CHG-0032-A1) | In progress |
| NEXT | ML | 70D promotion decision — operator-gated, or honest retirement | Planned |
| NEXT | VALIDATION | Counterfactual-driven policy review | Planned |
| LATER | ML | Temporal liquidity features · regime-conditional selection | Research Direction |
| LONG | EXECUTION | Broker abstraction beyond MT5 (`IMT5Port` seam) | Direction, not commitment |

## 🚀 Quickstart

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1    |   Linux/macOS:  source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]

nexus doctor          # read-only diagnostics (19 categories)
nexus start           # PAPER mode (default, safe) → Control Center at http://127.0.0.1:8080
nexus start --mode shadow   # live feed, ZERO order authority — the recommended evaluation mode
nexus stop
```

More entry paths and the first-run wizard: [`docs/getting-started/installation.md`](docs/getting-started/installation.md) ·
[`docs/getting-started/quickstart.md`](docs/getting-started/quickstart.md).

## 📦 Installation (3 paths)

1. **End users — packaged release (no Python):** download
   `NexusScalpEngine-<version>-win-x64-setup.exe` (or portable `.zip`) from
   [**Releases**](https://github.com/Opselon/NexusTradingForexBot/releases).
   First run opens the setup wizard (`nexus setup`) — **default mode PAPER, never LIVE silently**.
2. **PowerShell bootstrap (no Python, no Git, no admin):**
   ```powershell
   iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
   ```
   `. install.ps1 -DryRun` shows the plan as JSON · `-Repair` repairs without touching user data ·
   `-Commit <sha>` pins a reproducible version. Installs under `%LOCALAPPDATA%\Nexus`.
3. **Developers — from source:** see [Quickstart](#-quickstart).

Full reference: [`docs/INSTALL_WINDOWS.md`](docs/INSTALL_WINDOWS.md) · architecture: [`docs/INSTALLER_ARCHITECTURE.md`](docs/INSTALLER_ARCHITECTURE.md).

## 🖥️ Web UI

`nexus start` serves the **Control Center**: live M1 chart with Order Block /
FVG / swept-liquidity overlays and entry-SL-TP lines with risk tooltips;
Overview · Strategy Research · News Intelligence · Scalping Rules · Account
(Performance & Intelligence) · Debug Hub; live algorithm tuner
(`PUT /api/algo/config`, no restart). FastAPI REST `:8080/api/...`, tick stream
over SSE, dashboard over WebSocket.

<p align="center">
  <img src="pics/_shot_final_1920.png" alt="Account Center — 1920px" width="49%">
  <img src="pics/_shot_final_768.png" alt="Account Center — 768px" width="49%">
  <img src="pics/_shot_deep_state.png" alt="Deep state (expanded intelligence)" width="49%">
  <img src="pics/_case_many.png" alt="Many metrics view" width="49%">
</p>

## ⌨️ CLI

The bundled `nexus` console command is the operational control surface
(`nse` is a legacy alias; from source `python -m nexus_scalp.cli.main`).

```text
nexus start     --mode paper|shadow|live     Start engine (default paper; live needs confirmation)
nexus stop / restart                         Control the background engine
nexus status / health                        Full status · quick health verdict
nexus doctor                                 19-category diagnostics + suggested fixes
nexus setup / install                        First-run wizard
nexus logs [--tail N] [--errors]             Tail / filter / export logs
nexus config [--validate path]               Inspect / validate configuration (secrets masked)
nexus test --mode quick|unit|integration     Run test suites (never live-broker tests)
nexus update check|download|install|verify|rollback
nexus db hygiene status|plan|run             Database hygiene (audit-only by default)
nexus model-dataset-build / -experiment-create / -train / -validate / -replay
                                             Artifact-first model factory (research)
nexus forensic --deploy-gate                 Forensic pre-release gate
nexus export-diagnostics                     Sanitized diagnostics ZIP (never contains secrets)
```

Stable exit codes: `0` success · `1` runtime/validation · `2` usage ·
`3` environment blocked · `4` release verification · `5` update.
Full reference: [`docs/reference/cli-reference.md`](docs/reference/cli-reference.md).

## 🛡️ Safety — Demo → Shadow → Live

| Step | Action | Why |
| :--- | :--- | :--- |
| 1️⃣ | Log into a **DEMO account** in MT5 and confirm the account label | LIVE and demo look identical in MT5's login window |
| 2️⃣ | MT5 `Tools → Options → Expert Advisors → "Allow Algo Trading"` | Without it, orders are rejected |
| 3️⃣ | Copy `configs/live.yaml` → `configs/demo.yaml`; set demo credentials; keep `risk.max_concurrent_positions: 1` | Never touch a live config while real money is reachable |
| 4️⃣ | Run **SHADOW** for days; review dashboard + reports | Proves model/signals/gates before execution |
| 5️⃣ | Run the test suite weekly | Catches regressions before they reach a live account |
| 6️⃣ | Only then consider **LIVE**, small balance, `risk_per_trade_pct: 0.25` or lower | Leveraged XAUUSD scalping carries extreme risk |

The live adapter additionally refuses a terminal logged into a different
account than configured (**account-identity fail-safe**), and
`nexus start --mode live` prints the full risk panel and requires an explicit
interactive confirmation.

> [!WARNING]
> **This bot places REAL trades with REAL money in LIVE mode.** The engine's
> hard clamps protect the strategy — not your capital from market volatility.

## 📚 Documentation

**Documentation site (GitHub Pages):** <https://opselon.github.io/NexusTradingForexBot/>

| Section | Contents |
| :--- | :--- |
| [Getting started](docs/getting-started/quickstart.md) | installation · quickstart · first-run safety · configuration |
| [Project](docs/project/vision.md) | vision · scope · **status** · capability matrix · roadmap · milestones |
| [Architecture](docs/architecture/overview.md) | overview · system map · runtime · research stack · data flow · model pipeline · execution · observability · database |
| [Research](docs/research/methodology.md) | methodology · datasets · backtesting · walk-forward · OOS · replay · counterfactuals · validation · reproducibility |
| [Engineering](docs/engineering/quality.md) | quality gates · testing · CI · release process · security |
| [Guides](docs/guides/cli.md) | CLI · troubleshooting · common workflows |
| [Reference](docs/reference/glossary.md) | CLI reference · glossary · terminology · **FAQ** |
| [Contributing](docs/contributing/contribution-guide.md) | contribution guide · documentation workflow · adding a language |

**Languages:** English (source) · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) — coverage audited by `scripts/docs/check_translations.py`; RTL layouts for Persian/Arabic.

**Deep technical memory (agent-generated, preserved):**
[`agents/skill.md`](agents/skill.md) (architecture map) ·
[`agents/bugs.md`](agents/bugs.md) (forensic bug ledger) ·
[`agents/runtime_invariants.md`](agents/runtime_invariants.md) ·
[`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) ·
[`docs/RELEASE.md`](docs/RELEASE.md).

## 🐳 Docker

```bash
cp .env.example .env       # optional — safe defaults exist
docker compose up -d --build
```

Engine + Web UI in container-safe **PAPER** mode; dashboard at
`http://localhost:9090`; `/health` readiness (READY/DEGRADED = healthy);
canonical SQLite databases; migration gate at boot. Full reference:
[`docs/docker.md`](docs/docker.md).

## 🧰 Development

```bash
pytest tests/unit -q              # unit corpus (critical subset: tests/critical_suite.txt)
pytest tests/unit tests/integration -q
./beforePush.sh -SkipPush         # full gate: ruff · format · mypy · critical suite · forensic deploy gate
```

Contributors: read the [contribution guide](docs/contributing/contribution-guide.md),
claim a task in [`agents/taskboard.md`](agents/taskboard.md), and keep commits
atomic (`<Name>: <summary>`). Reuse > extend > refactor > create. The
[engineering memory](agents/skill.md) (architecture map, invariants, bug
ledger) is part of the review surface.

## 📁 Repository structure

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
pics/              Screenshots
docker/            entrypoint.sh · healthcheck.sh
```

## 📌 Status

Versioning is single-sourced in [`pyproject.toml`](pyproject.toml) (semver,
stamped into every artifact) — the release badge above reads the latest tag
dynamically. Current state, known limitations and the full evidence-graded
matrix: **[`docs/project/status.md`](docs/project/status.md)**.

Known limitations (published, not hidden): 70D research series candidate-only
with negative OOS evidence · Windows x64 only for the packaged release · news
engine opt-in · no CLI hot-swap to the PAPER adapter (risk-free validation via
SHADOW/demo).

## 🤝 Collaboration

We are expanding NSE into a global open-core quantitative framework and invite
quantitative ML researchers, low-latency systems engineers and institutional
traders to collaborate. Fork & PR (ruff, strict mypy, pytest coverage — the
gates enforce it), or open an issue with `[Research]` / `[Proposal]` tags.

## 📄 License & Disclaimer

**DISCLAIMER:** Algorithmic trading — especially leveraged XAUUSD/Gold scalping
— carries immense financial risk. This engine is provided strictly for
educational, academic research and simulation purposes. Always perform rigorous
backtesting and forward paper-trading before committing capital.

*Proprietary License — All Rights Reserved. Designed for Quantitative Excellence.*

---

<sub>
[Documentation](https://opselon.github.io/NexusTradingForexBot/) ·
[Roadmap](docs/project/roadmap.md) ·
[Status](docs/project/status.md) ·
[Issues](https://github.com/Opselon/NexusTradingForexBot/issues) ·
[Releases](https://github.com/Opselon/NexusTradingForexBot/releases)
</sub>
