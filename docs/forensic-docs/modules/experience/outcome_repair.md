# src/nexus_scalp/experience/outcome_repair.py

- PURPOSE: BUG-046 historical outcome repair — identifies past closed-trade
  outcomes corrupted by the 1-hour deal-lookup bug (realized_r=0 /
  reconstruction_source NONE despite a real broker close), re-queries broker
  deal history over a lifecycle-bounded window, and repairs ONLY the derived
  outcome layer through `ExperienceLedger.repair_outcome`.
- ARCHITECTURE LAYER: Application (offline repair job over the ledger + an
  injected broker-history accessor).
- RESPONSIBILITY: The five invariants in the docstring (lines 9-16): the
  immutable decision row in audit_experiences is NEVER modified; repair is
  IDEMPOTENT (same key → same value, never duplicates rows, never double-counts
  PnL); repair is BOUNDED (MAX_REPAIR_CANDIDATES=200 per pass, broker queries
  bounded); every repaired outcome carries repair provenance in its payload;
  when broker truth is still unavailable the outcome is left as-is and reported
  UNREPAIRED — never silently zeroed again.
- DEPENDENCIES: `experience.ledger.ExperienceLedger`,
  `experience.models` (ExperienceOutcome, ExperienceRecord),
  `experience.outcome_recovery.reconstruct_broker_outcome`, stdlib
  (json, sqlite3, dataclasses), observability.logging.
- CONNECTS TO: the caller wiring (MT5 adapter via `broker_deals_fn`), ledger
  (get_experience_by_key / repair_outcome / audit queue), outcome_recovery.
  Commanded via a CLI/worker path external to this module.
- KEY CONCEPTS:
  - `_is_zero_outcome` (lines 46-47): a zero-R outcome is a repair candidate
    only when |realized_r| < 1e-12 AND |realized_pnl| < 1e-9 — genuinely
    zero-valued results, not rounded ones.
  - `OutcomeRepairResult` (lines 50-67): bounded aggregate — candidates /
    repaired / unrepaired / skipped_no_broker + repaired_rows detail dict.
  - `OutcomeRepairJob` (lines 70-87): `broker_deals_fn(ticket, hours_back)`
    is INJECTED (tests fake deals; production passes the real MT5 adapter).
  - Candidate discovery: `_outcome_rows` (lines 93-111) — newest-first
    bounded scan of outcomes with a non-empty execution_id;
    `_candidates` (lines 113-131) — filters to zero-R rows whose payload
    `broker_outcome.reconstruction_source` is NOT one of BROKER_NATIVE /
    BROKER_DEALS / BROKER_DEALS_AGGREGATED (already-authoritative rows are
    not corrupt).
  - `run` (lines 148-180): executes one pass, per-candidate failure-isolated
    (candidate failure counts unrepaired, never aborts the pass), then
    `flush_repair_queue` (join the audit queue) so repaired outcomes are
    durable and immediately readable.
  - `_repair_one` (lines 182-332): key/ticket from the row; decision record
    required (else SKIPPED_NO_DECISION); lifecycle-bounded broker window —
    `window_h = max(hours_back or 72h, age_of_decision + 2h)`; broker query
    only when the ticket is all-digits; no deals → MATCH_FAILED unrepaired;
    direction derived from record.action (SELL wins over BUY substring check);
    `reconstruct_broker_outcome` with planned entry/SL/TP from the DECISION
    row (final_sl = record.stop_loss — the repair path has no in-flight SL
    history, a documented simplification), volume from broker aggregation
    falling back to outcome/decision volume; R multiple computed as
    net_pnl / (risk_distance × volume × CONTRACT_SZ 100.0) with a
    $1 floor on risk_usd; payload rebuilt with corrected scalars, broker
    outcome, exit_reason defaulted to HARD_SL_HIT when missing, and
    `repair_provenance` (repair_id repair_<key12>, old/new values, source,
    deal_ids, reason=BUG-046_1H_LOOKUP_WINDOW); validations via
    ExperienceOutcome.model_validate; write via repair_outcome; aggregates
    result.
  - `flush_repair_queue` (lines 335-341): `ledger.audit_repo._queue.join()`
    wrapped in try/except — safe to call after a repair pass.
- HOT PATH / PERFORMANCE: offline job only — bounded to 200 candidates per
  pass; one broker query per candidate; queue join drains the batch. Never on
  the tick path.
- EDGE CASES & PITFALLS:
  - The R multiple here uses an approximation: risk_usd is computed from the
    DECISION row's planned stop distance and a HARDCODED contract size of
    100.0 (line 259) — for non-XAUUSD instruments or non-100 contracts the
    repaired R is an estimate; repair provenance records the source so the
    approximation is visible, never silent.
  - `close_time=datetime.now(UTC)` at repair time (line 244) — the repaired
    BrokerOutcome carries the repair run's timestamp, NOT the true broker close
    time (which the 1h-lookup bug failed to capture); `duration_sec` is
    therefore also an estimate (now − decision).
  - Exit reason is NOT reclassified: `repaired_payload["exit_reason"]` falls
    back to "HARD_SL_HIT" only when the old payload was empty — the repair
    job does not run classify_exit_with_evidence, so a corrupt EXIT reason is
    left as-is (documented boundary; outcome_recovery is the classification
    authority).
  - `run()` calls `_outcome_rows` which selects ALL columns — bounded by
    max_candidates, but the LIMIT applies post-ORDER BY outcome_timestamp DESC;
    very old zero rows beyond the newest 200 tickets are never repaired in a
    single pass (multiple passes converge).