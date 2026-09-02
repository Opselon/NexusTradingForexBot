---
title: Observability
description: Structured logging, incidents, forensics health, deploy gate — how the system makes its own behavior auditable.
lang: en
---

# Observability

NSE treats observability as a subsystem with contracts, not a logging afterthought.
If the engine cannot show *why* it did something, its behavior is indistinguishable
from a bug — so showing why is engineered, not hoped for.

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
  CRITICAL/BLOCK · UNKNOWN/REVIEW (never passes). A release cannot ship green
  while the gate says BLOCK — the gate, not a human's optimism, decides.

## Debug console

`/api/debug/state` canonical snapshot (18 sections) + registry-driven 70D
feature matrix + contract validation + snapshot diff. Execution traces via
`/api/debug/trace`. The UI Debug Hub renders the same data the API serves —
no separate truth.

## What observability caught (evidence, not theory)

- BUG-105: dead shadow hook code found by flow forensics.
- The 154× crash-loop of an old process (mat1 Nx50 vs 70x128) surfaced through
  log forensics and became the BUG-185 record-contract fix.
- "200-but-wrong" news classification (`HTTP_SUCCESS_EMPTY`) — a server that
  answers 200 with garbage is *worse* than a 500; the health engine now
  classifies it.

## Diagnostics export

`nexus export-diagnostics` builds a sanitized ZIP — **never contains secrets**.

## Known gaps (tracked, not hidden)

The observability audit maintains a public gap ledger
(OBS-001…016 — e.g. redaction eating correlation IDs on some paths,
silent DB batch drops). See
`docs/architecture/observability-map.md` in the repository.
