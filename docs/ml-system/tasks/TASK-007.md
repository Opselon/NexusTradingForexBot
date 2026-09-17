# TASK-007 — Signed Official Model Bundle Verification & Staging Slot Isolation

Priority: P1
Status: EXECUTABLE AFTER TASK-004
Type: Distribution & Security
Dependencies: TASK-004
Blocks: TASK-015
Human Decision Required: NO
Risk: Medium (Cryptographic verification and atomic directory swapping)
Estimated Scope: Distribution verification drill, isolation test suite (~150 LOC)

## Objective
Verify the official model distribution pipeline (PR #250 rev2 contract), validating cryptographic Ed25519 signature checks, slot locking (.official-install.lock), and atomic staging isolation.

## Problem / Why
If official model bundles downloaded from GitHub Releases can be intercepted, tampered with, or corrupted during installation, untrusted model weights could be executed in live client environments.

## Current Evidence
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
- **Path:** `.github/workflows/official-model.yml` (Lines: `1-250`)
  - **Symbol:** `build-official-model-bundle`
  - **Behavior:** CI workflow that builds, validates, and packages official candidate bundles
  - **Classification:** `CI/CD`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Execute full offline distribution drill using mock cryptographic keys: build bundle -> generate Ed25519 signature -> stage -> verify -> install.
2. Assert that tampered weights (bit flip in model.pt) trigger Immediate SignatureMismatchError or HashMismatchError.
3. Assert that concurrent installations respect .official-install.lock and do not corrupt the active slot.

## Non-Goals
Do not sign or release unvalidated models to public GitHub releases.

## Preconditions
TASK-004 complete; Ed25519 key generation available.

## Dependencies
TASK-004

## Blocks
TASK-015 (ML Contract Drift Gate)

## Source Areas
- `src/nexus_scalp/model_provisioning/official_contract.py:1-350`
- `src/nexus_scalp/model_provisioning/official_install.py:1-420`

## Investigation
Verify that SlotManager asserts persistent lock liveness rather than lock file absence, matching project conventions.

## Implementation Plan
1. Run existing test suite pytest tests/unit/test_official_contract.py tests/unit/test_official_install.py.
2. Create tests/integration/test_official_model_tamper_proofing.py.
3. Test bit-flip tampering on model.pt: assert installation aborts before symlink swap.
4. Test missing scaler in bundle: assert verification fails.
5. Test process kill during extraction: assert rollback to previous slot succeeds.

## Tests
- `pytest tests/unit/test_official_contract.py -v`
- `pytest tests/unit/test_official_install.py -v`
- `pytest tests/integration/test_official_model_tamper_proofing.py -v`

## Validation / Benchmark
100% of tampered bundles are rejected with zero changes to active production slot.

## Evidence Required
- Test output showing 143+ focused official model distribution tests green
- Tamper-proofing test output demonstrating fail-closed rejection of corrupted artifacts

## Acceptance Criteria
1. All official model contract and installation tests pass.
2. Tampered or unsigned bundles fail closed.
3. Slot switching is atomic with persistent file locking.

## Failure / Abort Conditions
If any tampered file can be installed into active slot, STOP and report critical security failure.

## Human Stop Conditions
None.

## Expected Output
Verified and tamper-proof model distribution and slot management pipeline.
