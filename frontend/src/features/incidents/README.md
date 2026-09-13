# Incidents (bounded context)

Forensic incident console: filterable incident list + full detail drawer
(evidence, timeline, value traces, quarantine, recovery plan), report export,
evidence-zip href, search, one-click trace, value lineage, read-only forensic
probes, confirm-guarded reconcile (forensic audit).

- Invariants: incidents are backend-owned records; the UI never mutates them
  outside the reconcile command, and reconcile never creates duplicates
  (idempotent by incident_id, decided server-side).
- `resolved_without_evidence` and regression linkage render verbatim —
  dedup/impact classification is never recomputed in the browser.
- Evidence: diagnostics_state_routes.py (/api/diagnostics/*),
  incidents/models.py::as_dict; behavior reference Web/app.js tab-incidents
  + forensic_console.js.
