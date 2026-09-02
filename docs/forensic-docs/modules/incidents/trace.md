# src/nexus_scalp/incidents/trace.py

- PURPOSE: Forensic traces — the reusable "WHY" workflows (TASK-12 spec
  38..43) — read-only diagnostic procedures over audit.db / logs.
  NOTHING here mutates trading state, databases or configuration.
- ARCHITECTURE LAYER: Application (read-only forensic queries).
- RESPONSIBILITY: broker_ledger_divergence (spec 13), clock_skew (spec
  14), split_fill_groups (spec 15), outcome_forensics (spec 16),
  learning_pipeline_rates (spec 17), why_blocked/why_closed/why_no_learning/
  why_no_strategy/why_ui_empty (spec 39-43), news_incidents (spec 20),
  version_consistency (spec 22).
- DEPENDENCIES: sqlite3 (short-lived read connections), json, re, logging.
- CONNECTS TO: incidents __init__ exports → web diagnostics endpoints,
  reports bundles, worker callers.
- KEY CONCEPTS:
  - Shared column projections (_LEDGER_COLS/_BROKER_COLS/_EXP_COLS/
    _OUTCOME_COLS) and _safe_rows (query failure → [] + debug log, never
    raises).
  - broker_ledger_divergence (line 72): broker trades (window_days=90,
    ≤5000) mapped to ledger rows by ticket/position_id; |delta| > 0.01
    flagged; unmapped counted; READ-ONLY note (spec 13).
  - clock_skew (line 133): TWO distinct measurements — sync_lag (host UTC
    minus LATEST synced_at; >300s → TIMEBASE_DIVERGENCE) vs observed_data_
    age (reported separately, NEVER treated as clock skew) — a stale-data
    age is never mistaken for a live clock bug (TIME-1).
  - split_fill_groups (line 209): groups broker rows by master_order_id
    (project execution identity, "0"/"None" skipped); families with >1
    ticket are split fills.
  - outcome_forensics (line 256): zero R + zero PnL outcomes classified by
    reconstruction_source (NONE → SUSPECT_OUTCOME; NONE but broker row has
    |net_pnl|>0.005 → BROKER_RECOVERABLE — the broker has the truth, the
    accounting divergence family BUG-115; sourced → ZERO_WITH_SOURCE).
  - learning_pipeline_rates (line 349): experience→outcome→research→
    candidate rates with documented baselines (exp_to_out <0.25 & n_exp≥40
    → LEARNING_DATA_LOSS; out_to_res <0.05 & n_out≥40 & n_res==0 & n_out>0
    → OUTCOME_TO_RESEARCH_DROP — flags only when outcomes exist but never
    consumed; res_to_cand <0.05 & n_res≥5 → RESEARCH_TO_CANDIDATE_DROP).
  - why_blocked (line 409): audit_signals rejection_reason rows (ticket
    match or payload LIKE) + guard telemetry for the ticket's symbols.
  - why_closed (line 438): ledger row + lifecycle EXIT/CLOSE events +
    autopsy; conclusion "CLOSED via <mechanism> (evidence: <source>)".
  - why_no_learning (line 485): experience record lookup by execution_id/
    request_id/idempotency_key + outcomes + corrections; diagnosis
    NO_EXPERIENCE_RECORD / LEARNING_ENTERED / NO_OUTCOME_YET.
  - why_no_strategy (line 524): registry + intelligence registry counts +
    outcomes + recent research runs; diagnosis REGISTRY_POPULATED /
    CANDIDATES_PRESENT_BUT_NOT_PROMOTED / NO_VALIDATED_CANDIDATE.
  - why_ui_empty (line 567): backend COUNT per field (strategies/news/
    trades/ledger/experiences) — BACKEND_EMPTY vs BACKEND_HAS_DATA vs
    BACKEND_UNAVAILABLE.
  - news_incidents (line 609): NEWS_SOURCE_EMPTY (0 articles),
    NEWS_SOURCE_UNHEALTHY (news_health rows unhealthy), NEWS_ALL_NEUTRAL
    (≥20 recent articles and ≥90% NEUTRAL).
  - version_consistency (line 662): build-info.json backend version vs
    Web/app.js bundle stamp regex (first 800 chars) vs schema_meta
    schema_version — backend_web_version_mismatch → VERSION_INCONSISTENCY.
- HOT PATH / PERFORMANCE: bounded LIMITs (5000/500/10000/200); queries
  run only on demand via diagnostics.
- EDGE CASES & PITFALLS: _parse_ts here (line 187) is a LOCAL variant
  (strptime formats then fromisoformat) distinct from timebase._parse_ts —
  drift risk if the two evolve separately; `_safe_rows` swallows ALL
  sqlite errors so a missing table reads as empty results rather than
  an unavailable backend (why_ui_empty handles this explicitly with its
  own try); broker_ledger_divergence ledger query is UNBOUNDED by date
  (LIMIT 5000 rows, newest by close_time); why_closed exit_events slice
  uses "-10" (10 latest); version_consistency requires web bundle stamp
  regex to match a semver in the first 800 chars of app.js.