# src/nexus_scalp/governance/transaction.py

- PURPOSE: The ATOMIC promotion transaction (TASK-08 spec 8/29/37/38):
  VERIFY CANDIDATE → LOCK GOVERNANCE → RECORD OLD CHAMPION → ACTIVATE NEW
  → VERIFY NEW → COMMIT. NEVER leaves "no Champion" or a "half-promoted
  Champion". Crash recovery: the audit row is written BEFORE activation
  with PROMOTION_STARTED and updated on outcome — post-restart the audit
  table is the source of truth (spec 38).
- ARCHITECTURE LAYER: Application (governance orchestration).
- RESPONSIBILITY: execute_promotion_transaction — the full atomic flow;
  PromotionTransactionError; PROMOTION_TXN_STATES.
- DEPENDENCIES: governance.lock (PromotionLock), governance.models,
  governance.store, governance.verify (verify_candidate), logging.
- CONNECTS TO: UI/API promotion endpoint, GovernanceEngine.promote
  (single-step variant), operator wiring (activate/verify_new/
  rollback_activate callbacks supplied by the application).
- KEY CONCEPTS — THE FLOW:
  0. Actor gate: explicit non-system operator actor + non-empty
     approval_token (no implicit promotion).
  1. VERIFY CANDIDATE: fresh, read-only verify_candidate with the full
     10-gate + optional contracts evidence; ineligible → PROMOTION_FAILED
     audit + event, raise.
  2. LOCK GOVERNANCE: PromotionLock.try_acquire; failure →
     PROMOTION_FAILED + "PROMOTION_CONFLICT: another promotion in
     progress" (no partial write).
  3. RECORD OLD CHAMPION: _rec("PROMOTION_STARTED") — audit row with
     old/new champion pair, candidate hash, approval actor/token,
     rollback_target, recorded BEFORE any mutation (crash-recovery
     anchor).
  4. ACTIVATE NEW: activate(model_id, model_version) — on exception the
     candidate state is set REJECTED, PROMOTION_FAILED recorded,
     raise (previous Champion unchanged because activation never
     started).
  5. VERIFY NEW: verify_new callback must return {"ok": True}; any
     failure → automatic rollback via rollback_activate to the previous
     identity (or logged "manual rollback required" when no callback),
     candidate QUARANTINED, PROMOTION_ROLLED_BACK recorded, raise. This
     is the ONLY path that restores the previous Champion — and it
     restores the RUNTIME POINTER, never overwriting user data/migrated
     rows.
  6. COMMIT: set_state CHAMPION, PROMOTION_COMMITTED audit + event;
     returns the audit row. finally: lock.release() always.
  - _rec maps status → event code: COMMITTED → PROMOTION_EXECUTED,
    STARTED → PROMOTION_BLOCKED, ROLLED_BACK → ROLLBACK_EXECUTED,
    FAILED → "PROMOTION_FAILED".
- HOT PATH / PERFORMANCE: rare operator action; lock lifetime is the
  transaction duration (~ms); no tick-path involvement.
- EDGE CASES & PITFALLS: if step 4's activation raises AFTER partially
  swapping runtime state, the code records REJECTED but does not restore
  the old champion (no rollback_activate call on activation failure —
  only on post-activation verification failure); documented as
  "previous Champion unchanged" assumption — depends on the callback
  being atomic; if verify_new is None, step 5 is skipped entirely
  (post-activation smoke is optional); owner PID in lock file is written
  but never re-read for validation beyond staleness; events are recorded
  via the queued writer — a crash between _rec and commit leaves the
  PROMOTION_STARTED row as the only truth (per spec, that is the
  recovery contract).