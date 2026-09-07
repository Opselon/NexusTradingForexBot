# HANDOFF — INTEGRATION/RECOVERY MISSION (launcher fixture + launch gate)

**Agent:** Hermes-Integration-Recovery
**Date:** 2026-09-07 08:5x (+03:30)
**Commit:** ab9db747 — fix(test): align launcher fixtures with artifact integrity contract

## Root cause of the 6 launcher failures (PROVEN, not assumed)

All 6 failing tests (`test_launcher_paper_boundary_bug212.py`) construct a REAL
LiveEngine via `_make_engine()` with the DEFAULT AppConfig artifact path
(`artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`). Since the P1
artifact-trust gate landed (verify_artifact_integrity inside
ModelBundleStore._load_or_create_bundle, commits d9f6b577/37c25893/b1ece040),
every engine construction re-verifies the production champion against
manifest.json. The on-disk champion weights (sha256 bb1f0afe...) do NOT match
the manifest digest (c9982ddde...) — a fine-tune/paper-accept path swapped
weights after the manifest was written (backups pre_direct_bak c8c0... and
bak2_1788498273 763a25f6... do not match either). Construction fails closed:
HASH_MISMATCH -> ArtifactIntegrityError. THE GATE IS CORRECT. Same single root
cause for all 6; no other causes found.

## Fix (fixture-only, security preserved)

- `_make_engine(..., tmp_path=...)` points engine-boot tests at a hermetic
  bundle: `_write_verified_bundle()` emits model.pt + manifest.json together,
  digest computed FROM the weights (the exact production trust-chain contract).
- NEW negative coverage `test_artifact_integrity_gate_rejects_tampered_weights`:
  mutates weights AFTER manifest creation, asserts ArtifactIntegrityError with
  HASH_MISMATCH on construction. The gate stays real in tests.
- No production file touched, no verification disabled, no legacy allowance.

## Verified

- test_launcher_paper_boundary_bug212.py 11/11 (10 prior + 1 new)
- test_model_load_integrity.py 10/10; hot_swap_governance 8/8;
  bug232 mode_boundary + launcher_mode_persistence green;
  bug182b + bug130 green; runtime-safety mission + survival-drawdown green.
- `python -m compileall src/nexus_scalp/application` exit 0.
- Rerun AFTER commit: all green. Foreign WIP intact and untouched.

## CURRENT STATE / OWNERS

- HEAD: ab9db747. live_engine.py 4166 LOC (extraction wave continues: L14
  tick-pipeline pre-policy seam landed during this mission in commits
  466e1533/f8ef51a3/1d101bbb by other agents).
- live_engine.py / runtime_loop.py / model_bundle_store.py / order_write.py:
  NOT touched by me; clean at HEAD or foreign-dirty (champion_sync.py and
  tick_pipeline.py are foreign-dirty RIGHT NOW — active writer).
- app_settings DB at %LOCALAPPDATA%/NexusScalpEngine/databases/app_settings.db
  pins model.model_artifact_path to the 70d_liquidity champion — the on-disk
  manifest/weights mismatch will fail-closed the next REAL boot. Operator
  action needed: re-publish a coherent bundle (weights+manifest together) or
  rebind the sidecars. I did NOT touch production artifacts (fail-closed is
  the correct posture for untrusted weights).

## NEXT SAFE LIVEENGINE ACTION

ONE writer only, after current owners commit. The champion bundle repair
(atomic re-publish of weights+manifest) is the highest-value follow-up — it
is a DATA/artifact operation, not a code extraction, and should be owned by
the model-lifecycle lane, not another extraction wave.
