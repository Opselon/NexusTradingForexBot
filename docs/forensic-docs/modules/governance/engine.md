# src/nexus_scalp/governance/engine.py

- PURPOSE: Model Governance Engine — the truthful runtime registry +
  promotion state machine + rollback + health envelope (TASK-6 spec 3/21/
  22/23/27). Composable with the LiveEngine but holds NO execution
  references (no adapter/order manager/risk engine).
- ARCHITECTURE LAYER: Domain (governance / control plane).
- RESPONSIBILITY: registry_snapshot (six-category reconciliation),
  transition/promote_to_review/approve/promote/rollback (audited state
  machine with hard gates), health, promotion_preview/rollback_preview
  (read-only TASK-08 previews), emergency freeze/unfreeze/disable.
- DEPENDENCIES: governance.load_gate, governance.models (incl.
  PROMOTION_TRANSITIONS), governance.store, sqlite3, logging; verify
  imported lazily inside promotion_preview.
- CONNECTS TO: LiveEngine wiring, web promotion UI, GovernanceStore
  (audit.db), governance.transaction (atomic path), verify_candidate.
- KEY CONCEPTS:
  - REGISTRY TRUTHFULNESS: registry_snapshot reads experience_model_registry
    (read-only, LIMIT 500) and maps lifecycle_status →
    CURRENT_CHAMPION / CURRENT_CHALLENGER / SHADOW / PENDING_APPROVAL /
    RETIRED / FAILED. Champion resolution: lifecycle CHAMPION row, else
    champion_id, else artifact-path match. A file existing is never
    "current". Champion artifact hash/load-gate verification attached as
    champion_verification.
  - STATE MACHINE (models.PROMOTION_TRANSITIONS): RESEARCH→VALIDATED→
    CHALLENGER→SHADOW→READY_FOR_REVIEW→APPROVED→CHAMPION; any state may
    go REJECTED/RETIRED; CHAMPION only RETIRED (never silent demotion);
    REJECTED→RESEARCH only explicit re-entry. transition() raises
    PromotionGateError on illegal moves and records a PROMOTION_BLOCKED
    governance event with allowed_from_current.
  - SHADOW → CHAMPION is NEVER reachable directly (spec 21).
  - promote_to_review: SHADOW→READY_FOR_REVIEW requires the FULL 14-item
    checklist (CHECKLIST_EVIDENCE_KEYS: artifact/manifest/schema/scaler
    valid, oos_pass, robustness_pass, calibration_acceptable,
    no_class_collapse, no_severe_feature_drift, shadow_sample_floor,
    shadow_evidence_acceptable, latency_acceptable, no_critical_anomalies,
    rollback_target) — every key must be exactly True.
  - approve: READY_FOR_REVIEW→APPROVED requires an explicit operator
    actor (actor=="system" rejected).
  - promote: APPROVED→CHAMPION requires an approval_token (no
    auto-promotion), blocked when promotion_frozen or model in
    disabled_candidates; records the transition FIRST, then calls
    dep["activate"] callback; activation failure records a
    PROMOTION_BLOCKED event (evidence preserved, never deleted) and
    raises.
  - rollback: CHAMPION→RETIRED transition + ROLLBACK_EXECUTED event;
    NEVER restores old user data over migrated data — it restores the
    runtime pointer via dep["rollback_activate"] receiving the previous
    identity; evidence about the failed model is NEVER deleted. Failure
    of the rollback callback is logged, not raised.
  - health(): truthful champion/challenger/shadow JSON envelope +
    promotion_state summary.
  - EMERGENCY CONTROLS (spec 31, in-memory by design, event-ledgered):
    freeze_promotions / unfreeze_promotions (PROMOTION_FREEZE /
    PROMOTION_UNFREEZE events; distinct from Stop Bot — it only blocks
    promotions), disable_candidate (adds to set, QUARANTINES state in
    store, CANDIDATE_DISABLED event; evidence never deleted).
  - promotion_preview: read-only; verify_candidate for the 10 gates
    mapped into a UI gate_summary; `locked` = any promotion*.lock file
    exists in locks_dir.
- HOT PATH / PERFORMANCE: operator-frequency paths only; reads open
  short-lived read-only sqlite connections; never on tick path.
- EDGE CASES & PITFALLS: _promotion_state_summary returns {} when the
  store is not sqlite-backed; _current_champion_identity swallows errors
  to None; approve() accepts any non-system actor string (no real
  authentication — actor is self-declared); promote() records the
  transition BEFORE activation, so a failed activation leaves
  APPROVED→CHAMPION recorded in the ledger while the runtime pointer
  did not swap (recoverable only via rollback); health cache fields
  (_health_cache, _last_reconcile) are declared but never populated.