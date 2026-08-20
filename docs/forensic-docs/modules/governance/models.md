# src/nexus_scalp/governance/models.py

- PURPOSE: Canonical, versioned, FROZEN pydantic contracts of the live
  model-governance boundary (TASK-6). Imports NO adapter/order manager/
  risk engine/execution object — a governance bug can never place,
  modify or close a trade (INV-002/003/004).
- ARCHITECTURE LAYER: Domain (contracts/datatypes).
- RESPONSIBILITY: all governance value objects + enums; the canonical
  state machine table PROMOTION_TRANSITIONS and checklist.
- DEPENDENCIES: pydantic, enum, datetime only.
- CONNECTS TO: every governance module (store, engine, transaction,
  shadow_runtime, reporting, evidence) and consumers of the API.
- KEY CONCEPTS:
  - GovernanceErrorCode: bounded failure taxonomy (MODEL_LOAD_REJECTED,
    SCHEMA_MISMATCH, SCALER_MISMATCH, FEATURE_PARITY_FAILURE,
    NEWS_PARITY_FAILURE, SHADOW_TIMEOUT, SHADOW_QUEUE_FULL,
    PREDICTION_INVALID, ARTIFACT_HASH_MISMATCH, LIVE_DRIFT,
    PROMOTION_BLOCKED, ROLLBACK_EXECUTED, PROMOTION_EXECUTED,
    REGISTRY_RECONCILED).
  - GovernanceEvent: append-only audit row (frozen); stage ∈ LOAD_GATE/
    REGISTRY/SHADOW/PARITY/OUTCOME/CALIBRATION/DRIFT/PROMOTION/ROLLBACK/
    HEALTH/TELEGRAM; actor = operator/system/api:<endpoint>; timestamps
    normalized to UTC by validator.
  - LoadGateStep: the canonical 10-gate enum (ARTIFACT_EXISTS →
    HASH_VALID → MANIFEST_VALID → SCHEMA_VALID → INPUT_DIMENSION_VALID →
    SCALER_VALID → LABEL_SCHEMA_VALID → VALIDATION_STATUS_VALID →
    LIFECYCLE_ALLOWS_SHADOW → LOAD); LoadGateResult frozen verdict with
    failing_gate + error_code (default MODEL_LOAD_REJECTED).
  - RegistryCategory: the six truthful registry questions.
  - PromotionState + PROMOTION_TRANSITIONS: explicit state machine;
    ANY state may go REJECTED/RETIRED; CHAMPION only → RETIRED (never
    silently demoted); REJECTED→RESEARCH explicit re-entry only;
    SHADOW NEVER → CHAMPION directly.
  - PROMOTION_CHECKLIST: the 14 human-readable checklist items (spec 22)
    mirrored by engine.CHECKLIST_EVIDENCE_KEYS.
  - PromotionTransition: immutable audited transition (actor/reason/
    evidence snapshot/source_commit/artifact_hash).
  - CalibrationBucket [lo,hi) with label property; DriftAlert (kind
    PROBABILITY|ACTION|FEATURE|NEWS, severity WARN|CRITICAL); ShadowParity
    (same-input parity evidence; alignment IDENTICAL|NEWS_EXTENDED|NONE;
    latencies ms).
- HOT PATH / PERFORMANCE: pydantic validation on construction only;
  GovernanceEvent built per failure/comparison — negligible.
- EDGE CASES & PITFALLS: ShadowParity.max_abs_diff default 0.0 with
  ge=0.0 — the alignment module writes -1.0 for UNKNOWN parity, which
  would violate the constraint only if passed through the model directly
  (build_shadow_parity passes raw values; check: UNKNOWN state stores
  -1.0 — pydantic ge=0.0 would reject it; in practice UNKNOWN parity is
  only routed to ShadowParity with ok=True; Flag as potential
  validation pitfall); PromotionState(challenger_cohort) style strings
  would fail parsing (str-enum strict). Frozen models use
  model_dump(mode="json") for persistence.