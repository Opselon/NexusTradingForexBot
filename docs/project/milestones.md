---
title: Milestones & Project Story
description: How the project evolved — phases, turning points, and what each era contributed to the current architecture.
lang: en
---

# Milestones — the project story

This is a factual history reconstructed from the repository's own records
(taskboard, decision records, forensic reports). No invented narrative.

## Phase I — Engine core (single-agent era)

The original engine: hexagonal architecture, MT5 adapters, 50D causal features,
ScalpNet, policy matrix, risk engine, order manager, SQLite WAL ledger,
FastAPI Control Center. Foundation invariants established: no sync DB on the
tick path, no order authority for learning components.

## Phase II — Multi-agent forensic era

Development moved to a **multi-agent model** (up to 56+ parallel AI agents) with
a strict engineering contract: taskboard, locks, bug ledger, runtime invariants,
change control. The bug ledger (BUG-001…) and forensic acceptance reports date
from this era. Key products: database hygiene, migrations, incident response,
observability architecture, release/update engine.

## Phase III — The 60D/70D research series

The defining scientific arc. The base 50D contract was extended with news
context (10D) and liquidity intelligence (10D) into the **70D canonical
contract** (`scalp_v3`). A complete parity/validation stack was built:
schema hashing, inference validator with 10 rejection codes, replay
anti-leakage, golden corpora, fair A/B/C benchmark protocol.

**The honest result:** the 70D candidate's real-data walk-forward and shadow
benchmarks came back **negative/inconclusive** (OOS NOT_ELIGIBLE). The live
contract deliberately stayed 50D. This rejection is preserved as a first-class
result — it is the proof the validation gates actually gate.

## Phase IV — Model Factory & runtime hardening

Artifact-first model governance: versioned datasets/experiments/models with
manifests (inference needs no database), 10-gate model load gate, promotion
transactions, rollback previews. Live-engine hardening: record-contract
alignment (BUG-185), account-identity fail-safe (BUG-142), confidence
semantics repair (CHG-0042).

## Phase V — Research execution stack

Streaming replay, forward tests with frozen captures, canonical tick datasets
with provenance, and the NO_TRADE counterfactual engine (CHG-0041): walking
decisions the engine *didn't* take, with stratified evidence
(CONFIDENCE_GATE covered N=393 FR 45.0% meanR −0.506 — a valid filter;
SUPPORT-margin FR 60% meanR +1.35 — flagged for policy review).

## Where this leaves us

The platform's differentiator is not a winning strategy claim — it is that
**every claim is falsifiable and the falsification results are published**.
Current direction: see the [Roadmap](roadmap.md).

## Milestone index (selected)

| Milestone | Evidence |
| :--- | :--- |
| Multi-agent contract v2 | `agents/multi-agent-git-contract.md` |
| 70D parity acceptance | `docs/TASK-03-70D-PARITY-FINAL-REPORT.md` |
| 70D candidate validation (negative OOS) | `docs/TASK-05-70D-SHADOW-FINAL.md`, `docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md` |
| Release pipeline v9.0.x | `docs/RELEASE.md`, GitHub Releases |
| Research execution stack | `docs/` CHG-0035 artifacts, `agents/change_control.md` |
| Counterfactual engine | `agents/taskboard.md` TASK-NO-TRADE-CF-ENGINE |
