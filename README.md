<div align="center">

# NEXUS SCALP ENGINE

**Research-driven quantitative trading & execution platform —
data → features → models → policy → risk → execution → evidence,
in one auditable, multilingual-engineering pipeline.**

[![CI](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml/badge.svg)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml)
[![Release](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/release.yml/badge.svg)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/release.yml)
[![Docs](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/docs.yml/badge.svg)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/docs.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C.svg?logo=pytorch&logoColor=white)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MetaTrader-5-2962FF.svg)](https://www.mql5.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets·SSE-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.tech/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL_Ledger-003B57.svg?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![License](https://img.shields.io/badge/License-Proprietary-lightgrey.svg)](#-license--disclaimer)
[![Status](https://img.shields.io/badge/Runtime-Production_Hardened-2ea44f.svg)](docs/project/status.md)
[![Research](https://img.shields.io/badge/Research-Evidence_Graded-8a63d2.svg)](docs/research/methodology.md)

[📖 Documentation Site](https://opselon.github.io/NexusTradingForexBot/) ·
[🚀 Quickstart](#-quickstart) ·
[🏛️ Architecture](docs/architecture/overview.md) ·
[🧪 Research & Validation](docs/research/methodology.md) ·
[🗺️ Roadmap](docs/project/roadmap.md) ·
[🤝 Contributing](docs/contributing/contribution-guide.md)

**English** · [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) · [Español](https://opselon.github.io/NexusTradingForexBot/es/) · [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) · [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/)

<p>
  <img src="pics/web.png" alt="Nexus Trading Control Center" width="92%">
</p>

</div>

---

## What is this?

**Nexus Scalp Engine (NSE)** is an open-engineering quantitative trading
platform for MetaTrader 5 — primary market **XAUUSD (Gold) M1** plus major FX
pairs. It connects deep-learning inference (dual-path TCN + self-attention),
a **causal 50D feature contract** (extendable to the 70D research contract
with news + liquidity intelligence), an SMC policy matrix, a bounded
risk engine, deterministic research tooling (walk-forward, OOS gate, replay,
counterfactuals) and forensic observability into **one hexagonal,
event-driven runtime** — where every claim is traceable to evidence.

> **What makes it different:** the platform publishes its own negative
> results. The heavily-engineered 70D candidate was **rejected** by the
> out-of-sample gate — and that rejection is preserved as a first-class
> result. A validation layer that can say *no* is the entire point.

| You are… | Start here |
| :--- | :--- |
| 🧭 A visitor — "what is this?" | [Vision & Why](docs/project/vision.md) · [Project Status](docs/project/status.md) |
| 🚀 A user — "how do I run it?" | [Quickstart](#-quickstart) · [Installation](docs/getting-started/installation.md) |
| 🏛️ An engineer — "how does it work?" | [Architecture](docs/architecture/overview.md) · [Data Flow](docs/architecture/data-flow.md) |
| 🧪 A researcher — "is this valid?" | [Research Methodology](docs/research/methodology.md) · [OOS Gate](docs/research/out-of-sample.md) |
| 🤝 A contributor | [Contribution Guide](docs/contributing/contribution-guide.md) |
| 📚 Looking for something specific | [Full documentation index](docs/index.md) · [Glossary](docs/reference/glossary.md) · [FAQ](docs/reference/faq.md) |

---

## Why Nexus?

The engineering philosophy — enforced in code, contracts and CI, not just
stated:

- **Evidence before claims.** Metrics without evidence render `n/a` — never
  fake zeros. Runtime claims are graded (CODE / TEST / INTEGRATION / LIVE /
  RELEASE VERIFIED).
- **No lookahead.** Purged + embargoed walk-forward, strictly causal
  features, broker history REPLACE+ALIGN (INV-008).
- **Causal parity.** Live = replay = training feature semantics, bound by a
  hashed schema contract.
- **Runtime truth.** Broker facts and runtime gates outrank intent and stale
  caches (INV-011); historical ledger rows are immutable (INV-007).
- **Layered architecture.** Hexagonal ports-and-adapters; research/learning
  components physically hold no order authority (INV-002).
- **Failure isolation.** The tick hot path never blocks on DB, training or
  network (INV-001); incidents are diagnostic-only.
- **Validation before promotion.** OOS failure ⇒ REJECTED. Promotion is
  operator-gated. Candidates never promote themselves.

Full philosophy: [Vision](docs/project/vision.md) · Runtime invariants:
[`agents/runtime_invariants.md`](agents/runtime_invariants.md)

---

## System at a glance

```text
Market Data (MT5 / paper / gateway)
    ↓
Causal Features — 50D base (scalp_v1 · ACTIVE) → 70D research assembly
    ↓
Inference Contract Validator (schema hash · scaler dim · bounds)
    ↓
ScalpNet (dual-path TCN + self-attention, 4-logit head)
    ↓
Confidence Gate → Regime Guardian → SMC Policy Matrix (~30 rules)
    ↓
Risk Engine (fractional-Kelly sizing, margin clamps, exposure caps)
    ↓
Order Manager (60-scenario router, 11 position states, circuit breaker)
    ↓
Execution Boundary — IMT5Port → MT5 IPC / ZMQ gateway / paper  (LIVE = explicit consent)
    ↓
Accounting Ledger (SQLite WAL, immutable) → Experience → Autopsy
    ↓
Research Loop (datasets → walk-forward → OOS gate → shadow → operator promotion)
    ↓
Observability (structured logs · incidents · forensics · Telegram read-only)
    ↓
Control Center (FastAPI + WS/SSE) · API · Diagnostics
```

Deep dive: [Architecture overview](docs/architecture/overview.md) ·
[System map](docs/architecture/system-map.md) ·
[Data flow](docs/architecture/data-flow.md) · Authoritative internal map:
[`agents/skill.md`](agents/skill.md)

---

## Core capabilities (evidence-graded)

Legend: ✅ Certified · 🟢 Implemented · 🟡 Experimental · 🔵 Research · 📌 Planned

| Capability | Status | Evidence |
| :--- | :--- | :--- |
| 50D causal feature engine (`scalp_v1`, live contract) | ✅ | schema registry + parity/golden tests |
| 70D canonical contract (`scalp_v3`) | 🟢 research | `features/schema_contract.py` SSoT + hash + validator |
| ScalpNet model + artifact-first Model Factory | 🟢 | `models/`, `model_generation/` (manifests, no-DB inference) |
| Walk-forward + hard OOS gate | ✅ | BUG-183 regression suite; 70D candidate publicly rejected |
| Bit-exact replay + anti-leakage | 🟢 | replay parity suites |
| Streaming replay / forward tests (no `order_send`) | 🟢 | `research/streaming_replay.py`, `forward_test.py` |
| Counterfactual engine (NO_TRADE decisions walked) | 🔵 | CHG-0041: 2095 decisions, stratified evidence |
| Shadow runtime (zero order authority) | ✅ | `simulated=True` contracts, 60+ tests |
| Risk engine (Kelly sizing, clamps, kill switch) | ✅ | clamp + exposure suites |
| Execution (60-scenario router, 11 states) | 🟢 | golden-tested seam extractions |
| Immutable accounting ledger (`n/a` ≠ fake zeros) | 🟢 | `accounting/`, WAL audit DB |
| Experience / autopsy intelligence (13 detectors) | 🟢 | `experience/`, `intelligence/` |
| Incident response + forensic deploy gate | 🟢 | `incidents/`, `forensics/` (CRITICAL ⇒ BLOCK) |
| Control Center UI (buildless SPA, WS/SSE) | 🟢 | `Web/`, debug console (18 sections) |
| Windows installer + update/rollback (SHA-256, SBOM) | ✅ | release pipeline + verify suites |
| Provider health gate (bounded LLM/AI access) | ✅ | live smoke: 401 → instant auto-disable |
| News intelligence (opt-in, bounded gate) | 🟡 | boost ≤ 0.05 / penalty ≤ 0.10 — can never force a trade |
| Temporal liquidity features, MSLIE perception | 🔵 | research candidates, governance-gated |
| Multi-broker support | 📌 | `IMT5Port` seam exists; no second adapter yet |

Full matrix with per-row evidence links:
[Capability Matrix](docs/project/capabilities.md) — anything unlisted should
be assumed experimental, not production-ready.

---

## Research pipeline

```text
DATA → FEATURES → LABELING → TRAINING → BACKTEST → WALK-FORWARD → OOS GATE
     → ROBUSTNESS → COUNTERFACTUAL → REGISTRY → SHADOW → OPERATOR PROMOTION
```

Every stage writes provenance (dataset fingerprint, schema hash, git commit,
effective purge/embargo). `NOT_RECORDED` is written when honestly unknown —
never backfilled. Details: [Research Stack](docs/architecture/research-stack.md) ·
[Methodology](docs/research/methodology.md) ·
[Reproducibility](docs/research/reproducibility.md)

---

## Quickstart

```bash
nexus start            # PAPER mode — never LIVE silently
nexus doctor           # full diagnostics (19 categories)
nexus start --mode shadow   # live feed, zero order authority
```

| You want to… | Do this |
| :--- | :--- |
| Run it now (no install) | `NexusScalpEngine.exe` / `nexus start` → **PAPER** mode, Control Center at `http://127.0.0.1:8080` |
| Evaluate on real data safely | `nexus start --mode shadow` — zero order authority |
| Check your system | `nexus doctor` / `nexus health` |
| Open the dashboard | `nexus start` → browser at `http://127.0.0.1:8080` (`--port` to change) |
| Live trading (explicit) | `nexus start --mode live` — interactive confirmation required |
| Stop | `nexus stop` (daemon) · `Ctrl+C` (foreground) |

Detailed walkthrough: [Quickstart](docs/getting-started/quickstart.md) ·
[First-Run Safety](docs/getting-started/first-run.md)

---

## Installation

**1. End users — packaged Windows release (no Python required).**
Download `NexusScalpEngine-<version>-win-x64-setup.exe` (or portable `.zip`)
from [GitHub Releases](https://github.com/Opselon/NexusTradingForexBot/releases).
The installer bundles the full Python runtime (PyInstaller); first run opens
the setup wizard — mode defaults to **PAPER**, never silently LIVE. User data
lives in `%LOCALAPPDATA%\NexusScalpEngine` and survives upgrades/repairs.

**2. PowerShell bootstrap installer (no Python, no Git, no admin):**

```powershell
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
```

```powershell
.\install.ps1 -DryRun      # show the plan as JSON, mutate nothing
.\install.ps1 -Repair      # repair runtime, keep user data
.\install.ps1 -Commit sha  # reproducible pin (Commit > Tag > Branch)
```

**3. Developers — from source:**

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # Windows
pip install -e .[dev]
pytest tests/unit -q        # smoke-test the toolchain — no broker needed
```

Requirements: Windows 10/11 **x64** for native MT5 execution (**ARM64
explicitly unsupported** — the installer and `nexus doctor` say so); Python
3.11.x for source runs only; MT5 terminal with *Allow Algo Trading* enabled.
Linux = developer/Docker (remote-gateway) only.
Full details: [Installation](docs/getting-started/installation.md) ·
[`docs/INSTALL_WINDOWS.md`](docs/INSTALL_WINDOWS.md) ·
[`docs/RELEASE.md`](docs/RELEASE.md)

---

## First-Run Safety (Demo → Shadow → Live)

| Step | Action | Why |
| :--- | :--- | :--- |
| 1️⃣ | Log into a **DEMO account** in MT5; confirm the account number before touching the bot | LIVE and demo can look identical in MT5's login window |
| 2️⃣ | MT5: `Tools → Options → Expert Advisors → tick "Allow Algo Trading"` | Without this, orders are rejected |
| 3️⃣ | Copy `configs/live.yaml` → `configs/demo.yaml`; set `risk.max_concurrent_positions: 1`, low `risk_per_trade_pct`, `max_account_drawdown_pct` | Never touch a live config while real money can be reached |
| 4️⃣ | Run **SHADOW** for days; review dashboard + reports | Proves model/signals/gates before any execution |
| 5️⃣ | Run the test suite weekly | Catches regressions before they reach a live account |
| 6️⃣ | Only then consider **LIVE** with money you can afford to lose | Leveraged XAUUSD scalping carries extreme risk |

Engine-enforced protections: account-identity fail-safe (connect refuses a
terminal logged into a different account — BUG-142) · pre-flight doctor gate ·
PAPER default · LIVE interactive confirmation · zero order authority in
shadow/research · circuit breaker → SAFE_MODE · kill switch.

> 🚨 **This bot places REAL trades with REAL money in LIVE mode.** The
> engine's hard clamps protect the strategy — not your capital from market
> volatility.

---

## The `nexus` CLI

```text
nexus start     --mode paper|shadow|live    Start engine (default paper; live needs confirmation)
nexus stop / restart                        Control the background engine (honest dead-pid reporting)
nexus status / health                       Environment + health (READY / DEGRADED / NOT READY)
nexus doctor                                19-category diagnostics + suggested fixes
nexus config    [--validate path]           Inspect / validate configuration (secrets masked)
nexus logs      [--tail N] [--errors]       Tail / filter / export logs
nexus test      --mode quick|unit|integration
nexus update    check|latest|download|install|verify|status|history|rollback|doctor
nexus release info                          Installed release metadata
nexus repair                                Non-destructive derived-state repair
nexus export-diagnostics                    Sanitized ZIP (never contains secrets)
nexus db        hygiene status|plan|run     Database hygiene (audit-only by default)
nexus model-dataset-build / model-experiment-create / model-train / model-validate / model-replay
nexus uninstall                             Remove installation (user data preserved)
```

Exit codes (stable contract): `0` success · `1` runtime/validation ·
`2` invalid usage · `3` environment blocked · `4` release verification ·
`5` update not applicable/failed. `--json` / `--plain` for automation.
Full reference: [CLI guide](docs/guides/cli.md) · [`docs/CLI.md`](docs/CLI.md)

---

## Documentation

| Section | Contents |
| :--- | :--- |
| [Getting started](docs/getting-started/quickstart.md) | installation · quickstart · first-run safety · configuration |
| [Project](docs/project/vision.md) | vision · scope · **status** · capability matrix · roadmap · milestones |
| [Architecture](docs/architecture/overview.md) | overview · system map · runtime · data flow · model pipeline · execution · observability · database |
| [Research](docs/research/methodology.md) | methodology · datasets · backtesting · walk-forward · OOS · replay · counterfactuals · reproducibility |
| [Engineering](docs/engineering/quality.md) | quality gates · testing · CI · release process · security |
| [Guides](docs/guides/cli.md) | CLI · troubleshooting · common workflows |
| [Contributing](docs/contributing/contribution-guide.md) | contribution guide · documentation workflow · adding a language |
| [Reference](docs/reference/glossary.md) | CLI reference · glossary · terminology · FAQ |

🌍 **Documentation site (multilingual, RTL-ready):**
[opselon.github.io/NexusTradingForexBot](https://opselon.github.io/NexusTradingForexBot/) —
English · فارسی · Español · العربية · Deutsch, with translation status
audited by `scripts/docs/check_translations.py`.

Deep internal engineering memory (preserved as-is):
[`agents/skill.md`](agents/skill.md) (architecture map) ·
[`agents/bugs.md`](agents/bugs.md) (forensic bug ledger) ·
[`agents/runtime_invariants.md`](agents/runtime_invariants.md) ·
[`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) ·
[`docs/RELEASE.md`](docs/RELEASE.md)

---

## Project status

- **Version:** 9.0.6 — semver, single canonical source (`pyproject.toml`,
  stamped into every artifact). Releases v9.0.0 → v9.0.6 published via the
  hardened pipeline (SHA-256, manifest, SBOM, post-publish verification).
- **Runtime:** production-hardened (live MT5 execution with account-identity
  fail-safe; PAPER default; LIVE behind explicit confirmation).
- **Research:** honest posture — 70D series candidate-only with **negative
  OOS evidence**; live contract stays 50D; nothing auto-promoted.
- **Quality:** CI = ruff · mypy · critical suite (~780 tests; full unit
  corpus ~1900+) · CodeQL · Trivy · OSV · JS tests · docs validation.
- **Known limitations:** 70D candidate-only · Windows x64 packaged only ·
  news engine opt-in · no CLI hot-swap to paper adapter.

Honest, per-capability detail: [Project Status](docs/project/status.md) —
including what is *not* certified.

---

## Roadmap

Organized as NOW / NEXT / LATER / LONG TERM across ENGINE · RESEARCH ·
VALIDATION · RUNTIME · OBSERVABILITY · DOCS streams — every item carries an
objective, dependencies and a completion gate. Statuses: *Planned*,
*Under Evaluation*, *Research Direction*, *Completed*. Nothing here is
promised; items enter through the taskboard with a TASK-ID.

Highlights: rebuild 70D candidate evidence under corrected confidence
semantics (CHG-0042) · counterfactual-driven policy review · observability
gap-ledger burn-down (OBS-001..016) · translation coverage 100% of core
pages · operator-gated 70D champion decision.

Full roadmap: [Roadmap](docs/project/roadmap.md) · history:
[Milestones](docs/project/milestones.md)

---

## Repository structure

```text
src/nexus_scalp/   Core engine (hexagonal packages: domain, ports, adapters, features,
                   models, signals, risk, execution, application, research, shadow, …)
tests/             Unit + integration suites (critical manifest: tests/critical_suite.txt)
Web/               Control Center UI — buildless vanilla-JS SPA (no Node runtime needed)
agents/            Engineering memory: architecture map, bug ledger, contracts, taskboard
docs/              Engineering documentation (this platform's source content)
site/              GitHub Pages site (multilingual builder + translated content)
scripts/           Build/release scripts, quality gates, docs tooling (scripts/docs/)
configs/           base.yaml · live.yaml.example
installer/         PowerShell bootstrap installer (stage protocol)
pics/              Screenshots
docker/            entrypoint.sh · healthcheck.sh
scratch/           One-off diagnostic probes (not part of the application)
```

---

## 🐳 Docker

```bash
cp .env.example .env       # optional — safe defaults exist
docker compose up -d --build
```

Engine + Web UI in container-safe **PAPER** mode, canonical SQLite databases,
migration gate at boot, dashboard at `http://localhost:9090`, readiness at
`/health`. Full reference: [`docs/docker.md`](docs/docker.md).

---

## Development

- Quality gate before any push: `./beforePush.ps1` (ruff · format · mypy ·
  critical pytest suite · forensic deploy gate) — mirrors CI.
- Node.js is **build/dev/test-only** (Tailwind compile + JS test gate) —
  never part of the running engine (DEC-0002).
- Commit discipline: `<Author>: <summary>` + structured body, one coherent
  step per commit. Hot-path files are convention-locked
  ([`agents/locks.yaml`](agents/locks.yaml)).
- This repo is developed by coordinated AI agents under a strict
  [multi-agent contract](agents/multi-agent-git-contract.md) — the
  [contribution guide](docs/contributing/contribution-guide.md) distills the
  same discipline for human contributors.

We invite quantitative ML researchers, low-latency systems engineers and
institutional traders to collaborate: fork & PR (ruff/strict-mypy/pytest —
the gates enforce it), or open an issue with `[Research]` / `[Proposal]` tags.

---

## License & Disclaimer

**DISCLAIMER:** Algorithmic trading — especially leveraged XAUUSD/Gold
scalping — carries immense financial risk. This engine is provided strictly
for educational, academic research and simulation purposes. Always perform
rigorous backtesting and forward paper-trading before committing capital.
Nothing in this repository is investment advice or a promise of profit —
including the published negative results.

*Proprietary License — All Rights Reserved. Designed for Quantitative
Excellence.*

---

<div align="center">

[Documentation Site](https://opselon.github.io/NexusTradingForexBot/) ·
[Issues](https://github.com/Opselon/NexusTradingForexBot/issues) ·
[Releases](https://github.com/Opselon/NexusTradingForexBot/releases) ·
[Roadmap](docs/project/roadmap.md) ·
[Architecture](docs/architecture/overview.md) ·
[Contributing](docs/contributing/contribution-guide.md)

</div>
