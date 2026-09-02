# 70D Installation Compatibility

> TASK-09-70D-PRODUCTION-RELEASE — brief §32/§25/§26/§27.
> Agent: Hermes-ProdRel | Date: 2026-08-19
> Status: verified against the working tree + local release artifacts
> (release/v9.0.0, release/v9.1.0) + real-DB migration copies.

This document is the installation compatibility contract: which old
installations, databases, configurations, models and Web bundles a new
release safely inherits — and what the migration chain does for each.

---

## 1. Supported old versions (application)

| Old release | Upgrade path | Evidence |
| :--- | :--- | :--- |
| v9.0.0 (portable) | `nexus update` (GitHub Releases → verify → backup → migrate → install → health) | TASK-9 real Windows experiment (v9.0.0 → v9.1.0 via local GitHub stub), TEST-UP-01..35 |
| v9.1.0 | `nexus update` — no-op / newer | TASK-9 + this task's version block |
| Any older source/dev checkout | `git pull` + reinstall; DB migrated at startup gate | TASK-10 startup gate |

Downgrade is blocked (`DB_DOWNGRADE_BLOCKED`) when the DB schema is newer
than the app expects (TASK-10 §23).

## 2. Database versions supported (migration chain)

| Domain | Legacy (no meta) | Current | Chain |
| :--- | :--- | :--- | :--- |
| audit.db | baseline → 1 | **7** (2026-08-19) | 0002 orders ticket index → 0003 exit-evidence columns → 0004 close_time index → 0005 governance audit tables → 0006 incident tables → **0007 release_metadata (TASK-9)** |
| news.db | baseline → 1 | **2** | 0002 source-health index |
| candle_intel.db | baseline → 1 | **2** | 0002 closure composite index |

- A legacy DB (no `schema_meta`) with the expected business tables is
  baseline-recorded at version 1 and the pending migrations are applied.
- Migration is idempotent (second run = NOT_REQUIRED), checksummed,
  transactional; backup (WAL-aware) precedes every run.
- **Real evidence**: copies of `artifacts/audit.db` migrated v6 → v7 —
  `integrity_check ok`, 266 ledger rows / PnL sum −5359.84 unchanged,
  3635 broker trades unchanged, FK clean, backup recorded.
- Users are NEVER instructed to delete audit.db / news.db / candle_intel.db.

## 3. Configuration versions supported

| Old config | Migration | Result |
| :--- | :--- | :--- |
| No `liquidity_features_enabled` key | Pydantic default `False` | prior 50D behavior preserved; the switch is explicit and off by default |
| Old model path | ConfigMigrator preserves user overrides; secure store untouched | TEST-REL-14, TEST-UP-13 |
| Old Telegram/risk settings | preserved; credentials NEVER move into plaintext YAML (INV-010, BUG-080) | TEST-REL-15, TEST-UP-21 |
| `config_schema_version` absent | stamped `1` (idempotent) | ConfigMigrator |

## 4. Model versions supported

| Model class | Schema | Runtime status | Action |
| :--- | :--- | :--- | :--- |
| Champion 50D | scalp_v1 | ACTIVE / COMPATIBLE | loaded by live engine; never replaced |
| 60D candidates | scalp_v2 (momentum) / scalp_liquidity_v1 (liquidity) | LEGACY / COMPATIBLE (no liquidity dependency for v2; liquidity producer required for liquidity schema) | retained, replayable, never auto-deleted |
| 70D candidates | scalp_v4 | LEGACY / COMPATIBLE **only when** base scalp_v1 + liquidity producer available | dependency-gated at load (MODEL_NOT_RUNTIME_COMPATIBLE otherwise) |
| Unregistered schema id | any | FEATURE_SCHEMA_MISMATCH | blocked at load gate + release classifier |

Classification taxonomy: ACTIVE / LEGACY / RETAINED / ARCHIVABLE
(`nexus model-artifacts`). Nothing is pruned by a release.

## 5. Web bundle versions supported

| Bundle | Detection | Mismatch |
| :--- | :--- | :--- |
| Stamped `web_bundle_version` in build-info.json | reported by `/api/status` versioning block + `nexus version --json` | VERSION_INCONSISTENCY |
| Unstamped (dev) | live content-hash of served app.js/api_client.js/index.html/styles.css | same |
| Stale bundled app.js | live-hash catches it when it disagrees with the stamp | UI_VERSION_MISMATCH class, surfaced |

## 6. Rollback capabilities

- Application rollback: `nexus update rollback` — restores the previous
  application bundle; NEVER restores old user data over migrated data
  (BUG-091 regression covered by TEST-UP-19).
- Database rollback: migrations are additive + backup-before-change; a
  failed migration records the backup path and aborts (never destructive
  file replacement). `nexus db repair` handles drift.
- LIVE safety: updates are BLOCKED while the engine is LIVE without the
  explicit `--force` maintenance quiesce (INV-014, TEST-UP-10).

## 7. What an upgrade NEVER does

- Deletes or recreates any database.
- Deletes old 60D/50D model artifacts.
- Deletes research evidence / accounting history / model registry.
- Rewrites credentials.
- Silently promotes a 70D model.
- Replaces schema by file deletion.