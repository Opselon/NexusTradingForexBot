---
title: Nexus Scalp Engine — Documentation Index
description: Canonical map of every documentation category in the repository.
lang: en
---

# Nexus Scalp Engine — Documentation Index

This is the canonical documentation map. It is the single entry point for
finding where a document belongs and where an existing document lives.
The authoring rules that govern this tree are in
[documentation-governance.md](governance/documentation-governance.md).

> **One repository. One canonical documentation root for human-authored
> documentation: `docs/`.** Machine-consumed repository artifacts
> (`agents/`, root `CONTRACT.md`, tool-required files) are *not* human
> documentation and are governed by their own contracts — see
> [§ Exceptions](#exceptions) below.

## Start here

| Question | Page |
| :--- | :--- |
| What is Nexus and why does it exist? | [Vision](project/vision.md) |
| How do I run it? | [Quickstart](getting-started/quickstart.md) |
| How is the architecture structured? | [Architecture Overview](architecture/overview.md) |
| How does data become a decision? | [Data Flow](architecture/data-flow.md) |
| How is research validated? | [Research Methodology](research/methodology.md) |
| What is real vs experimental vs planned? | [Project Status](project/status.md) · [Capability Matrix](project/capabilities.md) |
| Where is the project going? | [Roadmap](project/roadmap.md) |
| How do I contribute? | [Contribution Guide](contributing/contribution-guide.md) |

## Documentation map

```text
docs/
├── README.md                  ← this index (canonical map)
├── index.md                   ← documentation hub (site source)
├── getting-started/           Installation · Quickstart · First-run · Configuration
├── project/                   Vision · Scope · Status · Capabilities · Roadmap · Milestones
├── architecture/              Overview · System map · Runtime · Data flow · Database
│   └── database/              DB catalog · migrations · hygiene matrices
├── research/                  Methodology · Backtesting · Validation · 70D program
│   └── 70d-program/           The 70D feature/research wave (contracts, baselines, reports)
├── engineering/               Quality · Testing · CI · Release process · Security
│   └── ops/                   Install · Release · Docker · Linux/WSL2 · CI operations
├── ml-system/                 ML system contracts, task ledger, ML task register
├── neural-studio/             Neural Studio architecture + API matrix
├── api/                       HTTP API platform reference
├── guides/                    CLI · API · Troubleshooting · Common workflows
├── governance/               Documentation contract · wave/lane ownership contracts
├── contributing/              Contribution guide · Docs authoring · Adding a language
├── reference/                 CLI reference · Glossary · Terminology · FAQ
├── testing/                   Test architecture · CI cost · Critical regressions
├── contracts/                 Cross-cutting trust/contract documents
├── decisions/                 ADRs
├── health/                    Health architecture maps
├── audit/                     Audit matrices + wave reports
├── forensics/                 Forensic evidence chain (read-only, labelled)
│   └── phase2-audit/          Generated Phase-2 DB integrity/lineage reports
├── forensic-docs/             Frozen forensic code-documentation pass (evidence)
├── forensic-reports/          Forensic reports
├── agent_handoffs/            Agent handoff records (append-only history)
├── historical/                Historical engineering record
│   ├── task-reports/          Task/phase completion reports (HISTORICAL)
│   └── root-contracts/        Retired root-level contract documents
└── archive/                   Deprecated / superseded material
```

Only categories that hold real documents exist. Do not create empty folders.

## Root-level files in `docs/`

A small set of documents live directly under `docs/` because **tooling reads
them at that exact path**. Each one is a machine-consumed interface, not a
filing omission:

| File | Who requires it there |
| :--- | :--- |
| `CLI.md` | CLI help-surface contract; `src/nexus_scalp/cli/*` + `tests/cli/*` |
| `RELEASE.md` | CLI exit-code contract; `src/nexus_scalp/release/*` + `tests/unit/test_wsl2_platform_registry.py` |
| `docker.md` | `Dockerfile`, `docker-compose.yml`, `docker/entrypoint.sh`, tests |
| `linux_wsl2_mt5_platform.md` | `tests/unit/test_wsl2_platform_registry.py` (reads content) |
| `INSTALLER_ARCHITECTURE.md`, `INSTALL_INTEGRITY.md`, `INSTALL_WINDOWS.md` | `installer/install.ps1` |
| `70D_DATA_CONTRACT.md` | `src/nexus_scalp/features/schema_contract.py` |
| `70D_TEMPORAL_FEATURE_CONTRACT.md` | `src/nexus_scalp/features/temporal.py` |
| `70D_SHADOW_RUNTIME.md` | `src/nexus_scalp/shadow/shadow70/liq_provider.py` |
| `70D_INCIDENT_RESPONSE_MODEL.md` | `src/nexus_scalp/incidents/models.py` |
| `DATABASE_HYGIENE.md` | `src/nexus_scalp/hygiene/__init__.py` |
| `TRAINING_SETUP.md` | `src/nexus_scalp/cli/provision_commands.py` |
| `paper_demo_parity.md` | `src/nexus_scalp/risk/paper_parity.py` |
| `LIQUIDITY_70D_OPTIMIZATION_REPORT.md` | `src/nexus_scalp/features/liquidity_engine_opt.py` |
| `OFFICIAL_MODEL_PUBLICATION.md` | `scripts/release/build_official_bundle.py` |
| `70D_LIQUIDITY_PARITY_REPORT.md` | `scripts/gen_70d_parity_report.py` |
| `LOCAL_QUALITY_GATE.md` | `scripts/ci/gate_parity.py`, `beforePush.sh` |
| `psi_small_sample_note.md` | `tests/unit/test_drift_breaker.py` |
| `CHAMPION_ARTIFACT_INCIDENT_20260819.md` | `tests/unit/test_70d_model_validation_task4.py` |
| `70D_PRODUCTION_DEPLOYMENT.md`, `70D_UPDATE_AND_MIGRATION.md`, `70D_INSTALLATION_COMPATIBILITY.md`, `70D_PRODUCTION_RELEASE_FORENSICS.md`, `70D_CURRENT_STATE_RECONCILIATION.md` | production error strings / CLI docs |
| `CI_ARCHITECTURE.md` | `scripts/ci/check_workflows.py`, `tests/ci/test_classify_changes.py` |
| `LIQUIDITY_70D_GOLDEN_BASELINE.json`, `LIQUIDITY_60D_50D_CONTRACT_SNAPSHOT.json`, `task5_champion_baseline.json` | test + forensic golden paths |

## Exceptions

The following are **not** human documentation and are explicitly outside this
tree. Their location is part of their contract. Do not move them to satisfy
documentation organization.

| Path | Classification | Why |
| :--- | :--- | :--- |
| `agents/` | Machine control plane | CI scripts, tests, agent orchestration and production error strings read/write these paths |
| `CONTRACT.md` | Pinned repository contract | A hard test pins it at repository root |
| `README.md` | Repository entry point | GitHub discoverability |
| `site/` | Generated/published documentation | Built from `docs/` + `site/content/` by `scripts/docs/build_site.py` |
| `LICENSE`, `.github/` | Repository infrastructure | Required at their locations |

New exceptions cannot be invented by an agent. Adding one requires a recorded
decision in `docs/decisions/` naming the tooling that requires the path.

## Languages

| Language | Status |
| :--- | :--- |
| 🇬🇧 [English](index.md) | Source of truth (complete) |
| 🇮🇷 [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) | Persian (partial — core pages) |
| 🇪🇸 [Español](https://opselon.github.io/NexusTradingForexBot/es/) | Spanish (partial — core pages) |
| 🇸🇦 [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) | Arabic (partial — core pages) |
| 🇩🇪 [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) | German (partial — core pages) |

Translation coverage and staleness are audited by
`scripts/docs/check_translations.py`. See
[Translation workflow](contributing/add-language.md).

## Validation

```bash
python scripts/docs/check_docs.py          # full doctor: links, anchors, translations, secrets, drift, build
python scripts/docs/check_translations.py  # coverage/staleness audit with numbers
```

CI runs the same checks on every docs-affecting change — `DOCS_HEALTH = PASS`
is the required context `Validate documentation`.
