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

Why artifact-first? Because a model without provenance is an opinion. When a
bundle carries its dataset ID and schema hash, the question "which data
produced this?" has a *byte-precise* answer, and the load gate can refuse
anything that cannot answer it.

## The 10-gate load gate

Every bundle attach (boot, hot-swap, promotion, rollback, bootstrap swap,
async retrain swap, collapse recovery) re-validates: manifest integrity, schema
hash match, scaler dimension == feature dimension, width-vs-declared contract
(BUG-141 guard), family compatibility (60D/70D matrix), and more. Failure mode
is always **loud rejection** with a diagnostic code (e.g.
`SCALER_MISMATCH`, `MODEL_INPUT_DIMENSION_MISMATCH`) — never silent fallback.

The historical record shows the gate working: a 60D model attaching to a 70D
runtime was blocked with `MODEL_INPUT_DIMENSION_MISMATCH` and the UI state was
correct — the gate refusing is the feature.

## Identity & semantics (dimension ≠ semantics)

A matching dimension is necessary but not sufficient. The full chain —
version, ordering, schema, scaler, serving bundle, champion/live identity,
output semantics — is checked. The CHG-0042 confidence-semantics repair is the
canonical example: the logits matched, the *meaning* didn't (raw 4-logit
probability was being read as directional confidence). The policy gate now
measures trained-class directional share.

## Online learning (bounded)

The engine supports bounded online fine-tuning with atomic checkpoint
rollbacks. Retrain records route through **one canonical builder**
(`_build_retrain_record()`): width is resolved from the loaded bundle
(scaler dim → model num_features → class fallback), the base block uses the
live 50D snapshot, news uses the canonical projection, liquidity requires a
VALID governor snapshot — and the record is **REFUSED (None)** when anything
is not VALID, never zero-filled (BUG-185 lineage; the silent death of the
learning loop is now impossible by construction).

## Champion/Challenger governance

- Candidates are stored `CHALLENGER` (shadow-eligible) only.
- Promotion is `READY_FOR_REVIEW → APPROVED → CHAMPION` — an **atomic,
  crash-recoverable transaction** with dedicated audit tables
  (`model_promotion_audit`, `model_rollback_audit`), a promotion preview API,
  and rollback preview.
- Emergency freeze/disable is operator-controlled.
- Auto-promotion is forbidden; shadow never mutates execution (INV-013/014/015).

## Governance contracts

`MODEL_GOVERNANCE v2` · `MODEL_LOAD_GATE` · `SHADOW_PARITY` ·
`PROMOTION_STATE_MACHINE` — indexed in
[`agents/contracts.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/contracts.md).
