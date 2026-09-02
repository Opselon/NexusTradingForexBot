---
title: Security
description: Security posture — secret handling, scanning, redaction, and the trust boundaries.
lang: en
---

# Security

## Secrets

- Secrets live in the **settings DB** / secret store — never in `live.yaml`,
  never committed (INV-010). Provider URLs/keys are redacted in health
  endpoints and logs (`[REDACTED_SECRET]`).
- `scripts/ci/scan_secrets.py` fails CI on secret-shaped strings in
  source/config/docs.
- Diagnostics export (`nexus export-diagnostics`) is sanitized by contract —
  never contains secrets.

## Scanning (CI)

CodeQL (SAST), Trivy (containers/FS), OSV dependency scanning, lockfile-diff,
secret scan — see [CI architecture](ci.md).

## Trust boundaries

| Boundary | Rule |
| :--- | :--- |
| broker ↔ engine | `IMT5Port` adapters only; account-identity fail-safe at connect |
| research/learning ↔ execution | zero order authority (INV-002); shadow never mutates execution |
| observability ↔ trading | incidents/forensics are diagnostic-only |
| Telegram | read-only outbound (INV-010) |
| provider gate | LLM/AI services bounded: rate limits, circuit breaker, auto-disable; can never touch trading |

## Trading-specific safety

The full risk surface (clamps, circuit breaker, kill switch, LIVE
confirmation, account fail-safe) is documented in
[First-Run Safety](../getting-started/first-run.md) — safety is a runtime
property, not a document.

## Reporting

This repository is proprietary (see README); security contact goes through the
repository owner.
