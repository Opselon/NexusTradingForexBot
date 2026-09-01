---
title: Observability
description: Structured logging, incidents, forensics health, deploy gate — how the system makes its own behavior auditable.
lang: en
---

# Observability

NSE treats observability as a subsystem with contracts, not a logging afterthought.

## Structured logging (`observability/logging.py`)

- Severity-split, date-organized storage: `logs/<sev>/YYYY/MM/YYYY-MM-DD.log`.
- ISO-8601 timestamps (broker-local offset aware), event/component/category/
  error-code fields, correlation context.
- Daily + size rotation with zero-loss part files; per-severity retention.
- Key + high-entropy redaction; ANSI-free stacks.

## Incident response (`incidents/`)

Canonical incident model (`INC-YYYY-hex8`, 8 statuses, 5 severities),
fingerprint correlation, causal-chain timeline reconstruction, value lineage
with first-divergence location, WHY workflows, MT5/ledger divergence +
clock-skew forensics. **Diagnostic-only by contract**: incidents never mutate
trading/risk/models/DB. Recovery plans are RECOMMENDED and approval-gated —
no destructive options. CLI: `nexus incidents …`; Telegram alerts for
CRITICAL/HIGH (throttled, deduped).

## Forensic health (`forensics/`)

- `ForensicHealthEngine` — read-only continuous checks (INV-70D-001..020):
  feature contract/deadness/drift, causal + parity canaries, model/scaler
  contract, DB integrity, duplicate economic outcomes, impossible excursions,
  silent fallback, "200-but-wrong" classification, worker no-progress, UI/API
  consistency, chart health, queue growth.
- **Deploy gate** (`nexus forensic --deploy-gate`, wired into `beforePush`):
  verdicts PASS/ALLOW · WARNING/ALLOW_WITH_WARNING · DEGRADED/REVIEW ·
  CRITICAL/BLOCK · UNKNOWN/REVIEW (never passes).

## Debug console

`/api/debug/state` canonical snapshot (18 sections) + registry-driven 70D
feature matrix + contract validation + snapshot diff. Execution traces via
`/api/debug/trace`.

## Diagnostics export

`nexus export-diagnostics` builds a sanitized ZIP — **never contains secrets**.

## Known gaps (tracked, not hidden)

The observability audit maintains a public gap ledger
(OBS-001…016 — e.g. redaction eating correlation IDs on some paths,
silent DB batch drops). See
`docs/architecture/observability-map.md` in the repository.
