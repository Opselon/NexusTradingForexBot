# src/nexus_scalp/governance/verify.py

- PURPOSE: FRESH, read-only re-verification of a candidate before
  promotion (TASK-08 spec 7/34). NEVER trusts cached governance state;
  every gate gets an explicit PASS/FAIL/SKIP/INCONCLUSIVE — a missing
  gate is NEVER silently GREEN and a soft score never overrides a hard
  failure (spec 18/19). PERFORMS NO WRITES and NO runtime mutation.
- ARCHITECTURE LAYER: Domain (governance gate).
- RESPONSIBILITY: verify_candidate — 14-gate verdict dict; VERIFY_GATES
  taxonomy.
- DEPENDENCIES: features.schema (FEATURE_SCHEMAS), load_gate.sha256_hex,
  governance.models, governance.store (best-effort event record),
  numpy lazily.
- CONNECTS TO: governance.transaction (step 1), engine.promotion_preview,
  UI preview; forensics deploy gate indirectly.
- KEY CONCEPTS — THE 14 GATES (each PASS/FAIL/SKIP/INCONCLUSIVE):
  1. artifact_exists — file present, size > 0.
  2. artifact_hash_matches — manifest artifact_hash == live sha256 (FAIL
     when manifest has none).
  3. manifest_valid — model_id/feature_schema_id/feature_dimension/
     class_count present.
  4. schema_registered — schema id ∈ FEATURE_SCHEMAS (never guessed).
  5. schema_matches_runtime — candidate schema == runtime_schema_id
     (SKIP when not provided).
  6. input_dimension_matches — feature_dimension == runtime_dimension
     (SKIP when runtime_dimension 0).
  7. scaler_valid — scaler present, mean/std dims == declared
     feature_dimension, all std > 0.
  8. feature_schema_hash_matches — manifest hash == provided (SKIP when
     not provided).
  9. liquidity_version_matches — manifest liquidity_algorithm_version ==
     provided (SKIP when absent).
  10. training_commit_recorded — manifest training_commit/source_commit
      present (SKIP when not provided).
  11. oos_artifact_recorded — manifest oos_artifact/oos_result present
      (SKIP when not provided).
  12. shadow_evidence_recorded — shadow_evidence.sample_floor_met True
      (SKIP when no evidence supplied).
  13. news_contract_valid — news_contract["valid"] (SKIP when None).
  14. liquidity_contract_valid — liquidity_contract["valid"] (SKIP when
      None).
  - ELIGIBILITY RULE (spec 18/19): eligible = no FAILURES and no SKIPS.
    SKIPPED mandatory evidence = INSUFFICIENT_EVIDENCE, never GREEN.
    Unknowns alone do not block eligibility (reported separately).
  - On ineligible + store present: records PROMOTION_BLOCKED_VERIFICATION
    governance event (best-effort, never raises on store failure).
- HOT PATH / PERFORMANCE: promotion-frequency; per-gate O(size) hashes /
  numpy reads; never on tick path.
- EDGE CASES & PITFALLS: line 148 `int(meta.get("input_dimension",
  declared_dim) or declared_dim)` — result DISCARDED (no assignment),
  so the manifest build_metadata.input_dimension is read but never
  compared against the state dict; the gate compares only raw
  feature_dimension vs runtime_dimension (the load gate's state-dict
  cross-check is NOT repeated here — a manifest lying about
  input_dimension via build_metadata would pass gate 6 if
  feature_dimension matched); a store that is None skips event records;
  gate detail strings include full error text (bounded by 400 chars for
  reasons, but per-gate detail is unbounded).