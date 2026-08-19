# 70D Production Release Forensics

> TASK-09-70D-PRODUCTION-RELEASE — Step 1 deliverable.
> Agent: Hermes-ProdRel | Date: 2026-08-19 | Branch: main
>
> This document is a forensic map of the ACTUAL production delivery architecture
> at task start. It answers the brief §2 questions for the real repository,
> not an idealized one. Every claim below was verified against the working
> tree and the local release artifacts (`release/v9.0.0`, `release/v9.1.0`).

---

## 1. Executive summary

The repository already contains the TASK-9 CLI update engine
(`src/nexus_scalp/release/updater.py`, commit `acdcd6f`), the TASK-10 database
migration engine (`src/nexus_scalp/database/`, commit `1966d42`) and the
TASK-11 hygiene worker (commit `93c55e5`). This TASK-9 continuation owns the
**70D production engineering layer**: model artifact release packaging,
runtime version-consistency across the app/db/feature/model/web stack, release
manifest schema coverage, the 70D installation/migration compatibility
matrices, and the TEST-REL-01..30 acceptance suite.

A parallel agent session has **uncommitted 70D work** in the working tree
(liquidity engine, `scalp_v4`/`scalp_liquidity_v1` schema registrations,
`liquidity_features_enabled` config flag). It is read-only for this task;
nothing in this task deletes, resets or stashes it.

---

## 2. Deployment architecture inventory (verified)

| Concern | Actual mechanism | Module |
| :--- | :--- | :--- |
| Application entrypoint (source) | `python -m nexus_scalp.cli.main` | `cli/main.py` |
| Packaged entrypoint | PyInstaller onedir `NexusScalpEngine.exe` / `NexusScalpEngine-CLI.exe`; `_internal/` bundle | `release/packaged_main.py`, `release/cli_shim.py` |
| CLI | Typer app: `nexus version`, `nexus update check/status/history/rollback/doctor`, `nexus db status/plan/migrate/verify/migrations/history/repair`, `nexus health`, `nexus doctor`, `nexus db hygiene …` | `cli/main.py`, `cli/db_commands.py` |
| Configuration | Pydantic `AppConfig` from `configs/base.yaml` (repo root for source runs; portable bundle root for packaged); `settings_service` secure store for credentials (`secrets.enc`, DPAPI), routing Telegram via `settings_service.set_telegram()` (INV-010, BUG-080) | `configuration/config.py`, `settings/` |
| Database bootstrap | Per-domain `CREATE TABLE IF NOT EXISTS` in each store (audit/news/candle_intel), WAL mode | `adapters/database/audit_repository.py`, `news/database.py`, `candle_intelligence/store.py` |
| Migration engine | TASK-10: per-domain versioned, checksummed migrations; baseline detection; startup gate; backup via SQLite streaming API; downgrade blocked; drift/tamper detection | `database/engine.py`, `database/registry.py`, `database/manifest.py`, `database/gate.py` |
| Model loader | `ModelLoadGate` (TASK-6): 10-gate check — artifact hash, manifest valid, model_id/version, schema registered, dimension vs state_dict, scaler dim/std, label schema, class_count, validation/lifecycle | `governance/load_gate.py` |
| Artifact registry | `ModelArtifactStore` writes `model.pt` + `model.scaler.npz` + `manifest.json` (feature_schema_id, feature_dimension, label_schema_id, class_count, artifact_hash, scaler_hash, algorithm versions via training records) | `model_generation/artifact_store.py` |
| Release packaging | `scripts/build/build_release.ps1` → checksums/manifest/SBOM via `scripts/build/update_helpers.py`; `release-manifest.json` embedded in portable tree; `.github/workflows/release.yml` mirrors locally | `release/packaging.py`, `scripts/build/update_helpers.py` |
| Windows installer | Inno Setup (`NexusScalpEngine-<v>-setup.exe`) — implemented, Portable path real-tested in TASK-9; installer path not yet exercised on a real run | `scripts/build/*.iss`, `docs/RELEASE.md` §5 |
| Web bundle | `Web/` served from repo root (dev) or `_internal/Web` (packaged). `index.html`+`app.js`+`api_client.js`+`styles.css`+`vendor/`. **No version marker inside the frontend** — no `WEB_BUNDLE_VERSION`, no build stamp in `app.js` | `web/server.py` `_resolve_web_dir` |
| Runtime directory separation | `release/paths.py`: install dir vs `%LOCALAPPDATA%\NexusScalpEngine` (data/config/logs/models); portable bundle keeps `artifacts/`, `data/`, `logs/` INSIDE the tree and `ApplicationInstaller` preserves them across swap (BUG-091 fix) | `release/paths.py`, `release/updater.py` |

