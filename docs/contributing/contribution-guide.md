---
title: Contribution Guide
description: How to contribute — bootstrap, ownership model, quality gates, commit contract, PR expectations.
lang: en
---

# Contribution Guide

## 0. Read the engineering memory first

This repository is developed by multiple coordinated AI agents under a strict
contract. A human contributor benefits from the same discipline:

1. [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) — authoritative architecture map
2. [`agents/multi-agent-git-contract.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/multi-agent-git-contract.md) — the collaboration contract
3. [`agents/contracts.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/contracts.md) + [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md) — contracts & invariants
4. [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md) — don't re-discover known bugs

## 1. Where things live

- Engine code: `src/nexus_scalp/` (layer map in [Architecture](../architecture/overview.md))
- Control Center UI: `Web/` (buildless vanilla JS; Node is build/test-only)
- Tests: `tests/` (unit / integration / golden / js / installer)
- Docs: `docs/` + this site (`site/`)
- Engineering memory: `agents/`

## 2. Bootstrap

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q          # must be green before you start
```

## 3. Change discipline

- **Claim before you code**: add a TASK row to `agents/taskboard.md`.
- **Reuse > extend > refactor > create** — check for an existing seam first.
- **Golden rule**: no semantic change without documentation; no contract
  change without a contract update; no bug fix without a reproducer when
  practical.
- **Locked paths** (`agents/locks.yaml`): hot-path files
  (`live_engine.py`, `order_manager.py`, `policy.py`) require explicit
  justification and golden tests.

## 4. Commits & PRs

- Commit format: `<AGENT-or-Name>: <summary>` with a structured body
  (what/why/evidence/verification).
- Commit per coherent step — never one giant commit.
- Every PR: quality gate green locally (`beforePush.ps1 -SkipPush`), tests
  travel with the fix, CI checks respected, documentation updated when
  behavior or contracts change.

## 5. Quality gates

`beforePush.sh` / `.ps1`: ruff → format → mypy → critical pytest suite →
forensic deploy gate. See [Quality & Testing](../engineering/quality.md).

## 6. Fork-and-PR flow

Fork & PR (PEP 8 via ruff, strict mypy, pytest coverage — the gates enforce
it), or open an issue with `[Research]` / `[Proposal]` tags. External PRs
should not touch `agents/` registries (agent-owned) except to add a taskboard
row.
