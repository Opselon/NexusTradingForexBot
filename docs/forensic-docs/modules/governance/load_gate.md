# src/nexus_scalp/governance/load_gate.py

- PURPOSE: Deterministic 10-gate model load gate (TASK-6 / CHG-0003 spec 4).
  A model CANNOT be loaded merely because its file exists; every gate
  failure reports the EXACT failing gate. PURE (never loads weights into
  a runtime, never disturbs the Champion bundle).
- ARCHITECTURE LAYER: Domain (governance input boundary).
- RESPONSIBILITY: ModelLoadGate.evaluate — run the 10 gates in order and
  return the first failure as a LoadGateResult; evaluate_from_registry —
  gate via an ArtifactStore-shaped accessor; module helpers sha256_hex,
  read_manifest_file, read_registry_lifecycle, evaluate_load_gate.
- DEPENDENCIES: features.schema (FEATURE_SCHEMAS), governance.models,
  observability.logging; torch/numpy imported lazily on the paths that
  need them.
- CONNECTS TO: GovernanceShadowRuntime/challenger load, engine
  (champion artifact verification), verify, promotion transaction,
  startup wiring.
- KEY CONCEPTS — THE 10 GATES (in order):
  1. ARTIFACT_EXISTS — file exists and size > 0.
  2. HASH_VALID — sha256 of the artifact matches manifest artifact_hash
     (mismatch → ARTIFACT_HASH_MISMATCH). Missing declared hash passes
     (actual recorded as evidence).
  3. MANIFEST_VALID — manifest dict present with model_id,
     feature_schema_id, feature_dimension, class_count.
  4. SCHEMA_VALID — feature_schema_id must be REGISTERED in
     FEATURE_SCHEMAS (never guessed).
  5. INPUT_DIMENSION_VALID — state-dict `input_projection.weight`
     width == manifest effective width (build_metadata.input_dimension
     overrides feature_dimension); state dim unknown → skipped, not
     failed.
  6. SCALER_VALID — scaler file present (default `<artifact>.scaler.npz`),
     mean/std dims == feature_dimension, std > 0 everywhere
     (SCALER_MISMATCH on any fault).
  7. LABEL_SCHEMA_VALID — label_schema_id present, class_count in {3,4}.
  8. VALIDATION_STATUS_VALID — final_validation_result present, or
     walk_forward/oos/robustness statuses contain no FAIL/REJECT;
     empty combined status is UNKNOWN→PASS-with-reason ("no explicit
     failure recorded"), NEVER a silent fail.
  9. LIFECYCLE_ALLOWS_SHADOW — lifecycle state (arg, else manifest role)
     parses to CHALLENGER/SHADOW or is in allow_shadow_states.
  10. LOAD — all gates passed → LoadGateResult(passed=True).
  - `_state_dict_input_dim` scans the state dict for
    input_projection.weight / projection.weight / net.0.weight or any
    2-D "projection"/"net."-prefixed key — tolerant of TCN/attention
    naming but returns None on any error (gate is skipped, not failed).
  - read_registry_lifecycle reads lifecycle_status from
    experience_model_registry via a read-only sqlite URI.
- HOT PATH / PERFORMANCE: not on the tick path (load-time only); hashing
  streams 64KB chunks; torch import cached by the interpreter.
- EDGE CASES & PITFALLS: empty-file artifacts fail ARTIFACT_EXISTS;
  scaler naming is a convention (`<artifact>.scaler.npz`) — callers with
  other layouts must pass scaler_path explicitly; gate 5 tolerates an
  unreadable state dict (purposely — a torch failure must not produce a
  false mismatch); gate 8 treats "no validation recorded" as UNKNOWN-
  pass, which is documented as lenient-by-design.