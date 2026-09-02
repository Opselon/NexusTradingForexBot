# src/nexus_scalp/incidents/accounting.py

- PURPOSE: Accounting divergence forensics (TASK-13 STEP-05/06). For every
  affected record (broker PnL != 0 but ledger PnL == 0) traces the chain
  BROKER → EXECUTION → LEDGER → OUTCOME → RESEARCH and finds the FIRST
  stage where real PnL becomes zero/missing. NEVER writes; produces
  recovery CANDIDATES with evidence/source/confidence/algorithm_version
  for a governed repair decision (spec 18/19).
- ARCHITECTURE LAYER: Application (read-only forensic engine).
- RESPONSIBILITY: audit_zero_pnl_ledger, per-record _analyze_record +
  _classify + _classify_zero_outcome, identity indexes, artifact builder.
- DEPENDENCIES: sqlite3, json, collections.Counter, logging.
- CONNECTS TO: incidents reports (artifact bundling), web diagnostics,
  governed repair workflows (candidates only).
- KEY CONCEPTS:
  - RECONSTRUCTION_ALGORITHM_VERSION = "agent13-reconcile-v1" — recovery
    candidates are version-stamped (spec 18/19) so a governed repair can
    cite the algorithm that produced them.
  - ROOT_CAUSE_CLASSES (spec 16): BROKER_SYNC_LOSS / RECONSTRUCTION_FAILURE
    / LEDGER_WRITE_FAILURE / LEDGER_UPDATE_FAILURE /
    DUPLICATE_SUPPRESSION_ERROR / ZERO_DEFAULT_BUG /
    OUTCOME_PROPAGATION_FAILURE / ROUNDING_ERROR /
    SPLIT_FILL_CONTEXT_ERROR / TIMESTAMP_MATCH_FAILURE / UNKNOWN.
  - ZERO_OUTCOME_CLASSES (spec 17): LEGITIMATELY_UNRESOLVED /
    RECOVERABLE_FROM_BROKER / RECOVERABLE_FROM_EXECUTION / CORRUPTED /
    DUPLICATE / PHANTOM / UNKNOWN.
  - audit_zero_pnl_ledger (line 97): JOIN audit_broker_trades ×
    audit_ledger on ticket=trade_id where |broker pnl|>0.01 and
    |ledger pnl|<0.005 (newest first, ≤500); experiences/outcomes are
    indexed once by execution_id/idempotency_key/request_id
    (setdefault — first wins).
  - _analyze_record (line 168): stage list BROKER→LEDGER→(OUTCOME)→
    (EXPERIENCE "present, no outcome"); first_incorrect = first stage
    after BROKER whose string value is "0.0"/"0"/"present, no outcome";
    first_correct_stage hard-coded BROKER; first_missing_stage = OUTCOME
    when no outcome row.
  - _classify (line 246): evidence-driven PROVEN chain for
    RECONSTRUCTION_FAILURE — close-time deal not yet visible locally →
    reconstruct_broker_outcome returned NONE with 0.0 fallback →
    profit_usd 0.0 persisted as FINAL ledger value → later broker sync
    populated the real PnL but no post-sync reconciliation ran (zero
    persisted as if real). Triggers: exit_reason_source empty →
    RECONSTRUCTION_FAILURE; experience w/o outcome →
    OUTCOME_PROPAGATION_FAILURE; zero outcome → RECONSTRUCTION_FAILURE;
    else UNKNOWN.
  - _classify_zero_outcome (line 271): payload.reconstruction_source
    NONE/MISSING → RECOVERABLE_FROM_BROKER; BROKER_DEALS →
    LEGITIMATELY_UNRESOLVED; else UNKNOWN.
  - recovery_candidate (line 210): RECOMMENDED status, confidence 0.95
    when broker source == BROKER_DEALS else 0.7, full evidence dict
    (gross/commission/swap/exit mechanism), status REQUIRES approval —
    never auto-applied.
- HOT PATH / PERFORMANCE: the JOIN is indexed-looking (ticket=trade_id)
    but audit_ledger has no (ticket) dedicated index guarantee; experience/
    outcome indexes scan FULL tables once per run — fine at ≤500 records.
- EDGE CASES & PITFALLS: outcome index keyed by execution_id first wins —
    an outcome row referenced by a later idempotency_key with different
    values is shadowed; zero_outcome_class is only computed when an outcome
    row exists AND its pnl < 0.005; recovery candidates require
    abs(broker)>0.01 AND abs(ledger)<0.005 — records outside that band
    produce no candidate; the complaint query needs identical trade_id ↔
    ticket string formats — a broker trade_id stored with different casing
    or implicit int conversion silently unmaps.