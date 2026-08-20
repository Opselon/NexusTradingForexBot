# src/nexus_scalp/model_lifecycle/champion.py

- **PURPOSE:** Champion model management — the production-authorized model (spec
  3/26/28/29). The Champion is the ONLY model allowed in the production inference
  path; loading verifies integrity (hash, schema, dimension, class count, scaler)
  and NEVER silently loads a corrupted artifact. Missing/invalid Champion is a
  supported cold-start state that records its lineage.
- **ARCHITECTURE LAYER:** Research/ML governance boundary for the production
  model artifact; no order authority.
- **RESPONSIBILITY:** Wrap artifact verification + hot-path caching of the
  verified Champion, and provide candidate/challenger STAGING paths so training
  never touches the Champion artifact (spec 16/33).
- **DEPENDENCIES:** `features.schema.FEATURE_SCHEMAS`, `integrity` (inspect_artifact,
  scaler_compatibility, SchemaCompatibilityError), `models.ModelArtifactInfo`,
  logger. NO adapter/order-manager/risk-engine imports.
- **CONNECTS TO:** trainer (candidate paths, parent-champion lineage), orchestrator
  (champion_or_none), LiveEngine/web via champion_or_none (hot path).

- **KEY CONCEPTS:**
  - `ChampionModel` (line 40): immutable description; constructor runs
    `inspect_artifact` immediately (line 72). Scaler path resolution (lines 59-71):
    explicit path, else canonical sibling `model.pt → model.scaler.npz` (the old
    `.pt.scaler.npz` suffix "silently missed the real file and logged a
    misleading 'scaler missing' warning on every verify" — commented fix).
    `available` = info.integrity_ok; `verify()` raises SchemaCompatibilityError on
    invalid artifact (default) and warns (not fails) on scaler mismatch.
  - `ChampionManager` (line 120): wraps the Champion path for Phase 10 consumers.
  - FINGERPRINT CACHE (lines 149-214, BUG-118): `champion_or_none()` is the ~2 Hz
    hot path (web/governance polls). Cache key = artifact fingerprint
    (size + mtime_ns, line 208). Once verified, the same ChampionModel is reused
    while the fingerprint is unchanged — the artifact is neither re-read nor
    re-logged. `[MODEL] CHAMPION VERIFIED` (line 174) logs ONLY when the cached
    instance changes or the hash differs (initial load or artifact rewrite —
    retrain/promotion/rollback/collapse recovery), fixing BUG-118 log spam.
    `force_reload=True` performs a fresh verify (startup, hot-swap). Cold-start
    None is also memoized with its fingerprint so repeated missing-artifact
    warnings are not re-emitted (lines 151-152, 198-199).
  - `candidate_artifact_path(run_id)` (line 216): `champion-parent/candidate/
    <run_id>/model.pt` — NEVER the champion path (spec 33). Scaler sibling
    `model.pt + ".scaler.npz"`. `challenger_artifact_path(run_id)` (line 223):
    `challengers/<run_id>/model.pt` — where a fully verified candidate is
    promoted as Challenger.

- **HOT PATH / PERFORMANCE:** champion_or_none = one stat() per call when cached
  (fingerprint), zero torch/numpy work; full verify only on fingerprint change or
  force_reload. This is the ~2 Hz governance poll cost.

- **EDGE CASES & PITFALLS:**
  - Fingerprint is (size, mtime_ns): a rewrite preserving both (rare) would
    evade the cache invalidation; content hash is only recomputed on verify.
  - `ChampionModel` construction in `load_champion` inspects even when the caller
    only wanted a cached answer — force_reload=True pays the full torch state-dict
    load.
  - `verify()` treats a missing scaler as acceptable (cold-start warn), so
    `available` can be True with no scaler — inference consumers must verify
    scaling elsewhere (integrity.scaler_compatibility still returns False).