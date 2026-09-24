# RTF-001 — PostgreSQL audit domain: migration says clean, schema is not

Lane: `agent/nse-always-on-runtime-fix` (worktree `C:\Users\Capsizer\source\repos\nse-runtime-fix`)
Base: `origin/main` @ `cbabadae`

## ROOT CAUSE (verified on the live database, 2026-09-24)

`[DB-MIGRATE] schema applied=37 errors=0` is **true and misleading at the same time**.

The provider-agnostic migration (`nexus_scalp/database/migration/__init__.py` →
`migrate_domain`) built its statement list from **one source only**:
`sqlite_ddl_statements()` — a regex scan of `conn.execute("""CREATE ...""")`
literals in `adapters/database/audit_repository.py`.

The audit domain's schema is split across two places:

1. the scanned `CREATE TABLE` literals (35 statements + 2 inline indexes),
2. **additive column heals** applied by `_add_column_if_missing(conn, table, col, ddl)`
   — 24 columns added by PRAGMA-gated `ALTER TABLE ... ADD COLUMN` inside the
   same `_create_*_tables` methods, i.e. **function calls on the connection,
   not triple-quoted literals.**

The scan only sees (1). So the migration genuinely applied 37 statements with
zero errors, and the 24 additive columns were **never translated to
PostgreSQL**. A PostgreSQL domain is provisioned with the baseline skeleton
alone — every consumer INSERT referencing `signal_dedup_key`, `account_source`,
`preferred_direction`, `raw_prob_*` etc. fails.

This is the SQLite/PG asymmetry: SQLite heals itself on every connect
(`_create_sqlite_tables` runs unconditionally and PRAGMA-gates each `ADD
COLUMN`); PostgreSQL only gets healed through the migration, which never
carried the additive contract. BUG-276/PERF-DEADLETTER fixed the *unique-index*
half of this for SQLite; the additive-column half stayed broken on PostgreSQL.

## REAL-DATABASE EVIDENCE (read-only probe; credentials resolved via
`resolve_audit_db_url()`, never printed)

```
CONNECTED db=nexusdb server=17.10
PG_TABLE_COUNT 35      REQ_TABLES 35     MISSING_TABLES []
REQUIRED_COLUMNS 463   PRESENT 437       MISSING 26
```

The 26 resolves to **24 real gaps** + 2 case-folding artifacts
(`audit_ledger.MAE_usd`/`MFE_usd`: the registry declares them UPPERCASE,
SQLite is case-insensitive so the heal works and PG folds the unquoted
identifier to `mae_usd` — verified: `SELECT MAE_usd FROM audit_ledger` → column `mae_usd`. Not a defect).

Live PG columns missing vs `APP_REQUIRED_COLUMNS`, all 24 healed by this fix:

- `audit_signals`: preferred_direction, raw_prob_buy, raw_prob_sell,
  raw_prob_no_trade, raw_prob_wait, confidence_source, spread_usd,
  **signal_dedup_key**, account_source
- `audit_account_snapshots`: **account_source**
- `audit_ledger`: entry_setup_snapshot, exit_reason_source, exit_evidence,
  exit_reason_confidence, reversal_events_json
- `research_runs`: status, run_outcome, snapshot_id, gates, completed_at
- `research_run_snapshots`: feature_schema_id, feature_dimension, model_id,
  git_commit

Bold = the two errors flooding the pasted runtime log.

Note `learning_cycles` and `runtime_risk_state` from the log are NOT missing
on the live DB today — all 35 required tables exist. Those log lines came
from the **stale checkout** (`nse-review-main` @ `c9851b51`, ~8 commits
behind, superseded by PR #417). The schema-drift class is the same, but the
specific table gaps were already fixed on `main`; only the additive-column
half survived.

## THE CASCADE (why one root cause generated most of the log storm)

```
additive columns never migrated to PG
   → every audit INSERT fails on the missing column
      → recovery "salvages" 0, dead-letters N
         → dead-letter store is the non-SQLite (PG) audit domain
            with NO write sink of its own
            → "DEAD-LETTER WRITE IMPOSSIBLE ... financial record unrecoverable"
               → record silently lost, ~1 critical/sec forever
```

Fixing the root cause kills the whole cascade: once the columns exist, the
INSERTs succeed, no dead-letter is attempted, no record is lost.

## FIX

`src/nexus_scalp/database/migration/__init__.py`:

1. **`additive_columns_statements()` (new)** — the additive column-heal
   contract as provider-agnostic SQL, derived from `APP_REQUIRED_COLUMNS`
   against the actual scanned baseline (it heals **only** the columns the
   baseline CREATEs do not declare — 24, not all 463). Authored in the
   SQLite dialect because that is the direction `translate_ddl` translates
   FROM, then `apply_schema` translates it to PG like every other statement.
2. **`migrate_domain`** — statement list is now dependency-ordered:
   baseline `CREATE TABLE` → additive `ADD COLUMN` → all `CREATE INDEX`
   (baseline + additive UNIQUE targets). Source order is unsafe: a baseline
   index can precede the column its predicate needs.
3. **`verify_domain_schema`** — now optionally takes `list_columns` and
   reports `missing_columns` / `missing_columns_count`, so the verifier
   catches a table present without its required columns — the exact drift
   the runtime hit on every INSERT while startup said READY.

`src/nexus_scalp/database/migration/pg_schema.py`:
`translate_ddl` handles the `ALTER TABLE ... ADD COLUMN` shape
(`ADD COLUMN IF NOT EXISTS` on PG; SQLite needs the plain form).

## WHY THIS IS NOT A NEW SOURCE OF TRUTH

`additive_columns_statements()` is **derived**, not hand-maintained: it
diffs `APP_REQUIRED_COLUMNS` against `sqlite_ddl_statements()`. If a future
commit authors an additive column directly in the scanned CREATEs, the
diff drops it automatically — no second list to keep in sync.

## TESTS

`tests/unit/test_rtf001_provider_agnostic_schema_contract.py` (new, 12 tests):
the scan sees only CREATEs; the additive set equals exactly the
baseline-missing columns; ordering is CREATE → ADD COLUMN → CREATE INDEX;
the 24 emitted columns match live PG's 24 gaps; end-to-end heal on SQLite
makes every required column present; the verifier detects missing columns
before the heal and is clean after; the migration record's `applied_count`
includes the additive statements (so the audit trail can no longer report
`applied=37` while 24 columns are absent).

`tests/unit/test_rtf001_real_postgresql_schema.py` (new, 2 tests, real PG):
builds the schema on a throwaway `nse_rtf001_test` database (created and
dropped per run; the live `nexusdb` is only CREATE/DROP'd, never written)
through the same translate_ddl path the engine uses at boot. Proves on real
PostgreSQL that the baseline alone leaves `audit_signals.signal_dedup_key`
and `audit_account_snapshots.account_source` absent, and that the full
migration lands all 463 required columns. This is the coverage that let
RTF-001 ship: the SQLite and PostgreSQL paths diverge exactly at
translate_ddl + execute, so a unit test on a fake executor cannot catch it.

## LIVE-DATABASE VERIFICATION (dry-run, no DDL executed against nexusdb)

`additive_columns_statements()` emits 24 statements. The live `nexusdb`
is missing exactly 24 required columns. The two sets are **identical**
(`emitted == live-missing: True`) — the fix closes the entire gap with
zero redundant statements. The two columns named in the runtime log are
both in that set:

* `audit_signals.signal_dedup_key`
* `audit_account_snapshots.account_source`
