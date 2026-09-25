# TASK-09-70D-PRODUCTION-RELEASE — Handoff

**Agent:** Hermes-ProdRel
**Role:** Production Deployment / Migration / Runtime Reliability Engineer
**Task:** TASK-09-70D-PRODUCTION-RELEASE (production engineering layer for the approved 70D system)
**Date:** 2026-08-19
**Branch:** `main`
**Starting HEAD:** `4001e4c` (HERMES-TASK1: ruff-format release hardening)
**Ending HEAD:** see Git section below (7 agent-labelled step commits)

---

## Summary

Delivered the production engineering layer the brief mandates: the 70D/
Liquidity system is now installable, upgradeable, migration-safe,
runtime-safe, observable and recoverable for BOTH new installations and
existing users — on top of the already-landed TASK-9 (CLI update engine),
TASK-10 (migration engine) and TASK-11 (hygiene) infrastructure. NO
algorithm/label/risk/execution changes; NO governance bypass; NO silent
promotion; NO database deletion; the parallel-session 70D liquidity
working tree was never touched.

## What was delivered (step commits)

| Step | Deliverable | Commit |
| :--- | :--- | :--- |
| 1 | Forensic deployment audit → `docs/70D_PRODUCTION_RELEASE_FORENSICS.md` (+ agent working notes) | `04cbecd` |
| 2 | `release/model_artifacts.py` — artifact identity (model_id/version/schema_id/dimension/schema_hash/artifact_hash/scaler_hash/algorithm_versions), ACTIVE/LEGACY/RETAINED/ARCHIVABLE classification (brief §38), 70D dependency check (base scalp_v1 + liquidity producer; MODEL_NOT_RUNTIME_COMPATIBLE with exact reason, NO silent fallback §11/§14). CLI `nexus model-artifacts [--json]`. 14 tests (TEST-REL-09/10/11/12/13/26) | `4444324` |
| 3 | `release/versioning.py` — RuntimeVersionBlock: application_version, commit, web_bundle_version (build stamp OR live content-hash of ACTUAL served assets — stale bundled app.js cannot hide, §16), feature_schema from canonical registry, per-domain DB schema versions, VERSION_INCONSISTENCY verdict (§15/§52). Wired into `/api/status` (`versioning` block) + `nexus version --json`. 10 tests (TEST-REL-16/27/30) | `733902f` |
| 4 | Release manifest schema coverage (§37): feature_schema/feature_schema_dimension/supported_model_schemas/web_bundle_version/db_schema_version/required_migrations ALL registry-derived (BUG-109: was hardcoded scalp_v1/50D). 5 tests (TEST-REL-27) | `6397085` |
| 5 | AUDIT-0007-release-metadata migration (audit v6→v7, additive release_metadata key/value table). **Proven on a copy of the real artifacts/audit.db**: v6→v7, integrity ok, 266 ledger rows / PnL −5359.84 unchanged, FK clean, backup recorded. Fixed the manifest/baseline interaction (migration-owned tables MUST NOT be manifest-listed — INV-018; aligns with parallel BUG-108 fix). 4 tests (TEST-REL-03/04/05) + phase18 suite updated 6→7 | `e6c3763` |
| 6 | TEST-REL-01..30 acceptance suite completed: `test_release_acceptance_phase19.py` (9 tests — fresh install, in-place upgrade, PnL bit-identical, ledger/research preserved, config migration preserves unrelated values, secrets never plaintext, failed-update recovery, realistic old-install chain). Full release suite: 84 passed | `296502e` |
| 7 | Docs: `docs/70D_INSTALLATION_COMPATIBILITY.md`, `docs/70D_PRODUCTION_DEPLOYMENT.md`, `docs/70D_UPDATE_AND_MIGRATION.md` (§32/§58) | `5cde486` |

Registries (committed): taskboard TASK-9 VERIFIED, contracts MODEL_RELEASE v1 +
VERSION_CONSISTENCY v1, runtime_invariants INV-018/019, change_control
CHG-0013 VERIFIED, bugs.md BUG-109 (manifest hardcode) + cross-ref BUG-108,
repository_state snapshot.

## Verification state

- VERIFIED: all 84 release tests (phase17 42 + 5 phase19 files 42),
  phase18 migration suite (43), ruff clean, mypy clean on new modules.
