# ML-PLAT-002 — Signed Official Model Bundle Verification & Staging Slot Isolation

STREAM: STREAM K — PLATFORM/MT5
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-GOV-001
AGENT_ROLE: AGENT-GIT
OWNERSHIP_SCOPE: src/nexus_scalp/model_provisioning/official_contract.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify the official model distribution pipeline (PR #250 rev2 contract), validating cryptographic Ed25519 signature checks, slot locking (.official-install.lock), and atomic staging isolation.

## WHY_IT_EXISTS
If official model bundles downloaded from GitHub Releases can be intercepted, tampered with, or corrupted during installation, untrusted model weights could be executed in live client environments.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_provisioning/official_contract.py` (Lines: `1-350`)
  - **Symbol:** `DistributionManifestV2, verify_bundle_signature`
  - **Behavior:** Verifies Ed25519 signature of manifest.json; checks schema_hash and consumer bounds
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_provisioning/official_install.py` (Lines: `1-420`)
  - **Symbol:** `SlotManager, install_official_bundle`
  - **Behavior:** Acquires flock on .official-install.lock; stages in slot_staging; verifies hashes; atomic symlink swap
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Ed25519 signature verification enforced.
- Persistent lock file .official-install.lock used.

## UNKNOWNs
- Slot symlink swap behavior on Windows file systems with active open file handles.

## SCOPE
Execute full offline distribution drill using mock cryptographic keys: build bundle -> generate signature -> stage -> verify -> install; assert tampered weights fail closed.

## NON_GOALS
Do not sign or release unvalidated models to public GitHub releases.

## SOURCE_AREAS
- `src/nexus_scalp/model_provisioning/official_contract.py`
- `src/nexus_scalp/model_provisioning/official_install.py`

## FILES_LIKELY_TO_CHANGE
- `tests/integration/test_official_model_tamper_proofing.py`

## INVESTIGATION_PLAN
Verify that SlotManager asserts persistent lock liveness rather than lock file absence.

## IMPLEMENTATION_PLAN
1. Create tests/integration/test_official_model_tamper_proofing.py.
2. Test bit-flip tampering on model.pt: assert installation aborts before symlink swap.
3. Test missing scaler in bundle: assert verification fails.
4. Test process kill during extraction: assert rollback to previous slot succeeds.

## TEST_PLAN
- `pytest tests/integration/test_official_model_tamper_proofing.py -v`

## BENCHMARK_PLAN
Execute 50 install drills; assert 100% of tampered bundles rejected.

## EVIDENCE_REQUIRED
- Test file: tests/integration/test_official_model_tamper_proofing.py
- Passing pytest execution output

## ACCEPTANCE_CRITERIA
1. All official model contract and installation tests pass.
2. Tampered or unsigned bundles fail closed.
3. Slot switching is atomic with persistent file locking.

## ABORT_CONDITIONS
If any tampered file can be installed into active slot, STOP and report critical security failure.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/integration/test_official_model_tamper_proofing.py`

## SHARED_FILE_RISK
Low. AGENT-GIT owns release distribution verification.
