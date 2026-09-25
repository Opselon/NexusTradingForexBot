# NSE-ALWAYS-ON-RUNTIME-FIX — FORENSICS CYCLE 1

**Lane**: `agent/nse-always-on-runtime-fix`
**Base**: `origin/main` @ `cbabadae` (lane worktree:
`C:/Users/Capsizer/source/repos/nse-runtime-fix`)
**Entry**: pasted runtime capture (2026-09-24 05:45:46+03:30 → 05:50:09+03:30,
v9.0.14) — the capture came from the `nse-review-main` clone (detached at
`c9851b51`, already merged to main as PR #417). **This lane operates on the
CURRENT origin/main, not the capture's stale base.**

## PROVEN FORENSIC EVIDENCE (reproduced, not inferred)

Executed the live config (provider=`postgresql`, host=127.0.0.1:5432/nexusdb)
against the real PostgreSQL instance from a hermetic probe:

```
repo._is_sqlite   = False
repo._db_url      = 'postgresql://postgres:***@127.0.0.1:5432/nexusdb'
repo._db_path     = ''            <-- consumers read THIS
```

Real PG schema dump (information_schema, read-only):

| table | cols | finding |
|---|---|---|
| audit_signals | 19 | `signal_dedup_key`, `preferred_direction`, raw_prob_* block ABSENT |
| audit_account_snapshots | 6 | `account_source` ABSENT |
| audit_guard_telemetry | 4 | present, but only `_pkey` index |
| learning_cycles | — | TABLE ABSENT from PG (lives only in SQLite) |
| runtime_risk_state | 17 | present with full breaker columns |

`psql` with the *stored* password is refused; the repo's own
`resolve_password()` (DPAPI secret store) works — the live engine reaches the
same database, so **the engine is authenticated and the schema it finds is
real.**

settings DB (`application_settings`, read-only):

```
execution.mode = LIVE                    # persisted 2026-09-20 19:09 by actor=web
database.provider = postgresql           # persisted 2026-09-24 by actor=db-fabric
database.postgresql_config = {...}       # actor=db-fabric
```

Probed consumer behavior with `_db_path = ""`:

```
sqlite3.connect('')            -> empty DB in process CWD, tables=[]
SELECT runtime_risk_state      -> OperationalError: no such table: runtime_risk_state
_resolve_cycle_store_db_path   -> ':memory:' (string check passes, table vanishes)
IncidentStore(audit_repo=repo) -> ValueError: IncidentStore requires db_path or audit_repo
Path(_db_path or '')           -> WindowsPath('.') .name == '' ; .exists() == True
```

## CAUSAL GRAPH — ONE ROOT, FIVE PRESENTATIONS

```
AuditRepository is provider-aware for QUEUED writes only (write plane)
   |
   +-- `_db_path` is set to "" when provider != sqlite  (line 360)
   |
   +-- ~20 DIRECT writer/reader paths still call `self._connect_sqlite()`
   |   (set_runtime_risk_state, breaker anchors, dead-letter fallback,
   |    experience lookup, flush, prune, retention...)
   |   => they open sqlite3.connect("") = a private empty DB in the CWD.
   |      Every one fails "no such table".
   |
   +-- `learning_cycles` is authored ONLY as an inline `sqlite3.connect`
   |   call inside LearningCycleStore — not in AuditRepository DDL.
   |   PG migration never sees it; SQLite fallback is :memory: (ephemeral).
   |
   +-- IncidentStore derives db_path from `audit_repo._db_path` (== "")
   |   => raises "requires db_path or audit_repo" every 36s.
   |
   +-- debug_snapshot does `Path(engine.audit._db_path or "")` =>
   |   WindowsPath('.') => `.name` empty => ValueError on DatabaseMigrationEngine
   |   construction. Probe `path.exists()` returns True (it IS the CWD),
   |   so the symptom surfaces as a *schema probe* error, not NO_PATH.
   |
   +-- DeadLetterStore._write_sink is None:
   |   `_dead_letter_write_sink()` is resolved at __init__ line ~392
   |   BEFORE `_build_write_plane()` (line ~412) which is what
   |   PROVISIONS the fabric's `audit` backend. get_domain_backend("audit")
   |   returns None at that moment → sink = None forever.
   |
   +-- PG schema drift: sqlite_ddl_statements() regex-scans
       `conn.execute("""...""")` literals that START WITH `CREATE`.
       The additive column heals are `_add_column_if_missing(...)`
       FUNCTION CALLS, not triple-quoted CREATEs → structurally invisible
       to the only code that provisions PostgreSQL. The repo ALREADY has
       the authoritative declarative contract (`APP_REQUIRED_COLUMNS` +
       `APP_UNIQUE_TARGETS`, stdlib-only, exactly for this); the PG path
       doesn't consume it. Result: nexusdb holds baseline skeletons only.
```

## DEFECT LEDGER (bucket per skill: a=code defect, b=noisy, c=env, d=hygiene)

| ID | Bucket | Root cause (PROVEN) | Files |
|---|---|---|---|
| RTF-001 | a | PG schema drift: additive column heals invisible to `sqlite_ddl_statements()` | `database/migration/__init__.py` |
| RTF-002 | a | `_db_path=""` + ~20 direct paths still call `_connect_sqlite()` | `adapters/database/audit_repository.py` |
| RTF-003 | a | dead-letter sink resolved before the write plane provisions the backend | `adapters/database/audit_repository.py` |
| RTF-004 | a | guard-telemetry `count` ambiguous under PG | `adapters/database/audit_repository.py` (+ callers) |
| RTF-005 | a | LearningCycleStore/IncidentStore use the wrong path under non-SQLite | `model_lifecycle/learning_loop.py` (+ `incidents/store.py` consumer) |
| RTF-006 | a | debug_snapshot `Path(_db_path or "")` on a DSN string | `web/debug_snapshot.py` |
| RTF-007 | a | Doctor DATA check requires `data/raw/XAUUSD_M1.parquet` while MT5 is the live source | `cli/doctor.py`, `release/health.py` |
| RTF-008 | c | FXStreet 403 / MarketWatch+Forexlive 301 (external, optional sources, backoff already correct) | none |
| RTF-009 | c | Telegram 401 invalid token (env or secure-store; auth breaker already trips correctly) | none |
| RTF-010 | b/c | Watchdog feed-stall episodes (market-closed quiet feed; recovery path already correct) | none |
| RTF-011 | c | `psycopg.Pipeline ... pipeline aborted` warnings on terminate (cleanup noise) | none |
| RTF-012 | d | DB hygiene orphans/duplicates/missing-index counts (audit-only, no auto-delete) | none |

## FIX CONTRACT (minimal, root-cause, fail-loud)

**RTF-001** — make the PG migration apply the declarative additive contract:
after the translated CREATE statements, apply
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (PG-native, idempotent) for every
`APP_REQUIRED_COLUMNS` entry, then `CREATE UNIQUE INDEX IF NOT EXISTS` for
every `APP_UNIQUE_TARGETS` entry. This is the SAME contract the SQLite side
heals from; one source of truth, no second column list. Schema verification
must compare REQUIRED columns vs `information_schema.columns`, and a missing
column must be a hard startup failure, not "READY".

**RTF-002** — a provider-aware connection accessor on AuditRepository:
`_connect_direct()` returns the SQLite connection when `_is_sqlite`, else the
fabric's read/write backend for the `audit` domain. Every direct
`self._connect_sqlite(...)` site routes through it. A direct write to a
non-SQLite domain must be explicit (executes outside the write plane's queue
by design — safety state, anchors, dead-letter). Never a silent wrong-DB.

**RTF-003** — resolve the dead-letter write sink LAZILY (at first record, or
after the write plane starts), not in `__init__` before provisioning. A
non-SQLite store that still has no sink must log CRITICAL and count the loss
(current behavior), never silently return None.

**RTF-004** — qualify the column in the ON CONFLICT upsert:
`DO UPDATE SET audit_guard_telemetry.count =
audit_guard_telemetry.count + 1` (PG-strict; SQLite accepts the qualified
form too, so ONE statement serves both providers).

**RTF-005** — LearningCycleStore and IncidentStore must resolve their backing
store from the ACTIVE provider, not a SQLite path derived from a provider
that is not SQLite. Under non-SQLite the state must be explicitly disabled
with an observable status, not silently ephemeral (`:memory:`) or a raised
error. (The `:memory:` fallback exists for test doubles; production PG must
not take it.)

**RTF-006** — debug_snapshot must not build a `Path` from a DSN string. When
the domain is not SQLite, report the provider + masked DSN and health from a
real connectivity probe, never a filesystem probe of "".

**RTF-007** — Doctor's DATA readiness must treat MT5 as the canonical live
source for the training contract (the runtime already boots, warms and
infers from MT5 bars). Keep the parquet check as a WARNING-level offline
training hint, not a FAIL that reports DEGRADED when the engine is healthy.

**OUT OF SCOPE / not code defects (RTF-008..012)** — environment and
hygiene; recorded, not code-changed.

## GOVERNANCE

- Isolated worktree; the shared checkout sits on a foreign branch
  (`agent/feature/website-icons`) with another agent's WIP — untouched.
- No locks yet for these files; `locks.yaml` entries will be added only if
  another lane concurrently edits the same paths.
- One PR per defect or one PR per cohesive root cause, conventional commits,
  ruff + targeted pytest per change, then CI → merge → post-merge validation.
