---
title: Model Pipeline
description: Artifact-first model factory, manifests, load gate, champion/challenger governance — how a model earns (and keeps) the right to run.
lang: en
---

# Model Pipeline

## Lifecycle

```text
DATASET (versioned artifact + quality gates + fingerprint)
  → EXPERIMENT (equal budgets/seeds/splits; dataset-fairness gate)
  → CANDIDATE MODEL (+ manifest: schema hash · scaler · dataset ID · git commit)
  → VALIDATION (walk-forward · OOS gate · robustness · calibration)
  → REGISTRY (content-addressed; challenger only)
  → LOAD GATE (10 gates, enforced at every attach)
  → SHADOW PARITY (zero order authority)
  → OPERATOR PROMOTION (READY_FOR_REVIEW → APPROVED → CHAMPION; atomic transaction)
  → ROLLBACK PREVIEW / EMERGENCY FREEZE
```

## Artifact-first Model Factory (`model_generation/`)

Datasets, experiments and models are **versioned filesystem artifacts with
manifests** — inference needs no database. The manifest carries
`feature_schema_hash`, `training_dataset_id`, scaler identity and git commit.
ScalpNet remains as the legacy baseline (control group) for benchmarking.

## The 10-gate load gate

Every bundle attach (boot, hot-swap, promotion, rollback, bootstrap swap,
async retrain swap, collapse recovery) re-validates: manifest integrity, schema
hash match, scaler dimension == feature dimension, width-vs-declared contract
(BUG-141 guard), family compatibility (60D/70D matrix), and more. Failure mode
is always **loud rejection** with a diagnostic code (e.g.
`SCALER_MISMATCH`, `MODEL_INPUT_DIMENSION_MISMATCH`) — never silent fallback.

## Online learning (bounded)

The engine supports bounded online fine-tuning with atomic checkpoint
rollbacks. Retrain records route through **one canonical builder**; width is
resolved from the loaded bundle (scaler dim → model num_features → class
fallback); records are REFUSED when the feature snapshot is not VALID — never
zero-filled (BUG-185 lineage).

## Governance contracts

- `MODEL_GOVERNANCE v2` · `MODEL_LOAD_GATE` · `SHADOW_PARITY` ·
  `PROMOTION_STATE_MACHINE`
- Promotion is an **atomic, crash-recoverable transaction** with audit tables
  (`model_promotion_audit`, `model_rollback_audit`).
- Auto-promotion is forbidden; shadow never mutates execution (INV-013/014/015).

## Identity & semantics (dimension ≠ semantics)

A matching dimension is necessary but not sufficient. The full chain —
version, ordering, schema, scaler, serving bundle, champion/live identity,
output semantics — is checked (the CHG-0042 confidence-semantics repair is the
canonical example: logits matched, semantics didn't).