---

## 3. The brief's TASK-2 questions — answered with evidence

### 3.1 How a brand-new user gets the current version
Download the zip/EXE from a GitHub release, unzip portable layout
(`NexusScalpEngine.exe`, `_internal/`, `Web/`, `configs/`, `artifacts/`,
`release-manifest.json`), run `NexusScalpEngine.exe` → first-start bootstrap
creates the databases (idempotent DDL), `nexus setup`/`nexus repair --model`
materialize the Champion model from the release bundle. `nexus health` reports
READY. **Proven**: release/v9.1.0 portable tree examined; clean-install test
in build_release.ps1.

### 3.2 How an existing user upgrades
`nexus update check` (GitHub Releases API only, never main.zip, INV-013) →
`nexus update` (check → download → SHA-256 + manifest verify → quiesce →
atomic user-data backup → canonical migration engine → zip-slip-safe tree swap
preserving `artifacts/ data/ logs/` → health gate → COMPLETED). State machine
via `UpdateState` (crash → ROLLBACK_REQUIRED in mutating states), single
instance lock, append-only `UpdateHistory` (no credentials). **Proven**: TASK-9
real Windows experiment v9.0.0→v9.1.0 via local GitHub stub.

### 3.3 How database migrations run
Startup gate (`database/gate.py::run_startup_migration_gate`) before READY:
version lookup → pending safe-additive migrations → apply → verify →
record in `schema_migrations` (checksummed). `nexus db migrate` and the
updater use the same engine. Baseline detection records legacy no-meta DBs at
baseline version. Backup happens before non-transactional migrations
(WAL-aware streaming backup). Log lines carry structured `[MIGRATION]` fields
(current/target/migration/status/duration_ms). **Proven**: TASK-10 handoff +
`tests/unit/test_database_migrations_phase18.py` (46 tests).

### 3.4 How model artifacts are discovered
`ModelArtifactStore` path convention `artifacts/models/<family>/<symbol>/<version>/`
containing `model.pt`, `model.scaler.npz`, `manifest.json`. Governance
registry (`governance/store.py`) tracks model_id/version/lifecycle_state/
artifact_hash/schema_id in DB tables + event ledger. Load gate resolves the
manifest and the weights/scaler paths (INV-013).

### 3.5 How 70D models are detected
The CANONICAL schema registry (`features/schema.py`) is the single source of
truth for dimensions: `scalp_v1` (50D, ACTIVE live contract), `scalp_v2`
(60D candidate, momentum family), `scalp_v4` (70D candidate contract
BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69), `scalp_liquidity_v1` (60D
liquidity candidate). A model manifest declaring `feature_schema_id` +
`feature_dimension` is 70D only if the schema id is registered AND the
dimension matches the registry (load gate step 4/5). The 70D layer is
candidate-only: nothing switches the live contract.

### 3.6 How 60D models remain compatible
Same mechanism: `scalp_v2` is registered with dimension 60; legacy 50D
`scalp_v1` stays ACTIVE. Load gate rejects unregistered schema ids. Training
writes candidate ids only (INV-016); promotion requires operator approval
(INV-015). No silent conversion/padding/truncation exists; none will be added.

### 3.7 How the UI bundle is selected
`web/server.py::_resolve_web_dir`: packaged `_internal/Web` next to the
package, else repo `Web/`. The choice is logged once; `is_dev_web_dir()`
distinguishes them.

### 3.8 How stale packaged UI is prevented — **GAP (fix in this task)**
There is **no web bundle version stamp** anywhere: `Web/app.js` has no build
id, the served assets carry no version header/query, the backend has no
`web_bundle_version`, and the release manifest does not record one. A stale
bundled `app.js` would be served silently. This is exactly the class of bug
BUG-079 / the TASK-brief §15-16 forbid. **Fix**: web-bundle version stamp
generated at build time, served by `/api/status`, declared in the release
manifest, compared by the diagnostics path.

### 3.9 How configuration is migrated
`ConfigMigrator` (updater.py) normalizes old config → current config →
validates, preserving unrelated overrides (TEST-UP-13). Secrets never leave
the secure store (SettingsGuard, TEST-UP-21). Pydantic `AppConfig` has a
default for the new `liquidity_features_enabled: bool = False` so old configs
without the key load unchanged (default false == prior 50D behavior).

