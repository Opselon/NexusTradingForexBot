# DATABASE HYGIENE — NSE (TASK-11)

> Policy + architecture for the background `DatabaseHygieneWorker`.
> **Core principle: CLEAN THE DATABASE, NOT THE HISTORY.** When the worker is
> not 100% certain a row is safe to delete, it keeps it (spec §73).

## 1. Worker pipeline

```
OBSERVE -> CLASSIFY -> PLAN -> VALIDATE -> CLEAN -> VERIFY
```

- Default first-run mode: **AUDIT_ONLY** — zero mutation, ever.
- Operator opt-in: `--mode SAFE_CLEAN --apply` (CLI) for approved safe
  classes; `AGGRESSIVE_CLEAN` requires separate explicit activation and still
  respects every hard invariant.
- LIVE execution mode: conservative — cache/temp/retention only; heavy
  archival/vacuum deferred to a maintenance window.

## 2. Data tiers (spec §3)

| Tier | Meaning | Auto-delete |
| :--- | :--- | :--- |
| TIER-0 | Immutable broker/financial truth | NEVER |
| TIER-1 | Canonical audit / experience | NEVER automatically |
| TIER-2 | Research / learning evidence | NEVER automatically (archive first) |
| TIER-3 | Strategy / model metadata | Never without migration/archive safety |
| TIER-4 | News intelligence | Never; derived/duplicates only |
| TIER-5 | Derived analytics | Bounded retention allowed |
| TIER-6 | Cache | TTL-based |
| TIER-7 | Temporary / job state | Short retention |
| TIER-8 | Legacy artifact | Only after TASK-10 migration verified |

Full per-table classification: `docs/DATABASE_HYGIENE_MATRIX.md`.

## 3. Approved cleanup classes (confidence must be 1.0)

Only these may EVER be auto-deleted:

1. `audit_signals` older than 7 d (existing BUG-054 purge contract).
2. `position_lifecycle_events` POSITION_MOVING older than 3 d.
3. `audit_guard_telemetry` older than 13 d.
4. candle_intel derived rows (candles, closures, patterns, regimes,
   risk_evaluations, trade_decisions, rule_vetoes) older than 30 d —
   REBUILDABLE (source = broker history / audit.db), rebuild deterministic.
5. candle_intel cache tables (`feature_vectors`, `trade_proposals`) older
   than 7 d — EXPIRED_CACHE.
6. candle_intel active-state mirrors (`open_positions`, `exit_signals`)
   older than 1 d — STALE_TEMP.
7. `news_health` older than 90 d.
8. stale worker-state rows (research/intelligence/news worker state older
   than 30 d with no active worker generation).
9. news_articles flagged `is_duplicate=1` whose canonical row (article_hash
   on `duplicate_of`) is PROVEN present — EXACT_DUPLICATE only.
10. NSE-owned temp files older than TTL, not locked (see §6).

Everything else: KEEP.

## 4. Hard invariants (spec §47, §15)

1. Never delete Tier-0 broker truth automatically.
2. Never delete canonical financial truth (ledger/outcomes/account snapshots).
3. Never delete migration history (TASK-10 `schema_meta` + registry).
4. Never delete model provenance.
5. Never delete research evidence by default (archive first).
6. Never delete historical losing trades.
7. Never mistake split fills for duplicates (order_id family = one economic
   trade — the detector records families as PROTECTED).
8. Never delete without a classification reason.
9. Never delete when confidence < 1.0.
10. Never clean while the DB is busy (bounded timeout → DEFER).
11. Never exceed per-cycle budget (rows/runtime/lock-time hard caps).
12. Never run cleanup in the tick hot path (`asyncio.to_thread` only).
13. Never report success before verification (`PRAGMA integrity_check` +
    `foreign_key_check` + financial aggregates after every batch).
14. Never delete a row whose replacement is not proven (canonical exists).
15. Never remove the final copy of authoritative evidence (archive first).

## 5. Duplicate policy

- Deterministic canonical identities ONLY: idempotency_key, position_id,
  trade_id, article_hash (+ verified duplicate_of), (article_id, run_id).
- Never same-PnL/same-price/same-timestamp heuristics.
- Identity confidence ∈ {EXACT_DUPLICATE, LIKELY_DUPLICATE, NOT_DUPLICATE,
  UNKNOWN}; only EXACT_DUPLICATE with a live canonical row may be deleted.
