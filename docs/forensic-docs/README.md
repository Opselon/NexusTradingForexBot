# Forensic Code Documentation — Nexus Scalp Engine (NSE)

**Author:** Hermes-DocEngineer (Forensic Code Documentation Engineer)
**Date:** 2026-08-20 · **Status:** COMPLETE (all repository source documented)

## What this is

The complete forensic documentation pass over the NexusTradingForexBot
repository — every source file read, analyzed, and documented with
purpose / architecture layer / responsibility / dependencies / how-it-
connects / key concepts (class & function intent) / hot-path notes /
edge cases & pitfalls.

**Compliance:** the in-repo `agents/skill.md` READ-ONLY directive
(line 7) is honored: this pass modified ZERO codebase files. All
documentation is delivered as this artifact tree. See
[00_COMPLIANCE.md](00_COMPLIANCE.md).

## Index

| Doc | Contents |
| :--- | :--- |
| [00_COMPLIANCE.md](00_COMPLIANCE.md) | Directive-conflict resolution + decision record |
| [01_ARCHITECTURE_MAP.md](01_ARCHITECTURE_MAP.md) | Full system mental model, layer map, design principles |
| [02_SYSTEM_FLOW.md](02_SYSTEM_FLOW.md) | Tick lifecycle, signal→risk→execution pipeline, ML lifecycle, data lifecycle |
| [03_BATCH_REPORTS.md](03_BATCH_REPORTS.md) | Per-batch completion reports |
| [04_ISSUES_LEDGER.md](04_ISSUES_LEDGER.md) | Architectural risks, doc/code mismatches, debt (with file:line) |
| [05_VERIFICATION_REPORT.md](05_VERIFICATION_REPORT.md) | No-change proof, coverage claims |

## Module pages (`modules/`)

Per-file forensic pages mirroring the source tree:

- **Core:** domain, ports, features (13), models, labeling, training,
  signals (8), risk, execution (order_manager), application (live_engine),
  configuration, market_data
- **Infra:** adapters (9: mt5/paper/database), web (server 168 routes,
  debug_snapshot, errors), cli, observability, settings
- **ML/research:** model_generation (22), model_lifecycle (12),
  research (20), strategies (+factory)
- **Intelligence:** experience (10), intelligence (9), accounting (8),
  reporting (5), news (+analysis/ingest/memory/sources), candle_intelligence
- **Ops/governance:** governance, shadow (+shadow70), forensics, incidents,
  hygiene, release (17), database
- **Tests:** tests/ (141 files — see 03_BATCH_REPORTS.md)
- **Root/tooling:** main.py, train_model.py, scripts/, Web assets,
  configs

## Coverage (AST-verified)

- 438 .py files (288 src / 141 tests / 7 scripts / 2 root) + 6 Web assets
  + 2 config YAMLs = **446 artifacts documented**
- ~182,000 source lines + ~56,700 test lines analyzed
- Zero functional changes (see 05_VERIFICATION_REPORT.md)