# src/nexus_scalp/forensics/experience_gap.py

- PURPOSE: Experience → Outcome gap forensics (TASK-12 §16-20). Answers
  "where does outcome information first disappear?" per experience
  (signal → execution → broker → close → ledger → outcome → experience →
  research) and classifies every missing outcome into the §18 taxonomy.
  CRITICAL (§19): a missing outcome NEVER becomes PnL=0/R=0/win=false
  silently — missing stays distinguishable from a genuine zero result.
  Thresholds come from config, not hardcoded numbers (§20).
- ARCHITECTURE LAYER: Application (forensics analysis, read-only).
- RESPONSIBILITY: analyze_experience_gap, classify_missing_outcome,
  load_gap_thresholds, persist_gap_report, GAP_CLASSES,
  DEFAULT_THRESHOLDS, ExperienceGapReport.
- DEPENDENCIES: sqlite3 (strict RO URI), json, yaml (lazy), logging.
- CONNECTS TO: checks.check_experience_outcome_gap (CHECK-ACC-04),
  periodic report, dashboard artifacts/forensics/
  experience_outcome_gap.json.
- KEY CONCEPTS:
  - classify_missing_outcome ORDER (proven vs the live DB 2026-08-19):
    0) decision layer FIRST — an experience WITHOUT an execution_id
       never traded → LEGITIMATELY_NO_OUTCOME (signal rejected by
       risk/policy/engine), NOT a pipeline drop;
    1) OPEN/PENDING/ACTIVE status → OPEN_TRADE (not closed yet);
    2) no broker rows → BROKER_HISTORY_MISSING;
    3) no ledger rows → LEDGER_MISSING;
    4) outcome row suppressed/duplicate → DUPLICATE_SUPPRESSION;
    5) closed > 30 days ago and never resolved → EXPIRED_CONTEXT;
    6) research/backtest strategy → LEGITIMATELY_NO_OUTCOME;
    7) payload mentions reconstruct/fail → RECONSTRUCTION_FAILURE;
    8) fallback UNKNOWN.
  - analyze_experience_gap: reads audit_experiences + outcomes; total/
    with/without counts; per-missing classification via broker/ledger
    lookups by ticket; age distribution in weekly buckets; DEFECT RATE =
    defect_classified / (defect_classified + with_outcome) — the
    TASK-12 correction: the gap only reflects a LEARNING-PIPELINE defect
    when an EXECUTED trade (execution identity) lost its outcome;
    legitimate never-traded samples never degrade status. Status via §20
    thresholds: defect_rate > degraded (0.50) → DEGRADED; > warning
    (0.20) → WARNING; 0 expected outcomes → PASS; empty DB → UNKNOWN.
    first_divergence = earliest timestamped experience without outcome.
  - load_gap_thresholds: configs/base.yaml → forensic_report.
    experience_gap {gap_rate_warning, gap_rate_degraded, recoverable_min};
    defaults otherwise; never raises.
- HOT PATH / PERFORMANCE: full-table scans of audit_experiences/outcomes
  — runs on report cadence only (bounded by report interval).
- EDGE CASES & PITFALLS:
  - status==PASS when total_expect_outcome==0 — even if every experience
    is missing (all legitimate) — correct by design but reads oddly.
  - classification UNKNOWN with no key is unrecoverable.
  - broker/ledger lookups match ticket via trade_id/position_id/
    master_order_id ORs — a ticket matching multiple rows overcounts
    "found" (presence test only).
  - DEFECT vs RECOVERABLE buckets: DUPLICATE_SUPPRESSION is counted
    recoverable; UNKNOWN/reconstruction/etc unrecoverable.
  - report.without_outcome is computed as total - with_outcome (outcomes
    are keyed differently — a decision's outcome counted against any
    experience with the same key can misattribute when keys collide).