# src/nexus_scalp/shadow/shadow70/runtime.py

- PURPOSE: 70D shadow load validation + runtime (TASK-05-70D-SHADOW
  spec 3/4/5/6/16/17): SHADOW_LOAD_GATE v1 (MANIFEST → ARTIFACT HASH →
  SCHEMA → DIMENSION → SCALER → MODEL LOAD → HEALTH CHECK → SHADOW READY).
  A malformed candidate NEVER enters the runtime. Strictly observational:
  no adapter/order manager/risk engine/policy (INV-018); inference is
  torch.inference_mode deterministic; 4-class output never reinterpreted.
- ARCHITECTURE LAYER: Domain (observation runtime).
- RESPONSIBILITY: Shadow70LoadValidator (deterministic load gate),
  Shadow70Runtime (attach/set_inference/pause/resume/stop/observe,
  _validate_vector/_build_observation/_record, summary/recent_window),
  Shadow70LoadResult, _InferenceFn, sha256_file/sha256_json.
- DEPENDENCIES: models, hashlib, math, time, logging; inference callable
  injected (no torch import here when the runner supplies it).
- CONNECTS TO: LiveEngine shadow70 wiring (builder supplies the 70D
  vector), liq_provider/news_provider (vector families), store/worker
  (persistence), UI.
- KEY CONCEPTS:
  - LOAD GATE sequence: CANDIDATE_EXISTS (None → NO_VALIDATED_CANDIDATE)
    → MANIFEST_VALID (model_id/version/artifact_path) →
    VALIDATION_STATUS_VALID (validation_result must be
    VALIDATED_CANDIDATE — anything else SHADOW_BLOCKED) → SCHEMA_VALID
    (scalp_v3) → INPUT_DIMENSION_VALID (70) → HASH_VALID (live sha256
    of the artifact; missing file SHADOW_LOAD_FAILED; mismatch vs
    contract SHADOW_LOAD_FAILED) → SCALER_VALID (live scaler hash vs
    contract) → FEATURE_SCHEMA_HASH (empty → SHADOW_DEGRADED, not
    blocked — provenance incomplete) → SHADOW_READY. Never raises.
  - Runtime states §32: IDLE/LOADING/READY/DEGRADED/BLOCKED/FAILED/
    STOPPED/PAUSED; attach maps the load verdict onto READY or
    FAILED/BLOCKED/IDLE; pause/resume/stop are explicit operator
    semantics.
  - observe() NEVER raises. Fault classification in order: state guard
    (not READY → SHADOW_BLOCKED) → vector validation (spec 6: dimension
    == 70, all finite (INV-70D-005), all in [-3,3] (INV-70D-006),
    feature_schema_hash matches contract (INV-70D-007), freshness <=
    FEATURE_FRESHNESS_SEC=300s, provenance hash present; failures →
    SHADOW_STALE_FEATURES (when only freshness failed, finite+in-range)
    or SHADOW_FEATURE_INVALID) → latency-bounded inference (exceeds
    SHADOW70_LATENCY_BUDGET_MS=50ms → SHADOW_INFERENCE_TIMEOUT) →
    disagreement classification.
  - _InferenceFn: wraps the injected callable; enforces exactly 4
    probabilities, finite 0..1, positive sum; renormalizes; argmax →
    action via the 4-class mapping; confidence = max prob.
  - observation_id deterministic:
    sha256_json("{snapshot_id}|{model_id}|{model_version}|{ts}") — a
    reconnect/retry duplicates NOTHING (idempotency key).
  - _record: bounded windows (_recent ≤ MAX_INMEMORY_OBSERVATIONS=2000,
    latency_ms ≤ 500); counters observations/valid/errors/dropped/
    timeouts/schema_mismatches/scaler_mismatches/feature_invalid;
    summary() truthful per spec 28/33/45 with agreements/disagreements
    over the recent window and avg/max/p95 latency.
- HOT PATH / PERFORMANCE: observe() runs per M1 decision — vector
  validation O(70), inference ms under inference_mode, persistence is a
  queue put (non-blocking); freshness check uses datetime.now(UTC)
  (fine at M1).
- EDGE CASES & PITFALLS: freshness compares datetime.now() vs the
  observation timestamp — a caller replaying old bars gets
  SHADOW_STALE_FEATURES by design (replay must pass sample_source field
  but timestamps still age); schema_ok compares only when BOTH hashes
  are non-empty (missing contract hash → schema_ok stays True — the
  LOAD gate already DEGRADED for missing feature_schema_hash, but a
  runtime constructed without that contract check passes); agreement
  counts in summary are over `self._recent` (bounded window, not all
  observations); _looks_stale only treats freshness loss as stale —
  dimension/non-finite failures are feature-invalid; dropped increments
  for every invalid observation with an error_code (note: SHADOW_BLOCKED
  observations also count as dropped).