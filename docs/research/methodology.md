---
title: Research Methodology
description: The end-to-end research methodology — how evidence is produced, graded, and allowed to influence the platform.
lang: en
---

# Research Methodology

## Principle

Research exists to produce **falsifiable evidence**. A candidate that cannot be
falsified (no OOS, no replay parity, no provenance) is not a candidate — it is
an opinion, and opinions do not touch the live path.

## The pipeline

Full detail in [Research Stack](../architecture/research-stack.md). Stages:

DATA → FEATURES → LABELING → TRAINING → BACKTEST → WALK-FORWARD → OOS GATE →
ROBUSTNESS → COUNTERFACTUAL → REGISTRY → SHADOW → OPERATOR PROMOTION.

Every stage writes provenance (dataset ID, schema hash, git commit, effective
purge/embargo) into run snapshots. `NOT_RECORDED` is written when truthfully
unknown — never backfilled.

## Evidence grading

Claims are graded CODE / TEST / INTEGRATION / LIVE / RELEASE VERIFIED.
Documentation and reports must carry the grade, not just the claim. A
"LIVE VERIFIED" claim means the behavior was observed on a running engine
against a real broker feed — the strongest grade there is.

## Research over moving implementations is forbidden

A repository rule born from a real failure (taskboard, 2026-08-19): analysis
is blocked when the implementation under study is mid-change. Freeze first,
measure second. Measuring a moving target produces numbers that belong to no
version of the code.

## Datasets

See [Datasets](datasets.md). Canonical tick datasets are fingerprinted and
immutable; bar datasets carry quality gates. Tick acquisition goes through the
certified adapter surface (`copy_ticks_range`, `COPY_TICKS_ALL`), deduped and
bounded, offline after acquisition.

## Where results go

- Candidate registry (content-addressed)
- Forensics evidence vault (`artifacts/forensics/`, `artifacts/validation/`)
- Taskboard + decision records
- Public honest reporting in [Project Status](../project/status.md)

## What the methodology has actually produced

- A **rejected flagship**: the 70D candidate failed OOS on real data — the
  gates bite, and the rejection is published.
- A **validated filter**: counterfactual walking of 2095 NO_TRADE decisions
  showed the confidence gate filtered trades with mean R −0.506 (a valid
  filter), while flagging a SUPPORT-margin stratum (+1.35 mean R) for policy
  review — evidence that changed what the *next* question is.
- **Purge/embargo defaults** silently disabled in the production research path
  were found (BUG-183) and fixed with regression tests, and the effective
  values are now recorded in every run.

That is the methodology working: sometimes the result is "no", and "no" is a
result.
