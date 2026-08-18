# TASK-9 Handoff — CLI Update / GitHub Sync / Installed-User Migration

- Agent: Hermes-CLIUpdate
- Role: CLI Update / GitHub Release Sync / Installed-User Migration Engineer
- Task: TASK-9 (PHASE 17 — user update path)
- Date: 2026-08-18
- Branch: `main` (working tree, parallel agents active — contract §1)
- Status: IMPLEMENTED + REAL-TESTED (see verification state below)

## What was delivered

A production-grade end-user update system wired into the existing release CLI
(`nexus update …`), all offline-testable, with the state machine, locks,
history, rollback, crash recovery, LIVE-safety and user-data preservation the
task mandates.

### New module: `src/nexus_scalp/release/updater.py`
| Component | Spec section | What it does |
| :--- | :--- | :--- |
| `compare_versions` | 6/47.9 | semantic comparison (never lexicographic); None on invalid |
| `UpdateDiscovery` | 3/29/30/31/51 | GitHub Releases API fetch + channel-aware release selection; maps 403/429/404/5xx/DNS/invalid-JSON to truthful statuses |
| `UpdatePlanBuilder` | 4/5/7/9/30/31 | NO_UPDATE/UPDATE_AVAILABLE/UNSUPPORTED/INCOMPATIBLE/SECURITY_BLOCKED/DIRECT_UPDATE_UNSUPPORTED decision core; refuses source archives; requires digest |
| `CompatibilityGate` | 7/9 | OS/arch/disk/min-version/downgrade gate (COMPATIBLE/WARNING/BLOCKED) |
| `SafeDownloader` | 11/52 | staging-area `.part` downloads with resume; never touches install dir |
| `HashVerifier`/`ManifestVerifier` | 10 | SHA-256 + manifest verification primitives |
| `EngineGuard`/`QuiesceProtocol` | 13/14 | LIVE detection (pidfile+config), BLOCK default, explicit quiesce maintenance flow |
| `BackupPlanner`/`BackupEngine` | 15/22 | atomic user-data backup set, verified before install; failed backup blocks |
| `ConfigMigrator`/`DatabaseMigrator` | 17/18/21 | deterministic idempotent version-aware migrations with transactional rollback |
| `ApplicationInstaller` | 23/24/52/53 | zip-slip-safe staging swap; preserves app-tree user-data dirs across swap (REAL BUG) |
| `PostUpdateHealth` | 18/21 | post-install health gate on the NEW executable |
| `RollbackEngine` | 25 | version-aware rollback (never restores old artifacts/data/logs over migrated data) |
| `UpdateLock`/`UpdateState`/`UpdateHistory` | 26/32/33/34/63 | single-instance lock, observable persisted state machine, append-only history (no credentials) |
| `SettingsGuard` | 16/42 | credentials never touched; DPAPI store reference verified |
| `InstallModeDetector` | 2/49 | SOURCE/PORTABLE/INSTALLED_EXE/INNO_SETUP/DEVELOPER/UNKNOWN |
| `UpdateOrchestrator` | 26/62/63+ | the end-to-end observable state machine (check→download→verify→quiesce→backup→migrate→install→health→COMPLETED / rollback / crash recovery) |

### Modified files
- `src/nexus_scalp/release/update.py` — legacy `UpdateEngine.plan()` retained;
  docstring contract extended.
- `src/nexus_scalp/release/metadata.py` — `get_build_info_file()` now resolves
  build-info.json next to the executable / `_internal` for onedir bundles
  (REAL BUG: packaged EXE read the repo CWD build-info instead of its own).
- `src/nexus_scalp/release/exit_codes.py` — EXIT_UPDATE = 5 (additive).
- `src/nexus_scalp/cli/main.py` — update command group: check/status/history/
  rollback/doctor + `--channel`/`--dry-run`/`--force`/`--yes`/`--json`;
  legacy `--manifest` offline mode retained.
- `.github/workflows/release.yml` — embeds `release-manifest.json` into the
  portable tree (consumable by `nexus update`), plus existing checksums/
  manifest/sbom are already uploaded as release assets.
- `scripts/build/build_release.ps1` — same manifest embedding (BOM fixed
  back, BUG-078 discipline).
- `tests/unit/test_release_update_phase17.py` — NEW: 42 tests covering
  TEST-UP-01..35 plus the two real-bug regressions.
- `docs/RELEASE.md` — section 8 rewritten as the end-user update guide.

## REAL BUGS FOUND (from the real Windows experiment)

