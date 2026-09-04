---
title: Project Status
description: Truthful status of every major capability — certified, implemented, experimental, planned. No marketing.
lang: en
---

# Project Status

> [!IMPORTANT]
> Status labels are evidence-graded. "Implemented" means the capability exists
> and is covered by tests in this repository. "Certified" is reserved for
> capabilities that additionally carry a formal forensic acceptance record.
> Nothing here claims guaranteed profitability.

## Snapshot

| Dimension | Status | Evidence |
| :--- | :--- | :--- |
| Version | **9.0.9** (semver, single source `pyproject.toml`) | release pipeline stamps every artifact |
| Release | **Published** (v9.0.0 → v9.0.9 tags; hardened pipeline: SHA-256, manifests, SBOM) | GitHub Releases |
| Runtime | Production-hardened; PAPER default; LIVE behind explicit confirmation | CLI contract + pre-flight doctor |
| Live execution | Working (MT5 Win32 IPC + ZMQ gateway) with account-identity fail-safe | BUG-142 regression suite |
| Research loop | Working end-to-end (datasets → walk-forward → OOS gate → candidate registry) | research/ suites |
| 50D live contract (`scalp_v1`) | **ACTIVE** — the live feature contract today | `features/schema.py` `ACTIVE_SCHEMA_ID` |
| 70D contract (`scalp_v3`) | Canonical research SSoT; **candidate-only** | `features/schema_contract.py`; docs/70D_* |
| 70D evidence to date | Real-data walk-forward + shadow benchmarks **negative / inconclusive** (OOS NOT_ELIGIBLE) | docs/TASK-05/TASK-09 final reports |
| Champion governance | RESTORED_CANDIDATE state, operator decision pending; nothing auto-promoted | governance/ + MODEL_GOVERNANCE v2 |
| CI | ruff · mypy · pytest (critical suite ~779 tests) · CodeQL · Trivy · OSV · JS tests | .github/workflows |
| Docs platform | This site + translation tooling (partial translations marked honestly) | scripts/docs/check_docs.py |

## Label definitions

- **CERTIFIED** — formal forensic acceptance with reproducible evidence (e.g.
  release verification gate, provider-gate live smoke).
- **IMPLEMENTED** — exists in code with regression coverage.
- **EXPERIMENTAL** — exists but explicitly gated off the live path or
  research-only (70D liquidity series, temporal candidates, MSLIE advisory).
- **PARTIAL** — working with documented limitations (news engine opt-in).
- **PLANNED** — designed/registered but not built (see [Roadmap](roadmap.md)).
- **DEPRECATED** — legacy paths kept for benchmarking only (e.g. 350D lineage
  naming; ScalpNet as legacy baseline for the model factory era).

## Known limitations (published, not hidden)

- 70D research series is candidate-only with negative OOS evidence so far; the
  live contract stays 50D until a candidate clears all gates **and** an operator
  promotes it.
- Windows x64 only for the packaged release.
- News engine is opt-in (disabled until a `news:` config block exists).
- No CLI hot-swap to the PAPER adapter — risk-free validation goes through
  SHADOW or a demo account.
- `nexus update check` truthfully reports `RELEASE_NOT_FOUND` only when no
  release exists.

The full forensic bug ledger (BUG-001…, root causes, evidence, regression
guards) is public: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