### 3.10 How failed upgrades are recovered
UpdateState persists every transition; crash in mutating states →
`ROLLBACK_REQUIRED` (never blind restart); `nexus update rollback` uses
`RollbackEngine` (never restores old user data over migrated, BUG-091);
migration failures record `status=failed` + backup path and abort the update
transaction; `nexus db repair` available for drift. **Proven**: TEST-UP-16/
19/25, TEST-DBM-39/40.

---

## 4. Release artifacts present (evidence)

- `release/v9.0.0/windows/x64/manifests/release-manifest.json` — v9.0.0
- `release/v9.1.0/windows/x64/manifests/release-manifest.json` — v9.1.0
- `release/v9.1.0/windows/x64/portable/release-manifest.json` — embedded copy
- `release/v9.1.0/windows/x64/portable/NexusScalpEngine.exe` etc. (real bundle)
- Champion model: `artifacts/models/scalp/XAUUSD/v1.0.0/{model.pt,
  model.scaler.npz}` (50D) — no manifest.json present on disk (governance
  registry derives hashes from the files; load gate tolerates a missing
  manifest but requires artifact hashes when present).

### 4.1 Manifest gaps found (PROVEN, fixed later in this task)
The generated `release-manifest.json` hardcodes:
- `"feature_schema": "scalp_v1"` / `"model_compatibility": "scalp_v1 / 50D"`
  — not derived from the live schema registry; the next schema (scalp_v4 /
  70D) WILL drift silently.
- No `web_bundle_version`, no `supported_model_schemas`, no
  `db_schema_version`, no `required_migrations`, no `application_version`
  alias key (brief §37 schema).
These are not user-facing defects today (v9.x ships scalp_v1 only) but they
are the exact production-release gap this task closes.

### 4.2 Web bundle content (evidence)
`Web/` contains `index.html`, `app.js`, `api_client.js`, `styles.css`,
`tailwind.css`, `vendor/` — no version/build stamp inside any file.

---

## 5. Runtime directory separation (verified)

- `release/paths.py` policy: install dir = Program Files/repo root; user data
  = `%LOCALAPPDATA%\NexusScalpEngine` (config/logs/data/models/cache).
- Portable bundle is the exception: ships `artifacts/ data/ logs/` inside the
  bundle (user convenience), and `ApplicationInstaller.USER_DATA_DIRS`
  preserves them across update swaps (BUG-091 regression covered).
- `nexus upgrade/update` never touches credentials; env var
  `NEXUS_UPDATE_*` documented.

---

## 6. Gaps owned by this task (TASK-9 continuation)

| # | Gap | Section | Evidence |
| :--- | :--- | :--- | :--- |
| G1 | Web bundle version stamp missing (stale-UI class) | §15/16 | No stamp in Web/; no web_bundle_version anywhere |
| G2 | Release manifest schema coverage missing (feature_schema hardcoded) | §37 | packaging.py generate_manifest |
| G3 | Runtime version-consistency block (app/commit/db/feature/model/web) absent | §15/52 | /api/status has no version block; no VERSION_INCONSISTENCY |
| G4 | 70D model release packaging + compatibility classification (ACTIVE/LEGACY/RETAINED/ARCHIVABLE) + dependency check (MODEL_NOT_RUNTIME_COMPATIBLE) | §9/11/36/38 | No artifact-classifier exists; load gate covers schema only |
| G5 | Migration metadata for 70D/model registry versioning (schema_meta is migration-owned; no model/feature schema version stamp at DB level) | §4 | registry.py has no AUDIT-0005 |
| G6 | Installation compatibility matrix + migration/model/config test matrices (TEST-REL-01..30) | §25/26/27/48 | Existing suites cover TEST-UP/TEST-DBM; no TEST-REL |
| G7 | UI update/migration status (real backend data, no hardcoded) | §34/55 | /api/status lacks update/migration/version block |

Non-gaps (already satisfied by landed infrastructure, verified above): safe
update workflow (§17), resumable update state machine (§18/19), atomicity
(§19), user-data separation (§20), health check (§21), LIVE safety (§22/23,
INV-014), rollback invariants (§24, BUG-091), migration idempotency/order
(§44/45, TASK-10), no-delete policy (§47), hash verification (§31),
telemetry discipline (§33/35/54 — extend, do not duplicate).

---

## 7. Constraints honored

- No algorithm/label/Triple-Barrier/RiskEngine/lot-sizing/exit-logic changes.
- No governance bypass, no silent promotion, no validation-threshold lowering.
- No database deletion; destructive schema ops only via versioned migrations.
- 60D/70D stay candidate-only; live contract remains scalp_v1 (50D).
- Parallel-session working tree (70D liquidity) is untouched.