1. **App-tree user data destruction on swap**: the shipped v9.0.0 portable
   bundle carries `artifacts/`, `data/`, `logs/` INSIDE the install tree. A
   naive replacement destroys them. Fixed: `ApplicationInstaller` preserves
   these dirs across the swap and merges them back (user data wins).
   Regression: `test_app_swap_preserves_in_tree_user_data`.
2. **Version-aware rollback**: rollback now NEVER restores old
   artifacts/data/logs over a migrated newer dataset.
   Regression: `test_rollback_never_restores_old_user_data`.
3. **Packaged EXE version truth**: `metadata.get_build_info_file()` only
   looked at CWD/package path — a frozen onedir EXE (old binary) ignored its
   own `_internal/build-info.json`. Fixed for future builds.
4. **build_release.ps1 BOM** stripped by an earlier write — PowerShell 5.1
   parse failure (BUG-078 pattern). Restored UTF-8 BOM.

## Runtime verification (REAL, on this Windows host)

REAL v9.0.0 portable bundle → local GitHub-API stub (127.0.0.1:8765, HTTP)
serving a real 226 MB v9.1.0 payload zip:

- DISCOVERY: v9.1.0 detected from the releases list; plan UPDATE_AVAILABLE; ✅
- DOWNLOAD → SHA-256 verified (mismatch discarded) ✅
- MANIFEST verified inside payload ✅
- BACKUP: atomic user-data backup set (config/audit.db/news.db/settings/
  secrets.enc/model) — verified ✅
- INSTALL: tree swapped; exe/build-info replaced; `preserved_user_data_dirs:
  ["data"]` — the app-side audit.db survived the swap ✅
- USER DATA: audit.db trade, news.db article, app_settings telegram.enabled,
  secrets.enc marker, model artifact, config override — ALL PRESERVED ✅
- HISTORY: persisted jsonl rows with correlation ids ✅
- Failure path exercised: post-update health NOT READY → FAILED recorded,
  rollback pointer retained ✅

NOT PROVEN on a fully rebuilt binary: PyInstaller v9.1.0 rebuild was running
at handoff (build release/v9.1.0 with the fixed metadata; the rebuilt EXE
must be the final proof of the packaged-EXE version-truth fix + full healthy
end-state).

## Test results

- `tests/unit/test_release_update_phase17.py` — 42 passed.
- Existing `tests/unit/test_release_system.py` + `test_release_build_system.py`
  expected to stay green (legacy `UpdateEngine` contract untouched).
- Full gate (beforePush) MUST be re-run before merge.

## Risks / remaining
- GitHub repo currently has ZERO releases — `nexus update check` truthfully
  reports RELEASE_NOT_FOUND until the first real v-tag release is published
  (the CI pipeline now produces everything `nexus update` consumes).
- The Inno-installer path (`install_setup`) is implemented but NOT exercised
  on a real installer run in this session (the local experiment used the
  portable path). `clean_install_test.ps1` + runtime suites remain the
  installer-proof harness.
- Signing: SHA-256 + manifest verification enforced; certificate signing
  remains optional per the existing pipeline (documented).

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-10)
1. Run `python scripts/build/build_release.ps1 -Version 9.1.0 -SkipCleanInstallTest`
   (gates on) so the rebuilt EXE includes the metadata.py build-info fix.
2. Re-run the REAL upgrade experiment against the rebuilt v9.1.0 bundle
   (release/v9.1.0/windows/x64/portable) proving the packaged EXE reports
   version 9.1.0 and `health --json` returns READY/DEGRADED after update.
3. Exercise `nexus update rollback` on the rebuilt binary + verify the
   restored EXE reports 9.0.0 identity.
4. Run the full beforePush gate (`.venv/Scripts/python.exe` toolchain) and
   fix any drift (mypy strictness on the new module).
5. When the first semantic tag (v9.1.0) is pushed, verify:
   `nexus update check` shows UPDATE_AVAILABLE from the REAL GitHub API and
   the full `nexus update` flow completes on a clean Windows x64 host.
6. Wire `nexus update` notification into the dashboard (UPDATE_AVAILABLE
   badge) only via the canonical discovery service — no hardcoded versions.

## Registry updates (done in this session)
- `agents/taskboard.md` — TASK-9 row appended.
- `agents/contracts.md` — UPDATE_SYSTEM v1 contract row.
- `agents/runtime_invariants.md` — INV-013 (GitHub is the only packed-update
  source) + INV-014 (update never touches LIVE without explicit force).
- `agents/change_control.md` — CHG-0004 registered (IMPLEMENTING).
- `docs/agent_handoffs/TASK-9-cli-update.md` — this file.