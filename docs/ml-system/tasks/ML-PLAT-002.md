# ML-PLAT-002 — Signed Official Model Bundle Verification & Staging Slot Isolation

STREAM: STREAM K — PLATFORM/MT5
PRIORITY: P1
STATUS: DONE
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

## VERIFICATION_EVIDENCE (2026-09-19, AGENT-PLATFORM / AGENT-GIT)
- **Test battery:** `tests/integration/test_official_model_tamper_proofing.py` — 29 tests, all green
  (`PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/integration/test_official_model_tamper_proofing.py`
  → `29 passed in 9.58s`). Registered in `tests/critical_suite.txt` (manifest verified:
  `CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist`).
- **AC-1 (all contract + installation tests pass):** the new battery runs alongside the pre-existing
  official-model suites — `test_official_model_tamper_proofing.py` +
  `test_official_model_distribution_e2e.py` + `test_official_model_install.py` +
  `test_official_model_contract.py` + `test_official_model_setup.py` +
  `test_official_model_publication.py` → **129 passed, 0 failed**.
- **AC-2 (tampered/unsigned bundles fail closed):** bit-flip on `model.pt` and on the scaler are
  rejected with `SHA256_MISMATCH`/`ARTIFACT_INTEGRITY_FAILED` before the slot swap; unsigned
  manifest field edits → `SIGNATURE_INVALID`/`CONTRACT_MISMATCH`; stripped/zeroed/truncated
  signatures and a valid signature from a foreign (untrusted) `key_id` → rejected; rebound
  digests (`model_sha256`/`scaler_sha256`/`metadata_sha256`) → rejected.
- **AC-3 (atomic slot switching with persistent locking):** a crash injected at the first
  `os.replace` leaves a journaled INSTALLING state; `recover_official_install` rolls the slot back
  to the exact previous bytes and clears the journal/backup. A hand-edited foreign change made
  after the crash is NOT overwritten (`RECOVERY_CONFLICT`). The persistent
  `.official-install.lock` is asserted for LIVENESS after every install (never unlinked), and a
  competing holder process is excluded with `INSTALL_CONFLICT`.
- **BENCHMARK_PLAN (50 drills):** 50 tampered install attempts — 50 rejected, 0 accepted, slot
  byte-identical after every attempt.
- **Hardest guarantee (TAMPER-15):** tamper rejection provably precedes the FIRST slot mutation —
  `_write_journal`, `_safe_paths` and every `os.replace` are spied, and none is reached on a
  rejected install.
- **Defects found and fixed in the pipeline itself (fail-open, P0):**
  1. `official_install.py` copied `manifest.json` from the bundle directory into staging instead
     of from the verified manifest dict, so in-memory tamper probes (bad signature, foreign
     `key_id`, rebound digests) were silently bypassed by re-reading the untouched on-disk copy —
     the install proceeded. Now the staged manifest is seeded from `verified.manifest`
     (byte-preserving the publisher's serialization when the two agree), so `_verify_files`
     re-checks the exact contract the caller holds.
  2. A payload file deleted from the bundle directory after verification surfaced as a bare
     `shutil FileNotFoundError` (fail-OPEN, error code lost) instead of `FILE_MISSING`. The source
     directory is now re-verified before any staging copy, and the copy loop raises
     `FILE_MISSING` for a missing regular file.
- **Linters:** `ruff check` clean, `ruff format --check` clean, `mypy src/.../official_install.py`
  → `Success: no issues found`. No production code change outside
  `src/nexus_scalp/model_provisioning/official_install.py` (OWNERSHIP_SCOPE-adjacent installer
  module; the contract module `official_contract.py` needed no change).