- VERIFIED: real-DB copy migration (audit v6→v7) with financial invariants.
- VERIFIED: CLI smoke — `nexus model-artifacts` classifies real artifacts
  (champ COMPATIBLE, 6D candidates rejected with reason),
  `nexus version --json` reports live web_bundle hash + CONSISTENT.
- NOT VERIFIED: full PyInstaller rebuild + real `nexus update` from GitHub
  (repo still has ZERO tagged releases; build_release.ps1 needs Git +
  signing environment). The release/v9.1.0 bundle predates this task's
  manifest schema coverage — a REBUILD is required to carry the new fields.
- NOT VERIFIED: dedicated UI panel for the versioning block (the API
  contract exists; frontend wiring is a UI task — brief §34/§55 backend
  truth is in place).

## Commits (agent-labelled, stepwise per user mandate)

```
04cbecd Hermes-ProdRel: TASK-9 forensic deployment audit ...
4444324 Hermes-ProdRel: model artifact release packaging + 70D/60D ...
733902f Hermes-ProdRel: runtime version-consistency block ...
6397085 Hermes-ProdRel: release manifest schema coverage ...
e6c3763 Hermes-ProdRel: AUDIT-0007 release_metadata migration ...
296502e Hermes-ProdRel: TEST-REL-01..30 acceptance suite ...
5cde486 Hermes-ProdRel: 70D installation/deployment/update-migration docs ...
```

## Files changed (this task)

- src/nexus_scalp/release/model_artifacts.py (NEW)
- src/nexus_scalp/release/versioning.py (NEW)
- src/nexus_scalp/release/packaging.py
- src/nexus_scalp/database/registry.py, database/manifest.py
- src/nexus_scalp/web/server.py, cli/main.py
- tests/unit/test_release_model_artifacts_phase19.py (NEW),
  test_release_versioning_phase19.py (NEW), test_release_manifest_phase19.py
  (NEW), test_release_migration_0007_phase19.py (NEW),
  test_release_acceptance_phase19.py (NEW), test_cli_db_phase18.py
- docs/70D_PRODUCTION_RELEASE_FORENSICS.md, docs/70D_INSTALLATION_
  COMPATIBILITY.md, docs/70D_PRODUCTION_DEPLOYMENT.md,
  docs/70D_UPDATE_AND_MIGRATION.md (NEW)
- agents/agent-9-70d-production.md (NEW), agents/taskboard.md,
  agents/contracts.md, agents/runtime_invariants.md,
  agents/change_control.md, agents/bugs.md, agents/repository_state.md

## Known risks / remaining

- PROVEN: schema versions audit=7, news=2, candle_intel=2 — the REAL
  artifacts/*.db are still at audit=6 until the app next starts (startup
  gate migrates automatically; NO user action needed).
- NOT PROVEN: packaged release with the new manifest/version fields
  (requires `scripts/build/build_release.ps1` rerun — Git + signing env).
- NOT PROVEN: `nexus update` against a REAL GitHub release (no v-tag yet).
- UNKNOWN: none blocking.

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-10)

1. Rebuild the release bundle: `powershell -File scripts/build/build_release.ps1 -Version 9.2.0` (or next version) so release-manifest.json carries the NEW schema-coverage fields (feature_schema_dimension, supported_model_schemas, web_bundle_version, db_schema_version, required_migrations) and build-info.json stamps web_bundle_version.
2. Verify the packaged EXE reports: `nexus version --json` → web_bundle block CONSISTENT; `nexus model-artifacts` lists the bundled model with its class; `nexus db status` → audit=7 (startup gate migrates automatically).
3. Publish the first tagged GitHub release (v9.2.0) with the CI artifacts; then verify `nexus update check` → UPDATE_AVAILABLE and the FULL update flow on a clean Windows x64 host (TASK-9's instructions step 5).
4. Wire the dashboard panel for the `/api/status` versioning block (UPDATE AVAILABLE / MIGRATION PENDING / CURRENT / DEGRADED rendered from REAL backend data — no hardcoded status; brief §34/§55).
5. If any migration/metadata needs change: register a NEW AUDIT-0008 migration in database/registry.py (never edit DDL outside the registry — INV-013; migration-owned tables never manifest-listed — INV-018).
6. Re-run beforePush (`.venv/Scripts/python.exe` toolchain) and the packaged-app smoke after the rebuild.