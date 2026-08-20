# src/nexus_scalp/incidents/impact.py

- PURPOSE: Impact analysis, quarantine, and recovery-plan generation
  (TASK-12 spec 25/27/28/29/30). Automatic impact estimation from OBSERVED
  evidence only (no fabricated numbers; unknown stays unknown); containment
  limited to explicitly safe actions; recovery plans generated RECOMMENDED
  and NEVER executed by the engine.
- ARCHITECTURE LAYER: Domain/Application (read-only analysis + governance).
- RESPONSIBILITY: ImpactAnalyzer (evidence-driven impact), QuarantineManager
  (non-destructive marks), RecoveryPlanner (template recovery plans +
  approval gate).
- DEPENDENCIES: incidents.models, sqlite3 (read-only), lazily imported
  incidents.occurrences.
- CONNECTS TO: worker (analysis pass), store, reports, web diagnostics.
- KEY CONCEPTS:
  - ImpactAnalyzer.analyze (line 41): occurrence-aware — when db_path is
    set, per-family counts come from occurrences.count_families keyed by the
    incident's identity fields (so a scan-time incident is never reported as
    "0 trades / 0 records" when real rows exist). semantics from occurrences
    gate which count wins: for ZERO_IMPACT/MEASURED/UNKNOWN_IMPACT,
    affected_records = max(ledger, positions, executions); otherwise falls
    back to len(incident.affected_records).
  - affected_time_range from the incident's own timeline min/max timestamps
    (line 106); research-run blast added for RESEARCH/LEARNING/DATA from
    research_runs count (line 112).
  - UI endpoints derived from category map (line 118) — never fabricated,
    category-keyed API path list; TELEGRAM → [].
  - `_classify_blast_radius` (line 153): DATA or any affected table →
    SYSTEM_WIDE; UI/API/VERSION/TELEGRAM/FEATURE/NEWS → COMPONENT;
    MT5/LEDGER/ACCOUNTING/LEARNING/RESEARCH/MODEL/EXPOSURE →
    CROSS_COMPONENT; else LOCAL.
  - QuarantineManager.mark_suspect (line 181): marks only — keeps original
    record + reason + incident_id + timestamp; downstream consumers may
    consult marks, no pipeline auto-rewrites on a mark (spec 30).
  - RecoveryPlanner.TEMPLATES (line 216): per-category canned recovery steps
    with kind (RECONCILE/REBUILD/REVALIDATE/QUARANTINE/BLOCK/MANUAL) and
    required_tests — e.g. MT5 REC-01 "reconcile broker history first; do
    NOT touch the ledger before reconciliation evidence exists"; MODEL
    "never load on file-exists"; MIGRATION "do not downgrade; re-run via
    the migration engine only"; EXECUTION-less by design (no category
    template for execution — category falls through to the MANUAL fallback).
  - generate() (line 380): fills what_failed/why/affected, trusts
    audit.db tier-0/tier-1 rows, suspects reconstruction_source=NONE zero
    PnL outcomes + quarantine entries + stale-cache lineage, and hard-wires
    must_not_change: no ledger/accounting rewrite, no retraining/Champion
    mutation, no RiskEngine/lot/SL-TP change, no automatic DB deletion.
    Unknown categories get MANUAL REC-01 with approval_required=True.
  - require_approval (line 439): governance invariant — any recovery step
    not in RECOMMENDED state raises; enforces RECOMMENDED → APPROVED →
    EXECUTING transition discipline (spec 29).
- HOT PATH / PERFORMANCE: on-demand (worker cycles / UI); research-runs
  count is a full COUNT(*) — fine for periodic runs.
- EDGE CASES & PITFALLS: `_count_research_runs` queries research_runs
  unprefixed (assumes table exists; exceptions swallowed → 0);
  affected_record_count uses max() of family counts — a LEDGER incident
  with 0 positions and 5 ledger rows reports 5; severity/blast radius are
  category-driven heuristics, order of checks matters (DATA checked before
  the cross-component list).