- Split-fill siblings (same order_id) = one economic trade = PROTECTED.

## 6. Orphan policy

- Detected: outcome↛experience, autopsy↛ledger, broker-trade↛ledger,
  analysis↛article. Classified EXPECTED_ORPHAN / RECOVERABLE / REBUILDABLE /
  CORRUPTION / UNKNOWN.
- Orphans are REPORTED, never auto-deleted. Broker-trade orphans are the
  migration-era ledger gap (pre-BUG-045) — historical evidence, archived.

## 7. Retention engine

`src/nexus_scalp/hygiene/retention.py` — per-table rules:

- minimum_retention / maximum_retention / archive_after / delete_after /
  never_delete; rows unknown to the registry default to KEEP.
- Existing BUG-054 purge (audit.signals 7d, moving 3d, guard 13d) is the
  evidence for those windows; candle-store rows 30d are derived mirrors of
  broker history; nothing else is invented without evidence.

## 8. Archive policy

- Location: `artifacts/archive/<database>/<table>/<archive_id>.jsonl`;
  journal at `artifacts/archive/_journal/hygiene_<run_id>.jsonl`.
- Every archive carries a manifest (archive_id, database, table,
  source_schema, row_count, time_range, created_at, sha256,
  retention_reason, software_version) and is VERIFIED by re-hash.
- Archived rows are never auto-loaded by runtime discovery.

## 9. Cleanup budgets (per cycle)

- max_rows_scanned 200k, max_rows_deleted 2k, max_rows_archived 5k,
  max_runtime 30s, max lock 2s, batch 200. Exceed → STOP/DEFER, continue
  next cycle. Budget is GLOBAL across tables.

## 10. Worker schedule

- startup: light health check (recover_interrupted).
- ~6h: classification scan (plan + AUDIT_ONLY/SAFE_CLEAN per operator mode).
- ~24h+: deep cycle (archive/verify).
- VACUUM: only in a maintenance window, never while LIVE, after
  checkpoint + WAL verify (never delete .db-wal/.db-shm manually).

## 11. CLI

```
nexus db hygiene status [--json]
nexus db hygiene plan [--database X] [--json]        # ZERO mutation
nexus db hygiene run [--mode AUDIT_ONLY|DRY_RUN|SAFE_CLEAN|AGGRESSIVE_CLEAN]
                    [--database X] [--apply] [--json]
nexus db hygiene pause|resume [--json]
nexus db hygiene history [--limit N] [--json]
```

Destructive action requires BOTH `--mode SAFE_CLEAN` (or AGGRESSIVE_CLEAN)
AND `--apply`; `--yes` can never bypass financial protection / integrity /
backup requirements.

## 12. Crash recovery

- Each run is persisted as IN_PROGRESS; on startup `recover_interrupted()`
  marks them INTERRUPTED. A destructive batch is NEVER resumed blindly from
  an unknown state (spec §66).

## 13. Developer instructions

- Schema changes (DROP TABLE/COLUMN, index creation/removal) go through
  TASK-10 migrations (`nexus_scalp/database/registry.py`) — the hygiene
  worker never performs destructive schema operations.
- New retention rules: edit `retention.py` + update the matrix doc; add a
  TEST-HYG-xx regression; evidence (real growth data) required, never invent
  durations.
- New cleanup classes: must satisfy confidence-1.0 + canonical-replacement +
  journal + verification; add to SAFE_CLEAN_CLASSES only after review.
- Hot path: the worker is synchronous; live_engine calls it via
  `asyncio.to_thread` at a 6h throttle (AUDIT_ONLY first run; SAFE_CLEAN
  only when operator-configured and not LIVE).

## 14. DB-specific rules

- audit.db: financial evidence tables NEVER touched; retention tables are
  signals/moving/guard/worker-state only.
- news.db: articles dedup by verified `duplicate_of` hash; health 90d;
  worker state 30d; all evidence tables (analysis/runs/entities/impacts/
  topics/sources/consensus/links) preserved.
- candle_intel.db: all row tables are derived mirrors of broker history /
  audit.db — rebuildable; bounded 30d retention for row stores, 7d cache,
  1d active-state; audit_log preserved.