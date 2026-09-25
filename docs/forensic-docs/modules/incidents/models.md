# src/nexus_scalp/incidents/models.py

- PURPOSE: Incident Response core (TASK-12) — the canonical incident
  structure, enums, and value-lineage contracts. One canonical incident per
  docs/70D_INCIDENT_RESPONSE_MODEL.md. Diagnostic-only by design (spec 0):
  nothing here can change trading behavior, RiskEngine, lots, SL/TP,
  execution, models or accounting history.
- ARCHITECTURE LAYER: Domain. Pure dataclasses + StrEnums, no I/O.
- RESPONSIBILITY: incident_id/detected_at/severity/category/status/first_
  and last_seen_at/component/operation/correlation_id/root_cause_status/
  evidence/impact/affected_*/recovery_status/recommended_action plus the
  dedup & correlation fields (fingerprint, repeated_count, bug linkage) and
  rich sub-structures (timeline, value traces, quarantine entries, recovery
  plan).
- DEPENDENCIES: stdlib only (hashlib, uuid, dataclasses, datetime, StrEnum).
- CONNECTS TO: everything in incidents/ — store, correlator, impact,
  lineage, worker, reports, telegram; web diagnostics.
- KEY CONCEPTS:
  - Enums: IncidentSeverity with `.rank` (INFO=0..CRITICAL=4) for
    escalation compare; IncidentStatus lifecycle OPEN→INVESTIGATING→
    ROOT_CAUSE_IDENTIFIED→CONTAINED→RECOVERY_READY→RECOVERED→FIXED→VERIFIED
    →CLOSED (+FALSE_POSITIVE which KEEPS the record, spec 64). VERIFIED
    requires evidence (fix + regression test); CLOSED is terminal.
    RootCauseConfidence deliberately has no "proven" shortcut — spec 32
    forbids labeling hypotheses as proven; PROVEN is the top rung.
    RecoveryState spec 29: recovery must be explicit + governed.
  - `incident_fingerprint` (line 401): sha256(category|component|
    error_code).lower()[:16] — stable root fingerprint for dedup (spec 31):
    55 identical exceptions → ONE incident with repeated_count=55.
  - `Incident.mark_seen` (line 453): monotonic last_seen, earliest
    first_seen; add_timeline_event integrates mark_seen automatically.
  - `as_dict` sorts timeline by timestamp (line 504) — deterministic
    serialization for reports/store.
  - ValueTrace.hops() (line 195): builds SOURCE_OF_TRUTH → transformations
    → cache → persistence → consumers chain (spec 8). Example: PnL MT5 deal
    → broker adapter → deal snapshot → reconciliation → accounting → API → UI.
  - QuarantineEntry: non-destructive mark (record_key + reason + incident_id,
    never deletes). RecoveryAction: proposal-only, approval_required=True,
    destructive flagged; REQUIRES operator approval for every step.
- HOT PATH / PERFORMANCE: none — created on incident detection/correlation
  only, not per tick.
- EDGE CASES & PITFALLS: QuarantineEntry.record_key is a string canonical
  identity (ticket/idempotency_key) — never assumed to be an int; Impact
  as_dict converts the time range to a list [start, end] (JSON-friendly);
  frozen dataclasses (LineageStep, ValueTrace, EvidenceItem, RecoveryAction)
  guarantee immutability of evidence.