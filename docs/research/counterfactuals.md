---
title: Counterfactuals
description: The NO_TRADE counterfactual engine — walking the decisions the engine didn't take, with stratified evidence.
lang: en
---

# Counterfactuals (CHG-0041)

## The question

Backtests score trades that happened. The counterfactual engine asks the
other question: **what would have happened on the decisions the engine did NOT
take?** If the engine abstains (NO_TRADE), was the abstention valuable?

## How it works

`research/counterfactual.py`:

1. Loads canonical tick datasets (fingerprinted, offline) and decision
   snapshots from `audit_signals`.
2. Joins each NO_TRADE decision to the tick window at decision time.
3. Applies **certified fill semantics** hypothetically at time T.
4. Walks ticks forward to compute MFE / MAE / R / cost / time-to-target.
5. Classifies each abstention using **documented rules** (never ad-hoc).
6. Stratifies evidence by gate, regime, confidence, session, direction.

## First evidence run (2026-09-01)

2095 NO_TRADE decisions (2026-08-24 → 09-01) walked over 3.2M ticks / 9-day
datasets; 476 covered:

| Stratum | N | Fill rate | mean R | Reading |
| :--- | :--- | :--- | :--- | :--- |
| CONFIDENCE_GATE | 393 | 45.0% | −0.506 | the gate filtered trades that would have lost on average — **valid filter** |
| SUPPORT-margin | (subset) | 60% | **+1.35** | flagged for policy-owner review — possible missed value |
| GUARDIAN rows | — | — | — | unresolvable (model abstained) — never fabricated |

## Boundaries

Counterfactual output is **evidence for policy review**, not a policy change.
The SUPPORT-margin finding belongs to the policy owner; the engine's behavior
changes only through the normal validated path.
