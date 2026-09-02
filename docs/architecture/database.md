---
title: Database Architecture
description: SQLite WAL canonical ledgers, additive migrations, hygiene engine — how persistence stays truthful and rebuildable.
lang: en
---

# Database Architecture

## Canonical stores

| DB | Contents | Notes |
| :--- | :--- | :--- |
| `artifacts/audit.db` | trading ledger, experience outcomes, accounting, research registry, strategy lifecycle, incidents, hygiene | one canonical ledger; SQLite **WAL** |
| `artifacts/news.db` | news ingestion, analysis, consensus, impacts | lazy schema on first save |
| `candle_intel.db` | candle intelligence | versioned schema |
| settings DB | secrets + user intent (Telegram creds via `settings_service`) | never in YAML (INV-010) |

## Write path

`AuditRepository` queues writes to a **background worker thread** — the tick
hot path never performs synchronous DB work (INV-001). Historical
experience/outcome/ledger rows are **immutable** (INV-007); corrections go
through evidence → reconstruction → derived records with provenance, never row
rewrites.

## Migrations

`src/nexus_scalp/database/` migration engine (TASK-10): per-domain schema
versions, checksummed registry, baseline detection, WAL-safe backups, OS-lock
concurrency control, drift detection, downgrade block, startup gate, `nexus db`
CLI. Migrations are **append-only** — financial truth, provenance and research
evidence are never deleted.

## Hygiene engine

Observe → classify → plan → validate → clean → verify. Non-destructive by
default (**AUDIT_ONLY**); quarantine is MOVE-MARK-REPORT with restore/resolve
and full provenance. Consistency rules validate TRADE/LEDGER/DATASET/NEWS
(read-only); index health is advisory. CLI: `nexus db hygiene status|plan|run`
(+ `--dry-run --deep`).

## Model artifacts (filesystem, not DB)

The Model Factory stores versioned datasets/experiments/models with manifests
under `artifacts/model_generation/` and `artifacts/models/` — inference needs
no database.

## Docker persistence

Named volumes `nexus-artifacts` + `nexus-data`; the container runs the
migration gate at boot. See [`docs/docker.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/docker.md).
