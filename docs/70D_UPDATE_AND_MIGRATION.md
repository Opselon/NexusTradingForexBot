# 70D Update and Migration

> TASK-09-70D-PRODUCTION-RELEASE — brief §58.
> Agent: Hermes-ProdRel | Date: 2026-08-19
> End-user + operator guide: upgrade path, migration path, rollback,
> crash recovery, CLI surface, UI status, troubleshooting.

---

## 1. Upgrade path (end user)

```
nexus update check          # what's available, no mutation
nexus update                # plan -> confirm -> backup -> download ->
                            # verify -> migrate -> install -> health -> COMPLETE
```

- Source: GitHub Releases ONLY (INV-013) — never main.zip.
- SHA-256 + release-manifest verification before any install.
- LIVE engine BLOCKS the update (INV-014) unless the documented `--force`
  maintenance quiesce is used — the updater never kills a live process and
  never liquidates positions.
- User databases/config/models are never touched by a normal update; the
  atomic backup set is verified before install.
- State machine is crash-recoverable: a crash in a mutating state reports
  ROLLBACK_REQUIRED (never a half-installed "healthy").

## 2. CLI surface (TASK-9 + this task)

| Command | Purpose |
| :--- | :--- |
| `nexus version` / `--json` | app version + build identity + **web_bundle identity + version_status** |
| `nexus update check` | current/target/size/migrations/compat/backup/live status — no mutation |
| `nexus update` | full update flow (plan→confirm→backup→download→verify→migrate→install→health) |
| `nexus update status` | persisted state machine; crash recovery verdict |
| `nexus update history` | append-only update journal (no credentials) |
| `nexus update rollback` | restore previous application (never old user data over migrated) |
| `nexus update doctor` | update-system diagnostics |
| `nexus db status/plan/migrate/verify/migrations/history/repair` | canonical migration engine (same engine the startup gate + updater use) |
| `nexus model-artifacts` | model release inventory: class + runtime compatibility (TASK-9) |
| `nexus health` / `doctor` | health matrix incl. DB migration state + versioning |
| `nexus forensic --deploy-gate` | release pre-flight (TASK-11) |

## 3. Migration path (automatic)

At startup the gate (`database/gate.py`) runs BEFORE READY:

```
DB VERSION -> CURRENT SCHEMA VERSION -> PENDING MIGRATIONS ->
SAFE ORDER -> APPLY -> VERIFY
```

- Log lines carry structured `[MIGRATION]` fields
  (current=/target=/migration=/status=/duration_ms=) — no silent migration.
- Migration failure STOPS startup (MIGRATION_FAILED with migration_id,
  database, stage, error, correlation_id, backup path) — a partially
  migrated runtime never starts.
- Backups are WAL-aware, retention-bounded, recorded with
  backup_id/schema_version/created_at/hash/size.
- Post-migration verification: `PRAGMA integrity_check`,
  `PRAGMA foreign_key_check`, schema version, required tables/indexes/
  columns, then accounting invariants (broker trade count, ledger count,
  realized PnL, outcome count, research count, registries).

### Current migration chain (2026-08-19)

| Domain | Current version | Migrations |
| :--- | :--- | :--- |
| audit | 7 | 0002..0007 (incl. **AUDIT-0007-release-metadata**, TASK-9) |
| news | 2 | 0002 |
| candle_intel | 2 | 0002 |

## 4. Rollback

- `nexus update rollback` — application rollback to the previous release;
  **never** restores old user data over migrated data (BUG-091).
- Database rollback is forward-compatible: migrations are additive with
  backup-before-change; failed migrations record the backup path. Downgrade
  of the DB is blocked, not forced.
- Failed release health check → FAILED recorded + rollback pointer
  retained (TEST-UP-16/18/19).

## 5. Crash recovery (brief §18/§42)

`UPDATE_STATE` machine: IDLE → CHECKING → DOWNLOADING → VERIFYING → READY →
QUIESCING → BACKING_UP → MIGRATING → INSTALLING → VERIFYING_INSTALL →
HEALTH_CHECK → COMPLETED | FAILED | ROLLBACK_REQUIRED.

A crash in BACKING_UP/MIGRATING/INSTALLING/VERIFYING_INSTALL/HEALTH_CHECK
reports ROLLBACK_REQUIRED on the next start — the state file is never
ignored, and no half-updated installation is ever presented as healthy.

## 6. UI status (real backend data — no hardcoded)

The dashboard's `/api/status` now carries a `versioning` block:
application_version, commit, web_bundle_version, feature schema,
per-domain database schema versions, migration state, and
version_status (CONSISTENT / VERSION_INCONSISTENCY). The UI can render
UPDATE AVAILABLE / MIGRATION PENDING / CURRENT / DEGRADED from this data
(the API contract exists; a dedicated panel is a UI task, not a backend
fabrication).

## 7. What is never required of the user

- Delete audit.db / news.db / candle_intel.db.
- Recreate schemas.
- Copy model files by hand.
- Edit configuration files unnecessarily.
- Lose history / accounting / research evidence.

## 8. Troubleshooting

| Symptom | Cause / action |
| :--- | :--- |
| update blocked (UPDATE_BLOCKED) | disk/live/db-locked/backup/verification — read the exact reason in `nexus update status`; resolve, retry |
| RELEASE_NOT_FOUND | no tagged GitHub release exists yet — publish the first v-tag (CI produces everything `nexus update` consumes) |
| VERSION_INCONSISTENCY | backend/web/db/model drift — see the `problems[]` list; rebuild the release bundle atomically |
| MODEL_NOT_RUNTIME_COMPATIBLE | schema/dimension/scaler/liquidity-producer missing — `nexus model-artifacts` names it |
| DB_MIGRATION_FAILED | backup path + failed state recorded; `nexus db repair`; never delete the DB |
| UI_VERSION_MISMATCH | stale bundled app.js vs backend — the live content-hash detects it; rebuild the Web bundle together with the backend |

## 9. Release telemetry

`[UPDATE]` structured logs carry event/from_version/to_version/
database_version/migration_version/model_schema/ui_version/status/
duration_ms — never secrets. Update completion is reported ONLY after the
post-update health gate passes (no fake success).