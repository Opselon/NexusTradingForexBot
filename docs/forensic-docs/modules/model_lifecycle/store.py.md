# src/nexus_scalp/model_lifecycle/store.py

- **PURPOSE:** Training Run Store & comparison persistence (spec 12/34/40).
  `training_runs` is an append-only, immutable record of every controlled
  training execution; `model_comparisons` stores Champion-vs-Challenger
  comparison lineage. Derived summaries are rebuildable; this truth is never
  modified.
- **ARCHITECTURE LAYER:** Research/ML persistence; no order authority.
- **RESPONSIBILITY:** Table creation (idempotent), immutable run/comparison
  writes through the AuditRepository background queue (never blocks live),
  bounded reads.
- **DEPENDENCIES:** sqlite3, audit_repository.AuditRepository (private
  `_is_sqlite`/`_db_path`/`_queue`), models (TrainingRun,
  ChampionChallengerComparison), logger.
- **CONNECTS TO:** orchestrator (save_run/save_comparison), worker (restore-
  inflight reads RUNNING rows), web/dashboards (get/list/summary).

- **KEY CONCEPTS:**
  - `ensure_schema()` (line 59): creates `training_runs` (24 columns, run_id
    UNIQUE, timestamps TEXT, JSON blobs TEXT with defaults) and
    `model_comparisons` (run_id UNIQUE, eligible INTEGER) plus indexes
    (dataset_id, status, candidate_model_id). Sqlite-only, idempotent.
  - `save_run` (line 133): serializes the frozen TrainingRun to one row via
    `INSERT OR REPLACE` — idempotent on run_id (a retried run ROW is replaced,
    though the run is immutable; "append-only" refers to the run set growing).
    Enqueued: `audit_repo._queue.put_nowait` (line 165) — the caller never
    blocks on the DB write.
  - `save_comparison` (line 171): comparison JSON blob + improvement_score +
    eligible int, same queued write pattern.
  - Reads (lines 197-274): `get_run`/`get_comparison` by run_id;
    `list_runs` bounded (default 50, max MAX_READ_LIMIT=2000);
    `list_comparisons` bounded (default 50, max 200). Short-lived connections
    with timeout=5.0, always closed. JSON blobs are NOT parsed on read — rows
    come back raw dicts with TEXT values (consumers must json.loads).
  - `summary()` (line 276): counts per run status + comparison count for the
    dashboard.

- **HOT PATH / PERFORMANCE:** Writes are fire-and-forget onto the audit queue —
  a full queue or a dead audit thread would drop writes SILENTLY (put_nowait
  raises and is caught → save_run returns False, logged). Reads are direct
  sqlite (short, unindexed-by-exception: list_runs ORDER BY started_at DESC uses
  no started_at index).

- **EDGE CASES & PITFALLS:**
  - Non-sqlite audit repo ⇒ every method returns False/None/[] silently —
  - lifecycle persistence is entirely disabled without any surfaced error.
  - `INSERT OR REPLACE` on training_runs: a re-used run_id REPLACES the prior
    row — the run record itself is immutable in memory, but persistence is
    last-writer-wins per run_id (e.g. worker crash + restart rewriting RUNNING→
    INCOMPLETE overwrites nothing else; but a re-run with the same id erases the
    earlier record's metrics/gates).
  - `finished_at` written as "" (empty string) when None — reads return "" not
    NULL, minor schema inconsistency.
  - Reads return unparsed JSON TEXT — API consumers that forget json.loads
    expose '"{...}"' strings to the dashboard.