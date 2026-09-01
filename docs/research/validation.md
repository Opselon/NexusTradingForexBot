---
title: Validation
description: The validation philosophy — what "validated" means, the 12 gates, and why rejections are published.
lang: en
---

# Validation

## Philosophy

Validation is the reason the platform exists. Every layer has its own gates,
and **a failure at any gate is terminal for that candidate** — no averaging,
no "close enough".

## The gate chain

| Layer | Gates |
| :--- | :--- |
| Dataset | quality gates, fingerprinting, provenance |
| Features | schema hash, dimension, bounds, ordering |
| Model | 10-gate load gate, calibration, min-evidence |
| Research | purge/embargo walk-forward, OOS floors, robustness stress |
| Governance | 14-gate candidate verification, promotion preview + transaction |
| Runtime | deploy gate (`nexus forensic --deploy-gate`) before any release |
| Release | SHA-256 digests, manifests, SBOM, post-publish verification |

## The 12 validation gates (candidate lifecycle)

The candidate validation path runs 12 gates over a candidate (manifest
integrity, schema family, scaler identity, dataset provenance, calibration,
OOS, robustness…). Governance verification adds a 14-gate matrix on top for
promotion readiness. A candidate that fails any gate is stored as CHALLENGER
at best — and only an operator can ever promote.

## Failure handling

Rejections are recorded with evidence and stay public: the 70D OOS rejection
and the benchmark history are in [Project Status](../project/status.md). A
failed validation is a **result**, not an embarrassment — it is what makes the
passing cases meaningful.
