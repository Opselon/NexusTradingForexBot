# src/nexus_scalp/incidents/occurrences.py

- PURPOSE: Occurrence-aware impact analysis (spec 22/25) — replaces the
  "0 trades / 0 records" black hole by counting ACTUAL affected rows keyed
  by the incident's own identity fields (ticket / execution_id / order_id /
  position_id / master order family / request_id / model / research run).
  Distinguishes ZERO_IMPACT (touches concrete objects, none matched) /
  UNKNOWN_IMPACT (no concrete identity in record) / NOT_YET_MEASURED /
  PRE_PERSISTENCE_FAILURE (defect pre-dates persistence) / MEASURED.
- ARCHITECTURE LAYER: Application (read-only DB forensics).
- RESPONSIBILITY: identity extraction from the incident record, family
  counts per table, semantics classification, pre-persistence detection,
  evidence attachment.
- DEPENDENCIES: incidents.models (EvidenceItem/Incident), sqlite3,
  incidents.timebase._parse_ts (lazy import).
- CONNECTS TO: impact.py ImpactAnalyzer (count_families +
  attach_occurrence_evidence), web diagnostics, reports.
- KEY CONCEPTS:
  - `_identity_values` (line 53): harvests identities from affected_records
    (digits or contains '-' → ticket; else order_id), timeline payloads
    (fuzzy key match: family name in key, or key in ticket/execution_id/
    order_id), evidence observed dicts, and correlation_id → request_id.
    Values "0"/""/"None" are dropped.
  - `_match_sql` (line 88): builds `? IN (cols)` OR-chains — ONE parameter
    position per identity value; "1=0" when no identities (no rows match).
  - count_families (line 114): per-table counts: audit_ledger (ticket,
    order_id) → affected_ledger_records + affected_trades; audit_experience_
    outcomes (execution_id, idempotency_key) → affected_outcomes;
    research_runs (run_id, strategy_id) → affected_research_records;
    audit_orders (order_id, parent_order_id) → affected_orders;
    audit_experiences (execution_id, request_id, idempotency_key) →
    affected_executions; audit_broker_trades (trade_id, position_id,
    master_order_id) → affected_positions. Family = None when the table is
    missing or there are no identities; semantics: no ids → UNKNOWN_IMPACT,
    ids but 0 known → ZERO_IMPACT, else MEASURED.
  - unobservable families stay None (never 0) — impact consumers must
    treat None as unknown, not zero.
  - `pre_persistence_detection` (line 173): compares incident first_seen
    with the EARLIEST row of audit_ledger.open_time / audit_experiences.
    timestamp / audit_broker_trades.synced_at — if first_seen predates the
    earliest row, the defect sat before persistence existed (notes; never
    raises).
  - `attach_occurrence_evidence` (line 216): appends a DATABASE-kind
    EvidenceItem carrying semantics + counts + identities as immutable
    observed data.
- HOT PATH / PERFORMANCE: per-incident scans only (worker cycles/about
  once per incident); each count is one COUNT(*); identity count is small —
  no full-table scans beyond sqlite_master introspection + MIN(col) in
  pre-persistence detection.
- EDGE CASES & PITFALLS: ledger counts double-count (affected_ledger_
  records and affected_trades query the SAME audit_ledger rows — the two
  "families" are aliases, so total_known sums rows twice); research_runs
  count is keyed on run_id/strategy_id columns that may not exist in older
  schemas (sqlite3.Error → 0, treated as measured-zero); fuzzy key match
  `family in kl` can misattribute payload keys (e.g. "master_order_id"
  also matches "order_id" family since the loop keeps the LAST matching
  family per key); MIN(col) comparisons are string-ordered ISO — safe for
  ISO-8601 text, unsafe for mixed formats.