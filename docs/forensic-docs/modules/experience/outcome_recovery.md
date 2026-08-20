# src/nexus_scalp/experience/outcome_recovery.py

- PURPOSE: Phase 14 outcome correlation and broker-close reconstruction —
  deterministic helpers that (a) classify a closed trade's outcome class,
  (b) classify the EXIT MECHANISM from broker deal evidence instead of trusting
  the internal state machine's label, (c) reconstruct the authoritative
  `BrokerOutcome` from broker deal history, and (d) resolve the idempotency key
  of a closed ticket when the originating request_id was lost.
- ARCHITECTURE LAYER: Application/domain helper layer — pure functions over
  broker-evidence dicts and the ledger; no adapters owned here (deal dicts are
  passed in by the caller/adapter).
- RESPONSIBILITY: UNKNOWN stays UNKNOWN (INV-012: never silently promote
  evidence to a label); every exit label carries provenance (evidence_source,
  evidence_detail, confidence); no synthetic numbers in reconstruction
  (reconstruction_source="NONE" flags snapshot estimates); deterministic
  correlation that never fabricates the original request identity.
- DEPENDENCIES: `experience.models` (BREAKEVEN_R_BAND, BrokerOutcome,
  ExitReason, ExperienceRecord, OutcomeClass, OutcomeCorrelationSource), sqlite3
  (typing only), observability.logging.
- CONNECTS TO: `intelligence.py` (resolve_outcome_correlation on the
  missing-request_id path), `outcome_repair.py` (reconstruct_broker_outcome for
  BUG-046), accounting forensics (`outcome_row_to_broker_outcome`), ledger
  (`get_experiences_by_order_id` as POSITION_STATE fallback).
- KEY CONCEPTS:
  - `classify_outcome_class` (lines 22-34): |r|>0.05 → WIN/LOSS else
    BREAK_EVEN; BREAK_EVEN is a REAL class (Phase 14), band mirrors the
    evaluator thresholds so counts always agree.
  - `is_protective_exit` (lines 37-47): True for SL/STOP/RISK_FREE/TRAIL
    mechanisms; explicit exclusions for "", UNKNOWN, MANUAL_CLOSE,
    SYSTEM_CLOSE, RECONCILIATION_CLOSE — genuine manual closures and engine
    systematic closes are NOT protective.
  - `classify_exit_with_evidence` (lines 50-209): the evidence-aware classifier
    (TASK-3 / BUG-083/085). Returns (exit_reason, evidence_source,
    evidence_detail, confidence). Decision ladder: engine-forced mechanism
    first (confidence 1.0); broker reason codes 5 (TP) / 4 (SL) / 6 (SO) /
    1,2 (client manual close — only when no protective evidence); reason 0 is
    AMBIGUOUS — with corroboration (comment/geometry/PnL) → MANUAL_CLOSE at
    0.8, else UNKNOWN at 0.2 (never assumed); reason 3 (EA/Expert close — the
    engine's own closes) NEVER assumed MANUAL — comment + SL geometry decide
    the protective class (BUG-081 rule: BE/trailing labels require
    `was_sl_modified` proof); nse_* comment prefixes; SL_GEOMETRY / TP_GEOMETRY
    fallbacks; weakest PnL-sign heuristics (0.4); final UNKNOWN 0.0.
  - `_classify_sl_geometry` (lines 212-235): requires final_sl and entry_price
    > 0 (else HARD_SL_HIT at 0.7); BE tolerance = max(0.5, 0.0005×entry) —
    final SL within tolerance of entry → BREAK_EVEN_SL_HIT only if
    was_sl_modified (else HARD_SL_HIT "SL at entry, never modified");
    trailed beyond entry → TRAILING_STOP_HIT; else HARD_SL_HIT.
  - `classify_exit_reason` (lines 238-279): thin wrapper returning only the
    reason string; `profit_usd=None` is treated as 0.0 for the classification
    heuristic ONLY — the caller distinguishes UNKNOWN PnL from zero PnL.
  - `reconstruct_broker_outcome` (lines 282-404): filters caller-deals by
    position_ticket == ticket; BUG-084 fix — dedupes matched_deal against the
    passed `history_deals` (appending it again double-counted the same physical
    close); aggregates multiple close deals (gross, commission, swap, volume
    summed, deal ids collected, last price, last reason/comment); builds
    net_pnl_usd = gross − |commission| − |swap|; sources BROKER_DEALS /
    BROKER_DEALS_AGGREGATED. No deal evidence → deterministic snapshot
    estimate flagged reconstruction_source="NONE" (gross/net zeroed honestly).
  - `resolve_outcome_correlation` (lines 407-466): resolution order —
    1. ORIGINAL_REQUEST (request_id present → exp_<request_id>);
    2. POSITION_STATE (request_id absent, ledger `get_experiences_by_order_id`
       returns EXACTLY ONE candidate → its key; >1 candidates → AMBIGUOUS →
       None);
    3. BROKER_TICKET_FALLBACK (deterministic `exp_bt_<ticket>` with explicit
       provenance) — the caller MUST verify a matching decision row before
       recording (intelligence.py does exactly that).
  - `outcome_row_to_broker_outcome` (lines 469-525): lifts persisted outcome-row
    broker fields (ticket from execution_id, close_time from
    outcome_timestamp) into a typed BrokerOutcome for accounting forensics when
    the JSON payload predates the typed field; returns None for empty rows.
    NOTE: all numeric fields on the lifted object are 0.0 — it exists only to
    carry the ticket/timestamp identity.
  - `_iso` (lines 512-525): safe datetime→isoformatter, naive→UTC.
- HOT PATH / PERFORMANCE: called only at close/reconciliation/repair time,
  never per tick; aggregation bounded by the number of deals per ticket.
- EDGE CASES & PITFALLS:
  - `classify_exit_with_evidence` uses price-delta tolerances hardcoded in
    price units: near_sl = |exit−sl| < 0.15, near_tp < 0.10 — symbol-dependent
    (fine for XAUUSD, questionable for JPY pairs); a documented heuristic.
  - reason code 5 (TP) REQUIRES (near_tp OR "tp" in comment) — a TP exit whose
    exit price drifted beyond 0.10 and whose comment lacks "tp" falls through
    to the heuristics instead of TP; conservative, by design.
  - `reconstruct_broker_outcome` overwrites reason_code/comment from the LAST
    deal row iterated (dict order) — with aggregated partial closes the last
    deal's reason wins, which may not be the closing mechanism of the position
    as a whole.
  - `outcome_row_to_broker_outcome` returns a row with exit_price 0.0 even
    when the caller has a real exit price — callers must not use the lifted
    object for price forensics, only identity.