# HANDOFF — 70D CHAMPION BUNDLE RECOVERY (integration-recovery mission)

**Agent:** Hermes-Bundle-Recovery
**Date:** 2026-09-07 09:0x (+03:30)
**Base HEAD:** ab9db747 · companion commit: (see git log — test_champion_bundle_recovery_contract)

## Verdict: RECOVERED (provenance-backed byte-identical restore; no blessing, no rehash)

### Trusted-source determination (Requirement 2/3)

The incoherence was NOT an unknown-provenance weight file. Chain of custody:

1. `manifest.json` (bundle_c9982ddde175, metadata_sha256 6953249e..., scaler_sha256
   b3c65b65...) IS the identity of record — authored 2026-09-05T00:08:54Z binding
   model_sha256 c9982ddde1755591112da679ba82bfca1eeff4def1700f9a7481ca18525dd9ce,
   dataset ds_70d_clean_m1_20260904 (3ae687ea...), scalp_v3 / 235b8fccc96b7e0e,
   git 6599e4d5, lineage CLEAN_HISTORICAL, production_eligible=true.
2. MLFix.md doc-log (2026-09-05 05:30 entry) + artifacts/forensics/
   agent1_promotion_record_20260905.json: Agent-1 PROMOTED
   pilot_70d_3class_20260905_000649 (sha c9982ddde...) to 70d_liquidity,
   replacing c8c0b5b0 (4-head provenance-incoherent artifact).
3. The promoted source still exists byte-intact:
   artifacts/model_generation/models/pilot_70d_3class_20260905_000649/model.pt
   == c9982ddde... (EXACTLY what the deployed manifest declares). Determinism
   proof: pilot_70d_3class_20260904_232534 model.pt is byte-identical (independent
   seed-42 double-run). Agent-17 forward-test freezes (FT-agent17-repeatability-v1,
   FT-...-1459de) fingerprint model_fingerprint c9982ddde1755591 + scaler b3c65b65.
4. Sidecars on the deployed path (meta 6953249e, scaler b3c65b65) are
   byte-identical to the promoted source sidecars — only model.pt drifted.
5. The drifted on-disk weights bb1f0afe (mtime 2026-09-07 08:10) are a
   post-promotion paper fine-tune product: tensor-diff vs c9982ddd across 24/31
   tensors, same key set/architecture; the training ledger shows the 2026-09-07
   paper runs (tr_phase12*/tr_confirm_e2e) were published only to candidate/
   subdirs as PAPER_GENERATED/production_eligible=false — no governed promotion
   of bb1f0afe exists anywhere (registry rows, promotion audit = 0 rows).
   => bb1f0afe is an ungoverned, non-production in-place mutation: NOT trusted,
   quarantined, not blessed (manifest was NOT rewritten to match it).

### Publication (Requirement 4/5/7)

Staged .recovery_staging_20260907 -> production verifiers (emission-gate
EMISSION_GATE_PASS head=3 input=70 seq=32 dataset=ds_70d_clean_m1_20260904;
verify_artifact_integrity VERIFIED) -> os.replace file-by-file, manifest LAST
(commit-marker semantics). Weights byte-identical to the promoted source — the
manifest digest was computed FROM those exact bytes at the original publication
and matches again. Quarantine: artifacts/forensics/
champion_recovery_quarantine_20260907/ (bb1f0afe bundle preserved for forensics).
Record: artifacts/forensics/champion_recovery_record_20260907.json.

### Validation (Requirement 8) — all executed against the LIVE serving path

- SHA-256: model.pt == c9982ddde... == manifest.model_sha256  PASS
- verify_bundle_against_manifest: every binding re-verified, no EmissionGateError PASS
- Manifest/schema validity: scalp_v3 / hash 235b8fccc96b7e0e / seq 32 / 3-class PASS
- 70D input contract: input_projection (128,70), scaler (70,) PASS
- Strict ScalpNet(70,3) load_state_dict + (2,32,70) forward -> (2,3) PASS
- verify_artifact_integrity: VERIFIED "digest matches manifest.json" PASS
- Serving resolution: engine _bundle.artifact_path = 70d_liquidity/model.pt,
  trainer rebound to scalp_v3/70D, CHAMPION VERIFIED hash=c9982ddde1755591 PASS
- Clean engine construction on DEFAULT AppConfig path (hermetic env): OK PASS

### Regression tests (Requirement 9)

NEW tests/unit/test_champion_bundle_recovery_contract.py (4 tests, all green):
1. coherent bundle loads + resolves as serving bundle (strict 70D/3-class)
2. weights mutated after publication -> HASH_MISMATCH, never served
3. partial publication (stale manifest) -> HASH_MISMATCH, cannot become serving
4. partial publication (manifest missing) -> LEGACY_UNVERIFIED, refused by
   default policy

Focused gate (all green, exit 0): test_champion_bundle_recovery_contract +
test_model_load_integrity + test_launcher_paper_boundary_bug212 +
test_hot_swap_governance + test_bug182b_online_train_width_contract +
test_liveengine_init_order_bug130. compileall exit 0.

### Honest notes

- No production behavior changed outside artifact publication/recovery; no src/
  file touched by this recovery (the uncommitted src/ diffs are OTHER agents').
- The registry still carries a stale CHAMPION row fingerprint (bb1f0afe...,
  scalp_v1/50d naming) written by a foreign agent's boot; it self-heals on the
  next engine boot (post-recovery construction re-registered c9982ddd/70D/scalp_v3).
- Multi-seed dispersion evidence for the champion remains OPEN (MLFix doc-log) —
  unchanged by this recovery.
