# src/nexus_scalp/research/store.py

- PURPOSE: PHASE 09B bounded READ facade over the research tables — the
  strategy_registry, research_runs, outcome-quality and worker-state reads
  for observability, forensics and the self-healing rebuild. This module
  owns NO write path (writes live in registry.py/observability.py through
  the AuditRepository queue).
- ARCHITECTURE LAYER: Application read facade (short-lived read-only SQLite
  connections; never blocks the live path).
- RESPONSIBILITY: norm-critical registry reads (list/get), run listing,
  registry summary (lifecycle counts), BUG-046 outcome-quality census, the
  TASK-4 research data-health summary ("why is the registry empty?"), and a
  conservative self-heal that only repairs derived lifecycle consistency.
- DEPENDENCIES: `adapters.database.audit_repository` (private `_db_path` /
  `_is_sqlite`), `research.discovery` (family_distribution,
  discover_candidates — imported lazily), observability.logging, stdlib
  (sqlite3, json).
- CONNECTS TO: web/API endpoints, worker telemetry reads, observability
  trace/analytics, factory orchestrator (_load_elite, _registry_rows_for_
  generation via get_registry_entry + _decode_registry_row).

- KEY CONCEPTS:
  - JSON safety (BUG-075, lines 25-63): `_json_text_safe` / `_registry_row_
    safe` normalize the JSON-text columns (context_definition,
    parent_strategy_ids, backtest, walkforward, oos, robustness, score,
    validation_lineage, retirement_reason) — historical `"null"` /
    `null` literals become `'{}'` so no frontend `JSON.parse` crashes.
  - `list_registry` (66-93) / `get_registry_entry` (96-122): bounded
    (default 200, clamp 2000) newest-first; versioned or latest-row reads.
  - `list_research_runs` (125-152): append-only run records, newest first,
    bounded 500.
  - `registry_summary` (155-175): total + lifecycle distribution.
  - `outcome_quality_summary` (178-241): BUG-046 diagnostics — total/closed
    outcomes, zero-R census (ABS(r)<1e-12 AND ABS(pnl)<1e-9), positive/
    negative R counts, and a bounded (2000 row) reconstruction-source census
    from outcome payloads (NONE_OR_MISSING bucket) — explains zero-R
    corruption vs genuinely no evidence.
  - `research_health_summary` (244-378): the TASK-4 answer to "why is the
    registry empty?" — source/canonical/closed ledger counts, eligibility
    audit (dataset_builder.audit()), family distribution, candidate census,
    validation attempt tallies (oos_pass/fail, robustness_fail, validated/
    rejected from result_summary rows), worker telemetry
    (last cycle/error from research_worker_state). Exceptions return a
    stable `{"available": False, "error": "HEALTH_SUMMARY_UNAVAILABLE"}`
    marker (CodeQL py/stack-trace-exposure, line 376).
  - `self_heal_research` (381-410): only repairs lifecycle CONSISTENCY —
    any entry whose embedded oos result is FAIL (and lifecycle not already
    REJECTED/DEGRADED/RETIRED) is model_copied to REJECTED and upserted;
    never touches historical validation truth.
- HOT PATH / PERFORMANCE: read-only, per-call 5s-timeout connections,
  bounded LIMITs everywhere; used on API/worker paths, never per tick.
- EDGE CASES & PITFALLS:
  - `outcome_quality_summary` loads full payload columns of 2000 rows per
    call — the bounded scan is a BUG-046 requirement but should be cached
    or windowed for frequent dashboard polling.
  - `research_health_summary` re-runs the FULL dataset audit + build +
    discovery on every call when dataset_builder is provided — expensive
    (O(ledger)); the API layer decides how often.
  - Worker-telemetry columns are read positionally (`row[0]`, `row[2]`)
    against `SELECT cycle_count, last_cycle_at, last_error` — safe today,
    but any reordering of the SELECT breaks the mapping silently (no
    column names used).
  - `self_heal_research` does not use registry.invariant_check and only
    fixes OOS-consistency, not walkforward/robustness/score mismatches —
    a VALIDATED entry with failed walkforward stays VALIDATED.
  - list/get return RAW dicts (rows + JSON text) except list_research_runs
    which returns raw dict rows without column normalization.