---
title: Nexus Scalp Engine — Documentation
description: Evidence-based documentation hub for the Nexus Scalp Engine quantitative trading research and execution platform.
lang: en
---

# Nexus Scalp Engine — Documentation

**Research-driven quantitative trading platform** — a hexagonal, event-driven
scalping engine for MetaTrader 5 (primary market: XAUUSD M1), built around
causal feature engineering, artifact-first model governance, deterministic
research tooling, and forensic observability.

> [!IMPORTANT]
> This documentation describes a **research and engineering platform**. It is
> not investment advice, and nothing here is a promise of profitability.
> Leveraged scalping carries extreme financial risk. See
> [Project Status](project/status.md) for what is certified vs experimental.

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
├── getting-started/   Installation · Quickstart · First-run · Configuration
├── project/           Vision · Scope · Status · Roadmap · Milestones
├── architecture/      Overview · System map · Runtime · Research stack ·
│                      Data flow · Observability · Database
├── research/          Methodology · Backtesting · Walk-forward · Validation ·
│                      Replay · Counterfactuals · Reproducibility
├── engineering/       Quality · Testing · CI · Release process · Security
├── guides/            CLI · Troubleshooting · Common workflows
├── contributing/      Contribution guide · Docs authoring · Adding a language
└── reference/         CLI reference · Glossary · Terminology · FAQ
```

## Languages

| Language | Status |
| :--- | :--- |
| 🇬🇧 [English](index.md) | Source of truth (complete) |
| 🇮🇷 [فارسی](https://opselon.github.io/NexusTradingForexBot/fa/) | Persian (partial — core pages) |
| 🇪🇸 [Español](https://opselon.github.io/NexusTradingForexBot/es/) | Spanish (partial — core pages) |
| 🇸🇦 [العربية](https://opselon.github.io/NexusTradingForexBot/ar/) | Arabic (partial — core pages) |
| 🇩🇪 [Deutsch](https://opselon.github.io/NexusTradingForexBot/de/) | German (partial — core pages) |

Translation coverage and staleness are audited by
`scripts/docs/check_translations.py` — numbers, not vibes. See
[Translation workflow](contributing/add-language.md).

## Deep technical documentation (repository-native)

The repository also carries a large body of agent-generated forensic and
contract documentation. The most important entry points:

- [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) — authoritative architecture map
- [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md) — forensic bug ledger (root causes, evidence, regression guards)
- [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md) — runtime invariants (INV-001…)
- [`docs/70D_DATA_CONTRACT.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/70D_DATA_CONTRACT.md) — 70D canonical feature contract
- [`docs/RELEASE.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/RELEASE.md) — release, installer, CLI, update & rollback guide

These are internal engineering artifacts: preserved as-is, linked, and not
rewritten by the documentation effort.
