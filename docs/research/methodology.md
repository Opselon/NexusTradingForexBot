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
Documentation and reports must carry the grade, not just the claim.

## Research over moving implementations is forbidden

A repository rule born from a real failure: analysis is blocked when the
implementation under study is mid-change (taskboard, 2026-08-19). Freeze first,
measure second.

## Datasets

See [Datasets](datasets.md). Canonical tick datasets are fingerprinted and
immutable; bar datasets carry quality gates.

## Where results go

- Candidate registry (content-addressed)
- Forensics evidence vault (`artifacts/forensics/`, `artifacts/validation/`)
- Taskboard + decision records
- Public honest reporting in [Project Status](../project/status.md)
