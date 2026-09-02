<div align="center">

# ⚡ NEXUS SCALP ENGINE

**A research-driven quantitative trading platform — connecting market data, causal
feature engineering, deep model inference, policy, risk control, replay,
execution, observability and reproducible validation into one auditable pipeline.**

**MetaTrader 5 · XAUUSD M1 (primary) · Python 3.11 · PyTorch · Hexagonal event-driven core**

[![Version](https://img.shields.io/badge/version-9.0.6-2f81f7?style=flat-square)](https://github.com/Opselon/NexusTradingForexBot/releases)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.2%2B-EE4C2C?style=flat-square&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![MetaTrader 5](https://img.shields.io/badge/MT5-Terminal-2962FF?style=flat-square)](https://www.mql5.com/)
[![CI](https://img.shields.io/badge/CI-ruff·mypy·pytest·CodeQL-2ea44f?style=flat-square)](https://github.com/Opselon/NexusTradingForexBot/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-GitHub_Pages-8250df?style=flat-square)](https://opselon.github.io/NexusTradingForexBot/)
[![Languages](https://img.shields.io/badge/languages-EN·FA·ES·AR·DE-eac54f?style=flat-square)](#documentation--languages)
[![Status](https://img.shields.io/badge/status-research_platform·honest_evidence-f0883e?style=flat-square)](docs/project/status.md)

</div>

---

> [!WARNING]
> **This engine can place real trades with real money in LIVE mode.** PAPER is
> the default — the engine never goes LIVE silently. Leveraged XAUUSD scalping
> carries extreme financial risk. This project is provided for research,
> engineering and simulation purposes. Nothing here is investment advice, and no
> profitability is claimed or implied. See
> [First-Run Safety](docs/getting-started/first-run.md).

---

## 🧭 Start here — answer in 30 seconds

| If you want to know… | Go to |
| :--- | :--- |
| **What is this?** | [Vision — why Nexus exists](docs/project/vision.md) |
| **How do I run it?** | [Quickstart](docs/getting-started/quickstart.md) · TL;DR below |
| **How does it work?** | [Architecture overview](docs/architecture/overview.md) · [data flow](docs/architecture/data-flow.md) |
| **What is real vs experimental?** | [Project status](docs/project/status.md) · [capability matrix](docs/project/capabilities.md) |
| **How is research validated?** | [Research methodology](docs/research/methodology.md) · [OOS gate](docs/research/out-of-sample.md) |
| **Where is it going?** | [Roadmap](docs/project/roadmap.md) |
| **How do I contribute?** | [Contribution guide](docs/contributing/contribution-guide.md) |

---

## Quick start (TL;DR)

| You want to… | Do this |
| :--- | :--- |
| **Run it now** (no install) | `nexus start` — defaults to **PAPER**, never LIVE |
| **Evaluate on real data, zero risk** | `nexus start --mode shadow` — live feed, **zero order authority** |
| **Check your system** | `nexus doctor` · `nexus health` |
| **Open the Control Center** | `nexus start` → `http://127.0.0.1:8080` |
| **Live trading (explicit)** | `nexus start --mode live` — interactive confirmation required |
| **Stop** | `nexus stop` (daemon) · `Ctrl+C` (foreground) |

Full path (clone → install → verify → run): **[Quickstart](docs/getting-started/quickstart.md)** ·
installation options: **[Installation](docs/getting-started/installation.md)** ·
Docker: `docker compose up -d --build` (PAPER, `:9090`).

**Get the packaged release:** [`NexusScalpEngine-<version>-win-x64-setup.exe`](https://github.com/Opselon/NexusTradingForexBot/releases)
(per-user, no admin, no Python — bundles the full runtime; first run opens the
setup wizard). One-line source bootstrap:

```powershell
iex (irm https://raw.githubusercontent.com/Opselon/NexusTradingForexBot/main/installer/install.ps1)
```

---

## What is Nexus?

Nexus Scalp Engine (NSE) is a **complete, auditable quantitative trading
pipeline**. It exists to answer one engineering question rigorously:

> *Can a trading system be built so that every claim it makes is falsifiable —
> and every failure is published rather than hidden?*

Instead of optimizing for a good-looking backtest, NSE optimizes for **evidence
before claims**: metrics without evidence render `n/a`, out-of-sample failure
means rejection, candidates never promote themselves, and the entire engineering
memory — architecture map, bug ledger, runtime invariants, decision records —
lives in the open repository.

- **Research-first, runtime-hardened.** Research/learning components hold zero
  order authority; the live path is protected by hard clamps and a pre-flight
  doctor gate.
- **Built as engineering memory.** Developed by a coordinated multi-agent
  engineering swarm (56+ agents) under a strict collaboration contract —
  every change is registered, tested and documented.
- **Honest by design.** The repository publishes its own negative results (see
  [the 70D rejection](docs/project/status.md)) — that is what makes the
  passing cases meaningful.

Full story: [Vision](docs/project/vision.md) · [Milestones](docs/project/milestones.md)

---

## System at a glance

```text
Market data (MT5 / paper / gateway)
      │
      ▼
Causal features ── 50D live contract (scalp_v1) · 70D research contract (scalp_v3)
      │                    Base 0..49 │ News 10D 50..59 │ Liquidity 10D 60..69
      ▼
Inference validator ── schema hash · scaler dim · bounds (loud rejection, never silent)
      │
      ▼
ScalpNet model ── dual-path TCN + self-attention · 4-logit head
      │
      ▼
Regime + Policy ── Regime Guardian · SMC matrix (~30 rules) · confidence gate
      │
      ▼
Risk engine ── fractional-Kelly sizing · margin ≤20% · HARD_MAX_LOTS=10 · kill switch
      │
      ▼
Execution ── OrderManager · 60-scenario router · 11 position states · circuit breaker
      │
      ▼  (research/shadow boundary — zero order authority)
Accounting ledger ── SQLite WAL · immutable history · post-trade autopsy
      │
      ▼
Observability ── structured logs · incidents · forensic health · deploy gate · Telegram
      │
      ▼
Control Center ── FastAPI + SSE/WS dashboard · live tuner · debug console
```

- Hexagonal ports-and-adapters: `IMT5Port` isolates broker IPC (Win32 / ZMQ / paper).
- Closed intelligence loop: trade → accounting → experience → autopsy → research →
  candidate → shadow → operator-gated promotion.
- Deep dives: [Architecture](docs/architecture/overview.md) ·
  [System map](docs/architecture/system-map.md) ·
  [Model pipeline](docs/architecture/model-pipeline.md) ·
  [Execution pipeline](docs/architecture/execution-pipeline.md)

---

## Core capabilities

Legend: ✅ Certified · 🟢 Implemented · 🟡 Experimental · 🔵 Research · 📌 Planned
(evidence-graded — the full matrix with per-row evidence lives in the
[capability matrix](docs/project/capabilities.md))

| Capability | Status | What it is |
| :--- | :--- | :--- |
| Causal 50D feature engine (`scalp_v1`) | ✅ | live contract — microstructure, order flow, SMC structure; NaN/Inf falls back deterministically |
| 70D research contract (`scalp_v3`) | 🟢 | canonical research SSoT with schema hashing + 10-code inference validator — **candidate-only** |
| Walk-forward + hard OOS gate | ✅ | purged + embargoed; OOS failure ⇒ REJECTED (has a public rejection on record) |
| Replay (bit-exact) + streaming replay | 🟢 | live = replay = training semantics; test-enforced no `order_send` |
| Counterfactual engine | 🔵 | walks NO_TRADE decisions on canonical ticks (2095 decisions studied) |
| Shadow runtime | ✅ | live feed, `simulated=True`, zero order authority |
| Champion/Challenger governance | 🟢 | 14-gate verify, atomic promotion transaction, rollback preview — operator-gated |
| Risk engine | ✅ | Kelly sizing, margin/exposure clamps, drawdown stop |
| Execution safeguards | 🟢 | 60-scenario router, breakeven lock, profit giveback, SAFE_MODE circuit breaker |
| Accounting ledger | 🟢 | one canonical SQLite WAL ledger; immutable rows; `n/a` over fake zeros |
| Experience/autopsy intelligence | 🟢 | behavior detectors, trade autopsy, evidence-gated anomalies |
| Incident response + forensics | 🟢 | correlation, lineage, deploy gate, health engine — diagnostic-only |
| Control Center UI | 🟢 | buildless FastAPI SPA: live chart + SMC overlays, tuner, debug hub |
| Windows installer + update/rollback | ✅ | per-user, no admin; SHA-256, manifests, SBOM; honest update states |
| News intelligence (opt-in) | 🟡 | bounded gate — can never force a trade or bypass risk |
| Multi-broker beyond MT5 | 📌 | `IMT5Port` is the seam; no second adapter exists yet |

---

## Research pipeline

```text
DATA → FEATURES → LABELING → TRAINING → BACKTEST → WALK-FORWARD → OOS GATE
     → ROBUSTNESS → COUNTERFACTUAL → REGISTRY → SHADOW → OPERATOR PROMOTION
```

Every stage writes provenance (dataset fingerprint, schema hash, git commit,
effective purge/embargo). `NOT_RECORDED` is written when honestly unknown —
never backfilled. Methodology: [Research](docs/research/methodology.md) ·
[Validation](docs/research/validation.md) · [Reproducibility](docs/research/reproducibility.md)

**The pipeline has teeth:** the 70D liquidity candidate — the most heavily
engineered research series in the repository — was **rejected** by the OOS gate
after real-data walk-forward and shadow benchmarks came back negative. The live
contract deliberately stayed 50D. Nothing was auto-promoted.

---

## Validation philosophy

- **No lookahead.** Purged + embargoed walk-forward; strictly causal features
  (liquidity confirmation bars, completed HTF buckets only) — INV-008.
- **Determinism.** Seeded training, frozen fingerprinted datasets, logical-clock
  replay; same inputs ⇒ same results.
- **Identity everything.** Dataset ID → schema hash → model manifest → git
  commit — validated by the 10-gate load gate at every attach.
- **Runtime truth.** Broker truth beats stale local state; settings intent ≠
  runtime gate; historical ledger rows are immutable.
- **Evidence capture.** Forensic health engine, deploy gate, incident correlation,
  machine-readable evidence artifacts (`artifacts/forensics/`, `artifacts/validation/`).

---

## Roadmap (NOW / NEXT / LATER)

| Horizon | Stream | Item | Status |
| :--- | :--- | :--- | :--- |
| NOW | VALIDATION | rebuild 70D candidate evidence with corrected confidence semantics (CHG-0042) | Under Evaluation |
| NOW | RESEARCH | counterfactual evidence deepening (NO_TRADE walks) | In progress |
| NOW | RUNTIME | retrain-record contract hardening (BUG-185 lineage) | Completed |
| NEXT | ML | 70D promotion decision — promote or retire honestly | Planned (operator-gated) |
| NEXT | VALIDATION | counterfactual-driven policy review (SUPPORT-margin stratum) | Planned |
| LATER | ML | temporal liquidity candidate (`scalp_v4_temporal`) | Research Direction |
| LATER | ENGINE | broker abstraction beyond MT5 | Under Evaluation |

Wording discipline: **Planned / Under Evaluation / Research Direction /
Completed** — never "coming soon", never guaranteed. Full roadmap with
objectives, dependencies and completion criteria:
[Roadmap](docs/project/roadmap.md).

---

## Project maturity

| Label | Meaning | Examples |
| :--- | :--- | :--- |
| ✅ **Certified** | formal forensic acceptance with reproducible artifacts | release verification, OOS gate behavior, provider-gate live smoke |
| 🟢 **Implemented** | exists with regression coverage | execution, accounting, governance, UI, installer |
| 🟡 **Experimental** | gated off the live path | 70D liquidity series, news engine, MSLIE |
| 🔵 **Research** | evidence generation | counterfactuals, temporal features |
| 📌 **Planned** | registered with completion criteria | multi-broker, promotion decision |

Being technically sophisticated and still experimental are compatible — this
project says both, loudly. Full honesty: [Project status](docs/project/status.md).

---

## Documentation & languages

**English (source):** [Getting started](docs/getting-started/quickstart.md) ·
[Project](docs/project/vision.md) · [Architecture](docs/architecture/overview.md) ·
[Research](docs/research/methodology.md) · [Engineering](docs/engineering/quality.md) ·
[Guides](docs/guides/cli.md) · [Reference](docs/reference/faq.md)

**Translations** (status audited by `scripts/docs/check_translations.py`; RTL
supported for FA/AR):

| 🇬🇧 English | 🇮🇷 فارسی | 🇪🇸 Español | 🇸🇦 العربية | 🇩🇪 Deutsch |
| :--- | :--- | :--- | :--- | :--- |
| source (complete) | [core pages](docs/fa/index.md) | [core pages](docs/es/index.md) | [core pages](docs/ar/index.md) | [core pages](docs/de/index.md) |

**GitHub Pages site:** <https://opselon.github.io/NexusTradingForexBot/> —
responsive, searchable, dark/light, RTL, multilingual.

**Deep technical documentation (repository-native, preserved as-is):**
[`agents/skill.md`](agents/skill.md) (authoritative architecture map) ·
[`agents/bugs.md`](agents/bugs.md) (forensic bug ledger) ·
[`agents/runtime_invariants.md`](agents/runtime_invariants.md) ·
[`docs/70D_DATA_CONTRACT.md`](docs/70D_DATA_CONTRACT.md) ·
[`docs/RELEASE.md`](docs/RELEASE.md) · [`docs/CLI.md`](docs/CLI.md)

---

## Development (source)

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Windows PowerShell (or source .venv/bin/activate)
pip install -e .[dev]
pytest tests/unit -q                # smoke-test the toolchain — no broker needed
```

Quality gate before any push: `./beforePush.ps1` (ruff → format → mypy →
critical pytest suite (~779 tests) → forensic deploy gate). CI mirrors it and
adds CodeQL, Trivy, OSV, JS tests. Details:
[Quality & testing](docs/engineering/quality.md) ·
[Contribution guide](docs/contributing/contribution-guide.md)

Requirements: Windows 10/11 **x64** for the MT5 adapter (ARM64 explicitly
unsupported; Linux = developer/Docker via remote gateway). MT5 terminal logged
into a **Demo** account first, with *"Allow Algo Trading"* enabled.

---

## Project status

- **Version:** 9.0.6 (semver; single canonical source `pyproject.toml`, stamped
  into every artifact).
- **Release:** published via the hardened pipeline — Windows x64 setup + portable
  ZIP with SHA-256 digests, manifests, SBOM, post-publish verification.
- **State:** production-hardened runtime with an honest research posture —
  70D series candidate-only (negative OOS evidence), live contract 50D,
  champion under governance review, nothing auto-promoted.
- **CI:** ruff · mypy · pytest critical suite · CodeQL · Trivy · OSV · JS tests.
- Full matrix: **[Project status](docs/project/status.md)** · bug ledger:
  [`agents/bugs.md`](agents/bugs.md)

---

## Collaboration

NSE is expanding into a global quantitative research framework and invites
quantitative ML researchers, low-latency systems engineers, and institutional
traders to collaborate. Fork & PR (ruff/mypy/pytest gates enforce the standards),
or open an issue with `[Research]` / `[Proposal]` tags. Start with the
[Contribution guide](docs/contributing/contribution-guide.md) and the
[architecture map](agents/skill.md).

---

<div align="center">

[Documentation](https://opselon.github.io/NexusTradingForexBot/) ·
[Architecture](docs/architecture/overview.md) ·
[Research](docs/research/methodology.md) ·
[Roadmap](docs/project/roadmap.md) ·
[Releases](https://github.com/Opselon/NexusTradingForexBot/releases) ·
[Issues](https://github.com/Opselon/NexusTradingForexBot/issues)

**License & disclaimer** — Proprietary, all rights reserved. Algorithmic
trading — especially leveraged XAUUSD scalping — carries immense financial risk.
Provided strictly for educational, academic research and simulation purposes.
Always perform rigorous backtesting and forward paper-trading before committing
capital.

</div>
