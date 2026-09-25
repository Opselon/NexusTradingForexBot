---
title: Roadmap
description: The engineering roadmap — NOW / NEXT / LATER / LONG TERM, with objectives, dependencies and completion criteria. Statuses: Planned, Under Evaluation, Research Direction, Completed.
lang: en
---

# Roadmap

> [!NOTE]
> Wording discipline: items are **Planned**, **Under Evaluation**,
> **Research Direction** or **Completed** — never "coming soon" and never
> guaranteed. Every item lists its completion gate.

## NOW (active workstreams)

| Stream | Item | Status | Objective | Completion criteria |
| :--- | :--- | :--- | :--- | :--- |
| VALIDATION | Rebuild 70D candidate evidence | Under Evaluation | fair A/B/C benchmark on real data with the corrected confidence semantics (CHG-0042) | validated candidate with reproducible walk-forward + OOS artifacts, or a documented stop |
| RESEARCH | Counterfactual evidence deepening | In progress | NO_TRADE decision walk on canonical tick datasets (CHG-0041, 2095 decisions) | stratified evidence merged into policy review |
| RUNTIME | Record-builder contract hardening | Completed (2026-09) | single canonical retrain-record builder; width-from-bundle resolution (BUG-185) | launch-gate test wired into all 7 bundle-mutation sites |
| ARCHITECTURE | Large-file decomposition program (CHG-0032-A1) | In progress | responsibility-based extraction with golden tests (cli/, web/, forensics/) | per-domain regression gates green; facades byte-identical surface |

## NEXT (next quarter horizon)

| Stream | Item | Status | Why | Dependencies |
| :--- | :--- | :--- | :--- | :--- |
| ML | 70D champion promotion decision (operator-gated) | Planned | the research contract must either earn promotion or be retired honestly | validated candidate + governance 14-gate verify |
| VALIDATION | Counterfactual-driven policy review | Planned | SUPPORT-margin stratum flagged (+1.35 meanR) deserves a policy-owner decision | counterfactual engine outputs |
| ENGINE | Recovery/exposure policy consolidation | Planned | recovery budget + exposure caps currently live across extracted seams | OM decomposition completion |
| DOCS | Translation coverage 100% of core pages | Planned | multilingual platform maturation | translation workflow (this site) |
| OBSERVABILITY | OBS-001..016 gap ledger burn-down | Planned | redaction/correlation fixes are queued | observability audit evidence |

## LATER (research directions)

| Stream | Item | Status | Notes |
| :--- | :--- | :--- | :--- |
| ML | Temporal liquidity features promotion candidate | Research Direction | `scalp_v4_temporal_candidate` (22D) must pass the full gate chain; never auto-promoted |
| ML | MSLIE perception integration into policy | Research Direction | currently advisory-only; would need its own validation campaign |
| RESEARCH | Regime-conditional model selection | Research Direction | regime classifier exists; per-regime champion policy is unproven |
| ENGINE | Multi-asset expansion beyond XAUUSD majors | Under Evaluation | feature/policy assumptions are XAUUSD-tuned today |

## LONG TERM (direction, not commitment)

| Stream | Item | Notes |
| :--- | :--- | :--- |
| EXECUTION | Broker abstraction beyond MT5 | `IMT5Port` is the seam; no second adapter exists yet |
| INFRASTRUCTURE | Optional PostgreSQL profile | settings/config plumbing exists (DB portability); production profile undecided |
| OPEN SOURCE | Selective open-core extraction | stated collaboration direction; scope undecided |

## Governance

New roadmap items enter through the taskboard
([`agents/taskboard.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/taskboard.md))
with a TASK-ID, an owner and completion criteria. Items without a completion
gate do not ship — that rule has produced the OOS rejections you can read about
in [Project Status](status.md).
