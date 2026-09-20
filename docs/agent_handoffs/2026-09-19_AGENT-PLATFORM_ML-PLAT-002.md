# 2026-09-19 — AGENT-PLATFORM — ML-PLAT-002

## Signed Official Model Bundle Verification & Staging Slot Isolation

**Status:** DONE — PR opened
**Branch:** `agent/platform/ml-plat-002` (off `origin/main` `e6a1b6b0`)
**Task:** `docs/ml-system/tasks/ML-PLAT-002.md` (P1, STREAM K, dep `ML-GOV-001` DONE)

## What was delivered

A full **offline distribution drill** with mock Ed25519 keys (socket connect hook
fails the test on any real egress): build test-only trained bundle → sign →
stage → verify → install, then 29 adversarial regressions against every
fail-closed contract of the rev2 official model pipeline.

New file: `tests/integration/test_official_model_tamper_proofing.py`
(registered in `tests/critical_suite.txt`, manifest verified: 211 paths).

## Defects found in production code (both fail-OPEN, both fixed)

### 1. Staging re-verified the wrong manifest (signature bypass)

`src/nexus_scalp/model_provisioning/official_install.py:71-79` copied
`manifest.json` from the bundle **directory** into staging, then `_verify_files`
re-read that copy. Any tamper held only in the caller's `verified.manifest`
dict — a rewritten signature, a foreign `key_id`, rebound digests — was
**silently bypassed**: the untouched on-disk copy re-verified cleanly and the
install **proceeded**.

Proven by the drill: `test_invalid_signature_rejected[zeroed]` and
`test_foreign_signing_key_rejected` both failed with `DID NOT RAISE`.

**Fix:** the staged manifest is now seeded from `verified.manifest` — the signed
contract the caller actually holds — so `_verify_files` re-checks exactly that
contract. When the on-disk file encodes the identical contract, its original
bytes are preserved verbatim (publisher serialization, indent=2), so published
and installed manifest bytes stay byte-identical for the comparators in
`test_official_model_distribution_e2e.py:205` and
`test_official_model_install.py:129`.

### 2. Missing payload file → bare `FileNotFoundError` (fail-open)

A required bundle file deleted **after** verification (e.g. `model.scaler.npz`)
hit `shutil.copy2` in the staging loop and raised an untyped
`FileNotFoundError` — the signed-contract error code (`FILE_MISSING`) was lost
and the failure was not attributable.

**Fix:** the source directory is re-verified (`_verify_files`) before any
staging copy, and the copy loop now raises `FILE_MISSING` for a missing regular
file. `test_required_file_removed_rejected` pins it.

No other production file changed. `official_contract.py` needed no change — the
rev2 static contract itself is sound; the gap was in the installer's re-check
ordering.

## Acceptance criteria — evidence

**AC-1 — all official model contract and installation tests pass.**
Combined run of the new battery + all 5 pre-existing official-model suites:

```
test_official_model_tamper_proofing.py
test_official_model_distribution_e2e.py
test_official_model_install.py
test_official_model_contract.py
test_official_model_setup.py
test_official_model_publication.py
→ 129 passed, 2 warnings in 9.76s
```

**AC-2 — tampered or unsigned bundles fail closed.**

| Probe | Result |
|---|---|
| bit-flip `model.pt` / `model.scaler.npz` | `SHA256_MISMATCH` / `ARTIFACT_INTEGRITY_FAILED`, before the slot swap |
| unsigned manifest field edit (dimension, class_count, model_version, bundle_id) | `SIGNATURE_INVALID` / `CONTRACT_MISMATCH` |
| signature stripped / zeroed / truncated | `SIGNATURE_INVALID` / `MANIFEST_MALFORMED` |
| valid signature, foreign untrusted `key_id` | rejected (trust-root lookup) |
| trusted-key signature over a replaced `key_id` | rejected |
| rebound `model_sha256` / `scaler_sha256` / `metadata_sha256` | rejected |
| required file removed from bundle | `FILE_MISSING` |
| payload filename outside the allowlist bound by the manifest | rejected |
| symlinked payload (path-traversal class) | `INSTALL_UNSAFE_PATH` / `FILE_MISSING` |
| symlinked serving slot | `INSTALL_UNSAFE_PATH` |

**AC-3 — slot switching is atomic with persistent file locking.**

- Crash injected at the first `os.replace` → journal left at `INSTALLING` →
  `recover_official_install` restores the exact previous bytes and clears the
  journal + backup.
- No partial new bytes can survive the crash window (asserted explicitly).
- A hand-edited foreign change made **after** the crash is never overwritten
  (`RECOVERY_CONFLICT`; the operator's bytes remain untouched).
- The persistent `.official-install.lock` is asserted for **liveness** after
  every install (never unlinked, never a symlink), and a competing holder
  **process** is excluded with `INSTALL_CONFLICT`.

**BENCHMARK_PLAN — 50 install drills:** 50 tampered attempts → 50 rejected,
0 accepted, slot byte-identical after every attempt, no journal/backup residue.

**Hardest guarantee (TAMPER-15):** tamper rejection provably **precedes the
first slot mutation**. `_write_journal`, `_safe_paths` and every `os.replace`
are spied; on a rejected install none of them is reached and no staging
directory is left behind.

## Gates

```
ruff check        → All checks passed!
ruff format --check → 2 files already formatted
mypy src/nexus_scalp/model_provisioning/official_install.py → Success: no issues found
verify_critical_suite_manifest.py → CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist
```

## Notes for reviewers

- `_assert_engine_stopped`, `_assert_replaceable` and `_validate_servable` are
  doubled in the drill (they read a pidfile / the live audit registry / repeat
  an already-exercised runtime probe). **Signature and SHA256 verification are
  exercised for real** — tamper detection must not depend on the doubled probes.
- `test_symlinked_slot_directory_rejected` points the slot symlink at a
  resolvable sibling directory rather than leaving it dangling: CPython 3.11's
  `Path.mkdir(exist_ok=True)` raises a bare `EEXIST` on a dangling symlink
  instead of reaching the `INSTALL_UNSAFE_PATH` guard. The symlink is unlinked
  in a `finally` (never the target) so the `tmp_path` fixture stays clean.
- The bundle builder reuses the shared e2e fixture
  `build_test_only_trained_bundle` (a small *trained* synthetic classifier —
  never a starter/provisioner model, never published).
