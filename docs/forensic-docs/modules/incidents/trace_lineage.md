# src/nexus_scalp/incidents/trace_lineage.py

- PURPOSE: One-Click Trace (spec 24/25/26) — full object-lineage
  resolution for incident_id / ticket / execution_id / request_id /
  model_id / order_id / position_id / research_run_id / training_run_id.
  Walks the REAL database lineage per input kind; NEVER fabricates links —
  an unestablished hop returns missing_link + reason + last_known_node
  (spec 26).
- ARCHITECTURE LAYER: Application (read-only forensics).
- RESPONSIBILITY: trace_lineage entry point + per-kind resolvers
  (_trace_incident with upstream/downstream, _trace_ticket_or_execution,
  _trace_model, _trace_research_run).
- DEPENDENCIES: sqlite3, incidents.models/store (IncidentStore).
- CONNECTS TO: web /api/diagnostics trace endpoint, incidents UI, reports.
- KEY CONCEPTS:
  - Kind dispatch (line 242): "INC-" prefix → incident (store lookup, with
    root_cause summary + affected_entities capped at 50 records / 20
    models + lineage via _trace_incident_lineage); run_/ds_ prefix →
    research run; non-digit, non-exp_ queries are probed against
    audit_experiences.model_id → model trace; everything else → ticket/
    execution/order trace.
  - _trace_ticket_or_execution (line 56): one node with ledger row
    (ticket/order_id), broker position (trade_id/position_id/
    master_order_id), outcome (execution_id/idempotency_key), experience
    (execution_id/idempotency_key/request_id). When the ledger row's
    order_id differs from the query value, the outcome/experience are
    re-resolved by order_id (idempotency_key) — the economic-execution
    identity bridge. Orders (audit_orders, ≤10), research runs by
    strategy_id (≤5), model_id surfaced when present.
  - _trace_model (line 168): model_registry (model_id/artifact_id, ≤5),
    experiences by model_id (≤20), research_runs via the distinct
    strategy_ids (IN-clause built with placeholders); missing_link "model"
    when nothing references it.
  - _trace_research_run (line 202): research_runs by run_id/dataset_id →
    strategy_registry row → outcomes_family count (join outcomes to
    experiences by idempotency_key filtered by strategy_id).
  - _trace_incident_lineage (line 150): downstream nodes for up to the
    first 10 affected_records identities.
- HOT PATH / PERFORMANCE: on-demand UI/API traces; bounded row caps
  everywhere (LIMIT 5/10/20/50); per-query connections with 10s timeouts.
- EDGE CASES & PITFALLS: kind dispatch has NO explicit position_id/
  request_id/training_run_id branches beyond the generic ticket trace —
  those ids route into _trace_ticket_or_execution and rely on its OR-
  matching; `run_`/`ds_` case handled lower-case only (Q.upper().startswith
  for INC only); a model_id that is ALL DIGITS is misrouted to the ticket
  trace (the `not q.isdigit()` gate at line 247); research_runs IN-clause
  with 0 strategy_ids is skipped; _rows swallows table-missing errors as
  empty (a missing audit_orders table reads as no orders).